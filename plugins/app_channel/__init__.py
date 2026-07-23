# -*- coding: utf-8 -*-
"""Authenticated HTTP/SSE channel for the Xiaoyou mobile App.

This plugin deliberately reuses ChatChannel's existing queue and plugin events.
It does not create a second chat model, memory database, or turn scheduler.
"""

import hmac
import json
import mimetypes
import os
import shutil
import sqlite3
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import plugins
from bridge.context import Context, ContextType
from bridge.reply import ReplyType
from channel.chat_channel import ChatChannel
from common.log import logger
from plugins import Plugin
from plugins.xiaoyou_common.app_transport import (
    app_receiver,
    get_app_service,
    register_app_service,
    register_app_store,
)
from plugins.xiaoyou_common.conversation_coordinator import note_user_activity
from plugins.xiaoyou_common.outbound_dispatcher import (
    record_assistant_message,
    record_delivered_assistant_long_memory,
)
from plugins.xiaoyou_common.recent_state_service import get_recent_state_service
from plugins.xiaoyou_common.runtime_paths import appdata_root, runtime_path
from plugins.xiaoyou_common.trace_service import (
    attach_input_trace,
    trace_event,
)


DATABASE_PATH = runtime_path(
    "app_channel",
    "app.db",
    env_var="XIAOYOU_APP_DB_PATH",
)
MEDIA_DIR = Path(appdata_root()) / "app_channel" / "media"


def _truthy(value):
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _safe_device_id(value):
    value = str(value or "").strip()
    if not value or len(value) > 128:
        return ""
    if any(ord(char) < 33 or char in "/\\:?" for char in value):
        return ""
    return value


