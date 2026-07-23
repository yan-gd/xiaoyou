# -*- coding:utf-8 -*-
"""Shared semantic routing for user images and Xiaoyou photo requests.

This module intentionally contains no intent keywords or regular expressions.
It asks one lightweight model to interpret the current utterance together with
recent conversation state, then caches the structured result on the Context so
QwenVision and XiaoyouLifePhoto consume the same decision.
"""

import json
import os
from dataclasses import dataclass

from common.log import logger
from plugins.xiaoyou_common.context_service import build_context_snapshot
from plugins.xiaoyou_common.model_gateway import chat_completion
from plugins.xiaoyou_common.thinking_config import build_thinking_payload


ROUTE_GENERATE = "generate_xiaoyou_photo"
ROUTE_IMAGE_FOLLOWUP = "image_followup"
ROUTE_INDEPENDENT = "independent_text"
ALLOWED_ROUTES = (ROUTE_GENERATE, ROUTE_IMAGE_FOLLOWUP, ROUTE_INDEPENDENT)
ALLOWED_TIME_SCOPES = ("now", "future", "past", "hypothetical", "unclear")
ALLOWED_SUBJECTS = ("xiaoyou", "yoyo", "both", "scene", "unknown")
CACHE_KEY = "xiaoyou_photo_semantic_route"


@dataclass(frozen=True)
class PhotoSemanticRoute:
    route: str = ROUTE_INDEPENDENT
    time_scope: str = "unclear"
    subject: str = "unknown"
    confidence: float = 0.0
    reason: str = ""
    model_ok: bool = False

    @property
    def should_generate(self):
        return self.model_ok and self.route == ROUTE_GENERATE and self.time_scope == "now"

    @property
    def is_image_followup(self):
        return self.route == ROUTE_IMAGE_FOLLOWUP


