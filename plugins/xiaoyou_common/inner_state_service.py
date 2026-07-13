# -*- coding: utf-8 -*-
"""Persistent, short-lived inner state for Xiaoyou's proactive behaviour.

The state is deliberately separate from persona files, ShortMemory and the
Aliyun long-term memory library.  It is a bounded affect vector: conversations
can move it, time gradually evolves it, and proactive decisions can read it.
"""

import json
import math
import os
import threading
import time
from datetime import datetime

from common.log import logger
from plugins.xiaoyou_common.context_service import build_context_snapshot
from plugins.xiaoyou_common.model_gateway import chat_completion
from plugins.xiaoyou_common.state_store import JsonStateStore
from plugins.xiaoyou_common.thinking_config import build_thinking_payload


STATE_KEYS = (
    "mood_valence",
    "energy",
    "security",
    "longing",
    "playfulness",
    "sensitivity",
    "expression_drive",
    "sharing_drive",
    "interruption_caution",
)

BASELINES = {
    "mood_valence": 0.62,
    "energy": 0.55,
    "security": 0.76,
    "longing": 0.28,
    "playfulness": 0.48,
    "sensitivity": 0.24,
    "expression_drive": 0.42,
    "sharing_drive": 0.34,
    "interruption_caution": 0.36,
}


def _clamp(value, minimum=0.0, maximum=1.0):
    try:
        return max(minimum, min(maximum, float(value)))
    except Exception:
        return minimum


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


