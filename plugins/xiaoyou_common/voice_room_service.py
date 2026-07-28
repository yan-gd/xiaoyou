# -*- coding: utf-8 -*-
"""Independent O2.0 realtime voice rooms for the Xiaoyou App.

Voice-room turns deliberately do not enter AppChannel's normal message inbox.
They are persisted in their own SQLite store and projected into Xiaoyou's
existing memory providers by a background FIFO after a complete turn exists.
"""

import io
import json
import os
import queue
import sqlite3
import struct
import threading
import time
import uuid
import wave
from pathlib import Path

from common.log import logger
from plugins.xiaoyou_common.context_service import build_character_context
from plugins.xiaoyou_common.runtime_paths import runtime_path


VOICE_ROOM_DATABASE_PATH = runtime_path(
    "app_channel",
    "voice_rooms.db",
    env_var="XIAOYOU_VOICE_ROOM_DB_PATH",
)

VOLC_DIALOG_URL = "wss://openspeech.bytedance.com/api/v3/realtime/dialogue"
VOLC_RESOURCE_ID = "volc.speech.dialog"
VOLC_APP_KEY = "PlgvMymc7f3tQnJ6"
O2_MODEL_VERSION = "1.2.1.1"

START_CONNECTION = 1
FINISH_CONNECTION = 2
START_SESSION = 100
FINISH_SESSION = 102
TASK_REQUEST = 200
END_ASR = 400

CONNECTION_STARTED = 50
CONNECTION_FAILED = 51
CONNECTION_FINISHED = 52
SESSION_STARTED = 150
SESSION_FINISHED = 152
SESSION_FAILED = 153
TTS_SENTENCE_START = 350
TTS_RESPONSE = 352
TTS_ENDED = 359
ASR_RESPONSE = 451
ASR_ENDED = 459
CHAT_RESPONSE = 550
CHAT_ENDED = 559
SERVER_ERROR = 599


def _truthy(value):
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _clamp_int(value, default, minimum, maximum):
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = int(default)
    return max(int(minimum), min(int(maximum), value))


def _clean_text(value, limit=12000):
    return str(value or "").replace("\x00", "").strip()[: max(0, int(limit))]