class AppInboxStore:
    """Durable App input/output inbox under ``data/app_channel``."""

    def __init__(self, path=DATABASE_PATH):
        self.path = str(path)
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=8)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=8000")
        return connection

    def _initialize(self):
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS devices (
                    device_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    platform TEXT NOT NULL DEFAULT '',
                    push_token TEXT NOT NULL DEFAULT '',
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at INTEGER NOT NULL,
                    last_seen_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS inputs (
                    message_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    client_sequence INTEGER,
                    client_created_at INTEGER,
                    status TEXT NOT NULL DEFAULT 'accepted',
                    accepted_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_app_inputs_device_time
                    ON inputs(device_id, accepted_at);

                CREATE TABLE IF NOT EXISTS actions (
                    action_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    input_id TEXT NOT NULL DEFAULT '',
                    trace_id TEXT NOT NULL DEFAULT '',
                    user_text TEXT NOT NULL DEFAULT '',
                    requested_parts INTEGER NOT NULL DEFAULT 0,
                    terminal_status TEXT NOT NULL DEFAULT 'queued',
                    delivery_complete INTEGER NOT NULL DEFAULT 0,
                    memory_recorded INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL,
                    completed_at INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_app_actions_device_time
                    ON actions(device_id, created_at);

                CREATE TABLE IF NOT EXISTS action_inputs (
                    action_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    PRIMARY KEY(action_id, message_id),
                    FOREIGN KEY(action_id) REFERENCES actions(action_id)
                );

                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    action_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    text TEXT NOT NULL DEFAULT '',
                    media_id TEXT NOT NULL DEFAULT '',
                    remote_url TEXT NOT NULL DEFAULT '',
                    mime_type TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    acknowledged_at INTEGER,
                    FOREIGN KEY(action_id) REFERENCES actions(action_id)
                );
                CREATE INDEX IF NOT EXISTS idx_app_events_device_sequence
                    ON events(device_id, sequence);

                CREATE TABLE IF NOT EXISTS media (
                    media_id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    local_path TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                """
            )
            input_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(inputs)")
            }
            if "status" not in input_columns:
                connection.execute(
                    """
                    ALTER TABLE inputs
                    ADD COLUMN status TEXT NOT NULL DEFAULT 'accepted'
                    """
                )
            connection.execute(
                """
                UPDATE actions
                SET memory_recorded=0
                WHERE memory_recorded=2
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO action_inputs(action_id, message_id)
                SELECT action_id, input_id
                FROM actions
                WHERE input_id != ''
                """
            )
            connection.execute(
                """
                UPDATE inputs
                SET status='failed'
                WHERE status IN ('accepted', 'queued')
                  AND NOT EXISTS (
                      SELECT 1
                      FROM action_inputs
                      WHERE action_inputs.message_id=inputs.message_id
                  )
                """
            )

    def register_device(self, device_id, session_id, platform="", push_token=""):
        device_id = _safe_device_id(device_id)
        session_id = str(session_id or "").strip()
        if not device_id or not session_id:
            return False
        now = int(time.time())
        with self.lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO devices(
                    device_id, session_id, platform, push_token,
                    active, created_at, last_seen_at
                ) VALUES(?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    session_id=excluded.session_id,
                    platform=excluded.platform,
                    push_token=excluded.push_token,
                    active=1,
                    last_seen_at=excluded.last_seen_at
                """,
                (
                    device_id,
                    session_id,
                    str(platform or "")[:40],
                    str(push_token or "")[:2048],
                    now,
                    now,
                ),
            )
        return True

    def preferred_device(self, session_id):
        with self.lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT device_id
                FROM devices
                WHERE session_id=? AND active=1
                ORDER BY last_seen_at DESC
                LIMIT 1
                """,
                (str(session_id or ""),),
            ).fetchone()
        return str(row["device_id"] or "") if row else ""

    def accept_input(
        self,
        *,
        message_id,
        session_id,
        device_id,
        text,
        client_sequence=None,
        client_created_at=None,
    ):
        now = int(time.time())
        self.register_device(device_id, session_id)
        with self.lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT status FROM inputs WHERE message_id=?",
                (str(message_id),),
            ).fetchone()
            if existing:
                if str(existing["status"] or "") == "failed":
                    connection.execute(
                        """
                        UPDATE inputs
                        SET status='accepted', accepted_at=?
                        WHERE message_id=?
                        """,
                        (now, str(message_id)),
                    )
                    return True
                return False
            cursor = connection.execute(
                """
                INSERT INTO inputs(
                    message_id, session_id, device_id, text,
                    client_sequence, client_created_at, status, accepted_at
                ) VALUES(?, ?, ?, ?, ?, ?, 'accepted', ?)
                """,
                (
                    str(message_id),
                    str(session_id),
                    str(device_id),
                    str(text),
                    int(client_sequence) if client_sequence is not None else None,
                    int(client_created_at) if client_created_at else None,
                    now,
                ),
            )
            connection.execute(
                "UPDATE devices SET last_seen_at=? WHERE device_id=?",
                (now, str(device_id)),
            )
            inserted = cursor.rowcount > 0
        return inserted

    def mark_input_status(self, message_id, status):
        status = str(status or "").strip().lower()
        if status not in ("accepted", "queued", "responded", "failed"):
            raise ValueError("invalid_input_status")
        with self.lock, self._connect() as connection:
            connection.execute(
                "UPDATE inputs SET status=? WHERE message_id=?",
                (status, str(message_id or "")),
            )

    def queue_action(
        self,
        *,
        action_id,
        session_id,
        device_id,
        source,
        parts=None,
        image_path="",
        image_url="",
        trace_id="",
        input_id="",
        source_message_ids=None,
        user_text="",
    ):
        device_id = _safe_device_id(device_id)
        action_id = str(action_id or "").strip()
        session_id = str(session_id or "").strip()
        if not action_id or not session_id or not device_id:
            return False

        parts = [
            str(part or "").strip()
            for part in (parts or [])
            if str(part or "").strip()
        ]
        image_url = str(image_url or "").strip()
        source_message_ids = [
            str(item or "").strip()
            for item in (source_message_ids or [])
            if str(item or "").strip()
        ]
        if input_id and str(input_id) not in source_message_ids:
            source_message_ids.append(str(input_id))
        if not parts and not image_path and not image_url:
            return False

        now = int(time.time())
        with self.changed, self._connect() as connection:
            existing = connection.execute(
                "SELECT 1 FROM actions WHERE action_id=?",
                (action_id,),
            ).fetchone()
            if existing:
                return True

            media = self._copy_media(image_path, device_id) if image_path else None
            event_count = len(parts) + int(bool(media or image_url))
            if event_count < 1:
                return False
            inserted = connection.execute(
                """
                INSERT OR IGNORE INTO actions(
                    action_id, session_id, device_id, source, input_id, trace_id,
                    user_text, requested_parts, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    action_id,
                    session_id,
                    device_id,
                    str(source or "app")[:80],
                    str(input_id or "")[:128],
                    str(trace_id or "")[:128],
                    str(user_text or "")[:4000],
                    event_count,
                    now,
                ),
            ).rowcount > 0
            if not inserted:
                return True

            for message_id in source_message_ids:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO action_inputs(action_id, message_id)
                    VALUES(?, ?)
                    """,
                    (action_id, message_id),
                )
            position = 0
            if media or image_url:
                connection.execute(
                    """
                    INSERT INTO events(
                        event_id, action_id, device_id, position, kind, media_id,
                        remote_url, mime_type, created_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        uuid.uuid4().hex,
                        action_id,
                        device_id,
                        position,
                        "image",
                        media["media_id"] if media else "",
                        image_url,
                        media["mime_type"] if media else "",
                        now,
                    ),
                )
                position += 1

            for part in parts:
                connection.execute(
                    """
                    INSERT INTO events(
                        event_id, action_id, device_id, position,
                        kind, text, created_at
                    ) VALUES(?, ?, ?, ?, 'text', ?, ?)
                    """,
                    (
                        uuid.uuid4().hex,
                        action_id,
                        device_id,
                        position,
                        part,
                        now,
                    ),
                )
                position += 1
            for message_id in source_message_ids:
                connection.execute(
                    """
                    UPDATE inputs
                    SET status='responded'
                    WHERE message_id=?
                    """,
                    (message_id,),
                )
            self.changed.notify_all()
        return True

    def _copy_media(self, image_path, device_id):
        source = Path(str(image_path or "")).resolve()
        if not source.is_file():
            return None
        try:
            if source.stat().st_size > 25 * 1024 * 1024:
                return None
        except OSError:
            return None
        suffix = source.suffix.lower()
        if suffix not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            suffix = ".bin"
        media_id = uuid.uuid4().hex
        target = MEDIA_DIR / (media_id + suffix)
        shutil.copy2(str(source), str(target))
        mime_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        with self.lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO media(media_id, device_id, local_path, mime_type, created_at)
                VALUES(?, ?, ?, ?, ?)
                """,
                (media_id, device_id, str(target), mime_type, int(time.time())),
            )
        return {"media_id": media_id, "mime_type": mime_type}

    def events_after(self, device_id, after=0, limit=50):
        device_id = _safe_device_id(device_id)
        if not device_id:
            return []
        limit = max(1, min(int(limit or 50), 200))
        with self.lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT e.*, a.source, a.terminal_status, a.requested_parts
                FROM events e
                JOIN actions a ON a.action_id=e.action_id
                WHERE e.device_id=? AND e.sequence>?
                  AND (
                      a.terminal_status='queued'
                      OR e.acknowledged_at IS NOT NULL
                  )
                ORDER BY e.sequence ASC
                LIMIT ?
                """,
                (device_id, int(after or 0), limit),
            ).fetchall()
        return [self._event_dict(row) for row in rows]

    def wait_for_events(self, device_id, after=0, timeout=25):
        events = self.events_after(device_id, after)
        if events:
            return events
        with self.changed:
            self.changed.wait(timeout=max(0.1, min(float(timeout or 25), 30)))
        return self.events_after(device_id, after)

    @staticmethod
    def _event_dict(row):
        return {
            "sequence": int(row["sequence"]),
            "event_id": str(row["event_id"]),
            "action_id": str(row["action_id"]),
            "position": int(row["position"]),
            "kind": str(row["kind"]),
            "text": str(row["text"] or ""),
            "media_id": str(row["media_id"] or ""),
            "remote_url": str(row["remote_url"] or ""),
            "mime_type": str(row["mime_type"] or ""),
            "source": str(row["source"] or ""),
            "created_at": int(row["created_at"]),
            "acknowledged": bool(row["acknowledged_at"]),
            "terminal_status": str(row["terminal_status"] or "queued"),
            "requested_parts": int(row["requested_parts"] or 0),
        }

    def history(self, device_id, before=0, limit=100):
        device_id = _safe_device_id(device_id)
        if not device_id:
            return []
        limit = max(1, min(int(limit or 100), 300))
        before_clause = "AND accepted_at<?" if before else ""
        args = [device_id]
        if before:
            args.append(int(before))
        args.append(limit)
        with self.lock, self._connect() as connection:
            input_rows = connection.execute(
                """
                SELECT message_id, text, accepted_at
                FROM inputs
                WHERE device_id=? %s
                ORDER BY accepted_at DESC
                LIMIT ?
                """ % before_clause,
                tuple(args),
            ).fetchall()
            event_rows = connection.execute(
                """
                SELECT e.*, a.source, a.terminal_status, a.requested_parts
                FROM events e
                JOIN actions a ON a.action_id=e.action_id
                WHERE e.device_id=?
                  AND (
                      a.terminal_status='queued'
                      OR e.acknowledged_at IS NOT NULL
                  )
                ORDER BY e.created_at DESC, e.sequence DESC
                LIMIT ?
                """,
                (device_id, limit),
            ).fetchall()
        items = [
            {
                "id": str(row["message_id"]),
                "role": "user",
                "kind": "text",
                "text": str(row["text"]),
                "created_at": int(row["accepted_at"]),
            }
            for row in input_rows
        ]
        items.extend(
            {
                "id": str(row["event_id"]),
                "action_id": str(row["action_id"]),
                "role": "assistant",
                "kind": str(row["kind"]),
                "text": str(row["text"] or ""),
                "media_id": str(row["media_id"] or ""),
                "remote_url": str(row["remote_url"] or ""),
                "created_at": int(row["created_at"]),
                "terminal_status": str(row["terminal_status"] or "queued"),
                "requested_parts": int(row["requested_parts"] or 0),
            }
            for row in event_rows
        )
        items.sort(key=lambda item: (item["created_at"], item["id"]))
        return items[-limit:]

    def latest_sequence(self, device_id):
        with self.lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT COALESCE(MAX(sequence), 0) AS latest
                FROM events
                WHERE device_id=?
                """,
                (_safe_device_id(device_id),),
            ).fetchone()
        return int(row["latest"] or 0) if row else 0

    def acknowledge(self, action_id, device_id, status, event_ids=None):
        action_id = str(action_id or "").strip()
        device_id = _safe_device_id(device_id)
        status = str(status or "").strip().lower()
        if status not in ("complete", "partial", "failed", "cancelled"):
            raise ValueError("invalid_terminal_status")
        now = int(time.time())
        with self.lock, self._connect() as connection:
            action = connection.execute(
                "SELECT * FROM actions WHERE action_id=? AND device_id=?",
                (action_id, device_id),
            ).fetchone()
            if not action:
                return None

            already_terminal = str(action["terminal_status"]) in (
                "complete",
                "partial",
                "failed",
                "cancelled",
            )
            if not already_terminal:
                if status == "complete":
                    connection.execute(
                        "UPDATE events SET acknowledged_at=? WHERE action_id=?",
                        (now, action_id),
                    )
                elif status == "partial":
                    normalized_ids = [
                        str(item or "").strip()
                        for item in (event_ids or [])
                        if str(item or "").strip()
                    ]
                    for event_id in normalized_ids:
                        connection.execute(
                            """
                            UPDATE events SET acknowledged_at=?
                            WHERE action_id=? AND event_id=?
                            """,
                            (now, action_id, event_id),
                        )
                acknowledged_count = connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM events
                    WHERE action_id=? AND acknowledged_at IS NOT NULL
                    """,
                    (action_id,),
                ).fetchone()["count"]
                requested_parts = int(action["requested_parts"] or 0)
                delivery_complete = (
                    status == "complete"
                    and acknowledged_count == requested_parts
                )
                connection.execute(
                    """
                    UPDATE actions
                    SET terminal_status=?, delivery_complete=?, completed_at=?
                    WHERE action_id=?
                    """,
                    (
                        status,
                        1 if delivery_complete else 0,
                        now,
                        action_id,
                    ),
                )

            action = connection.execute(
                "SELECT * FROM actions WHERE action_id=?",
                (action_id,),
            ).fetchone()
            delivered_rows = connection.execute(
                """
                SELECT *
                FROM events
                WHERE action_id=? AND acknowledged_at IS NOT NULL
                ORDER BY position ASC
                """,
                (action_id,),
            ).fetchall()
        sent_text = "\n".join(
            str(row["text"] or "").strip()
            for row in delivered_rows
            if str(row["kind"]) == "text" and str(row["text"] or "").strip()
        )
        return {
            "action_id": action_id,
            "session_id": str(action["session_id"]),
            "source": str(action["source"]),
            "input_id": str(action["input_id"] or ""),
            "trace_id": str(action["trace_id"] or ""),
            "user_text": str(action["user_text"] or ""),
            "terminal_status": str(action["terminal_status"]),
            "delivery_complete": bool(action["delivery_complete"]),
            "memory_recorded": bool(action["memory_recorded"]),
            "sent_text": sent_text,
            "sent_parts": len(delivered_rows),
            "requested_parts": int(action["requested_parts"] or 0),
            "completed_at": int(action["completed_at"] or now),
        }

    def mark_memory_recorded(self, action_id):
        with self.lock, self._connect() as connection:
            connection.execute(
                "UPDATE actions SET memory_recorded=1 WHERE action_id=?",
                (str(action_id or ""),),
            )

    def claim_memory_recording(self, action_id):
        with self.lock, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE actions
                SET memory_recorded=2
                WHERE action_id=? AND memory_recorded=0
                """,
                (str(action_id or ""),),
            )
        return cursor.rowcount > 0

    def release_memory_recording(self, action_id):
        with self.lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE actions
                SET memory_recorded=0
                WHERE action_id=? AND memory_recorded=2
                """,
                (str(action_id or ""),),
            )

    def media(self, media_id, device_id):
        with self.lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM media WHERE media_id=? AND device_id=?",
                (str(media_id or ""), _safe_device_id(device_id)),
            ).fetchone()
        if not row:
            return None
        path = Path(str(row["local_path"])).resolve()
        try:
            path.relative_to(MEDIA_DIR.resolve())
        except ValueError:
            return None
        if not path.is_file():
            return None
        return path, str(row["mime_type"])


