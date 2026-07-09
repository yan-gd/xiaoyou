# -*- coding:utf-8 -*-
import os
import requests

import plugins
from plugins import *
from bridge.reply import Reply, ReplyType
from common.log import logger
from plugins.xiaoyou_common.time_context import build_time_context


@plugins.register(
    name="PatPatReply",
    desc="Use LLM to naturally reply to real WeChat patpat events",
    version="0.2-clean",
    author="yoyo",
    desire_priority=9999,
)
class PatPatReply(Plugin):
    def __init__(self):
        super().__init__()
        self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
        logger.info("[PatPatReply] inited")

    def on_handle_context(self, e_context: EventContext):
        context = e_context["context"]
        kwargs = getattr(context, "kwargs", {}) or {}

        if kwargs.get("isgroup"):
            return

        text = str(context.content or "").strip()
        msg = kwargs.get("msg")
        raw = str(getattr(msg, "__dict__", "")) if msg is not None else ""
        joined = "\n".join([text, raw])

        # 只识别真正拍一拍，不再拦截 hello 的默认介绍 prompt
        if "拍了拍" not in joined and "拍拍" not in joined and "patpat" not in joined.lower():
            return

        logger.info("[PatPatReply] real patpat detected text=%r", text[:120])

        reply = self._ask_llm(text)
        if reply:
            e_context["reply"] = Reply(ReplyType.TEXT, reply)
            e_context.action = EventAction.BREAK_PASS
        else:
            # 不允许预设回复：模型失败就沉默吃掉
            e_context.action = EventAction.BREAK

    def _ask_llm(self, raw_text):
        api_key = os.getenv("OPEN_AI_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            return ""

        base = (os.getenv("OPEN_AI_API_BASE") or "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
        model = os.getenv("PATPAT_REPLY_MODEL") or os.getenv("MODEL") or "qwen3.7-plus"
        character_desc = os.getenv("CHARACTER_DESC", "").strip()
        _xiaoyou_time_context = build_time_context()
        if _xiaoyou_time_context and _xiaoyou_time_context not in character_desc:
            character_desc = (character_desc + "\n\n" + _xiaoyou_time_context).strip()

        prompt = f"""你要替小悠回复一个微信“拍一拍”。

小悠的人设：
{character_desc}

事件：
YoYo 刚刚在微信里拍了拍小悠。

原始内容：
{raw_text}

要求：
1. 直接输出小悠要发给 YoYo 的微信内容。
2. 不要介绍功能，不要提 #help，不要像机器人。
3. 不要说系统、插件、事件、接口。
4. 可以傲娇、撒娇、反击、调侃，像女朋友被拍了一下。
5. 通常 1 到 3 行，按语义自然换行。
6. 不要使用固定模板。
"""

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.85,
            "max_tokens": 260,
            "enable_thinking": False,
        }

        headers = {
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
        }

        try:
            r = requests.post(base + "/chat/completions", headers=headers, json=payload, timeout=45)

            if r.status_code >= 400 and "enable_thinking" in r.text:
                payload.pop("enable_thinking", None)
                r = requests.post(base + "/chat/completions", headers=headers, json=payload, timeout=45)

            if r.status_code >= 400:
                logger.warning("[PatPatReply] llm error %s: %s", r.status_code, r.text[:500])
                return ""

            return r.json()["choices"][0]["message"]["content"].strip()[:500]
        except Exception:
            logger.exception("[PatPatReply] llm failed")
            return ""
