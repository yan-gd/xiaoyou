# -*- coding: utf-8 -*-
"""Privacy-safe in-process tracing for Xiaoyou's conversation pipeline."""

import contextvars
import os
import re
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass

from common.log import logger


TRACE_ID_KEY = "xiaoyou_trace_id"
INPUT_ID_KEY = "xiaoyou_input_id"
PARENT_TRACE_ID_KEY = "xiaoyou_parent_trace_id"

_CURRENT_LINK = contextvars.ContextVar("xiaoyou_trace_link", default=None)
_SAFE_ATTRS = {
    "action_kind",
    "batch_size",
    "component",
    "delivered",
    "elapsed_ms",
    "error_kind",
    "image_sent",
    "input_type",
    "input_version",
    "memory_recorded",
    "model",
    "ok",
    "outcome",
    "priority",
    "purpose",
    "reason",
    "record_source",
    "requested_parts",
    "role",
    "sent_parts",
    "source",
    "stale",
    "status_code",
    "thinking_fallback",
}


@dataclass(frozen=True)
class TraceLink:
    trace_id: str = ""
    input_id: str = ""
    session_id: str = ""
    parent_trace_id: str = ""


class TraceService:
    """Keep a bounded trace index and emit content-free structured events."""

    def __init__(self):
        self.lock = threading.RLock()
        self.traces = OrderedDict()

    def enabled(self):
        return os.getenv("XIAOYOU_TRACE_ENABLED", "true").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

    def begin(
        self,
        *,
        session_id="",
        source="unknown",
        trace_id="",
        input_id="",
        parent_trace_id="",
        force_new=False,
    ):
        session_id = _safe_id(session_id)
        source = _safe_token(source, "unknown")
        trace_id = "" if force_new else _safe_id(trace_id)
        trace_id = trace_id or uuid.uuid4().hex[:16]
        input_id = _safe_id(input_id)
        parent_trace_id = _safe_id(parent_trace_id)
        now = time.time()

        link = TraceLink(trace_id, input_id, session_id, parent_trace_id)
        if not self.enabled():
            _CURRENT_LINK.set(link)
            return link

        created = False
        with self.lock:
            trace = self.traces.get(trace_id)
            if trace is None:
                trace = {
                    "trace_id": trace_id,
                    "input_id": input_id,
                    "session_id": session_id,
                    "parent_trace_id": parent_trace_id,
                    "source": source,
                    "created_at": now,
                    "updated_at": now,
                    "events": [],
                }
                self.traces[trace_id] = trace
                created = True
            else:
                if input_id and not trace.get("input_id"):
                    trace["input_id"] = input_id
                if session_id:
                    trace["session_id"] = session_id
                if parent_trace_id and not trace.get("parent_trace_id"):
                    trace["parent_trace_id"] = parent_trace_id
                trace["updated_at"] = now
                self.traces.move_to_end(trace_id)
            self._trim_locked()

            link = TraceLink(
                trace_id,
                str(trace.get("input_id") or input_id),
                str(trace.get("session_id") or session_id),
                str(trace.get("parent_trace_id") or parent_trace_id),
            )

        _CURRENT_LINK.set(link)

        if created:
            self._append_event(link, "trace_started", "started", attrs={"source": source})
        return link

    def attach_input(self, context, source="chat_channel"):
        kwargs = getattr(context, "kwargs", {}) or {}
        trace_id = _safe_id(kwargs.get(TRACE_ID_KEY))
        input_id = _safe_id(kwargs.get(INPUT_ID_KEY))
        created = not trace_id
        if created:
            trace_id = uuid.uuid4().hex[:16]
        if not input_id:
            input_id = uuid.uuid4().hex[:16]

        link = self.begin(
            session_id=kwargs.get("session_id") or kwargs.get("receiver"),
            source=source,
            trace_id=trace_id,
            input_id=input_id,
            parent_trace_id=kwargs.get(PARENT_TRACE_ID_KEY),
        )
        kwargs[TRACE_ID_KEY] = link.trace_id
        kwargs[INPUT_ID_KEY] = link.input_id
        if link.parent_trace_id:
            kwargs[PARENT_TRACE_ID_KEY] = link.parent_trace_id
        context.kwargs = kwargs

        if created:
            self.event(
                "input_received",
                status="accepted",
                link=link,
                attrs={
                    "source": source,
                    "input_type": getattr(getattr(context, "type", None), "name", None)
                    or str(getattr(context, "type", "")),
                    "input_version": kwargs.get("xiaoyou_input_version"),
                    "batch_size": kwargs.get("xiaoyou_input_batch_size", 1),
                },
            )
        return link

    def activate_context(self, context):
        kwargs = getattr(context, "kwargs", {}) or {}
        trace_id = _safe_id(kwargs.get(TRACE_ID_KEY))
        if not trace_id:
            return self.attach_input(context)
        return self.begin(
            session_id=kwargs.get("session_id") or kwargs.get("receiver"),
            source="context",
            trace_id=trace_id,
            input_id=kwargs.get(INPUT_ID_KEY),
            parent_trace_id=kwargs.get(PARENT_TRACE_ID_KEY),
        )

    def current(self):
        link = _CURRENT_LINK.get()
        return link if isinstance(link, TraceLink) else TraceLink()

    def ensure(
        self,
        *,
        session_id="",
        source="unknown",
        trace_id="",
        input_id="",
        parent_trace_id="",
    ):
        trace_id = _safe_id(trace_id)
        if trace_id:
            return self.begin(
                session_id=session_id,
                source=source,
                trace_id=trace_id,
                input_id=input_id,
                parent_trace_id=parent_trace_id,
            )

        current = self.current()
        safe_session = _safe_id(session_id)
        if current.trace_id and (
            not safe_session
            or not current.session_id
            or current.session_id == safe_session
        ):
            return current
        return self.begin(
            session_id=safe_session,
            source=source,
            input_id=input_id,
            parent_trace_id=parent_trace_id,
        )

    def begin_action(
        self,
        *,
        session_id,
        source,
        trace_id="",
        input_id="",
        parent_trace_id="",
    ):
        if trace_id:
            return self.begin(
                session_id=session_id,
                source=source,
                trace_id=trace_id,
                input_id=input_id,
                parent_trace_id=parent_trace_id,
            )
        return self.begin(
            session_id=session_id,
            source=source,
            input_id=input_id,
            parent_trace_id=parent_trace_id,
            force_new=True,
        )

    def event(
        self,
        stage,
        *,
        status="",
        link=None,
        trace_id="",
        input_id="",
        session_id="",
        model_call_id="",
        lease_id="",
        action_id="",
        memory_record_id="",
        attrs=None,
    ):
        if not self.enabled():
            return False
        if not isinstance(link, TraceLink):
            if trace_id:
                safe_trace_id = _safe_id(trace_id)
                with self.lock:
                    trace = self.traces.get(safe_trace_id) or {}
                link = TraceLink(
                    safe_trace_id,
                    _safe_id(input_id) or str(trace.get("input_id") or ""),
                    _safe_id(session_id) or str(trace.get("session_id") or ""),
                    str(trace.get("parent_trace_id") or ""),
                )
            else:
                link = self.ensure(
                    session_id=session_id,
                    source=(attrs or {}).get("component")
                    or (attrs or {}).get("source")
                    or stage,
                    input_id=input_id,
                )
        event = {
            "ts": time.time(),
            "stage": _safe_token(stage, "unknown"),
            "status": _safe_token(status, "unknown") if status else "",
            "model_call_id": _safe_id(model_call_id),
            "lease_id": _safe_id(lease_id),
            "action_id": _safe_id(action_id),
            "memory_record_id": _safe_id(memory_record_id),
            "attrs": _safe_attrs(attrs),
        }
        return self._append_event(link, event["stage"], event["status"], event=event)

    def rebind_session(self, trace_id, session_id):
        trace_id = _safe_id(trace_id)
        session_id = _safe_id(session_id)
        if not trace_id or not session_id:
            return False
        with self.lock:
            trace = self.traces.get(trace_id)
            if trace is None:
                return False
            trace["session_id"] = session_id
            trace["updated_at"] = time.time()
            current = self.current()
            if current.trace_id == trace_id:
                _CURRENT_LINK.set(
                    TraceLink(
                        current.trace_id,
                        current.input_id,
                        session_id,
                        current.parent_trace_id,
                    )
                )
        return True

    def snapshot(self, trace_id):
        trace_id = _safe_id(trace_id)
        with self.lock:
            trace = self.traces.get(trace_id)
            if trace is None:
                return {}
            return {
                key: [
                    {
                        **dict(item),
                        "attrs": dict(item.get("attrs") or {}),
                    }
                    for item in value
                ]
                if key == "events"
                else value
                for key, value in trace.items()
            }

    def _append_event(self, link, stage, status="", attrs=None, event=None):
        if not self.enabled() or not link.trace_id:
            return False
        if event is None:
            event = {
                "ts": time.time(),
                "stage": _safe_token(stage, "unknown"),
                "status": _safe_token(status, "unknown") if status else "",
                "model_call_id": "",
                "lease_id": "",
                "action_id": "",
                "memory_record_id": "",
                "attrs": _safe_attrs(attrs),
            }

        with self.lock:
            trace = self.traces.get(link.trace_id)
            if trace is None:
                trace = {
                    "trace_id": link.trace_id,
                    "input_id": link.input_id,
                    "session_id": link.session_id,
                    "parent_trace_id": link.parent_trace_id,
                    "source": "unknown",
                    "created_at": event["ts"],
                    "updated_at": event["ts"],
                    "events": [],
                }
                self.traces[link.trace_id] = trace
            events = trace.setdefault("events", [])
            events.append(event)
            max_events = _env_int("XIAOYOU_TRACE_MAX_EVENTS", 80, minimum=10)
            trace["events"] = events[-max_events:]
            trace["updated_at"] = event["ts"]
            self.traces.move_to_end(link.trace_id)
            self._trim_locked()

        attrs_text = " ".join(
            "%s=%s" % (key, value)
            for key, value in sorted(event.get("attrs", {}).items())
        )
        logger.info(
            "[Trace] stage=%s status=%s trace_id=%s input_id=%s session=%s "
            "model_call_id=%s lease_id=%s action_id=%s memory_record_id=%s%s",
            event.get("stage") or "-",
            event.get("status") or "-",
            link.trace_id,
            link.input_id or "-",
            _mask_session(link.session_id),
            event.get("model_call_id") or "-",
            event.get("lease_id") or "-",
            event.get("action_id") or "-",
            event.get("memory_record_id") or "-",
            (" " + attrs_text) if attrs_text else "",
        )
        return True

    def _trim_locked(self):
        max_traces = _env_int("XIAOYOU_TRACE_MAX_TRACES", 200, minimum=20)
        while len(self.traces) > max_traces:
            self.traces.popitem(last=False)