class AppRuntimeChannel(ChatChannel):
    """A ChatChannel whose send target is the durable App inbox."""

    def __init__(self, store, canonical_session_id):
        self.store = store
        self.canonical_session_id = canonical_session_id
        super().__init__()

    def submit_text(
        self,
        *,
        text,
        message_id,
        device_id,
        client_sequence=None,
        client_created_at=None,
    ):
        text = str(text or "").strip()
        device_id = _safe_device_id(device_id)
        message_id = str(message_id or "").strip()
        if not text:
            raise ValueError("empty_text")
        if len(text) > 12000:
            raise ValueError("text_too_long")
        if not message_id or len(message_id) > 128:
            raise ValueError("invalid_message_id")
        if not device_id:
            raise ValueError("invalid_device_id")

        inserted = self.store.accept_input(
            message_id=message_id,
            session_id=self.canonical_session_id,
            device_id=device_id,
            text=text,
            client_sequence=client_sequence,
            client_created_at=client_created_at,
        )
        if not inserted:
            return False

        receiver = app_receiver(device_id)
        kwargs = {
            "session_id": self.canonical_session_id,
            "receiver": receiver,
            "isgroup": False,
            "origin_ctype": ContextType.TEXT,
            "xiaoyou_transport": "app",
            "xiaoyou_app_device_id": device_id,
            "xiaoyou_input_id": message_id,
            "xiaoyou_source_message_ids": [message_id],
            "xiaoyou_defer_memory_until_delivery": True,
        }
        context = Context(ContextType.TEXT, text)
        context.kwargs = kwargs
        for key, value in kwargs.items():
            context[key] = value

        try:
            attach_input_trace(context, source="app_receive")
        except Exception:
            logger.exception("[AppChannel] failed to attach input trace")
        note_user_activity(
            self.canonical_session_id,
            activity_ts=time.time(),
            source="app_input",
            turn_id=message_id,
            trace_id=context.get("xiaoyou_trace_id", ""),
            input_id=message_id,
        )
        try:
            self.produce(context)
        except Exception:
            self.store.mark_input_status(message_id, "failed")
            raise
        self.store.mark_input_status(message_id, "queued")
        return True

    def send(self, reply, context):
        kwargs = getattr(context, "kwargs", {}) or {}
        action_id = str(
            kwargs.get("xiaoyou_outbound_action_id") or uuid.uuid4().hex[:16]
        )
        reply_type = getattr(getattr(reply, "type", None), "name", "")
        content = str(getattr(reply, "content", "") or "").strip()
        receiver = str(kwargs.get("receiver") or "")
        parts = []
        image_path = ""
        image_url = ""
        if reply_type == getattr(ReplyType.TEXT, "name", "TEXT"):
            parts = [content]
        elif reply_type in ("IMAGE",):
            image_path = content
        elif reply_type in ("IMAGE_URL",):
            image_url = content
        else:
            logger.warning(
                "[AppChannel] unsupported reply type=%s action_id=%s",
                reply_type or "-",
                action_id,
            )
            return False

        queued = self.store.queue_action(
            action_id=action_id,
            session_id=str(kwargs.get("session_id") or self.canonical_session_id),
            device_id=str(kwargs.get("xiaoyou_app_device_id") or receiver[4:]),
            source="chat_channel",
            parts=parts,
            image_path=image_path,
            image_url=image_url,
            trace_id=kwargs.get("xiaoyou_trace_id", ""),
            input_id=kwargs.get("xiaoyou_input_id", ""),
            source_message_ids=kwargs.get("xiaoyou_source_message_ids") or [],
            user_text=kwargs.get("long_memory_user_text")
            or kwargs.get("short_memory_current_user_text", ""),
        )
        if queued:
            kwargs["xiaoyou_delivery_deferred"] = True
            context.kwargs = kwargs
        return queued


