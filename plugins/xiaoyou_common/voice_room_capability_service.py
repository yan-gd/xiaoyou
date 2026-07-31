# -*- coding: utf-8 -*-
"""Model-first capability bridge for completed voice-room exchanges.

The realtime conversation path must stay responsive, so capability planning
and side effects run on a background FIFO.  The model sees the complete user
and assistant exchange and may request one reminder and/or one life photo.
There is deliberately no keyword, regular-expression, or phrase-list router in
this module.
"""

import json
import os
import queue
import threading
from datetime import datetime

from common.log import logger
from plugins.xiaoyou_common.app_transport import app_receiver
from plugins.xiaoyou_common.model_gateway import chat_completion
from plugins.xiaoyou_common.outbound_dispatcher import send_action
from plugins.xiaoyou_common.thinking_config import build_thinking_payload


ALLOWED_ACTIONS = ("create_reminder", "generate_life_photo")
_STOP = object()


def _truthy(value):
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _clean_text(value, limit=4000):
    return str(value or "").replace("\x00", "").strip()[: max(0, int(limit))]


def _float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _parse_json_object(value):
    text = str(value or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(text[start : end + 1])
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None


def plan_voice_room_actions(exchange):
    """Ask the model for structured actions from one completed exchange."""
    exchange = exchange if isinstance(exchange, dict) else {}
    user_text = _clean_text(exchange.get("user_text"))
    assistant_text = _clean_text(exchange.get("assistant_text"))
    session_id = _clean_text(exchange.get("session_id"), 128)
    turn_id = _clean_text(exchange.get("turn_id"), 160)
    if not user_text or not assistant_text:
        return []

    now = datetime.now().astimezone()
    payload = {
        "model": os.getenv(
            "XIAOYOU_VOICE_CAPABILITY_MODEL",
            "qwen3.7-plus",
        ).strip()
        or "qwen3.7-plus",
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是小悠实时语音房的能力决策器。你必须理解完整语义、"
                    "上下文指代和时间关系，不能依赖关键词、固定短语或正则匹配。"
                    "只决定本轮是否需要执行能力，不生成聊天回复。\n"
                    "可用能力只有：\n"
                    "1. create_reminder：YoYo 明确请小悠在未来某个可确定时间"
                    "提醒、叫醒或通知他。必须把相对时间结合当前本地时间换算成"
                    "带时区的绝对 ISO 8601 时间；时间无法可靠确定时不执行。\n"
                    "2. generate_life_photo：YoYo 明确要求小悠现在生成、拍摄或"
                    "分享一张新的生活照。未来计划、回忆、假设、否定、转述或仅仅"
                    "谈论照片时不执行。\n"
                    "用户原话是行动依据；小悠的回复只能帮助理解指代，不能凭回复"
                    "自行制造行动。普通聊天输出空 actions。每种能力每轮最多一次。"
                    "只输出合法 JSON，不要 Markdown。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "current_local_time": now.isoformat(timespec="seconds"),
                        "timezone": str(now.tzinfo),
                        "user_text": user_text,
                        "assistant_text": assistant_text,
                        "output_schema": {
                            "actions": [
                                {
                                    "type": (
                                        "create_reminder | "
                                        "generate_life_photo"
                                    ),
                                    "confidence": 0.0,
                                    "due_at": (
                                        "reminder only: absolute ISO 8601"
                                    ),
                                    "task": "reminder only",
                                    "subject": (
                                        "photo only: xiaoyou | yoyo | both | "
                                        "scene | unknown"
                                    ),
                                    "request_text": "photo only",
                                    "reason": "brief semantic reason",
                                }
                            ]
                        },
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "temperature": 0.1,
        "max_tokens": 600,
        **build_thinking_payload("XIAOYOU_VOICE_CAPABILITY"),
    }
    result = chat_completion(
        component="VoiceRoomCapability",
        purpose="completed_turn_capability_plan",
        payload=payload,
        timeout=int(os.getenv("XIAOYOU_VOICE_CAPABILITY_TIMEOUT", "25")),
        session_id=session_id,
        input_id="voice-room:" + turn_id if turn_id else "",
    )
    if not result.ok:
        logger.info(
            "[VoiceRoomCapability] planner unavailable turn_id=%s kind=%s",
            turn_id[:48] or "-",
            result.error_kind or "unknown",
        )
        return []

    data = _parse_json_object(result.content)
    raw_actions = data.get("actions") if isinstance(data, dict) else []
    if not isinstance(raw_actions, list):
        return []
    threshold = max(
        0.0,
        min(
            1.0,
            _float(
                os.getenv(
                    "XIAOYOU_VOICE_CAPABILITY_MIN_CONFIDENCE",
                    "0.78",
                ),
                0.78,
            ),
        ),
    )
    normalized = []
    seen_types = set()
    for raw in raw_actions:
        if not isinstance(raw, dict):
            continue
        action_type = _clean_text(raw.get("type"), 80)
        confidence = _float(raw.get("confidence"), 0.0)
        if (
            action_type not in ALLOWED_ACTIONS
            or action_type in seen_types
            or confidence < threshold
        ):
            continue
        item = {
            "type": action_type,
            "confidence": confidence,
            "reason": _clean_text(raw.get("reason"), 500),
        }
        if action_type == "create_reminder":
            item["due_at"] = _clean_text(raw.get("due_at"), 100)
            item["task"] = _clean_text(raw.get("task"), 600)
            if not item["due_at"] or not item["task"]:
                continue
        else:
            subject = _clean_text(raw.get("subject"), 40).lower()
            item["subject"] = (
                subject
                if subject in ("xiaoyou", "yoyo", "both", "scene", "unknown")
                else "unknown"
            )
            item["request_text"] = (
                _clean_text(raw.get("request_text"), 1200) or user_text
            )
        seen_types.add(action_type)
        normalized.append(item)
    return normalized


class VoiceRoomCapabilityService:
    """Background FIFO for model planning and capability side effects."""

    def __init__(
        self,
        *,
        instances_provider=None,
        planner=None,
        dispatcher=None,
        receiver_builder=None,
        start_worker=True,
    ):
        self.enabled = _truthy(
            os.getenv("XIAOYOU_VOICE_ROOM_CAPABILITIES_ENABLED", "true")
        )
        self.instances_provider = instances_provider
        self.planner = planner or plan_voice_room_actions
        self.dispatcher = dispatcher or send_action
        self.receiver_builder = receiver_builder or app_receiver
        self.jobs = queue.Queue(maxsize=256)
        self._seen = set()
        self._seen_order = []
        self._lock = threading.RLock()
        self.worker = None
        if self.enabled and start_worker:
            self.worker = threading.Thread(
                target=self._loop,
                daemon=True,
                name="XiaoyouVoiceRoomCapabilities",
            )
            self.worker.start()

    def _instances(self):
        if self.instances_provider is not None:
            value = self.instances_provider()
            return value if isinstance(value, dict) else {}
        import plugins

        manager = getattr(plugins, "instance", None)
        value = getattr(manager, "instances", {}) if manager else {}
        return value if isinstance(value, dict) else {}

    def submit(self, exchange):
        if not self.enabled or not isinstance(exchange, dict):
            return False
        job = dict(exchange)
        key = _clean_text(job.get("turn_id"), 180)
        if not key:
            return False
        with self._lock:
            if key in self._seen:
                return False
            self._seen.add(key)
            self._seen_order.append(key)
            if len(self._seen_order) > 2048:
                expired = self._seen_order.pop(0)
                self._seen.discard(expired)
        try:
            self.jobs.put_nowait(job)
            return True
        except queue.Full:
            with self._lock:
                self._seen.discard(key)
                if key in self._seen_order:
                    self._seen_order.remove(key)
            logger.error(
                "[VoiceRoomCapability] queue full turn_id=%s",
                key[:48],
            )
            return False

    def _loop(self):
        while True:
            job = self.jobs.get()
            try:
                if job is _STOP:
                    return
                self.process(job)
            except Exception:
                logger.exception(
                    "[VoiceRoomCapability] background execution failed"
                )
            finally:
                self.jobs.task_done()

    def process(self, exchange):
        actions = self.planner(dict(exchange or {}))
        results = []
        for action in actions:
            try:
                results.append(self._execute(exchange, action))
            except Exception as error:
                logger.exception(
                    "[VoiceRoomCapability] action failed type=%s turn_id=%s",
                    str(action.get("type") or "-")[:80],
                    str(exchange.get("turn_id") or "-")[:48],
                )
                results.append(
                    {
                        "type": str(action.get("type") or ""),
                        "ok": False,
                        "error": str(error)[:300],
                    }
                )
        return results

    def _execute(self, exchange, action):
        action_type = str(action.get("type") or "")
        instances = self._instances()
        session_id = _clean_text(exchange.get("session_id"), 128)
        device_id = _clean_text(exchange.get("device_id"), 160)
        turn_id = _clean_text(exchange.get("turn_id"), 180)
        user_text = _clean_text(exchange.get("user_text"))
        assistant_text = _clean_text(exchange.get("assistant_text"))
        if action_type == "create_reminder":
            reminder_plugin = instances.get("REMINDERLOVE")
            creator = getattr(
                reminder_plugin,
                "create_voice_reminder",
                None,
            )
            if not callable(creator):
                raise RuntimeError("reminder_capability_unavailable")
            reminder = creator(
                session_id=session_id,
                receiver=self.receiver_builder(device_id),
                due_at=action.get("due_at"),
                task=action.get("task"),
                original=user_text,
                turn_id=turn_id,
            )
            ok = bool(reminder)
            logger.info(
                "[VoiceRoomCapability] reminder result turn_id=%s ok=%s due=%s",
                turn_id[:48],
                ok,
                str((reminder or {}).get("due_text") or "-")[:40],
            )
            return {
                "type": action_type,
                "ok": ok,
                "reminder_id": str((reminder or {}).get("id") or ""),
            }

        if action_type == "generate_life_photo":
            photo_plugin = instances.get("XIAOYOULIFEPHOTO")
            creator = getattr(photo_plugin, "create_voice_share", None)
            if not callable(creator):
                raise RuntimeError("life_photo_capability_unavailable")
            share = creator(
                session_id=session_id,
                user_text=action.get("request_text") or user_text,
                assistant_text=assistant_text,
                subject=action.get("subject") or "unknown",
            )
            if not share:
                return {"type": action_type, "ok": False}
            caption = _clean_text(share.get("caption"), 600)
            receipt = self.dispatcher(
                session_id=session_id,
                source="voice_room_life_photo",
                image_path=share.get("path"),
                parts=[caption] if caption else [],
                receiver=self.receiver_builder(device_id),
                record_memory=False,
                input_id="voice-room:" + turn_id,
            )
            delivered = bool(
                getattr(receipt, "ok", False)
                or getattr(receipt, "queued", False)
            )
            if delivered:
                marker = getattr(photo_plugin, "mark_voice_sent", None)
                if callable(marker):
                    marker(session_id, share)
            else:
                discard = getattr(photo_plugin, "discard_share", None)
                if callable(discard):
                    discard(share)
            logger.info(
                "[VoiceRoomCapability] photo result turn_id=%s ok=%s",
                turn_id[:48],
                delivered,
            )
            return {
                "type": action_type,
                "ok": delivered,
                "action_id": str(getattr(receipt, "action_id", "") or ""),
            }
        return {"type": action_type, "ok": False, "error": "unsupported"}

    def close(self):
        if self.worker is None:
            return
        try:
            self.jobs.put_nowait(_STOP)
        except queue.Full:
            return
        self.worker.join(timeout=1.0)
