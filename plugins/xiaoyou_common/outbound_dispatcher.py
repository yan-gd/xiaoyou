# -*- coding: utf-8 -*-
"""Central outbound dispatcher for Xiaoyou's direct WeChat actions.

Capability plugins decide *what* should be sent.  This module owns the shared
side effects: resolving the current WeChat receiver, serializing sends per
canonical session, checking turn freshness, collecting delivery receipts and
writing successfully delivered external assistant messages to ShortMemory.
"""

import threading
import time
import uuid

from common.log import logger
from plugins.xiaoyou_common.trace_service import ensure_trace, trace_event


_LOCKS_GUARD = threading.Lock()
_SESSION_LOCKS = {}


class SendReceipt:
    def __init__(
        self,
        action_id="",
        source="",
        session_id="",
        receiver="",
        ok=False,
        stale=False,
        image_sent=False,
        sent_parts=None,
        error="",
        memory_recorded=False,
        trace_id="",
        input_id="",
        memory_record_id="",
        lease_id="",
    ):
        self.action_id = str(action_id or "")
        self.source = str(source or "")
        self.session_id = str(session_id or "")
        self.receiver = str(receiver or "")
        self.ok = bool(ok)
        self.stale = bool(stale)
        self.image_sent = bool(image_sent)
        self.sent_parts = list(sent_parts or [])
        self.error = str(error or "")
        self.memory_recorded = bool(memory_recorded or memory_record_id)
        self.trace_id = str(trace_id or "")
        self.input_id = str(input_id or "")
        self.memory_record_id = str(memory_record_id or "")
        self.lease_id = str(lease_id or "")

    @property
    def sent_text(self):
        return "\n".join(self.sent_parts).strip()

    @property
    def delivered(self):
        return bool(self.image_sent or self.sent_parts)


def resolve_receiver(session_id, fallback=""):
    """Resolve the current temporary WeChat receiver for a canonical session.

    When XiaoyouIdentity is loaded its answer is authoritative, including an
    empty answer used to prevent sending to a stale login-period identifier.
    The fallback is only used when the identity capability is unavailable.
    """
    session_id = str(session_id or "").strip()
    fallback = str(fallback or "").strip()
    try:
        import plugins

        manager = getattr(plugins, "instance", None)
        instances = getattr(manager, "instances", {}) if manager else {}
        identity = instances.get("XIAOYOUIDENTITY")
        resolver = getattr(identity, "resolve_receiver", None)
        if callable(resolver):
            return str(resolver(session_id) or "").strip()
    except Exception:
        logger.exception(
            "[OutboundDispatcher] identity receiver lookup failed session=%s",
            session_id or "-",
        )

    if fallback == session_id and fallback and not fallback.startswith("@"):
        return ""
    return fallback


def context_is_current(channel=None, context=None, freshness_check=None):
    """Fail closed when a supplied turn or capability freshness check fails."""
    checker = getattr(channel, "is_context_current", None)
    if callable(checker):
        try:
            if not checker(context):
                return False
        except Exception:
            logger.exception("[OutboundDispatcher] context freshness check failed")
            return False

    if callable(freshness_check):
        try:
            if not freshness_check():
                return False
        except Exception:
            logger.exception("[OutboundDispatcher] capability freshness check failed")
            return False

    return True


def send_text(
    *,
    session_id,
    source,
    text="",
    parts=None,
    receiver="",
    channel=None,
    context=None,
    freshness_check=None,
    delay_before_part=None,
    record_memory=True,
    memory_text="",
    trace_id="",
    input_id="",
    lease_id="",
):
    return send_action(
        session_id=session_id,
        source=source,
        text=text,
        parts=parts,
        receiver=receiver,
        channel=channel,
        context=context,
        freshness_check=freshness_check,
        delay_before_part=delay_before_part,
        record_memory=record_memory,
        memory_text=memory_text,
        trace_id=trace_id,
        input_id=input_id,
        lease_id=lease_id,
    )