class AppRequestHandler(BaseHTTPRequestHandler):
    server_version = "XiaoyouApp/1.0"
    protocol_version = "HTTP/1.1"
    plugin = None

    def log_message(self, format_string, *args):
        logger.info("[AppChannel] http " + format_string, *args)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/v1/health":
            self._json(200, {"ok": True, "service": "xiaoyou-app"})
            return
        if not self._authorized():
            return
        query = parse_qs(parsed.query)
        device_id = (query.get("device_id") or [""])[0]
        if parsed.path == "/v1/events":
            events = self.plugin.store.events_after(
                device_id,
                after=self._integer((query.get("after") or [0])[0], 0),
                limit=self._integer((query.get("limit") or [50])[0], 50),
            )
            self._json(200, {"events": events})
            return
        if parsed.path == "/v1/events/stream":
            self._sse(device_id, query)
            return
        if parsed.path == "/v1/history":
            history = self.plugin.store.history(
                device_id,
                before=self._integer((query.get("before") or [0])[0], 0),
                limit=self._integer((query.get("limit") or [100])[0], 100),
            )
            self._json(
                200,
                {
                    "messages": history,
                    "last_event_sequence": self.plugin.store.latest_sequence(
                        device_id
                    ),
                },
            )
            return
        if parsed.path.startswith("/v1/media/"):
            media_id = parsed.path[len("/v1/media/"):].strip("/")
            self._media(media_id, device_id)
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        if not self._authorized():
            return
        try:
            payload = self._body()
            if parsed.path == "/v1/devices":
                device_id = _safe_device_id(payload.get("device_id"))
                if not device_id:
                    raise ValueError("invalid_device_id")
                self.plugin.store.register_device(
                    device_id,
                    self.plugin.canonical_session_id,
                    platform=payload.get("platform", ""),
                    push_token=payload.get("push_token", ""),
                )
                self._json(200, {"ok": True, "device_id": device_id})
                return
            if parsed.path == "/v1/messages":
                accepted = self.plugin.runtime.submit_text(
                    text=payload.get("text"),
                    message_id=payload.get("message_id"),
                    device_id=payload.get("device_id"),
                    client_sequence=payload.get("client_sequence"),
                    client_created_at=payload.get("created_at"),
                )
                self._json(
                    202,
                    {
                        "accepted": True,
                        "duplicate": not accepted,
                        "message_id": str(payload.get("message_id") or ""),
                    },
                )
                return
            prefix = "/v1/deliveries/"
            if parsed.path.startswith(prefix):
                action_id = parsed.path[len(prefix):].strip("/")
                receipt = self.plugin.acknowledge(
                    action_id=action_id,
                    device_id=payload.get("device_id"),
                    status=payload.get("terminal_status", "complete"),
                    event_ids=payload.get("event_ids") or [],
                )
                if receipt is None:
                    self._json(404, {"error": "delivery_not_found"})
                else:
                    self._json(200, {"ok": True, "receipt": receipt})
                return
            self._json(404, {"error": "not_found"})
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
        except Exception:
            logger.exception("[AppChannel] request failed path=%s", parsed.path)
            self._json(500, {"error": "internal_error"})

    def _authorized(self):
        expected = self.plugin.token
        supplied = str(self.headers.get("Authorization") or "")
        if supplied.startswith("Bearer "):
            supplied = supplied[7:].strip()
        if expected and hmac.compare_digest(supplied, expected):
            return True
        self._json(401, {"error": "unauthorized"})
        return False

    def _body(self):
        length = self._integer(self.headers.get("Content-Length"), 0)
        if length < 1 or length > 1024 * 1024:
            raise ValueError("invalid_body_size")
        raw = self.rfile.read(length)
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("invalid_json_object")
        return payload

    def _sse(self, device_id, query):
        after = self._integer((query.get("after") or [0])[0], 0)
        events = self.plugin.store.wait_for_events(device_id, after, timeout=25)
        body = "".join(
            "id: %s\nevent: message\ndata: %s\n\n"
            % (
                event["sequence"],
                json.dumps(event, ensure_ascii=False, separators=(",", ":")),
            )
            for event in events
        )
        if not body:
            body = "event: heartbeat\ndata: {}\n\n"
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _media(self, media_id, device_id):
        item = self.plugin.store.media(media_id, device_id)
        if not item:
            self._json(404, {"error": "media_not_found"})
            return
        path, mime_type = item
        payload = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "private, max-age=86400")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status, payload):
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    @staticmethod
    def _integer(value, default):
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default)


