# -*- coding:utf-8 -*-
import os
import re
import requests

import plugins
from plugins import *
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger
from plugins.xiaoyou_common.time_context import build_time_context


@plugins.register(
    name="XiaoyouChat",
    desc="Xiaoyou's own normal text chat handler",
    version="0.2-context-clean",
    author="yoyo",
    desire_priority=-10000,
)
class XiaoyouChat(Plugin):
    def __init__(self):
        super().__init__()
        self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
        logger.info("[XiaoyouChat] inited")

    def on_handle_context(self, e_context: EventContext):
        if not self._enabled():
            return

        context = e_context["context"]

        if context.type != ContextType.TEXT:
            return

        kwargs = getattr(context, "kwargs", {}) or {}
        if kwargs.get("isgroup"):
            return

        raw_context = str(context.content or "").strip()
        if not raw_context:
            return

        current_text = self._extract_plain_user_text(raw_context)
        if not current_text:
            return

        if current_text.startswith("$"):
            logger.info("[XiaoyouChat] ignore legacy dollar command text=%r", current_text[:100])
            return

        logger.info("[XiaoyouChat] handling normal text chat current_text=%r", self._log_safe_text(current_text))

        reply = self._ask_llm(raw_context, current_text)

        if reply:
            e_context["reply"] = Reply(ReplyType.TEXT, reply)
            e_context.action = EventAction.BREAK_PASS
            return

        logger.warning("[XiaoyouChat] no reply sent because llm failed and preset fallback is disabled")
        e_context.action = EventAction.BREAK

    def _enabled(self):
        return os.getenv("XIAOYOU_CHAT_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")

    def _ask_llm(self, raw_context, current_text):
        api_key = os.getenv("OPEN_AI_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            logger.warning("[XiaoyouChat] OPEN_AI_API_KEY missing")
            return ""

        base = (os.getenv("OPEN_AI_API_BASE") or "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
        model = os.getenv("XIAOYOU_CHAT_MODEL") or os.getenv("MODEL") or "qwen3.7-plus"
        character_desc = os.getenv("CHARACTER_DESC", "").strip()
        time_context = build_time_context()

        system_prompt = """%s

%s

额外规则：
- 你正在微信里和 YoYo 日常聊天。
- 直接输出小悠要发给 YoYo 的微信内容。
- 不要 Markdown，不要标题，不要解释你的思考。
- 不要说自己是模型、系统、插件、接口或 AI。
- 不要把当前现实时间当成固定回复模板。
- 不要每次主动报时，除非 YoYo 明确问时间。
- 按语义自然换行；一行就是一条微信消息。
""" % (
            character_desc,
            time_context,
        )

        user_prompt = self._build_user_prompt(raw_context, current_text)

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": float(os.getenv("XIAOYOU_CHAT_TEMPERATURE", os.getenv("TEMPERATURE", "0.75"))),
            "max_tokens": int(os.getenv("XIAOYOU_CHAT_MAX_TOKENS", "700")),
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
                timeout=int(os.getenv("XIAOYOU_CHAT_TIMEOUT", "45")),
            )

            if r.status_code >= 400 and "enable_thinking" in r.text:
                payload.pop("enable_thinking", None)
                r = requests.post(
                    base + "/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=int(os.getenv("XIAOYOU_CHAT_TIMEOUT", "45")),
                )

            if r.status_code >= 400:
                logger.warning("[XiaoyouChat] llm error %s: %s", r.status_code, r.text[:500])
                return ""

            content = r.json()["choices"][0]["message"].get("content", "")
            return self._clean_reply(content)

        except Exception:
            logger.exception("[XiaoyouChat] llm failed")
            return ""

    def _build_user_prompt(self, raw_context, current_text):
        raw_context = str(raw_context or "").strip()
        current_text = str(current_text or "").strip()

        if raw_context and raw_context != current_text:
            return """下面是上游插件整理给你的上下文，只能作为参考，不要逐条复述，也不要说“根据记忆/根据上下文”。

[参考上下文]
%s

[YoYo 当前原话]
%s""" % (
                raw_context[:5000],
                current_text,
            )

        return """[YoYo 当前原话]
%s""" % current_text

    def _extract_plain_user_text(self, content):
        text = str(content or "").strip()

        markers = [
            "YOYO 当前发来的微信消息：",
            "YoYo 当前发来的微信消息：",
            "[用户当前消息]",
            "[已有上下文与当前消息]",
            "现在 YoYo 回复：",
        ]

        for marker in markers:
            if marker in text:
                text = text.rsplit(marker, 1)[1].strip()

        lines = [x.strip() for x in text.splitlines() if x.strip()]
        if lines:
            text = lines[-1]

        text = re.sub(r"^(YoYo|YOYO|用户|我)[:：]\s*", "", text).strip()
        return re.sub(r"\s+", " ", text)

    def _clean_reply(self, text):
        text = str(text or "").strip()
        text = re.sub(r"^```(?:text)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip().strip('"“”')
        text = re.sub(r"^(小悠|Xiaoyou|AI|助手)[:：]\s*", "", text).strip()
        return text[:1200] if text else ""

    def _log_safe_text(self, text):
        text = str(text or "").replace("\n", " ")
        return text[:120]