def send_image(
    *,
    session_id,
    source,
    image_path,
    receiver="",
    channel=None,
    context=None,
    freshness_check=None,
    record_memory=False,
    memory_text="",
    trace_id="",
    input_id="",
    lease_id="",
):
    return send_action(
        session_id=session_id,
        source=source,
        image_path=image_path,
        receiver=receiver,
        channel=channel,
        context=context,
        freshness_check=freshness_check,
        record_memory=record_memory,
        memory_text=memory_text,
        trace_id=trace_id,
        input_id=input_id,
        lease_id=lease_id,
    )


def send_action(
    *,
    session_id,
    source,
    text="",
    parts=None,
    image_path="",
    receiver="",
    channel=None,
    context=None,
    freshness_check=None,
    delay_before_part=None,
    image_to_text_delay=0.0,
    record_memory=True,
    memory_text="",
    trace_id="",
    input_id="",
    lease_id="",
):
    """Send an image and/or ordered text parts as one serialized action."""
    action_id = uuid.uuid4().hex[:16]
    session_id = str(session_id or "").strip()
    source = str(source or "unknown").strip() or "unknown"
    receiver = resolve_receiver(session_id, receiver)
    image_path = str(image_path or "").strip()
    normalized_parts = _normalize_parts(text=text, parts=parts)
    context_kwargs = getattr(context, "kwargs", {}) or {}
    trace_link = ensure_trace(
        session_id=session_id,
        source=source,
        trace_id=trace_id or context_kwargs.get("xiaoyou_trace_id", ""),
        input_id=input_id or context_kwargs.get("xiaoyou_input_id", ""),
    )

    receipt = SendReceipt(
        action_id=action_id,
        source=source,
        session_id=session_id,
        receiver=receiver,
        trace_id=trace_link.trace_id,
        input_id=trace_link.input_id,
        memory_record_id=context_kwargs.get("xiaoyou_memory_record_id", ""),
        lease_id=lease_id,
    )
    trace_event(
        "outbound_started",
        status="started",
        link=trace_link,
        action_id=action_id,
        lease_id=lease_id,
        attrs={
            "source": source,
            "requested_parts": len(normalized_parts),
            "image_sent": False,
        },
    )

    if not session_id:
        receipt.error = "session_missing"
        _log_rejected(receipt)
        return receipt
    if not receiver:
        receipt.error = "receiver_unavailable"
        _log_rejected(receipt)
        return receipt
    if not image_path and not normalized_parts:
        receipt.error = "empty_action"
        _log_rejected(receipt)
        return receipt

    session_lock = _session_lock(session_id)
    with session_lock:
        if not context_is_current(channel, context, freshness_check):
            receipt.stale = True
            receipt.error = "stale_before_send"
            _log_rejected(receipt)
            return receipt

        try:
            from lib import itchat

            if image_path:
                result = itchat.send_image(image_path, toUserName=receiver)
                if result is False:
                    receipt.error = "image_send_returned_false"
                    _log_completed(receipt, requested_parts=len(normalized_parts))
                    return receipt
                receipt.image_sent = True

            if image_path and normalized_parts and image_to_text_delay:
                delay = max(0.0, float(image_to_text_delay or 0.0))
                if delay:
                    time.sleep(delay)
                if not context_is_current(channel, context, freshness_check):
                    receipt.stale = True
                    receipt.error = "stale_after_image"
                    _record_delivered_memory(receipt, record_memory, memory_text)
                    _log_completed(receipt, requested_parts=len(normalized_parts))
                    return receipt

            for index, part in enumerate(normalized_parts):
                if not context_is_current(channel, context, freshness_check):
                    receipt.stale = True
                    receipt.error = "stale_before_part"
                    break

                delay = _part_delay(delay_before_part, index, part)
                if delay > 0:
                    logger.info(
                        "[OutboundDispatcher] delaying action_id=%s source=%s part=%s/%s "
                        "chars=%s delay=%.2fs",
                        action_id,
                        source,
                        index + 1,
                        len(normalized_parts),
                        len(part),
                        delay,
                    )
                    time.sleep(delay)
                    if not context_is_current(channel, context, freshness_check):
                        receipt.stale = True
                        receipt.error = "stale_after_delay"
                        break

                result = itchat.send(part, toUserName=receiver)
                if result is False:
                    receipt.error = "text_send_returned_false"
                    break
                receipt.sent_parts.append(part)

        except Exception as exc:
            receipt.error = "send_exception:%s" % type(exc).__name__
            logger.exception(
                "[OutboundDispatcher] send failed action_id=%s source=%s session=%s",
                action_id,
                source,
                session_id,
            )
        requested_delivered = (
            (not image_path or receipt.image_sent)
            and len(receipt.sent_parts) == len(normalized_parts)
        )
        receipt.ok = bool(requested_delivered and not receipt.stale and not receipt.error)
        _record_delivered_memory(receipt, record_memory, memory_text)
        _record_delivered_long_memory(receipt, context=context)
        _log_completed(receipt, requested_parts=len(normalized_parts))
        return receipt


