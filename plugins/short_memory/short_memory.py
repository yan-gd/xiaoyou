# -*- coding:utf-8 -*-
import os
import re
import json
import time
import threading
from datetime import datetime

import requests
import plugins
from plugins import *
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger


DATA_FILE = os.path.join(os.path.dirname(__file__), "short_memory.json")
LOCK = threading.Lock()


@plugins.register(
    name="ShortMemory",
    desc="Short term conversational memory for Xiaoyou",
    version="0.1",
    author="yoyo",
    desire_priority=15,
)
class ShortMemory(Plugin):
    def __init__(self):
        super().__init__()
        self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
        self.handlers[Event.ON_DECORATE_REPLY] = self.on_decorate_reply
        logger.info("[ShortMemory] inited")

    def on_handle_context(self, e_context: EventContext):
        if not self._enabled():
            return

        context = e_context["context"]
        kwargs = getattr(context, "kwargs", {}) or {}

        # 小悠目前主要是私聊陪伴，群聊短期记忆先不混在一起。
        if kwargs.get("isgroup"):
            return

        if context.type not in (ContextType.TEXT, ContextType.IMAGE, ContextType.VOICE):
            return

        session_id = self._get_session_id(context)
        if not session_id:
            return

        if context.type == ContextType.TEXT:
            text = self._extract_plain_user_text(context.content)
            if not text:
                return

            if self._is_list_cmd(text):
                self._mark_skip_reply(context)
                e_context["reply"] = Reply(ReplyType.TEXT, self._list_memory(session_id))
                e_context.action = EventAction.BREAK_PASS
                return

            if self._is_clear_cmd(text):
                self._clear_session(session_id)
                self._mark_skip_reply(context)
                e_context["reply"] = Reply(ReplyType.TEXT, "好，我把这阵子的聊天小尾巴清掉啦。")
                e_context.action = EventAction.BREAK_PASS
                return

            original_content = str(context.content or "")
            short_context = self._build_injection(session_id)
            if short_context:
                context.content = """[小悠的短期记忆]
以下内容是你和 YoYo 最近的聊天，只用于自然接话、延续情绪和避免重复追问。
不要主动说“根据短期记忆”，不要逐条复述，像真的记得刚刚聊过一样自然使用。

%s

[已有上下文与当前消息]
%s""" % (short_context, original_content)

            self._append_message(session_id, "user", text)
            self._mark_context_session(context, session_id)
            return

        if context.type == ContextType.IMAGE:
            self._append_message(session_id, "user", "[YoYo 发来了一张图片]")
            self._mark_context_session(context, session_id)
            return

        if context.type == ContextType.VOICE:
            self._append_message(session_id, "user", "[YoYo 发来了一条语音]")
            self._mark_context_session(context, session_id)

    def on_decorate_reply(self, e_context: EventContext):
        if not self._enabled():
            return

        reply = e_context["reply"]
        context = e_context["context"]

        if not reply or reply.type != ReplyType.TEXT:
            return

        text = str(reply.content or "").strip()
        if not text:
            return

        kwargs = getattr(context, "kwargs", {}) or {}
        if kwargs.get("isgroup"):
            return
        if kwargs.get("short_memory_skip_reply"):
            return

        session_id = kwargs.get("short_memory_session_id") or self._get_session_id(context)
        if not session_id:
            return

        self._append_message(session_id, "assistant", self._clean_message(text))

    def _enabled(self):
        return os.getenv("SHORT_MEMORY_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")

    def _summary_enabled(self):
        return os.getenv("SHORT_MEMORY_SUMMARY_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")

    def _get_session_id(self, context):
        kwargs = getattr(context, "kwargs", {}) or {}
        return kwargs.get("session_id") or kwargs.get("receiver") or "default"

    def _mark_context_session(self, context, session_id):
        try:
            kwargs = getattr(context, "kwargs", {}) or {}
            kwargs["short_memory_session_id"] = session_id
            context.kwargs = kwargs
        except Exception:
            pass

    def _mark_skip_reply(self, context):
        try:
            kwargs = getattr(context, "kwargs", {}) or {}
            kwargs["short_memory_skip_reply"] = True
            context.kwargs = kwargs
        except Exception:
            pass

    def _append_message(self, session_id, role, content):
        content = self._clean_message(content)
        if not content:
            return

        now = int(time.time())

        with LOCK:
            data = self._load_all()
            item = data.get(session_id, self._empty_session())
            item.setdefault("messages", [])
            item.setdefault("summaries", [])
            item.setdefault("pending_archive", [])
            item["messages"].append({
                "role": role,
                "content": content,
                "ts": now,
            })
            item["updated_at"] = now
            item = self._trim_session(item)
            data[session_id] = item
            self._save_all(data)

        logger.info("[ShortMemory] appended %s message for session=%s", role, session_id)

    def _trim_session(self, item):
        messages = item.get("messages", [])
        now = int(time.time())
        raw_ttl = int(os.getenv("SHORT_MEMORY_RAW_TTL_SECONDS", "86400"))
        max_messages = int(os.getenv("SHORT_MEMORY_MAX_MESSAGES", "60"))

        keep = []
        archive = []

        for msg in messages:
            ts = int(msg.get("ts") or 0)
            if raw_ttl > 0 and ts and now - ts > raw_ttl:
                archive.append(msg)
            else:
                keep.append(msg)

        if len(keep) > max_messages:
            archive.extend(keep[:-max_messages])
            keep = keep[-max_messages:]

        item["messages"] = keep
        item["summaries"] = self._trim_summaries(item.get("summaries", []))

        if archive:
            item = self._queue_archive(item, archive)

        return item

    def _trim_summaries(self, summaries):
        ttl = int(os.getenv("SHORT_MEMORY_SUMMARY_TTL_SECONDS", "604800"))
        if ttl <= 0:
            return summaries

        now = int(time.time())
        return [
            s for s in summaries
            if now - int(s.get("updated_at") or s.get("created_at") or 0) <= ttl
        ]

    def _queue_archive(self, item, archive):
        if not self._summary_enabled():
            return item

        item.setdefault("pending_archive", [])
        item["pending_archive"].extend(archive)

        min_messages = int(os.getenv("SHORT_MEMORY_SUMMARY_MIN_MESSAGES", "8"))
        if len(item["pending_archive"]) < min_messages:
            max_pending = int(os.getenv("SHORT_MEMORY_PENDING_ARCHIVE_MAX", "80"))
            item["pending_archive"] = item["pending_archive"][-max_pending:]
            return item

        summary = self._generate_summary(item.get("summaries", []), item["pending_archive"])
        if not summary:
            max_pending = int(os.getenv("SHORT_MEMORY_PENDING_ARCHIVE_MAX", "80"))
            item["pending_archive"] = item["pending_archive"][-max_pending:]
            return item

        now = int(time.time())
        item.setdefault("summaries", [])
        item["summaries"].append({
            "text": summary,
            "created_at": now,
            "updated_at": now,
        })

        max_summaries = int(os.getenv("SHORT_MEMORY_MAX_SUMMARIES", "8"))
        item["summaries"] = item["summaries"][-max_summaries:]
        item["pending_archive"] = []
        return item

    def _generate_summary(self, old_summaries, archive):
        api_key = os.getenv("OPEN_AI_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
        base = (os.getenv("OPEN_AI_API_BASE") or "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
        model = os.getenv("SHORT_MEMORY_SUMMARY_MODEL") or os.getenv("MODEL") or "qwen3.7-plus"

        if not api_key:
            logger.warning("[ShortMemory] summary enabled but api key is missing")
            return ""

        old_text = "\n".join("- " + s.get("text", "") for s in old_summaries[-3:] if s.get("text"))
        chat_text = self._format_messages(archive, limit=80)

        prompt = """你是小悠的短期聊天摘要器。
请把一段过期的微信聊天压缩成给小悠自己看的短期摘要，用于未来几天自然接话。

只保留：
- YoYo 这几天正在经历的事、情绪、烦恼、期待
- 刚聊过但未来几天可能还会接上的话题
- 小悠已经答应过的陪伴、催促、关心点

不要保留：
- 没意义寒暄
- 一次性问题细节
- 密码、验证码、密钥等敏感凭证
- “系统/摘要/模型”这类措辞

输出 3 到 6 条短句即可，不要 Markdown 标题。

已有短期摘要：
%s

要压缩的聊天：
%s""" % (old_text if old_text else "暂无", chat_text)

        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            "temperature": 0.2,
            "max_tokens": 260,
            "enable_thinking": False,
        }

        headers = {
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
        }

        try:
            r = requests.post(
                base + "/chat/completions",
                headers=headers,
                json=payload,
                timeout=45,
            )

            if r.status_code >= 400 and "enable_thinking" in r.text:
                payload.pop("enable_thinking", None)
                r = requests.post(
                    base + "/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=45,
                )

            if r.status_code >= 400:
                logger.warning("[ShortMemory] summary error %s: %s", r.status_code, r.text[:500])
                return ""

            data = r.json()
            text = data["choices"][0]["message"]["content"].strip()
            return self._clean_summary(text)

        except Exception:
            logger.exception("[ShortMemory] summary failed")
            return ""

    def _build_injection(self, session_id):
        item = self._get_session(session_id)
        messages = item.get("messages", [])
        summaries = item.get("summaries", [])

        parts = []

        if summaries:
            summary_lines = []
            for summary in summaries[-3:]:
                text = summary.get("text", "").strip()
                if text:
                    summary_lines.append(text)
            if summary_lines:
                parts.append("[最近几天的简短摘要]\n" + "\n".join(summary_lines))

        inject_messages = int(os.getenv("SHORT_MEMORY_INJECT_MESSAGES", "24"))
        recent = messages[-inject_messages:]
        if recent:
            parts.append("[刚刚聊过的内容]\n" + self._format_messages(recent, limit=inject_messages))

        if not parts:
            return ""

        text = "\n\n".join(parts)
        max_chars = int(os.getenv("SHORT_MEMORY_INJECT_MAX_CHARS", "2200"))
        if max_chars > 0 and len(text) > max_chars:
            text = text[-max_chars:]

        return text

    def _format_messages(self, messages, limit=24):
        lines = []
        for msg in messages[-limit:]:
            role = msg.get("role")
            name = "YoYo" if role == "user" else "小悠"
            content = self._clean_message(msg.get("content", ""))
            if content:
                lines.append("%s：%s" % (name, content))
        return "\n".join(lines)

    def _list_memory(self, session_id):
        item = self._get_session(session_id)
        messages = item.get("messages", [])
        summaries = item.get("summaries", [])

        if not messages and not summaries:
            return "我这边暂时还没有短期聊天记忆。"

        parts = []
        if summaries:
            parts.append("最近摘要：")
            for summary in summaries[-3:]:
                text = summary.get("text", "").strip()
                if text:
                    parts.append(text)

        if messages:
            parts.append("刚刚聊过：")
            for line in self._format_messages(messages[-12:], limit=12).splitlines():
                parts.append(line)

        return "\n".join(parts)

    def _is_list_cmd(self, text):
        patterns = [
            r"^查看短期记忆$",
            r"^短期记忆$",
            r"^最近聊了什么$",
            r"^你刚刚记得什么$",
        ]
        return any(re.search(p, text) for p in patterns)

    def _is_clear_cmd(self, text):
        patterns = [
            r"^清空短期记忆$",
            r"^删除短期记忆$",
            r"^忘掉刚刚的聊天$",
            r"^清空最近聊天$",
        ]
        return any(re.search(p, text) for p in patterns)

    def _extract_plain_user_text(self, content):
        text = str(content or "").strip()

        markers = [
            "现在 YoYo 回复：",
            "[用户当前消息]",
        ]

        for marker in markers:
            if marker in text:
                text = text.rsplit(marker, 1)[1].strip()

        return self._clean_message(text)

    def _clean_message(self, text):
        text = str(text or "").strip()
        text = re.sub(r"\s+", " ", text)
        max_chars = int(os.getenv("SHORT_MEMORY_MESSAGE_MAX_CHARS", "500"))
        if max_chars > 0 and len(text) > max_chars:
            text = text[:max_chars]
        return text

    def _clean_summary(self, text):
        text = str(text or "").strip()
        text = re.sub(r"^```(?:text)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()
        max_chars = int(os.getenv("SHORT_MEMORY_SUMMARY_MAX_CHARS", "800"))
        if max_chars > 0 and len(text) > max_chars:
            text = text[:max_chars]
        return text

    def _empty_session(self):
        now = int(time.time())
        return {
            "messages": [],
            "summaries": [],
            "pending_archive": [],
            "created_at": now,
            "updated_at": now,
        }

    def _get_session(self, session_id):
        with LOCK:
            data = self._load_all()
            return data.get(session_id, self._empty_session())

    def _clear_session(self, session_id):
        with LOCK:
            data = self._load_all()
            data[session_id] = self._empty_session()
            self._save_all(data)
            logger.info("[ShortMemory] cleared session=%s", session_id)

    def _load_all(self):
        if not os.path.exists(DATA_FILE):
            return {}
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            logger.exception("[ShortMemory] load failed")
            return {}

    def _save_all(self, data):
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DATA_FILE)
