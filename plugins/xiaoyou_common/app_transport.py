# -*- coding: utf-8 -*-
"""Channel-neutral bridge used by the optional Xiaoyou App transport.

The concrete SQLite inbox lives in ``plugins.app_channel``.  Core capabilities
import this small module instead of importing the plugin, which keeps the
existing WeChat-only runtime usable when the App channel is disabled.
"""

import threading


_LOCK = threading.RLock()
_STORE = None
_SERVICE = None


def register_app_store(store):
    global _STORE
    with _LOCK:
        _STORE = store


def unregister_app_store(store):
    global _STORE
    with _LOCK:
        if _STORE is store:
            _STORE = None


def get_app_store():
    with _LOCK:
        return _STORE


def register_app_service(service):
    global _SERVICE
    with _LOCK:
        if _SERVICE is None:
            _SERVICE = service
        return _SERVICE


def get_app_service():
    with _LOCK:
        return _SERVICE


def app_receiver(device_id):
    device_id = str(device_id or "").strip()
    return "app:%s" % device_id if device_id else ""


def app_device_id(receiver):
    receiver = str(receiver or "").strip()
    if not receiver.startswith("app:"):
        return ""
    return receiver[4:].strip()


def is_app_receiver(receiver):
    return bool(app_device_id(receiver))


def preferred_app_receiver(session_id):
    store = get_app_store()
    if store is None:
        return ""
    try:
        device_id = store.preferred_device(session_id)
    except Exception:
        return ""
    return app_receiver(device_id)


def queue_app_action(
    *,
    action_id,
    session_id,
    receiver,
    source,
    parts=None,
    image_path="",
    image_url="",
    trace_id="",
    input_id="",
    source_message_ids=None,
    user_text="",
):
    store = get_app_store()
    device_id = app_device_id(receiver)
    if store is None or not device_id:
        return False
    return bool(
        store.queue_action(
            action_id=action_id,
            session_id=session_id,
            device_id=device_id,
            source=source,
            parts=list(parts or []),
            image_path=image_path,
            image_url=image_url,
            trace_id=trace_id,
            input_id=input_id,
            source_message_ids=list(source_message_ids or []),
            user_text=user_text,
        )
    )
