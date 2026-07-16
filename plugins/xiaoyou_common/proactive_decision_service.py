# -*- coding: utf-8 -*-
"""One semantic decision for every autonomous Xiaoyou interaction."""

import json
import os
from dataclasses import dataclass, field

from common.log import logger
from plugins.xiaoyou_common.context_service import build_context_snapshot
from plugins.xiaoyou_common.model_gateway import chat_completion
from plugins.xiaoyou_common.thinking_config import build_thinking_payload


ALLOWED_ACTIONS = ("none", "text", "photo")


@dataclass(frozen=True)
class ProactiveDecision:
    action: str = "none"
    text: str = ""
    photo_intent: str = ""
    next_evaluation_seconds: int = 900
    confidence: float = 0.0
    reason: str = ""
    state_deltas: dict = field(default_factory=dict)
    model_ok: bool = False


def decide_proactive_action(*, session_id, activity, inner_state, normalize_delay):
    activity = activity if isinstance(activity, dict) else {}
    last_user_text = str(activity.get("last_user_text") or "").strip()
    snapshot = build_context_snapshot(
        content=last_user_text,
        session_id=session_id,
        long_memory_query=(
            "YoYo最近的状态、约定、情绪和小悠此刻是否适合主动联系："
            + last_user_text[:300]
        ),
        long_memory_max_results=max(1, int(os.getenv("PROACTIVE_MEMORY_TOP_N", "20"))),
        include_character=True,
        include_short_memory=True,
        short_memory_max_chars=max(
            1500,
            int(os.getenv("PROACTIVE_RECENT_CONTEXT_MAX_CHARS", "5000")),
        ),
        component="XiaoyouProactiveDecision",
    )
    recent = activity.get("recent_proactive_texts")
    if not isinstance(recent, list):
        recent = []
    prompt = """你是小悠主动意识的唯一决策中枢。现在不是必须发消息的任务，而是一次重新感受关系和当前时刻的机会。请结合人格、完整近期语境、相关记忆、现实时间和小悠动态内在状态，自主决定保持安静、发一条文字，还是分享一张真正生成的生活照。

重要原则：
- 不使用固定时间表，不因为“已经过了多久”就机械联系；时间只是语境之一。
- 不以关键词决定媒介。photo只在当前情绪、生活场景和关系语境确实适合视觉表达时选择。
- 不责怪YoYo没有回复，不汇报系统状态，不套用固定关心话术。
- text只写实际发送文字，不得用“[图片：……]”或任何图片占位描述冒充照片。
- photo时text必须留空，photo_intent写清楚此刻为什么想分享以及想表达的感觉；具体镜头、动作和配文交给生活照规划器。
- none完全正常。即使选择none，也要自主给出下一次值得重新考虑的时间。
- next_evaluation_seconds是重新判断时间，不是发送倒计时；可从几十秒到数天，不要套用4分钟、2小时、4小时或6小时。
- 结合近期主动记录避免连续重复同一种表达或媒介。

当前时间：
%s

核心人格：
%s

动态内在状态：
%s

最近聊天：
%s

相关长期记忆（只作语境，不在此写入）：
%s

最近主动表达：
%s

YoYo设置的免打扰时段偏好（作为语境与打扰风险参考，不是机械禁令）：
%s

最近活动事实：
%s

只输出合法JSON：
{
  "action": "none | text | photo",
  "text": "action=text时实际发送的微信文字，否则为空",
  "photo_intent": "action=photo时交给生活照规划器的自然语义，否则为空",
  "next_evaluation_seconds": 1800,
  "confidence": 0.0,
  "state_deltas": {"longing": 0.0, "expression_drive": 0.0, "sharing_drive": 0.0, "interruption_caution": 0.0},
  "reason": "简短内部理由"
}""" % (
        snapshot.time_context,
        snapshot.character_context or "暂无",
        json.dumps(inner_state or {}, ensure_ascii=False),
        snapshot.short_memory or "暂无",
        snapshot.long_memory or "暂无",
        json.dumps(recent[-8:], ensure_ascii=False),
        os.getenv("PROACTIVE_QUIET_HOURS", "").strip() or "未设置",
        json.dumps(
            {
                "last_user_text": last_user_text[:600],
                "last_assistant_text": str(activity.get("last_assistant_text") or "")[:600],
                "last_user_at": int(activity.get("last_user_ts") or 0),
                "last_assistant_at": int(activity.get("last_assistant_ts") or 0),
            },
            ensure_ascii=False,
        ),
    )
    payload = {
        "model": os.getenv("XIAOYOU_PROACTIVE_DECISION_MODEL", os.getenv("PROACTIVE_MODEL", "qwen3.7-max")),
        "messages": [
            {
                "role": "system",
                "content": "你只做小悠主动行为决策，只输出合法JSON。",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": float(os.getenv("XIAOYOU_PROACTIVE_DECISION_TEMPERATURE", "0.75")),
        "max_tokens": 900,
        **build_thinking_payload("XIAOYOU_PROACTIVE_DECISION"),
    }
    result = chat_completion(
        component="XiaoyouProactiveDecision",
        purpose="decide_action",
        payload=payload,
        timeout=int(os.getenv("XIAOYOU_PROACTIVE_DECISION_TIMEOUT", "45")),
        session_id=session_id,
    )
    fallback = normalize_delay(os.getenv("XIAOYOU_PROACTIVE_FAILURE_RETRY_SECONDS", "900"))
    if not result.ok:
        return ProactiveDecision(next_evaluation_seconds=fallback, reason="decision model unavailable")
    data = _parse_json(result.content)
    if not isinstance(data, dict):
        logger.warning("[XiaoyouProactiveDecision] invalid model JSON")
        return ProactiveDecision(next_evaluation_seconds=fallback, reason="invalid decision JSON")

    action = str(data.get("action") or "none").strip().lower()
    if action not in ALLOWED_ACTIONS:
        action = "none"
    text = str(data.get("text") or "").strip()[:1000]
    photo_intent = str(data.get("photo_intent") or "").strip()[:1200]
    if action == "text" and text.upper() == "NO_MESSAGE":
        action = "none"
        text = ""
    if action == "text" and not text:
        action = "none"
    if action == "photo" and not photo_intent:
        action = "none"
    if action != "text":
        text = ""
    if action != "photo":
        photo_intent = ""
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence") or 0.0)))
    except Exception:
        confidence = 0.0
    raw_deltas = data.get("state_deltas") if isinstance(data.get("state_deltas"), dict) else {}
    deltas = {}
    for key in ("mood_valence", "energy", "security", "longing", "playfulness", "sensitivity", "expression_drive", "sharing_drive", "interruption_caution"):
        try:
            deltas[key] = max(-0.25, min(0.25, float(raw_deltas.get(key, 0.0))))
        except Exception:
            continue
    decision = ProactiveDecision(
        action=action,
        text=text,
        photo_intent=photo_intent,
        next_evaluation_seconds=normalize_delay(data.get("next_evaluation_seconds")),
        confidence=confidence,
        reason=str(data.get("reason") or "").strip()[:300],
        state_deltas=deltas,
        model_ok=True,
    )
    logger.info(
        "[XiaoyouProactiveDecision] session=%s action=%s confidence=%.2f next=%ss reason=%s",
        _mask(session_id),
        decision.action,
        decision.confidence,
        decision.next_evaluation_seconds,
        decision.reason[:120],
    )
    return decision


def _parse_json(value):
    text = str(value or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            return None


def _mask(value):
    value = str(value or "")
    return value if len(value) <= 10 else value[:5] + "..." + value[-4:]