def _json_bytes(payload):
    return json.dumps(
        payload if isinstance(payload, dict) else {},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def build_client_frame(event, payload=None, *, session_id="", audio=False):
    """Build one Volcengine V3 event frame.

    Event-bearing client frames use flag ``0b0100``.  Session events append the
    UUID before the payload size; connection events do not have a session id.
    """
    body = (
        bytes(payload or b"")
        if audio
        else _json_bytes(payload if isinstance(payload, dict) else {})
    )
    message_type = 0x2 if audio else 0x1
    serialization = 0x0 if audio else 0x1
    frame = bytearray(
        (
            0x11,
            (message_type << 4) | 0x04,
            serialization << 4,
            0x00,
        )
    )
    frame.extend(struct.pack(">I", int(event)))
    session = str(session_id or "").encode("utf-8")
    if session:
        frame.extend(struct.pack(">I", len(session)))
        frame.extend(session)
    frame.extend(struct.pack(">I", len(body)))
    frame.extend(body)
    return bytes(frame)


def parse_server_frame(data):
    """Decode a Volcengine V3 server event into a transport-neutral mapping."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    raw = bytes(data or b"")
    if len(raw) < 4:
        raise VoiceRoomProviderError("volc_frame_too_short")
    header_size = (raw[0] & 0x0F) * 4
    if header_size < 4 or len(raw) < header_size:
        raise VoiceRoomProviderError("volc_invalid_header")
    message_type = raw[1] >> 4
    flags = raw[1] & 0x0F
    serialization = raw[2] >> 4
    compression = raw[2] & 0x0F
    if compression:
        raise VoiceRoomProviderError("volc_compressed_frame_unsupported")

    offset = header_size
    event = 0
    session_id = ""
    if flags & 0x04:
        if len(raw) < offset + 4:
            raise VoiceRoomProviderError("volc_event_missing")
        event = struct.unpack(">I", raw[offset:offset + 4])[0]
        offset += 4

        # Server event frames carry a session/connect id.  Retain a guarded
        # fallback for error frames that omit it.
        if len(raw) >= offset + 8:
            candidate_size = struct.unpack(">I", raw[offset:offset + 4])[0]
            remaining_after_id = len(raw) - offset - 4 - candidate_size
            if 0 <= candidate_size <= 4096 and remaining_after_id >= 4:
                offset += 4
                session_id = raw[offset:offset + candidate_size].decode(
                    "utf-8",
                    errors="replace",
                )
                offset += candidate_size

    if len(raw) < offset + 4:
        payload_bytes = raw[offset:]
    else:
        payload_size = struct.unpack(">I", raw[offset:offset + 4])[0]
        offset += 4
        if payload_size > len(raw) - offset:
            raise VoiceRoomProviderError("volc_invalid_payload_size")
        payload_bytes = raw[offset:offset + payload_size]

    payload = payload_bytes
    if serialization == 0x1 and payload_bytes:
        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VoiceRoomProviderError("volc_invalid_json") from exc
    elif serialization == 0x1:
        payload = {}
    return {
        "message_type": message_type,
        "event": event,
        "session_id": session_id,
        "payload": payload,
        "payload_bytes": payload_bytes,
    }


def normalize_dialog_context(records, *, max_items=20, max_chars=7000):
    """Create complete alternating QA pairs without text/keyword parsing."""
    messages = []
    for record in records or []:
        if not isinstance(record, dict):
            continue
        role = str(record.get("role") or "").strip().lower()
        text = _clean_text(record.get("content", record.get("text", "")), 2400)
        if role not in ("user", "assistant") or not text:
            continue
        timestamp = _clamp_int(record.get("ts", record.get("timestamp", 0)), 0, 0, 2**31 - 1)
        if messages and messages[-1]["role"] == role:
            messages[-1]["text"] = _clean_text(
                messages[-1]["text"] + "\n" + text,
                3000,
            )
            messages[-1]["timestamp"] = max(
                messages[-1]["timestamp"],
                timestamp,
            )
        else:
            messages.append(
                {
                    "role": role,
                    "text": text,
                    "timestamp": timestamp,
                }
            )

    # The provider requires user/assistant pairs.  Drop incomplete edges rather
    # than inventing model or user speech.
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    while messages and messages[-1]["role"] != "assistant":
        messages.pop()
    paired = []
    index = 0
    while index + 1 < len(messages):
        if (
            messages[index]["role"] == "user"
            and messages[index + 1]["role"] == "assistant"
        ):
            paired.extend((messages[index], messages[index + 1]))
            index += 2
        else:
            index += 1

    limit = max(2, int(max_items))
    if limit % 2:
        limit -= 1
    paired = paired[-limit:]
    budget = max(0, int(max_chars))
    while paired and sum(len(item["text"]) for item in paired) > budget:
        paired = paired[2:]
    return paired


def wav_to_pcm16(audio_bytes):
    """Validate and unpack the App's 16 kHz mono PCM WAV recording."""
    try:
        with wave.open(io.BytesIO(bytes(audio_bytes or b"")), "rb") as source:
            if (
                source.getnchannels() != 1
                or source.getsampwidth() != 2
                or source.getframerate() != 16000
                or source.getcomptype() != "NONE"
            ):
                raise VoiceRoomError("voice_room_audio_must_be_pcm16_16k_mono")
            return source.readframes(source.getnframes())
    except (wave.Error, EOFError) as exc:
        raise VoiceRoomError("invalid_voice_room_wav") from exc


def pcm16_to_wav(pcm_bytes, *, sample_rate=24000):
    target = io.BytesIO()
    with wave.open(target, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(int(sample_rate))
        output.writeframes(bytes(pcm_bytes or b""))
    return target.getvalue()


def wav_duration_ms(wav_bytes):
    try:
        with wave.open(io.BytesIO(bytes(wav_bytes or b"")), "rb") as source:
            rate = source.getframerate()
            return int(source.getnframes() * 1000 / rate) if rate else 0
    except (wave.Error, EOFError):
        return 0


class VoiceRoomError(RuntimeError):
    pass


class VoiceRoomProviderError(VoiceRoomError):
    pass


class VoiceRoomStore:
    def __init__(self, path=VOICE_ROOM_DATABASE_PATH):
        self.path = str(path)
        self.lock = threading.RLock()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=8)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=8000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self):
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS voice_rooms (
                    room_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    provider_dialog_id TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at INTEGER NOT NULL,
                    ended_at INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_voice_rooms_device
                    ON voice_rooms(device_id, started_at DESC);

                CREATE TABLE IF NOT EXISTS voice_room_turns (
                    turn_id TEXT PRIMARY KEY,
                    room_id TEXT NOT NULL,
                    turn_index INTEGER NOT NULL,
                    user_text TEXT NOT NULL,
                    assistant_text TEXT NOT NULL,
                    user_duration_ms INTEGER NOT NULL DEFAULT 0,
                    assistant_duration_ms INTEGER NOT NULL DEFAULT 0,
                    audio_media_id TEXT NOT NULL DEFAULT '',
                    audio_mime_type TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    memory_status TEXT NOT NULL DEFAULT 'pending',
                    FOREIGN KEY(room_id) REFERENCES voice_rooms(room_id)
                        ON DELETE CASCADE,
                    UNIQUE(room_id, turn_index)
                );
                CREATE INDEX IF NOT EXISTS idx_voice_room_turns_room
                    ON voice_room_turns(room_id, turn_index);

                CREATE TABLE IF NOT EXISTS voice_room_continuity (
                    session_id TEXT PRIMARY KEY,
                    provider_dialog_id TEXT NOT NULL DEFAULT '',
                    updated_at INTEGER NOT NULL
                );
                """
            )

    @staticmethod
    def _room(row):
        if row is None:
            return None
        value = dict(row)
        for key in ("started_at", "ended_at", "created_at", "updated_at"):
            value[key] = int(value.get(key) or 0)
        value["turn_count"] = int(value.get("turn_count") or 0)
        return value

    @staticmethod
    def _turn(row):
        if row is None:
            return None
        value = dict(row)
        for key in (
            "turn_index",
            "user_duration_ms",
            "assistant_duration_ms",
            "created_at",
        ):
            value[key] = int(value.get(key) or 0)
        return value

    def create_room(self, session_id, device_id, *, title="耳边的一会儿"):
        now = int(time.time())
        room_id = uuid.uuid4().hex
        with self.lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO voice_rooms(
                    room_id, session_id, device_id, title, status,
                    started_at, created_at, updated_at
                ) VALUES(?, ?, ?, ?, 'active', ?, ?, ?)
                """,
                (
                    room_id,
                    str(session_id),
                    str(device_id),
                    _clean_text(title, 80) or "耳边的一会儿",
                    now,
                    now,
                    now,
                ),
            )
        return self.get_room(room_id, device_id)

    def get_room(self, room_id, device_id="", *, include_turns=False):
        with self.lock, self._connect() as connection:
            params = [str(room_id or "")]
            device_clause = ""
            if device_id:
                device_clause = " AND r.device_id = ?"
                params.append(str(device_id))
            row = connection.execute(
                """
                SELECT r.*,
                    (SELECT COUNT(*) FROM voice_room_turns t
                     WHERE t.room_id = r.room_id) AS turn_count
                FROM voice_rooms r
                WHERE r.room_id = ?
                """ + device_clause,
                params,
            ).fetchone()
            room = self._room(row)
            if room is not None and include_turns:
                rows = connection.execute(
                    """
                    SELECT * FROM voice_room_turns
                    WHERE room_id = ? ORDER BY turn_index ASC
                    """,
                    (str(room_id),),
                ).fetchall()
                room["turns"] = [self._turn(value) for value in rows]
            return room

    def list_rooms(self, device_id, *, limit=30):
        limit = _clamp_int(limit, 30, 1, 100)
        with self.lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT r.*,
                    (SELECT COUNT(*) FROM voice_room_turns t
                     WHERE t.room_id = r.room_id) AS turn_count
                FROM voice_rooms r
                WHERE r.device_id = ?
                ORDER BY r.started_at DESC, r.created_at DESC
                LIMIT ?
                """,
                (str(device_id or ""), limit),
            ).fetchall()
        return [self._room(row) for row in rows]

    def add_turn(
        self,
        *,
        turn_id,
        room_id,
        user_text,
        assistant_text,
        user_duration_ms=0,
        assistant_duration_ms=0,
        audio_media_id="",
        audio_mime_type="",
    ):
        now = int(time.time())
        with self.lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM voice_room_turns WHERE turn_id = ?",
                (str(turn_id),),
            ).fetchone()
            if existing:
                return self._turn(existing), False
            row = connection.execute(
                "SELECT COALESCE(MAX(turn_index), 0) + 1 AS next_index "
                "FROM voice_room_turns WHERE room_id = ?",
                (str(room_id),),
            ).fetchone()
            turn_index = int(row["next_index"] or 1)
            connection.execute(
                """
                INSERT INTO voice_room_turns(
                    turn_id, room_id, turn_index, user_text, assistant_text,
                    user_duration_ms, assistant_duration_ms, audio_media_id,
                    audio_mime_type, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(turn_id),
                    str(room_id),
                    turn_index,
                    _clean_text(user_text),
                    _clean_text(assistant_text),
                    max(0, int(user_duration_ms or 0)),
                    max(0, int(assistant_duration_ms or 0)),
                    str(audio_media_id or ""),
                    str(audio_mime_type or ""),
                    now,
                ),
            )
            connection.execute(
                "UPDATE voice_rooms SET updated_at = ? WHERE room_id = ?",
                (now, str(room_id)),
            )
            saved = connection.execute(
                "SELECT * FROM voice_room_turns WHERE turn_id = ?",
                (str(turn_id),),
            ).fetchone()
        return self._turn(saved), True

    def finish_room(self, room_id, device_id):
        now = int(time.time())
        with self.lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE voice_rooms
                SET status = 'complete', ended_at = ?, updated_at = ?
                WHERE room_id = ? AND device_id = ?
                """,
                (now, now, str(room_id), str(device_id)),
            )
        return self.get_room(room_id, device_id, include_turns=True)

    def interrupt_active_rooms(self):
        """Close rooms whose provider socket disappeared with the process."""
        now = int(time.time())
        with self.lock, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE voice_rooms
                SET status = 'interrupted', ended_at = ?, updated_at = ?
                WHERE status = 'active'
                """,
                (now, now),
            )
            return max(0, int(cursor.rowcount or 0))

    def set_provider_dialog_id(self, room_id, session_id, dialog_id):
        dialog_id = str(dialog_id or "").strip()
        if not dialog_id:
            return
        now = int(time.time())
        with self.lock, self._connect() as connection:
            connection.execute(
                "UPDATE voice_rooms SET provider_dialog_id = ?, updated_at = ? "
                "WHERE room_id = ?",
                (dialog_id, now, str(room_id)),
            )
            connection.execute(
                """
                INSERT INTO voice_room_continuity(
                    session_id, provider_dialog_id, updated_at
                ) VALUES(?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    provider_dialog_id = excluded.provider_dialog_id,
                    updated_at = excluded.updated_at
                """,
                (str(session_id), dialog_id, now),
            )

    def continuity_dialog_id(self, session_id):
        with self.lock, self._connect() as connection:
            row = connection.execute(
                "SELECT provider_dialog_id FROM voice_room_continuity "
                "WHERE session_id = ?",
                (str(session_id),),
            ).fetchone()
        return str(row["provider_dialog_id"] or "") if row else ""

    def mark_memory(self, turn_id, status):
        with self.lock, self._connect() as connection:
            connection.execute(
                "UPDATE voice_room_turns SET memory_status = ? WHERE turn_id = ?",
                (str(status or "failed"), str(turn_id)),
            )

    def pending_memory_turns(self, limit=200):
        with self.lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT t.*, r.session_id
                FROM voice_room_turns t
                JOIN voice_rooms r ON r.room_id = t.room_id
                WHERE t.memory_status IN ('pending', 'failed')
                ORDER BY t.created_at ASC, t.turn_index ASC LIMIT ?
                """,
                (_clamp_int(limit, 200, 1, 1000),),
            ).fetchall()
        return [self._turn(row) for row in rows]


class VoiceRoomMemoryProjector:
    """Per-process FIFO that projects complete voice turns asynchronously."""

    def __init__(self, store, instances_provider=None):
        self.store = store
        self.instances_provider = instances_provider
        self.jobs = queue.Queue(maxsize=512)
        self.worker = threading.Thread(
            target=self._loop,
            daemon=True,
            name="XiaoyouVoiceRoomMemory",
        )
        self.worker.start()
        for turn in self.store.pending_memory_turns():
            self.submit(turn)

    def submit(self, turn):
        if not isinstance(turn, dict):
            return False
        job = dict(turn)
        job.setdefault("_projection_attempt", 0)
        try:
            self.jobs.put_nowait(job)
            return True
        except queue.Full:
            logger.error(
                "[VoiceRoomMemory] queue full turn_id=%s",
                str(turn.get("turn_id") or "-")[:48],
            )
            return False

    def _instances(self):
        if self.instances_provider is not None:
            value = self.instances_provider()
            return value if isinstance(value, dict) else {}
        import plugins

        manager = getattr(plugins, "instance", None)
        value = getattr(manager, "instances", {}) if manager else {}
        return value if isinstance(value, dict) else {}

    def _loop(self):
        while True:
            turn = self.jobs.get()
            try:
                self.project(turn)
            except Exception as error:
                attempt = int(turn.get("_projection_attempt") or 0) + 1
                if (
                    str(error) in (
                        "short_memory_unavailable",
                        "long_memory_unavailable",
                    )
                    and attempt <= 12
                ):
                    turn["_projection_attempt"] = attempt
                    logger.info(
                        "[VoiceRoomMemory] waiting for memory plugins "
                        "turn_id=%s attempt=%s",
                        str(turn.get("turn_id") or "-")[:48],
                        attempt,
                    )
                    time.sleep(min(5.0, 0.5 * attempt))
                    self.submit(turn)
                else:
                    self.store.mark_memory(turn.get("turn_id"), "failed")
                    logger.exception(
                        "[VoiceRoomMemory] projection failed turn_id=%s",
                        str(turn.get("turn_id") or "-")[:48],
                    )
            finally:
                self.jobs.task_done()

    def project(self, turn):
        session_id = _clean_text(turn.get("session_id"), 128)
        user_text = _clean_text(turn.get("user_text"))
        assistant_text = _clean_text(turn.get("assistant_text"))
        turn_id = _clean_text(turn.get("turn_id"), 128)
        if not session_id or not user_text or not assistant_text or not turn_id:
            raise VoiceRoomError("voice_room_memory_payload_invalid")
        instances = self._instances()
        short_memory = instances.get("SHORTMEMORY")
        append_user = getattr(short_memory, "append_external_user_message", None)
        append_assistant = getattr(
            short_memory,
            "append_external_assistant_message",
            None,
        )
        if not callable(append_user) or not callable(append_assistant):
            raise VoiceRoomError("short_memory_unavailable")

        input_id = "voice-room:" + turn_id
        append_user(
            session_id,
            user_text,
            source="voice_room",
            input_id=input_id,
            action_id=turn_id,
        )
        append_assistant(
            session_id,
            assistant_text,
            source="voice_room",
            input_id=input_id,
            action_id=turn_id,
        )

        long_memory = instances.get("LONGTERMMEMORY")
        append_long = getattr(
            long_memory,
            "append_delivered_assistant_message",
            None,
        )
        if not callable(append_long):
            raise VoiceRoomError("long_memory_unavailable")
        append_long(
            session_id,
            assistant_text,
            user_text=user_text,
            source="voice_room",
            action_id=turn_id,
            input_id=input_id,
            delivery_complete=True,
            terminal_status="complete",
            completed_at=int(turn.get("created_at") or time.time()),
        )
        try:
            from plugins.xiaoyou_common.recent_state_service import (
                get_recent_state_service,
            )

            get_recent_state_service().schedule_update(
                session_id,
                user_text=user_text,
                assistant_text=assistant_text,
                last_user_ts=int(turn.get("created_at") or time.time()),
                input_id=input_id,
            )
        except Exception:
            logger.exception(
                "[VoiceRoomMemory] recent state scheduling failed "
                "turn_id=%s",
                turn_id[:48],
            )
        self.store.mark_memory(turn_id, "complete")
        logger.info(
            "[VoiceRoomMemory] projected turn_id=%s session=%s",
            turn_id[:48],
            session_id[:48],
        )
        return True


class VolcO2RealtimeSession:
    """One persistent server-side session with the Volcengine O2.0 model."""

    def __init__(
        self,
        *,
        app_id,
        access_key,
        session_id,
        start_payload,
        timeout=45,
        websocket_factory=None,
    ):
        self.app_id = str(app_id or "").strip()
        self.access_key = str(access_key or "").strip()
        self.session_id = str(session_id or "").strip() or uuid.uuid4().hex
        self.start_payload = start_payload
        self.timeout = max(10, int(timeout or 45))
        self.websocket_factory = websocket_factory
        self.socket = None
        self.dialog_id = ""
        self.lock = threading.RLock()

    def _connect_socket(self):
        headers = [
            "X-Api-App-ID: " + self.app_id,
            "X-Api-Access-Key: " + self.access_key,
            "X-Api-Resource-Id: " + VOLC_RESOURCE_ID,
            "X-Api-App-Key: " + VOLC_APP_KEY,
            "X-Api-Connect-Id: " + uuid.uuid4().hex,
        ]
        if self.websocket_factory is not None:
            return self.websocket_factory(
                VOLC_DIALOG_URL,
                headers=headers,
                timeout=self.timeout,
            )
        try:
            import websocket
        except ImportError as exc:
            raise VoiceRoomProviderError(
                "websocket_client_dependency_missing"
            ) from exc
        return websocket.create_connection(
            VOLC_DIALOG_URL,
            header=headers,
            timeout=self.timeout,
            enable_multithread=True,
        )

    def _send(self, event, payload=None, *, audio=False, session=False):
        frame = build_client_frame(
            event,
            payload,
            session_id=self.session_id if session else "",
            audio=audio,
        )
        self.socket.send_binary(frame)

    def _receive(self):
        value = self.socket.recv()
        if value is None:
            raise VoiceRoomProviderError("volc_connection_closed")
        frame = parse_server_frame(value)
        if frame["message_type"] == 0xF or frame["event"] in (
            CONNECTION_FAILED,
            SESSION_FAILED,
            SERVER_ERROR,
        ):
            payload = frame.get("payload")
            detail = (
                json.dumps(payload, ensure_ascii=False)
                if isinstance(payload, dict)
                else str(payload or "")
            )
            raise VoiceRoomProviderError(
                "volc_realtime_error:" + detail[:500]
            )
        return frame

    def _wait_for(self, expected):
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            frame = self._receive()
            if frame["event"] in expected:
                return frame
        raise VoiceRoomProviderError("volc_event_timeout")

    def start(self):
        if not self.app_id or not self.access_key:
            raise VoiceRoomProviderError("volc_credentials_missing")
        with self.lock:
            self.socket = self._connect_socket()
            self._send(START_CONNECTION, {})
            self._wait_for({CONNECTION_STARTED})
            self._send(
                START_SESSION,
                self.start_payload,
                session=True,
            )
            frame = self._wait_for({SESSION_STARTED})
            payload = frame.get("payload") or {}
            if isinstance(payload, dict):
                self.dialog_id = str(payload.get("dialog_id") or "").strip()
            return self.dialog_id

    @staticmethod
    def _merge_stream_text(current, incoming):
        incoming = _clean_text(incoming)
        if not incoming:
            return current
        if incoming.startswith(current):
            return incoming
        if current.endswith(incoming):
            return current
        return current + incoming

    def process_turn(self, pcm_bytes):
        if not self.socket:
            raise VoiceRoomProviderError("volc_session_not_started")
        with self.lock:
            pcm = bytes(pcm_bytes or b"")
            if not pcm:
                raise VoiceRoomError("empty_voice_room_audio")
            for offset in range(0, len(pcm), 640):
                self._send(
                    TASK_REQUEST,
                    pcm[offset:offset + 640],
                    audio=True,
                    session=True,
                )
                time.sleep(0.02)
            self._send(END_ASR, {}, session=True)

            user_text = ""
            assistant_text = ""
            audio = bytearray()
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                frame = self._receive()
                event = frame["event"]
                payload = frame.get("payload")
                if event == ASR_RESPONSE and isinstance(payload, dict):
                    for result in payload.get("results") or []:
                        if not isinstance(result, dict):
                            continue
                        text = _clean_text(result.get("text"))
                        if text and (
                            not result.get("is_interim") or not user_text
                        ):
                            user_text = text
                elif event == CHAT_RESPONSE and isinstance(payload, dict):
                    assistant_text = self._merge_stream_text(
                        assistant_text,
                        payload.get("content"),
                    )
                elif event == TTS_SENTENCE_START and isinstance(payload, dict):
                    assistant_text = self._merge_stream_text(
                        assistant_text,
                        payload.get("text"),
                    )
                elif event == TTS_RESPONSE:
                    audio.extend(frame.get("payload_bytes") or b"")
                elif event == TTS_ENDED:
                    break
            else:
                raise VoiceRoomProviderError("volc_turn_timeout")

            if not user_text:
                raise VoiceRoomProviderError("volc_asr_text_missing")
            if not assistant_text:
                raise VoiceRoomProviderError("volc_reply_text_missing")
            if not audio:
                raise VoiceRoomProviderError("volc_reply_audio_missing")
            return {
                "user_text": user_text,
                "assistant_text": assistant_text,
                "audio_pcm": bytes(audio),
            }

    def close(self):
        with self.lock:
            socket = self.socket
            self.socket = None
            if socket is None:
                return
            try:
                frame = build_client_frame(
                    FINISH_SESSION,
                    {},
                    session_id=self.session_id,
                )
                socket.send_binary(frame)
            except Exception:
                pass
            try:
                socket.send_binary(build_client_frame(FINISH_CONNECTION, {}))
            except Exception:
                pass
            try:
                socket.close()
            except Exception:
                pass


class VoiceRoomService:
    def __init__(
        self,
        *,
        store=None,
        media_store=None,
        relationship_service=None,
        instances_provider=None,
        session_factory=None,
    ):
        self.enabled = _truthy(
            os.getenv("XIAOYOU_VOICE_ROOM_ENABLED", "true")
        )
        self.app_id = (
            os.getenv("XIAOYOU_VOICE_ROOM_APP_ID", "").strip()
            or os.getenv("XIAOYOU_APP_TTS_APP_ID", "").strip()
        )
        self.access_key = (
            os.getenv("XIAOYOU_VOICE_ROOM_ACCESS_KEY", "").strip()
            or os.getenv("XIAOYOU_APP_TTS_ACCESS_KEY", "").strip()
        )
        self.model = os.getenv(
            "XIAOYOU_VOICE_ROOM_MODEL",
            O2_MODEL_VERSION,
        ).strip()
        if self.model != O2_MODEL_VERSION:
            logger.error(
                "[VoiceRoom] refusing non-O2.0 model=%s; expected=%s",
                self.model,
                O2_MODEL_VERSION,
            )
            self.enabled = False
        self.speaker = os.getenv(
            "XIAOYOU_VOICE_ROOM_SPEAKER",
            "zh_female_xiaohe_jupiter_bigtts",
        ).strip()
        self.timeout = _clamp_int(
            os.getenv("XIAOYOU_VOICE_ROOM_TIMEOUT", "45"),
            45,
            15,
            120,
        )
        self.idle_timeout = _clamp_int(
            os.getenv("XIAOYOU_VOICE_ROOM_IDLE_TIMEOUT", "300"),
            300,
            60,
            3600,
        )
        self.loudness_rate = _clamp_int(
            os.getenv("XIAOYOU_VOICE_ROOM_LOUDNESS_RATE", "100"),
            100,
            -50,
            100,
        )
        self.store = store or VoiceRoomStore()
        interrupted = self.store.interrupt_active_rooms()
        if interrupted:
            logger.info(
                "[VoiceRoom] recovered interrupted rooms count=%s",
                interrupted,
            )
        self.media_store = media_store
        self.relationship_service = relationship_service
        self.instances_provider = instances_provider
        self.session_factory = session_factory or VolcO2RealtimeSession
        self.memory = VoiceRoomMemoryProjector(
            self.store,
            instances_provider=instances_provider,
        )
        self.sessions = {}
        self.session_last_used = {}
        self.lock = threading.RLock()
        threading.Thread(
            target=self._reap_idle_sessions,
            daemon=True,
            name="XiaoyouVoiceRoomReaper",
        ).start()

    @property
    def available(self):
        return bool(self.enabled and self.app_id and self.access_key)

    def _instances(self):
        if self.instances_provider is not None:
            value = self.instances_provider()
            return value if isinstance(value, dict) else {}
        import plugins

        manager = getattr(plugins, "instance", None)
        value = getattr(manager, "instances", {}) if manager else {}
        return value if isinstance(value, dict) else {}

    def _dialog_context(self, session_id):
        memory = self._instances().get("SHORTMEMORY")
        builder = getattr(
            memory,
            "build_dialog_context_for_external_consumer",
            None,
        )
        records = builder(session_id) if callable(builder) else []
        return normalize_dialog_context(
            records,
            max_items=_clamp_int(
                os.getenv("XIAOYOU_VOICE_ROOM_CONTEXT_MESSAGES", "20"),
                20,
                2,
                40,
            ),
            max_chars=_clamp_int(
                os.getenv("XIAOYOU_VOICE_ROOM_CONTEXT_MAX_CHARS", "7000"),
                7000,
                500,
                10000,
            ),
        )

    def _start_payload(self, session_id):
        character = _clean_text(
            build_character_context(include_time=True),
            3400,
        )
        style = _clean_text(
            os.getenv(
                "XIAOYOU_VOICE_ROOM_SPEAKING_STYLE",
                "像真实亲密通话一样自然交流，语气细腻、有停顿和情绪，不念说明文字。",
            ),
            500,
        )
        dialog = {
            "bot_name": _clean_text(
                os.getenv("XIAOYOU_VOICE_ROOM_BOT_NAME", "小悠"),
                20,
            )
            or "小悠",
            "system_role": character,
            "speaking_style": style,
            "dialog_context": self._dialog_context(session_id),
            "extra": {
                "input_mod": "push_to_talk",
                "enable_loudness_norm": True,
                "enable_conversation_truncate": True,
                "model": O2_MODEL_VERSION,
            },
        }
        continuity = self.store.continuity_dialog_id(session_id)
        if continuity:
            dialog["dialog_id"] = continuity
        return {
            "asr": {
                "audio_info": {
                    "format": "pcm",
                    "sample_rate": 16000,
                    "channel": 1,
                }
            },
            "tts": {
                "speaker": self.speaker,
                "audio_config": {
                    "channel": 1,
                    "format": "pcm_s16le",
                    "sample_rate": 24000,
                    "loudness_rate": self.loudness_rate,
                },
            },
            "dialog": dialog,
        }

    def create_room(self, *, session_id, device_id, title="耳边的一会儿"):
        if not self.available:
            raise VoiceRoomError("voice_room_o2_not_configured")
        room = self.store.create_room(session_id, device_id, title=title)
        provider = self.session_factory(
            app_id=self.app_id,
            access_key=self.access_key,
            session_id=room["room_id"],
            start_payload=self._start_payload(session_id),
            timeout=self.timeout,
        )
        try:
            dialog_id = provider.start()
        except Exception:
            self.store.finish_room(room["room_id"], device_id)
            raise
        self.store.set_provider_dialog_id(
            room["room_id"],
            session_id,
            dialog_id,
        )
        with self.lock:
            self.sessions[room["room_id"]] = provider
            self.session_last_used[room["room_id"]] = time.monotonic()
        return self.store.get_room(
            room["room_id"],
            device_id,
            include_turns=True,
        )

    def response_payload(self, room):
        if not isinstance(room, dict):
            return None
        value = dict(room)
        turns = []
        for item in value.get("turns") or []:
            if not isinstance(item, dict):
                continue
            turn = dict(item)
            media_id = str(turn.get("audio_media_id") or "")
            turn["audio_url"] = (
                "/v1/media/" + media_id if media_id else ""
            )
            turns.append(turn)
        if "turns" in value:
            value["turns"] = turns
        return value

    def list_rooms(self, device_id, *, limit=30):
        return self.store.list_rooms(device_id, limit=limit)

    def get_room(self, room_id, device_id):
        return self.store.get_room(room_id, device_id, include_turns=True)

    def process_turn(
        self,
        *,
        room_id,
        device_id,
        turn_id,
        audio_bytes,
        mime_type,
        duration_ms=0,
    ):
        turn_id = _clean_text(turn_id, 128)
        if not turn_id:
            raise VoiceRoomError("invalid_voice_room_turn_id")
        room = self.store.get_room(room_id, device_id)
        if room is None:
            raise VoiceRoomError("voice_room_not_found")
        if room["status"] != "active":
            raise VoiceRoomError("voice_room_closed")
        if str(mime_type or "").split(";", 1)[0].strip().lower() not in (
            "audio/wav",
            "audio/x-wav",
        ):
            raise VoiceRoomError("voice_room_requires_wav")
        with self.lock:
            provider = self.sessions.get(str(room_id))
            if provider is not None:
                self.session_last_used[str(room_id)] = time.monotonic()
        if provider is None:
            raise VoiceRoomError("voice_room_session_expired")

        pcm = wav_to_pcm16(audio_bytes)
        result = provider.process_turn(pcm)
        reply_wav = pcm16_to_wav(result["audio_pcm"], sample_rate=24000)
        if self.media_store is None:
            raise VoiceRoomError("voice_room_media_store_unavailable")
        media = self.media_store.save_media_bytes(
            reply_wav,
            device_id,
            "audio/wav",
        )
        if not media:
            raise VoiceRoomError("voice_room_audio_save_failed")
        turn, inserted = self.store.add_turn(
            turn_id=turn_id,
            room_id=room_id,
            user_text=result["user_text"],
            assistant_text=result["assistant_text"],
            user_duration_ms=duration_ms,
            assistant_duration_ms=wav_duration_ms(reply_wav),
            audio_media_id=media["media_id"],
            audio_mime_type=media["mime_type"],
        )
        if inserted:
            turn["session_id"] = room["session_id"]
            self.memory.submit(turn)
        with self.lock:
            if str(room_id) in self.sessions:
                self.session_last_used[str(room_id)] = time.monotonic()
        return {
            "accepted": inserted,
            "duplicate": not inserted,
            "turn": self.response_payload({"turns": [turn]})["turns"][0],
        }

    def finish_room(self, room_id, device_id):
        room = self.store.get_room(room_id, device_id, include_turns=True)
        if room is None:
            raise VoiceRoomError("voice_room_not_found")
        with self.lock:
            provider = self.sessions.pop(str(room_id), None)
            self.session_last_used.pop(str(room_id), None)
        if provider is not None:
            provider.close()
        finished = self.store.finish_room(room_id, device_id)
        if (
            self.relationship_service is not None
            and finished
            and finished.get("turn_count")
        ):
            try:
                self.relationship_service.record_voice_memory(
                    session_id=finished["session_id"],
                    started_at=finished["started_at"],
                    ended_at=finished["ended_at"],
                    turn_count=finished["turn_count"],
                    duration_ms=max(
                        0,
                        (finished["ended_at"] - finished["started_at"]) * 1000,
                    ),
                    title=finished["title"],
                )
            except Exception:
                logger.exception(
                    "[VoiceRoom] keepsake save failed room_id=%s",
                    str(room_id)[:48],
                )
        return finished

    def close_all(self):
        with self.lock:
            sessions = list(self.sessions.values())
            self.sessions.clear()
            self.session_last_used.clear()
        for session in sessions:
            session.close()

    def _reap_idle_sessions(self):
        while True:
            time.sleep(30)
            now = time.monotonic()
            with self.lock:
                expired = [
                    room_id
                    for room_id, touched_at in self.session_last_used.items()
                    if now - touched_at >= self.idle_timeout
                ]
            for room_id in expired:
                room = self.store.get_room(room_id)
                if room is None:
                    continue
                try:
                    self.finish_room(room_id, room["device_id"])
                    logger.info(
                        "[VoiceRoom] idle room finalized room_id=%s",
                        room_id[:48],
                    )
                except Exception:
                    logger.exception(
                        "[VoiceRoom] idle room cleanup failed room_id=%s",
                        room_id[:48],
                    )
