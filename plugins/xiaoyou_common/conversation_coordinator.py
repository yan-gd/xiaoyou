# -*- coding: utf-8 -*-
"""Session-level activity and autonomous-action coordination for Xiaoyou."""

import os
import threading
import time
import uuid

from common.log import logger
from plugins.xiaoyou_common.trace_service import (
    begin_action_trace,
    current_trace_link,
    ensure_trace,
    trace_event,
)


DEFAULT_PRIORITIES = {
    "reminder": 100,
    "reconnect": 90,
    "followup": 70,
    "proactive": 40,
}
AUTONOMOUS_GAP_KINDS = {"followup", "proactive"}


class ActionLease:
    def __init__(
        self,
        coordinator,
        *,
        accepted,
        session_id,
        kind,
        source,
        token="",
        reason="",
        priority=0,
        disabled=False,
        trace_id="",
        input_id="",
    ):
        self.coordinator = coordinator
        self.accepted = bool(accepted)
        self.session_id = str(session_id or "")
        self.kind = str(kind or "")
        self.source = str(source or "")
        self.token = str(token or "")
        self.reason = str(reason or "")
        self.priority = int(priority or 0)
        self.disabled = bool(disabled)
        self.trace_id = str(trace_id or "")
        self.input_id = str(input_id or "")
        self.finished = False

    def current(self):
        if not self.accepted or self.finished:
            return False
        if self.disabled:
            return True
        return self.coordinator.is_current(self.session_id, self.token)

    def complete(self, delivered=False, detail=""):
        if self.finished:
            return False
        self.finished = True
        if self.disabled:
            trace_event(
                "lease_completed",
                status="coordinator_disabled",
                trace_id=self.trace_id,
                input_id=self.input_id,
                session_id=self.session_id,
                attrs={
                    "action_kind": self.kind,
                    "source": self.source,
                    "delivered": bool(delivered),
                    "outcome": "coordinator_disabled",
                },
            )
            return True
        return self.coordinator.complete(
            self.session_id,
            self.token,
            delivered=delivered,
            detail=detail,
        )

    def cancel(self, reason="cancelled"):
        if self.finished:
            return False
        self.finished = True
        if self.disabled:
            trace_event(
                "lease_cancelled",
                status="coordinator_disabled",
                trace_id=self.trace_id,
                input_id=self.input_id,
                session_id=self.session_id,
                attrs={
                    "action_kind": self.kind,
                    "source": self.source,
                    "reason": reason,
                },
            )
            return True
        return self.coordinator.cancel(self.session_id, self.token, reason=reason)


