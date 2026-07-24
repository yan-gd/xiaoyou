# -*- coding: utf-8 -*-
"""Model-first reply-medium decisions for authenticated App text turns.

There are deliberately no intent keywords, regular expressions, or local
semantic shortcuts in this module. Every eligible App text reply is judged
from the current exchange and recent conversation by a lightweight model.
"""

import json
import os
from dataclasses import dataclass

from common.log import logger
from plugins.xiaoyou_common.context_service import build_context_snapshot
from plugins.xiaoyou_common.model_gateway import chat_completion
from plugins.xiaoyou_common.thinking_config import build_thinking_payload


MEDIUM_TEXT = "text"
MEDIUM_VOICE = "voice"
ALLOWED_MEDIA = (MEDIUM_TEXT, MEDIUM_VOICE)


@dataclass(frozen=True)
class AppVoiceReplyDecision:
    medium: str = MEDIUM_TEXT
    confidence: float = 0.0
    reason: str = ""
    model_ok: bool = False
    forced: bool = False

    @property
    def use_voice(self):
        return self.medium == MEDIUM_VOICE


class AppVoiceReplyDecisionService:
    """Choose text or voice without replacing Xiaoyou's generated reply."""

    def __init__(self):
        self.enabled = _truthy(
            os.getenv("XIAOYOU_APP_TEXT_VOICE_DECISION_ENABLED", "true")
        )
        self.model = (
            os.getenv("XIAOYOU_APP_VOICE_ROUTE_MODEL", "qwen3.7-plus").strip()
            or "qwen3.7-plus"
        )
        self.timeout = max(
            5,
            min(
                int(os.getenv("XIAOYOU_APP_VOICE_ROUTE_TIMEOUT", "20") or 20),
                60,
            ),
        )
        self.context_max_chars = max(
            600,
            min(
                int(
                    os.getenv(
                        "XIAOYOU_APP_VOICE_ROUTE_CONTEXT_MAX_CHARS",
                        "2600",
                    )
                    or 2600
                ),
                8000,
            ),
        )

    def decide(
        self,
        *,
        input_kind,
        user_text,
        assistant_text,
        session_id="",
        trace_id="",
        input_id="",
    ):
        input_kind = str(input_kind or "").strip().lower()
        if input_kind == "voice":
            return AppVoiceReplyDecision(
                medium=MEDIUM_VOICE,
                confidence=1.0,
                reason="App voice input keeps a voice reply",
                model_ok=True,
                forced=True,
            )
        if input_kind != "text" or not self.enabled:
            return AppVoiceReplyDecision(
                reason=(
                    "not an App text turn"
                    if input_kind != "text"
                    else "text voice decision disabled"
                )
            )

        user_text = str(user_text or "").strip()
        assistant_text = str(assistant_text or "").strip()
        if not user_text or not assistant_text:
            return AppVoiceReplyDecision(reason="exchange text unavailable")

        recent_context = ""
        try:
            snapshot = build_context_snapshot(
                content=user_text,
                session_id=str(session_id or ""),
                include_character=False,
                include_short_memory=True,
                short_memory_max_chars=self.context_max_chars,
                component="AppVoiceReplyDecision",
            )
            recent_context = str(snapshot.short_memory or "").strip()
        except Exception:
            logger.exception(
                "[AppVoiceReplyDecision] recent context unavailable"
            )

        prompt = """你是小悠 App 的回复媒介决策器。聊天回复已经由小悠生成，你只决定这一次应该把同一份回复作为文字发送，还是合成为语音发送。

必须进行完整语义判断，不能依靠词语命中：
- 若 YoYo 当前明确或结合上文实际希望听到小悠的声音、希望这次用声音表达，选择 voice。
- 即使没有直接指定媒介，如果当前内容的情绪、语气、声音表现或亲密互动明显更适合由小悠亲口说出，你也可以自主选择 voice。
- 若只是讨论语音功能、转述别人的要求、否定或取消语音、计划以后再听，不能误判为当前要发语音。
- 若文字更便于阅读、保存、查看结构，或没有充分理由改变日常文字媒介，选择 text。
- 不能修改、续写或评价小悠的回复；不能因为语音更亲密就每轮都选择 voice。
- 最近对话只用于理解指代和延续意图，当前 YoYo 原话与当前小悠回复优先。

最近对话：
<recent>
%s
</recent>

YoYo 当前文字：
<user>
%s
</user>

小悠已经生成、等待发送的回复：
<assistant>
%s
</assistant>

只输出合法 JSON：
{
  "medium": "voice | text",
  "confidence": 0.0,
  "reason": "简短说明为什么这种媒介更符合当前真实语境"
}""" % (
            recent_context[: self.context_max_chars] or "暂无",
            user_text[:4000],
            assistant_text[:4000],
        )
        result = chat_completion(
            component="AppVoiceReplyDecision",
            purpose="choose_reply_medium",
            payload={
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "你只判断小悠 App 本轮回复媒介，只输出合法JSON，"
                            "不生成聊天回复。"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.15,
                "max_tokens": 240,
                **build_thinking_payload("XIAOYOU_APP_VOICE_ROUTE"),
            },
            timeout=self.timeout,
            session_id=str(session_id or ""),
            trace_id=str(trace_id or ""),
            input_id=str(input_id or ""),
        )
        if not result.ok:
            logger.warning(
                "[AppVoiceReplyDecision] model unavailable input_id=%s error=%s",
                str(input_id or "-"),
                str(getattr(result, "error_kind", "model_failed")),
            )
            return AppVoiceReplyDecision(reason="medium model unavailable")

        data = _parse_json(result.content)
        if not isinstance(data, dict):
            logger.warning(
                "[AppVoiceReplyDecision] invalid JSON input_id=%s",
                str(input_id or "-"),
            )
            return AppVoiceReplyDecision(reason="invalid medium model JSON")

        medium = str(data.get("medium") or "").strip().lower()
        if medium not in ALLOWED_MEDIA:
            medium = MEDIUM_TEXT
        try:
            confidence = max(
                0.0,
                min(1.0, float(data.get("confidence") or 0.0)),
            )
        except (TypeError, ValueError):
            confidence = 0.0
        decision = AppVoiceReplyDecision(
            medium=medium,
            confidence=confidence,
            reason=str(data.get("reason") or "").strip()[:300],
            model_ok=True,
        )
        logger.info(
            "[AppVoiceReplyDecision] medium=%s confidence=%.2f input_id=%s",
            decision.medium,
            decision.confidence,
            str(input_id or "-"),
        )
        return decision


def _parse_json(value):
    text = str(value or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            return json.loads(text[start : end + 1])
        except (TypeError, ValueError):
            return None


def _truthy(value):
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")