class InnerStateService:
    def __init__(self, path=None):
        appdata = os.getenv("APPDATA_DIR", "").strip() or os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )
        path = path or os.path.join(appdata, "xiaoyou_inner_state", "state.json")
        self.store = JsonStateStore(
            path,
            backup_path=path + ".backup",
            name="xiaoyou_inner_state",
            default_factory=lambda: {"schema_version": 1, "sessions": {}},
        )
        self.lock = threading.RLock()
        self.data = self._load()

    def get(self, session_id, now=None, persist=True):
        session_id = str(session_id or "").strip()
        now = float(now or time.time())
        with self.lock:
            item = self._session(session_id)
            changed = self._evolve_time(item, now)
            if changed and persist:
                self.store.save(self.data)
            return self._public(item)

    def update_from_exchange(
        self,
        session_id,
        *,
        user_text="",
        assistant_text="",
        last_user_ts=0,
    ):
        """Asynchronously called after a completed reply; never blocks chat."""
        session_id = str(session_id or "").strip()
        if not session_id:
            return {"state": {}, "next_evaluation_seconds": self._fallback_delay()}

        with self.lock:
            item = self._session(session_id)
            self._evolve_time(item, time.time())
            if last_user_ts:
                item["last_user_at"] = max(
                    float(item.get("last_user_at") or 0),
                    float(last_user_ts),
                )
            self.store.save(self.data)
            current = self._public(item)
        snapshot = build_context_snapshot(
            content=str(user_text or ""),
            session_id=session_id,
            include_character=True,
            include_short_memory=True,
            short_memory_max_chars=max(
                1200,
                int(os.getenv("XIAOYOU_INNER_STATE_CONTEXT_MAX_CHARS", "5000")),
            ),
            component="XiaoyouInnerState",
        )
        prompt = """你负责更新小悠此刻的内在状态，而不是生成聊天回复。请依据完整语义、关系氛围、当前时间和旧状态判断本轮交流造成的细微变化，禁止用关键词命中或套固定情绪。

状态均为0到1：
- mood_valence：愉悦、舒展程度；越低越低落或不舒服。
- energy：精神与行动能量。
- security：关系中的安心与被理解感。
- longing：惦念、依恋和想靠近YoYo的程度。
- playfulness：调皮、撒娇、逗他的倾向。
- sensitivity：脆弱、委屈、容易被触动的程度。
- expression_drive：想主动说点什么、继续互动的冲动。
- sharing_drive：想分享生活片段或照片的冲动。
- interruption_caution：担心打扰YoYo、倾向给他空间的程度。

你只输出本轮造成的增量，不输出新的绝对值。每项delta必须在-0.35到0.35之间。没有可靠依据的项写0。状态变化应克制、连续，不能因为一句普通玩笑大幅翻转。
next_evaluation_seconds由你自主决定：表示如果YoYo之后没有新消息，小悠过多久值得重新感受一次并判断要不要主动联系。它不是发送倒计时，可以从几十秒到数天；不要套用固定的4分钟、2小时或6小时。

当前时间：
%s

小悠人格：
%s

图片和当前交流之前的近期聊天：
%s

旧状态：
%s

YoYo本轮原话：
%s

小悠本轮实际回复：
%s

只输出合法JSON：
{
  "deltas": {"mood_valence": 0.0, "energy": 0.0, "security": 0.0, "longing": 0.0, "playfulness": 0.0, "sensitivity": 0.0, "expression_drive": 0.0, "sharing_drive": 0.0, "interruption_caution": 0.0},
  "confidence": 0.0,
  "emotion_note": "不复述隐私内容的简短状态说明",
  "next_evaluation_seconds": 600,
  "reason": "简短理由"
}""" % (
            snapshot.time_context or datetime.now().isoformat(),
            snapshot.character_context or "暂无",
            snapshot.short_memory or "暂无",
            json.dumps(current, ensure_ascii=False),
            str(user_text or "")[:1200] or "暂无",
            str(assistant_text or "")[:1200] or "暂无",
        )
        payload = {
            "model": os.getenv("XIAOYOU_INNER_STATE_MODEL", "qwen3.7-plus"),
            "messages": [
                {
                    "role": "system",
                    "content": "你只更新小悠的短期内在状态，只输出合法JSON，不写聊天回复。",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.25,
            "max_tokens": 700,
            **build_thinking_payload("XIAOYOU_INNER_STATE"),
        }
        result = chat_completion(
            component="XiaoyouInnerState",
            purpose="update_after_exchange",
            payload=payload,
            timeout=int(os.getenv("XIAOYOU_INNER_STATE_TIMEOUT", "25")),
            session_id=session_id,
        )
        if not result.ok:
            return {"state": current, "next_evaluation_seconds": self._fallback_delay()}

        data = _parse_json(result.content)
        if not isinstance(data, dict):
            logger.warning("[XiaoyouInnerState] invalid model JSON")
            return {"state": current, "next_evaluation_seconds": self._fallback_delay()}

        confidence = _clamp(data.get("confidence", 0.5))
        deltas = data.get("deltas") if isinstance(data.get("deltas"), dict) else {}
        now = time.time()
        with self.lock:
            item = self._session(session_id)
            self._evolve_time(item, now)
            values = item.setdefault("values", dict(BASELINES))
            for key in STATE_KEYS:
                delta = _clamp(deltas.get(key, 0.0), -0.35, 0.35) * confidence
                values[key] = _clamp(float(values.get(key, BASELINES[key])) + delta)
            item["last_updated_at"] = now
            if last_user_ts:
                item["last_user_at"] = max(float(item.get("last_user_at") or 0), float(last_user_ts))
            events = item.get("recent_events") if isinstance(item.get("recent_events"), list) else []
            events.append({
                "ts": int(now),
                "note": str(data.get("emotion_note") or "").strip()[:180],
                "reason": str(data.get("reason") or "").strip()[:240],
            })
            item["recent_events"] = events[-12:]
            self.store.save(self.data)
            state = self._public(item)

        delay = self.normalize_delay(data.get("next_evaluation_seconds"))
        logger.info(
            "[XiaoyouInnerState] updated session=%s valence=%.2f energy=%.2f longing=%.2f expression=%.2f sharing=%.2f next=%ss",
            self._mask(session_id),
            state["mood_valence"],
            state["energy"],
            state["longing"],
            state["expression_drive"],
            state["sharing_drive"],
            delay,
        )
        return {"state": state, "next_evaluation_seconds": delay}

    def apply_decision_feedback(self, session_id, *, action, delivered, deltas=None, reason=""):
        now = time.time()
        with self.lock:
            item = self._session(session_id)
            self._evolve_time(item, now)
            values = item.setdefault("values", dict(BASELINES))
            for key, value in (deltas or {}).items():
                if key in STATE_KEYS:
                    values[key] = _clamp(
                        float(values.get(key, BASELINES[key]))
                        + _clamp(value, -0.25, 0.25)
                    )
            if delivered and action in ("text", "photo"):
                values["expression_drive"] = _clamp(values["expression_drive"] - 0.16)
                values["longing"] = _clamp(values["longing"] - 0.10)
                if action == "photo":
                    values["sharing_drive"] = _clamp(values["sharing_drive"] - 0.22)
            item["last_updated_at"] = now
            item["last_action"] = {
                "ts": int(now),
                "action": str(action or "none"),
                "delivered": bool(delivered),
                "reason": str(reason or "")[:240],
            }
            self.store.save(self.data)
            return self._public(item)

    def normalize_delay(self, value):
        minimum = max(15, int(os.getenv("XIAOYOU_PROACTIVE_EVALUATION_MIN_SECONDS", "30")))
        maximum = max(minimum, int(os.getenv("XIAOYOU_PROACTIVE_EVALUATION_MAX_SECONDS", "604800")))
        try:
            return max(minimum, min(maximum, int(float(value))))
        except Exception:
            return max(minimum, min(maximum, self._fallback_delay()))

    def _fallback_delay(self):
        return max(60, int(os.getenv("XIAOYOU_PROACTIVE_FAILURE_RETRY_SECONDS", "900")))

    def _load(self):
        data = self.store.load()
        if not isinstance(data, dict):
            data = {"schema_version": 1, "sessions": {}}
        data.setdefault("schema_version", 1)
        if not isinstance(data.get("sessions"), dict):
            data["sessions"] = {}
        return data

    def _session(self, session_id):
        sessions = self.data.setdefault("sessions", {})
        item = sessions.setdefault(str(session_id or ""), {})
        values = item.get("values") if isinstance(item.get("values"), dict) else {}
        item["values"] = {
            key: _clamp(values.get(key, BASELINES[key])) for key in STATE_KEYS
        }
        item.setdefault("last_updated_at", time.time())
        item.setdefault("last_user_at", 0)
        item.setdefault("recent_events", [])
        return item

    def _evolve_time(self, item, now):
        last = float(item.get("last_updated_at") or now)
        elapsed = max(0.0, min(7 * 86400.0, now - last))
        if elapsed < 5:
            return False
        half_life = max(0.5, float(os.getenv("XIAOYOU_INNER_STATE_HALF_LIFE_HOURS", "8")))
        blend = 1.0 - math.exp(-elapsed / (half_life * 3600.0))
        values = item.setdefault("values", dict(BASELINES))
        hour = datetime.fromtimestamp(now).hour
        energy_target = 0.30 if hour < 7 else (0.58 if hour < 19 else 0.46)
        targets = dict(BASELINES)
        targets["energy"] = energy_target
        idle_hours = max(0.0, (now - float(item.get("last_user_at") or now)) / 3600.0)
        targets["longing"] = _clamp(BASELINES["longing"] + min(0.28, math.log1p(idle_hours) * 0.07))
        for key in STATE_KEYS:
            old = float(values.get(key, BASELINES[key]))
            values[key] = _clamp(old + (targets[key] - old) * blend)
        item["last_updated_at"] = now
        return True

    def _public(self, item):
        values = item.get("values") or {}
        result = {key: round(_clamp(values.get(key, BASELINES[key])), 4) for key in STATE_KEYS}
        result["last_updated_at"] = int(float(item.get("last_updated_at") or 0))
        result["last_user_at"] = int(float(item.get("last_user_at") or 0))
        result["recent_events"] = list(item.get("recent_events") or [])[-6:]
        return result

    def _mask(self, value):
        value = str(value or "")
        return value if len(value) <= 10 else value[:5] + "..." + value[-4:]


_INSTANCE = None
_INSTANCE_LOCK = threading.Lock()


def get_inner_state_service():
    global _INSTANCE
    with _INSTANCE_LOCK:
        if _INSTANCE is None:
            _INSTANCE = InnerStateService()
        return _INSTANCE