def _safe_id(value):
    value = str(value or "").strip()
    return re.sub(r"[^A-Za-z0-9_.:@-]", "_", value)[:80]


def _safe_token(value, default=""):
    value = str(value or default).strip().lower()
    value = re.sub(r"[^a-z0-9_.:-]+", "_", value)
    return value[:80] or default


def _mask_session(value):
    value = str(value or "")
    if not value:
        return "-"
    if value.startswith("@") or len(value) > 20:
        return value[:5] + "..." + value[-4:]
    return value


def _safe_attrs(attrs):
    cleaned = {}
    for key, value in (attrs or {}).items():
        key = str(key or "")
        if key not in _SAFE_ATTRS or value is None or value == "":
            continue
        if isinstance(value, bool):
            cleaned[key] = value
        elif isinstance(value, (int, float)):
            cleaned[key] = round(value, 3) if isinstance(value, float) else value
        else:
            cleaned[key] = _safe_token(value, "unknown")
    return cleaned


def _env_int(name, default, minimum=0):
    try:
        value = int(os.getenv(name, str(default)))
    except Exception:
        value = default
    return max(minimum, value)


trace_service = TraceService()


def attach_input_trace(context, source="chat_channel"):
    return trace_service.attach_input(context, source=source)


def activate_context_trace(context):
    return trace_service.activate_context(context)


def current_trace_link():
    return trace_service.current()


def ensure_trace(**kwargs):
    return trace_service.ensure(**kwargs)


def begin_action_trace(**kwargs):
    return trace_service.begin_action(**kwargs)


def trace_event(stage, **kwargs):
    return trace_service.event(stage, **kwargs)


def rebind_trace_session(trace_id, session_id):
    return trace_service.rebind_session(trace_id, session_id)


def trace_snapshot(trace_id):
    return trace_service.snapshot(trace_id)