def record_assistant_message(
    session_id,
    text,
    source,
    trace_id="",
    input_id="",
    action_id="",
    return_record_id=False,
):
    """Write an externally delivered assistant action to ShortMemory."""
    session_id = str(session_id or "").strip()
    text = str(text or "").strip()
    source = str(source or "outbound").strip() or "outbound"
    if not session_id or not text:
        return False
    try:
        import plugins

        manager = getattr(plugins, "instance", None)
        instances = getattr(manager, "instances", {}) if manager else {}
        short_memory = instances.get("SHORTMEMORY")
        record_with_receipt = getattr(
            short_memory,
            "append_external_assistant_message_with_receipt",
            None,
        )
        if return_record_id and callable(record_with_receipt):
            return str(record_with_receipt(
                session_id,
                text,
                source=source,
                trace_id=trace_id,
                input_id=input_id,
                action_id=action_id,
            ) or "")
        record = getattr(short_memory, "append_external_assistant_message", None)
        if not callable(record):
            logger.warning(
                "[OutboundDispatcher] ShortMemory external API unavailable source=%s session=%s",
                source,
                session_id,
            )
            return False
        try:
            recorded = bool(record(
                session_id,
                text,
                source=source,
                trace_id=trace_id,
                input_id=input_id,
                action_id=action_id,
            ))
        except TypeError:
            recorded = bool(record(session_id, text, source=source))
        return "" if return_record_id else recorded
    except Exception:
        logger.exception(
            "[OutboundDispatcher] memory write failed source=%s session=%s",
            source,
            session_id,
        )
        return False


def record_delivered_assistant_long_memory(
    session_id,
    text,
    source,
    *,
    user_text="",
    action_id="",
    trace_id="",
    input_id="",
    delivery_complete=True,
    terminal_status="complete",
    completed_at=0,
):
    """Queue a delivered assistant turn for role-bound long-memory governance."""
    session_id = str(session_id or "").strip()
    text = str(text or "").strip()
    if not session_id or not text:
        return False
    try:
        import plugins

        manager = getattr(plugins, "instance", None)
        instances = getattr(manager, "instances", {}) if manager else {}
        long_memory = instances.get("LONGTERMMEMORY")
        record = getattr(
            long_memory,
            "append_delivered_assistant_message",
            None,
        )
        if not callable(record):
            logger.warning(
                "[OutboundDispatcher] LongTermMemory delivered API unavailable "
                "source=%s session=%s",
                str(source or "outbound")[:60],
                session_id,
            )
            return False
        return bool(
            record(
                session_id,
                text,
                user_text=str(user_text or ""),
                source=str(source or "outbound"),
                action_id=str(action_id or ""),
                trace_id=str(trace_id or ""),
                input_id=str(input_id or ""),
                delivery_complete=bool(delivery_complete),
                terminal_status=str(terminal_status or "complete"),
                completed_at=int(completed_at or time.time()),
            )
        )
    except Exception:
        logger.exception(
            "[OutboundDispatcher] LongTermMemory delivered write failed "
            "source=%s session=%s",
            str(source or "outbound")[:60],
            session_id,
        )
        return False


def _normalize_parts(text="", parts=None):
    if parts is None:
        parts = [text]
    elif isinstance(parts, str):
        parts = [parts]
    normalized = []
    for part in parts or []:
        part = str(part or "").strip()
        if part:
            normalized.append(part)
    return normalized


def _session_lock(session_id):
    with _LOCKS_GUARD:
        lock = _SESSION_LOCKS.get(session_id)
        if lock is None:
            lock = threading.RLock()
            _SESSION_LOCKS[session_id] = lock
        return lock