def classify_photo_semantics(
    *,
    text,
    session_id,
    pending_user_image=False,
    context=None,
):
    """Return one semantic route and share it through ``context.kwargs``."""
    text = str(text or "").strip()
    session_id = str(session_id or "").strip()
    cached = _cached_route(context, text)
    if cached is not None:
        return cached

    if not text:
        route = PhotoSemanticRoute(reason="empty text")
        _cache_route(context, text, route)
        return route

    snapshot = build_context_snapshot(
        content=text,
        session_id=session_id,
        include_character=False,
        include_short_memory=True,
        short_memory_max_chars=max(
            800,
            int(os.getenv("XIAOYOU_PHOTO_ROUTE_CONTEXT_MAX_CHARS", "3500")),
        ),
        component="PhotoIntentService",
    )
    prompt = """你是小悠系统唯一的照片语义路由器。你必须依据完整语义、最近对话、时态、指代和当前是否有待理解图片做判断，禁止依靠关键词命中。

需要区分三条路由：
1. generate_xiaoyou_photo：YoYo此刻明确希望小悠生成、拍摄或分享一张新的照片。只有现在要执行才可选择；未来计划、回忆、假设、否定、转述、讨论拍照技术都不能选择。
2. image_followup：当前存在YoYo刚发来的待理解图片，这句话是在评价、追问、解释或延续那张图片。即使没有出现“图、照片、刚才”等字样，只要语境上承接图片也属于此项。
3. independent_text：普通聊天、已经转向新话题，或无法可靠确认以上两项。

判断原则：
- “等会、进去以后、下次、以后再”属于future，不能现在生成。
- YoYo说自己拍了、看到了或保存了一张照片，不等于要求小悠生成新照片。
- 当前有待理解图片时，明确的新照片生成请求优先于图片补充；完全无关的新话题则选择independent_text。
- 必须结合最近对话理解省略表达，不能要求当前原话独自重复照片对象。若小悠刚刚明确表示马上拍摄或分享照片，而 YoYo 当前是在自然接受、确认、等待或继续这一动作，应视为双方正在执行的即时照片请求，选择generate_xiaoyou_photo和now。
- 若最近对话表明照片已经真实送达，后续评价或回应不能再次生成同一张照片。
- 不确定时保守选择independent_text，绝不靠单个词猜测。

最近对话：
%s

当前是否有YoYo待理解的图片：%s
YoYo当前原话：%s

只输出合法JSON：
{
  "route": "generate_xiaoyou_photo | image_followup | independent_text",
  "time_scope": "now | future | past | hypothetical | unclear",
  "subject": "xiaoyou | yoyo | both | scene | unknown",
  "confidence": 0.0,
  "reason": "简短语义理由"
}""" % (
        snapshot.short_memory or "暂无",
        "是" if pending_user_image else "否",
        text,
    )
    payload = {
        "model": os.getenv("XIAOYOU_PHOTO_ROUTE_MODEL", "qwen3.7-plus"),
        "messages": [
            {
                "role": "system",
                "content": "你只做语义路由，只输出合法JSON，不要Markdown。",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 300,
        **build_thinking_payload("XIAOYOU_PHOTO_ROUTE"),
    }
    result = chat_completion(
        component="PhotoIntentService",
        purpose="photo_semantic_route",
        payload=payload,
        timeout=int(os.getenv("XIAOYOU_PHOTO_ROUTE_TIMEOUT", "20")),
        session_id=session_id,
    )
    if not result.ok:
        # A failed classifier must never create an image.  When an image is
        # pending, preserve the old safe behavior and let the text accompany it.
        route = PhotoSemanticRoute(
            route=ROUTE_IMAGE_FOLLOWUP if pending_user_image else ROUTE_INDEPENDENT,
            reason="semantic router unavailable",
            model_ok=False,
        )
        _cache_route(context, text, route)
        return route

    data = _parse_json(result.content)
    if not isinstance(data, dict):
        route = PhotoSemanticRoute(
            route=ROUTE_IMAGE_FOLLOWUP if pending_user_image else ROUTE_INDEPENDENT,
            reason="semantic router returned invalid JSON",
            model_ok=False,
        )
        _cache_route(context, text, route)
        return route

    raw_route = str(data.get("route") or "").strip().lower()
    raw_time = str(data.get("time_scope") or "").strip().lower()
    raw_subject = str(data.get("subject") or "").strip().lower()
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence") or 0.0)))
    except Exception:
        confidence = 0.0
    route = PhotoSemanticRoute(
        route=raw_route if raw_route in ALLOWED_ROUTES else ROUTE_INDEPENDENT,
        time_scope=raw_time if raw_time in ALLOWED_TIME_SCOPES else "unclear",
        subject=raw_subject if raw_subject in ALLOWED_SUBJECTS else "unknown",
        confidence=confidence,
        reason=str(data.get("reason") or "").strip()[:300],
        model_ok=True,
    )
    # A generation route without an immediate time scope is intentionally
    # normalized away so downstream plugins cannot accidentally execute it.
    if route.route == ROUTE_GENERATE and route.time_scope != "now":
        route = PhotoSemanticRoute(
            route=ROUTE_INDEPENDENT,
            time_scope=route.time_scope,
            subject=route.subject,
            confidence=route.confidence,
            reason="non-immediate photo intent: " + route.reason,
            model_ok=True,
        )
    logger.info(
        "[PhotoIntentService] route session=%s pending=%s route=%s time=%s subject=%s confidence=%.2f",
        _mask_session(session_id),
        bool(pending_user_image),
        route.route,
        route.time_scope,
        route.subject,
        route.confidence,
    )
    _cache_route(context, text, route)
    return route


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
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            return None


def _cached_route(context, text):
    kwargs = getattr(context, "kwargs", {}) if context is not None else {}
    value = kwargs.get(CACHE_KEY) if isinstance(kwargs, dict) else None
    if not isinstance(value, dict) or value.get("text") != text:
        return None
    try:
        return PhotoSemanticRoute(**value.get("route", {}))
    except Exception:
        return None


def _cache_route(context, text, route):
    if context is None:
        return
    kwargs = getattr(context, "kwargs", {}) or {}
    kwargs[CACHE_KEY] = {
        "text": text,
        "route": {
            "route": route.route,
            "time_scope": route.time_scope,
            "subject": route.subject,
            "confidence": route.confidence,
            "reason": route.reason,
            "model_ok": route.model_ok,
        },
    }
    context.kwargs = kwargs


def _mask_session(value):
    value = str(value or "")
    if len(value) <= 10:
        return value or "-"
    return value[:5] + "..." + value[-4:]
