# -*- coding:utf-8 -*-
import os
import re
import time

import plugins
from plugins import *
from bridge.reply import ReplyType
from bridge.context import ContextType
from common.log import logger
from lib import itchat


@plugins.register(
    name="SplitReply",
    desc="Split long text replies into multiple WeChat bubbles",
    version="0.1",
    author="yoyo",
    desire_priority=99,
)
class SplitReply(Plugin):
    def __init__(self):
        super().__init__()
        self.handlers[Event.ON_SEND_REPLY] = self.on_send_reply
        logger.info("[SplitReply] inited")

    def on_send_reply(self, e_context: EventContext):
        if not self._enabled():
            return

        reply = e_context["reply"]
        context = e_context["context"]

        if not reply or reply.type != ReplyType.TEXT:
            return

        text = str(reply.content or "").strip()
        if not text:
            return

        # 太短不拆
        min_len = int(os.getenv("SPLIT_REPLY_MIN_LEN", "18"))
        if len(text) < min_len:
            return

        # 技术/代码类内容尽量不拆，避免格式炸掉
        if "```" in text or text.startswith("{") or text.startswith("["):
            return

        receiver = self._get_receiver(context)
        if not receiver:
            logger.warning("[SplitReply] receiver not found, skip split")
            return

        parts = self._split_text(text)

        if len(parts) <= 1:
            return

        max_parts = int(os.getenv("SPLIT_REPLY_MAX_PARTS", "6"))
        parts = parts[:max_parts]

        logger.info("[SplitReply] split reply into %s bubbles: %r", len(parts), parts)

        for idx, part in enumerate(parts):
            part = part.strip()
            if not part:
                continue

            if idx > 0:
                delay = self._delay_for_part(part)
                logger.info("[SplitReply] delay %.2fs before bubble %s, chars=%s", delay, idx + 1, len(part))
                time.sleep(delay)

            itchat.send(part, toUserName=receiver)

        # 阻止原来的整段消息再次发送
        # 老版 CoW 某些位置不会自动尊重 BREAK，所以这里也把原 reply 清空
        try:
            reply.content = ""
        except Exception:
            pass

        try:
            if context is not None:
                kwargs = getattr(context, "kwargs", {}) or {}
                kwargs["split_reply_sent"] = True
                context.kwargs = kwargs
        except Exception:
            pass

        e_context.action = EventAction.BREAK

    def _enabled(self):
        return os.getenv("SPLIT_REPLY_ENABLED", "true").strip().lower() in (
            "1", "true", "yes", "on"
        )

    def _get_receiver(self, context):
        if context is None:
            return None

        kwargs = getattr(context, "kwargs", {}) or {}

        receiver = kwargs.get("receiver")
        if receiver:
            return receiver

        msg = kwargs.get("msg")
        if msg is not None:
            return (
                getattr(msg, "from_user_id", None)
                or getattr(msg, "other_user_id", None)
                or getattr(msg, "to_user_id", None)
            )

        return None

    def _delay_for_part(self, part):
        try:
            delay_per_char = float(os.getenv("SPLIT_REPLY_DELAY_PER_CHAR", "0.2"))
        except Exception:
            delay_per_char = 0.2

        if delay_per_char < 0:
            delay_per_char = 0

        return len(str(part or "")) * delay_per_char

    def _split_text(self, text):
        text = text.strip()

        # 先按模型主动换行拆
        raw_parts = []
        for line in re.split(r"\n+", text):
            line = line.strip()
            if line:
                raw_parts.append(line)

        # 如果没有换行，再按中文聊天常见标点拆
        if len(raw_parts) <= 1:
            raw_parts = self._split_by_sentence(text)

        # 对过长的单段继续细拆
        max_chars = int(os.getenv("SPLIT_REPLY_MAX_CHARS", "28"))
        final_parts = []

        for part in raw_parts:
            part = part.strip()
            if not part:
                continue

            if len(part) <= max_chars:
                final_parts.append(part)
            else:
                final_parts.extend(self._split_long_part(part, max_chars))

        # 合并特别短的碎片，避免一个 emoji 一个泡
        final_parts = self._merge_tiny_parts(final_parts)

        return [p for p in final_parts if p.strip()]

    def _split_by_sentence(self, text):
        # 保留标点
        pieces = re.split(r"([。！？!?~～…]+)", text)
        parts = []

        buf = ""
        for piece in pieces:
            if not piece:
                continue

            buf += piece

            if re.match(r"^[。！？!?~～…]+$", piece):
                if buf.strip():
                    parts.append(buf.strip())
                buf = ""

        if buf.strip():
            parts.append(buf.strip())

        # 如果还是拆不开，就按逗号类再拆
        if len(parts) <= 1:
            parts = [p.strip() for p in re.split(r"[，,；;]", text) if p.strip()]

        return parts

    def _split_long_part(self, part, max_chars):
        # 长句优先按逗号/停顿拆
        chunks = [x.strip() for x in re.split(r"([，,；;、])", part) if x.strip()]

        parts = []
        buf = ""

        for chunk in chunks:
            if len(buf + chunk) <= max_chars:
                buf += chunk
            else:
                if buf.strip():
                    parts.append(buf.strip())
                buf = chunk

        if buf.strip():
            parts.append(buf.strip())

        # 如果还是有超长段，硬切
        final = []
        for p in parts:
            if len(p) <= max_chars:
                final.append(p)
            else:
                for i in range(0, len(p), max_chars):
                    final.append(p[i:i + max_chars])

        return final

    def _merge_tiny_parts(self, parts):
        min_chars = int(os.getenv("SPLIT_REPLY_TINY_MERGE", "6"))

        merged = []
        buf = ""

        for part in parts:
            part = part.strip()
            if not part:
                continue

            if not buf:
                buf = part
                continue

            if len(buf) < min_chars:
                buf = buf + "\n" + part
            else:
                merged.append(buf)
                buf = part

        if buf:
            merged.append(buf)

        return merged