@plugins.register(
    name="AppChannel",
    desc="Authenticated mobile App channel sharing Xiaoyou's existing runtime",
    version="1.0-http-sse-receipts",
    author="yoyo",
    desire_priority=10001,
)
class AppChannel(Plugin):
    def __init__(self):
        super().__init__()
        self.enabled = _truthy(os.getenv("XIAOYOU_APP_ENABLED", "false"))
        self.canonical_session_id = (
            os.getenv("XIAOYOU_CANONICAL_SESSION_ID", "yoyo").strip() or "yoyo"
        )
        self.token = os.getenv("XIAOYOU_APP_TOKEN", "").strip()
        self.store = None
        self.runtime = None
        self.httpd = None
        if not self.enabled:
            logger.info("[AppChannel] disabled")
            return
        if len(self.token) < 24:
            logger.error(
                "[AppChannel] disabled: XIAOYOU_APP_TOKEN must contain at least 24 characters"
            )
            self.enabled = False
            return

        existing = get_app_service()
        if existing is not None:
            self.store = existing.store
            self.runtime = existing.runtime
            self.httpd = existing.httpd
            logger.info(
                "[AppChannel] reused existing HTTP runtime after plugin reload"
            )
            return

        self.store = AppInboxStore()
        register_app_store(self.store)
        self.runtime = AppRuntimeChannel(self.store, self.canonical_session_id)
        host = os.getenv("XIAOYOU_APP_HOST", "0.0.0.0").strip() or "0.0.0.0"
        port = int(os.getenv("XIAOYOU_APP_PORT", "8787"))
        AppRequestHandler.plugin = self
        self.httpd = ThreadingHTTPServer((host, port), AppRequestHandler)
        register_app_service(self)
        threading.Thread(
            target=self.httpd.serve_forever,
            daemon=True,
            name="XiaoyouAppHTTP",
        ).start()
        logger.info(
            "[AppChannel] inited bind=%s:%s database=%s session=%s",
            host,
            port,
            DATABASE_PATH,
            self.canonical_session_id,
        )

    def acknowledge(self, *, action_id, device_id, status, event_ids):
        receipt = self.store.acknowledge(
            action_id,
            device_id,
            status,
            event_ids,
        )
        if not receipt or receipt["memory_recorded"] or not receipt["sent_text"]:
            return receipt
        if not self.store.claim_memory_recording(receipt["action_id"]):
            receipt["memory_recorded"] = True
            return receipt

        try:
            memory_record_id = record_assistant_message(
                receipt["session_id"],
                receipt["sent_text"],
                receipt["source"] or "app_delivery",
                trace_id=receipt["trace_id"],
                input_id=receipt["input_id"],
                action_id=receipt["action_id"],
                return_record_id=True,
            )
            record_delivered_assistant_long_memory(
                receipt["session_id"],
                receipt["sent_text"],
                receipt["source"] or "app_delivery",
                user_text=receipt["user_text"],
                action_id=receipt["action_id"],
                trace_id=receipt["trace_id"],
                input_id=receipt["input_id"],
                delivery_complete=receipt["delivery_complete"],
                terminal_status=receipt["terminal_status"],
                completed_at=receipt["completed_at"],
            )
            if receipt["user_text"]:
                get_recent_state_service().schedule_update(
                    receipt["session_id"],
                    user_text=receipt["user_text"],
                    assistant_text=receipt["sent_text"],
                    last_user_ts=receipt["completed_at"],
                    trace_id=receipt["trace_id"],
                    input_id=receipt["input_id"],
                )
            if memory_record_id:
                self.store.mark_memory_recorded(receipt["action_id"])
                receipt["memory_recorded"] = True
                receipt["memory_record_id"] = str(memory_record_id)
            else:
                self.store.release_memory_recording(receipt["action_id"])
        except Exception:
            self.store.release_memory_recording(receipt["action_id"])
            raise

        trace_event(
            "outbound_completed",
            status=receipt["terminal_status"],
            trace_id=receipt["trace_id"],
            input_id=receipt["input_id"],
            session_id=receipt["session_id"],
            action_id=receipt["action_id"],
            memory_record_id=str(memory_record_id or ""),
            attrs={
                "source": receipt["source"],
                "transport": "app",
                "delivered": bool(receipt["sent_parts"]),
                "delivery_complete": receipt["delivery_complete"],
                "sent_parts": receipt["sent_parts"],
                "requested_parts": receipt["requested_parts"],
            },
        )
        return receipt