class ConversationCoordinator:
    def __init__(self):
        self.lock = threading.RLock()
        self.sessions = {}

    def enabled(self):
        return os.getenv("XIAOYOU_COORDINATOR_ENABLED", "true").strip().lower() in (
            "1", "true", "yes", "on"
        )

    def note_user_activity(
        self,
        session_id,
        activity_ts=None,
        source="wechat",
        turn_id="",
        trace_id="",
        input_id="",
    ):
        session_id = str(session_id or "").strip()
        if not session_id:
            return False
        link = ensure_trace(
            session_id=session_id,
            source=source,
            trace_id=trace_id,
            input_id=input_id,
        )
        now = float(activity_ts or time.time())
        with self.lock:
            state = self._session(session_id)
            state["generation"] = int(state.get("generation") or 0) + 1
            state["last_user_ts"] = max(float(state.get("last_user_ts") or 0), now)
            state["last_user_source"] = str(source or "")[:60]
            state["last_turn_id"] = str(turn_id or "")[:80]
            active = state.get("active")
            if isinstance(active, dict):
                trace_event(
                    "lease_cancelled",
                    status="cancelled_by_user",
                    trace_id=active.get("trace_id", ""),
                    input_id=active.get("input_id", ""),
                    session_id=session_id,
                    lease_id=active.get("token", ""),
                    attrs={
                        "action_kind": active.get("kind"),
                        "source": active.get("source"),
                        "reason": "cancelled_by_user",
                    },
                )
                logger.info(
                    "[ConversationCoordinator] active action invalidated by user input "
                    "session=%s kind=%s source=%s token=%s",
                    session_id,
                    active.get("kind"),
                    active.get("source"),
                    _short_token(active.get("token")),
                )
                self._append_history(state, active, "cancelled_by_user")
                state["active"] = None
        trace_event(
            "user_activity",
            status="observed",
            link=link,
            attrs={"source": source, "input_version": turn_id},
        )
        return True

    def claim(
        self,
        session_id,
        *,
        kind,
        source,
        priority=None,
        ttl_seconds=None,
        observed_user_ts=None,
        trace_id="",
        input_id="",
        parent_trace_id="",
    ):
        session_id = str(session_id or "").strip()
        kind = str(kind or "unknown").strip().lower()
        source = str(source or kind).strip()
        priority = int(
            DEFAULT_PRIORITIES.get(kind, 10) if priority is None else priority
        )
        link = begin_action_trace(
            session_id=session_id,
            source=source,
            trace_id=trace_id,
            input_id=input_id,
            parent_trace_id=parent_trace_id,
        )

        if not self.enabled():
            trace_event(
                "lease_claimed",
                status="coordinator_disabled",
                link=link,
                attrs={
                    "action_kind": kind,
                    "source": source,
                    "priority": priority,
                },
            )
            return ActionLease(
                self,
                accepted=True,
                session_id=session_id,
                kind=kind,
                source=source,
                priority=priority,
                disabled=True,
                reason="coordinator_disabled",
                trace_id=link.trace_id,
                input_id=link.input_id,
            )
        if not session_id:
            return self._rejected(session_id, kind, source, priority, "session_missing")

        now = time.time()
        ttl = float(
            ttl_seconds
            if ttl_seconds is not None
            else os.getenv("XIAOYOU_COORDINATOR_CLAIM_TTL_SECONDS", "300")
        )
        ttl = max(5.0, ttl)

        with self.lock:
            state = self._session(session_id)
            self._expire_active(state, now)

            last_user_ts = float(state.get("last_user_ts") or 0)
            # Several legacy state files store second-resolution integer
            # timestamps while incoming activity uses sub-second precision.
            # A one-second tolerance treats the same event as identical while
            # still rejecting genuinely newer user activity.
            if observed_user_ts is not None and last_user_ts >= float(observed_user_ts or 0) + 1.0:
                return self._rejected(
                    session_id, kind, source, priority, "user_activity_changed"
                )

            settle = max(
                0.0,
                float(os.getenv("XIAOYOU_COORDINATOR_INPUT_SETTLE_SECONDS", "3")),
            )
            if last_user_ts and now - last_user_ts < settle:
                return self._rejected(
                    session_id, kind, source, priority, "recent_user_activity"
                )

            if kind in AUTONOMOUS_GAP_KINDS:
                gap = max(
                    0.0,
                    float(os.getenv("XIAOYOU_COORDINATOR_AUTONOMOUS_GAP_SECONDS", "120")),
                )
                last_delivery = state.get("last_delivery") or {}
                if gap and now - float(last_delivery.get("ts") or 0) < gap:
                    return self._rejected(
                        session_id,
                        kind,
                        source,
                        priority,
                        "autonomous_gap_after_%s" % (last_delivery.get("kind") or "action"),
                    )

            active = state.get("active")
            if isinstance(active, dict):
                active_priority = int(active.get("priority") or 0)
                if priority <= active_priority:
                    return self._rejected(
                        session_id,
                        kind,
                        source,
                        priority,
                        "active_%s_priority_%s" % (
                            active.get("kind") or "action",
                            active_priority,
                        ),
                    )

                logger.info(
                    "[ConversationCoordinator] action preempted session=%s old_kind=%s "
                    "old_priority=%s new_kind=%s new_priority=%s",
                    session_id,
                    active.get("kind"),
                    active_priority,
                    kind,
                    priority,
                )
                self._append_history(state, active, "preempted_by_%s" % kind)
                trace_event(
                    "lease_cancelled",
                    status="preempted",
                    trace_id=active.get("trace_id", ""),
                    input_id=active.get("input_id", ""),
                    session_id=session_id,
                    lease_id=active.get("token", ""),
                    attrs={
                        "action_kind": active.get("kind"),
                        "source": active.get("source"),
                        "reason": "preempted",
                    },
                )

            token = uuid.uuid4().hex
            claim = {
                "token": token,
                "session_id": session_id,
                "kind": kind,
                "source": source,
                "priority": priority,
                "created_at": now,
                "expires_at": now + ttl,
                "generation": int(state.get("generation") or 0),
                "trace_id": link.trace_id,
                "input_id": link.input_id,
            }
            state["active"] = claim
            logger.info(
                "[ConversationCoordinator] action claimed session=%s kind=%s source=%s "
                "priority=%s token=%s ttl=%.0fs",
                session_id,
                kind,
                source,
                priority,
                _short_token(token),
                ttl,
            )
            trace_event(
                "lease_claimed",
                status="accepted",
                link=link,
                lease_id=token,
                attrs={
                    "action_kind": kind,
                    "source": source,
                    "priority": priority,
                },
            )
            return ActionLease(
                self,
                accepted=True,
                session_id=session_id,
                kind=kind,
                source=source,
                token=token,
                priority=priority,
                trace_id=link.trace_id,
                input_id=link.input_id,
            )

    def is_current(self, session_id, token):
        session_id = str(session_id or "").strip()
        token = str(token or "")
        with self.lock:
            state = self._session(session_id)
            self._expire_active(state, time.time())
            active = state.get("active")
            return bool(
                isinstance(active, dict)
                and active.get("token") == token
                and int(active.get("generation") or 0) == int(state.get("generation") or 0)
            )

    def complete(self, session_id, token, delivered=False, detail=""):
        session_id = str(session_id or "").strip()
        token = str(token or "")
        now = time.time()
        with self.lock:
            state = self._session(session_id)
            active = state.get("active")
            if not isinstance(active, dict) or active.get("token") != token:
                if delivered:
                    state["last_delivery"] = {
                        "ts": now,
                        "kind": "late_or_preempted",
                        "source": "unknown",
                        "detail": str(detail or "")[:80],
                    }
                    logger.warning(
                        "[ConversationCoordinator] non-current action delivered session=%s token=%s",
                        session_id,
                        _short_token(token),
                    )
                return False

            outcome = "delivered" if delivered else "completed_without_delivery"
            self._append_history(state, active, outcome)
            if delivered:
                state["last_delivery"] = {
                    "ts": now,
                    "kind": active.get("kind"),
                    "source": active.get("source"),
                    "detail": str(detail or "")[:80],
                }
            state["active"] = None
            logger.info(
                "[ConversationCoordinator] action completed session=%s kind=%s source=%s "
                "token=%s delivered=%s",
                session_id,
                active.get("kind"),
                active.get("source"),
                _short_token(token),
                bool(delivered),
            )
            trace_event(
                "lease_completed",
                status=outcome,
                trace_id=active.get("trace_id", ""),
                input_id=active.get("input_id", ""),
                session_id=session_id,
                lease_id=token,
                attrs={
                    "action_kind": active.get("kind"),
                    "source": active.get("source"),
                    "delivered": bool(delivered),
                    "outcome": outcome,
                },
            )
            return True

    def cancel(self, session_id, token, reason="cancelled"):
        session_id = str(session_id or "").strip()
        token = str(token or "")
        with self.lock:
            state = self._session(session_id)
            active = state.get("active")
            if not isinstance(active, dict) or active.get("token") != token:
                return False
            self._append_history(state, active, str(reason or "cancelled")[:80])
            state["active"] = None
            logger.info(
                "[ConversationCoordinator] action cancelled session=%s kind=%s source=%s "
                "token=%s reason=%s",
                session_id,
                active.get("kind"),
                active.get("source"),
                _short_token(token),
                reason,
            )
            trace_event(
                "lease_cancelled",
                status="cancelled",
                trace_id=active.get("trace_id", ""),
                input_id=active.get("input_id", ""),
                session_id=session_id,
                lease_id=token,
                attrs={
                    "action_kind": active.get("kind"),
                    "source": active.get("source"),
                    "reason": reason,
                },
            )
            return True

    def snapshot(self, session_id):
        with self.lock:
            state = self._session(str(session_id or "").strip())
            active = dict(state.get("active")) if isinstance(state.get("active"), dict) else None
            return {
                "generation": int(state.get("generation") or 0),
                "last_user_ts": float(state.get("last_user_ts") or 0),
                "active": active,
                "last_delivery": dict(state.get("last_delivery") or {}),
                "history": [dict(item) for item in state.get("history", [])],
            }

    def _session(self, session_id):
        return self.sessions.setdefault(
            session_id,
            {
                "generation": 0,
                "last_user_ts": 0.0,
                "last_user_source": "",
                "last_turn_id": "",
                "active": None,
                "last_delivery": {},
                "history": [],
            },
        )

    def _expire_active(self, state, now):
        active = state.get("active")
        if not isinstance(active, dict):
            return
        if now < float(active.get("expires_at") or 0):
            return
        self._append_history(state, active, "lease_expired")
        trace_event(
            "lease_cancelled",
            status="expired",
            trace_id=active.get("trace_id", ""),
            input_id=active.get("input_id", ""),
            session_id=active.get("session_id", ""),
            lease_id=active.get("token", ""),
            attrs={
                "action_kind": active.get("kind"),
                "source": active.get("source"),
                "reason": "lease_expired",
            },
        )
        state["active"] = None

    def _append_history(self, state, action, outcome):
        history = state.setdefault("history", [])
        history.append({
            "ts": time.time(),
            "token": str(action.get("token") or "")[:16],
            "kind": str(action.get("kind") or "")[:40],
            "source": str(action.get("source") or "")[:60],
            "priority": int(action.get("priority") or 0),
            "outcome": str(outcome or "")[:80],
            "trace_id": str(action.get("trace_id") or "")[:16],
            "input_id": str(action.get("input_id") or "")[:16],
        })
        state["history"] = history[-30:]

    def _rejected(self, session_id, kind, source, priority, reason):
        link = current_trace_link()
        logger.info(
            "[ConversationCoordinator] action rejected session=%s kind=%s source=%s "
            "priority=%s reason=%s",
            session_id or "-",
            kind,
            source,
            priority,
            reason,
        )
        trace_event(
            "lease_rejected",
            status="rejected",
            link=link,
            attrs={
                "action_kind": kind,
                "source": source,
                "priority": priority,
                "reason": reason,
            },
        )
        return ActionLease(
            self,
            accepted=False,
            session_id=session_id,
            kind=kind,
            source=source,
            priority=priority,
            reason=reason,
            trace_id=link.trace_id,
            input_id=link.input_id,
        )


_COORDINATOR = ConversationCoordinator()


def note_user_activity(
    session_id,
    activity_ts=None,
    source="wechat",
    turn_id="",
    trace_id="",
    input_id="",
):
    return _COORDINATOR.note_user_activity(
        session_id,
        activity_ts=activity_ts,
        source=source,
        turn_id=turn_id,
        trace_id=trace_id,
        input_id=input_id,
    )


def claim_action(session_id, **kwargs):
    return _COORDINATOR.claim(session_id, **kwargs)


def coordinator_snapshot(session_id):
    return _COORDINATOR.snapshot(session_id)


def _short_token(token):
    return str(token or "")[:8] or "-"