def _part_delay(delay_before_part, index, part):
    if not callable(delay_before_part):
        return 0.0
    try:
        return max(0.0, float(delay_before_part(index, part) or 0.0))
    except Exception:
        logger.exception(
            "[OutboundDispatcher] delay calculation failed part=%s chars=%s",
            index + 1,
            len(part),
        )
        return 0.0


def _record_delivered_memory(receipt, enabled, memory_text):
    if not enabled or not receipt.delivered:
        return False
    text = str(memory_text or receipt.sent_text or "").strip()
    if not text:
        return False
    memory_record_id = record_assistant_message(
        receipt.session_id,
        text,
        receipt.source,
        trace_id=receipt.trace_id,
        input_id=receipt.input_id,
        action_id=receipt.action_id,
        return_record_id=True,
    )
    receipt.memory_record_id = str(memory_record_id or "")
    receipt.memory_recorded = bool(receipt.memory_record_id)
    return receipt.memory_recorded


def _record_delivered_long_memory(receipt, context=None):
    if not receipt.sent_text:
        return False
    context_kwargs = getattr(context, "kwargs", {}) or {}
    if receipt.ok:
        terminal_status = "complete"
    elif receipt.stale:
        terminal_status = "stale_partial"
    else:
        terminal_status = "partial"
    return record_delivered_assistant_long_memory(
        receipt.session_id,
        receipt.sent_text,
        receipt.source,
        user_text=context_kwargs.get("long_memory_user_text", ""),
        action_id=receipt.action_id,
        trace_id=receipt.trace_id,
        input_id=receipt.input_id,
        delivery_complete=receipt.ok,
        terminal_status=terminal_status,
        completed_at=time.time(),
    )


def _log_rejected(receipt):
    trace_event(
        "outbound_completed",
        status="rejected",
        trace_id=receipt.trace_id,
        input_id=receipt.input_id,
        session_id=receipt.session_id,
        action_id=receipt.action_id,
        lease_id=receipt.lease_id,
        memory_record_id=receipt.memory_record_id,
        attrs={
            "source": receipt.source,
            "ok": False,
            "stale": receipt.stale,
            "reason": receipt.error or "rejected",
            "sent_parts": len(receipt.sent_parts),
            "image_sent": receipt.image_sent,
            "memory_recorded": receipt.memory_recorded,
        },
    )
    logger.info(
        "[OutboundDispatcher] rejected action_id=%s source=%s session=%s receiver=%s "
        "stale=%s error=%s",
        receipt.action_id,
        receipt.source,
        receipt.session_id or "-",
        _mask_receiver(receipt.receiver),
        receipt.stale,
        receipt.error or "-",
    )


def _log_completed(receipt, requested_parts):
    trace_event(
        "outbound_completed",
        status="ok" if receipt.ok else "partial_or_failed",
        trace_id=receipt.trace_id,
        input_id=receipt.input_id,
        session_id=receipt.session_id,
        action_id=receipt.action_id,
        lease_id=receipt.lease_id,
        memory_record_id=receipt.memory_record_id,
        attrs={
            "source": receipt.source,
            "ok": receipt.ok,
            "stale": receipt.stale,
            "reason": receipt.error or "completed",
            "image_sent": receipt.image_sent,
            "sent_parts": len(receipt.sent_parts),
            "requested_parts": int(requested_parts or 0),
            "memory_recorded": receipt.memory_recorded,
            "delivered": receipt.delivered,
        },
    )
    logger.info(
        "[OutboundDispatcher] completed action_id=%s source=%s session=%s receiver=%s "
        "ok=%s stale=%s image_sent=%s parts=%s/%s memory_recorded=%s error=%s",
        receipt.action_id,
        receipt.source,
        receipt.session_id or "-",
        _mask_receiver(receipt.receiver),
        receipt.ok,
        receipt.stale,
        receipt.image_sent,
        len(receipt.sent_parts),
        int(requested_parts or 0),
        receipt.memory_recorded,
        receipt.error or "-",
    )


def _mask_receiver(receiver):
    receiver = str(receiver or "")
    if len(receiver) <= 10:
        return receiver or "-"
    return receiver[:5] + "..." + receiver[-4:]
