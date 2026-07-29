# -*- coding: utf-8 -*-
"""Independent O2.0 realtime voice rooms for the Xiaoyou App.

Voice-room turns deliberately do not enter AppChannel's normal message inbox.
They are persisted in their own SQLite store and projected into Xiaoyou's
existing memory providers by a background FIFO after a complete turn exists.
"""

import base64
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
from collections import deque
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
TTS_SENTENCE_END = 351
TTS_RESPONSE = 352
TTS_ENDED = 359
ASR_INFO = 450
ASR_RESPONSE = 451
ASR_ENDED = 459
CONVERSATION_TRUNCATE = 513
CLIENT_INTERRUPT = 515
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


def merge_dialog_context_sources(
    *record_sets,
    max_items=20,
    max_chars=7000,
):
    """Merge independently valid dialog histories without duplicating pairs.

    Voice turns are projected into ShortMemory asynchronously.  During that
    small window the voice continuity ledger is the authoritative source; once
    projection completes both sources contain the same pair.  Pair-level
    de-duplication keeps either phase safe without parsing the conversation.
    """
    pairs = {}
    sequence = 0
    for records in record_sets:
        normalized = normalize_dialog_context(
            records,
            max_items=200,
            max_chars=100000,
        )
        for index in range(0, len(normalized) - 1, 2):
            user = normalized[index]
            assistant = normalized[index + 1]
            key = (user["text"], assistant["text"])
            timestamp = max(
                int(user.get("timestamp") or 0),
                int(assistant.get("timestamp") or 0),
            )
            sequence += 1
            previous = pairs.get(key)
            if previous is None or timestamp >= previous["timestamp"]:
                pairs[key] = {
                    "timestamp": timestamp,
                    "sequence": sequence,
                    "messages": (user, assistant),
                }

    ordered = sorted(
        pairs.values(),
        key=lambda item: (item["timestamp"], item["sequence"]),
    )
    limit = max(2, int(max_items))
    if limit % 2:
        limit -= 1
    ordered = ordered[-(limit // 2):]
    budget = max(0, int(max_chars))
    while (
        ordered
        and sum(
            len(message["text"])
            for pair in ordered
            for message in pair["messages"]
        )
        > budget
    ):
        ordered.pop(0)
    return [
        message
        for pair in ordered
        for message in pair["messages"]
    ]


def wav_to_pcm16(audio_bytes):
    """Unpack Android PCM WAV and normalize it to 16 kHz mono PCM16.

    Android audio devices are allowed to replace the requested sample rate or
    channel count with a supported value.  The record plugin writes that
    negotiated format into the WAV header, so rejecting anything other than
    exact 16 kHz mono makes otherwise valid recordings device-dependent.
    """
    try:
        with wave.open(io.BytesIO(bytes(audio_bytes or b"")), "rb") as source:
            channels = source.getnchannels()
            sample_width = source.getsampwidth()
            sample_rate = source.getframerate()
            if (
                channels not in (1, 2)
                or sample_width != 2
                or sample_rate < 8000
                or sample_rate > 96000
                or source.getcomptype() != "NONE"
            ):
                raise VoiceRoomError("voice_room_audio_must_be_pcm16_16k_mono")
            frames = source.readframes(source.getnframes())
    except (wave.Error, EOFError) as exc:
        raise VoiceRoomError("invalid_voice_room_wav") from exc

    if len(frames) < sample_width * channels:
        raise VoiceRoomError("empty_voice_room_audio")
    sample_count = len(frames) // 2
    samples = list(struct.unpack("<%dh" % sample_count, frames))
    if channels == 2:
        samples = [
            (samples[index] + samples[index + 1]) // 2
            for index in range(0, len(samples) - 1, 2)
        ]
    if sample_rate != 16000 and samples:
        target_count = max(1, round(len(samples) * 16000 / sample_rate))
        if target_count == 1 or len(samples) == 1:
            samples = [samples[0]]
        else:
            source_span = len(samples) - 1
            target_span = target_count - 1
            normalized = []
            for index in range(target_count):
                position = index * source_span / target_span
                left = int(position)
                right = min(left + 1, source_span)
                fraction = position - left
                value = round(
                    samples[left] * (1.0 - fraction)
                    + samples[right] * fraction
                )
                normalized.append(max(-32768, min(32767, value)))
            samples = normalized
    return struct.pack("<%dh" % len(samples), *samples)


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
                    delivery_complete INTEGER NOT NULL DEFAULT 1,
                    terminal_status TEXT NOT NULL DEFAULT 'complete',
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

                CREATE TABLE IF NOT EXISTS voice_room_context_turns (
                    context_id TEXT PRIMARY KEY,
                    room_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    user_text TEXT NOT NULL,
                    assistant_text TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    terminal_status TEXT NOT NULL DEFAULT 'complete'
                );
                CREATE INDEX IF NOT EXISTS idx_voice_room_context_session
                    ON voice_room_context_turns(
                        session_id, created_at DESC, context_id DESC
                    );
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(voice_room_turns)"
                ).fetchall()
            }
            if "delivery_complete" not in columns:
                connection.execute(
                    "ALTER TABLE voice_room_turns ADD COLUMN "
                    "delivery_complete INTEGER NOT NULL DEFAULT 1"
                )
            if "terminal_status" not in columns:
                connection.execute(
                    "ALTER TABLE voice_room_turns ADD COLUMN "
                    "terminal_status TEXT NOT NULL DEFAULT 'complete'"
                )
            connection.execute(
                """
                INSERT OR IGNORE INTO voice_room_context_turns(
                    context_id, room_id, session_id, user_text,
                    assistant_text, created_at, terminal_status
                )
                SELECT
                    t.turn_id, t.room_id, r.session_id, t.user_text,
                    t.assistant_text, t.created_at, t.terminal_status
                FROM voice_room_turns t
                JOIN voice_rooms r ON r.room_id = t.room_id
                WHERE t.user_text != '' AND t.assistant_text != ''
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
        value["delivery_complete"] = bool(
            value.get("delivery_complete", 1)
        )
        value["terminal_status"] = str(
            value.get("terminal_status") or "complete"
        )
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
        delivery_complete=True,
        terminal_status="complete",
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
                    audio_mime_type, created_at, delivery_complete,
                    terminal_status
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    1 if delivery_complete else 0,
                    str(terminal_status or "complete"),
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

    def remember_dialog_turn(
        self,
        *,
        context_id,
        room_id,
        session_id,
        user_text,
        assistant_text,
        created_at=None,
        terminal_status="complete",
    ):
        context_id = _clean_text(context_id, 220)
        user_text = _clean_text(user_text)
        assistant_text = _clean_text(assistant_text)
        if not context_id or not user_text or not assistant_text:
            return False
        created_at = max(0, int(created_at or time.time()))
        with self.lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO voice_room_context_turns(
                    context_id, room_id, session_id, user_text,
                    assistant_text, created_at, terminal_status
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(context_id) DO UPDATE SET
                    user_text=excluded.user_text,
                    assistant_text=excluded.assistant_text,
                    created_at=excluded.created_at,
                    terminal_status=excluded.terminal_status
                """,
                (
                    context_id,
                    str(room_id or ""),
                    str(session_id or ""),
                    user_text,
                    assistant_text,
                    created_at,
                    str(terminal_status or "complete"),
                ),
            )
        return True

    def recent_dialog_context(self, session_id, *, limit_pairs=20):
        limit_pairs = _clamp_int(limit_pairs, 20, 1, 100)
        with self.lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT context_id, user_text, assistant_text, created_at
                FROM voice_room_context_turns
                WHERE session_id = ?
                  AND user_text != ''
                  AND assistant_text != ''
                ORDER BY created_at DESC, context_id DESC
                LIMIT ?
                """,
                (str(session_id or ""), limit_pairs),
            ).fetchall()
        records = []
        for row in reversed(rows):
            timestamp = int(row["created_at"] or 0)
            context_id = str(row["context_id"] or "")
            records.extend(
                (
                    {
                        "id": context_id + ":user",
                        "role": "user",
                        "content": str(row["user_text"] or ""),
                        "ts": timestamp,
                    },
                    {
                        "id": context_id + ":assistant",
                        "role": "assistant",
                        "content": str(row["assistant_text"] or ""),
                        "ts": timestamp,
                    },
                )
            )
        return records

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
        if not session_id or not user_text or not turn_id:
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
        long_memory = instances.get("LONGTERMMEMORY")
        delivery_complete = bool(turn.get("delivery_complete", True))
        terminal_status = str(
            turn.get("terminal_status")
            or ("complete" if delivery_complete else "partial")
        )
        if assistant_text:
            append_assistant(
                session_id,
                assistant_text,
                source="voice_room",
                input_id=input_id,
                action_id=turn_id,
            )
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
                delivery_complete=delivery_complete,
                terminal_status=terminal_status,
                completed_at=int(turn.get("created_at") or time.time()),
            )
        else:
            append_long_user = getattr(
                long_memory,
                "append_external_user_message",
                None,
            )
            if not callable(append_long_user):
                raise VoiceRoomError("long_memory_unavailable")
            append_long_user(
                session_id,
                user_text,
                source="voice_room",
                action_id=turn_id,
                input_id=input_id,
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
        self.send_lock = threading.RLock()
        self.turn_lock = threading.RLock()
        self.stop_event = threading.Event()
        self.receive_thread = None
        self.audio_sender_thread = None
        self.heartbeat_thread = None
        self.audio_condition = threading.Condition(threading.RLock())
        self.audio_queue = deque()
        self.audio_queue_bytes = 0
        self.audio_sender_error = None
        self.event_handler = None
        self.audio_next_send_at = 0.0
        self.audio_bytes_sent = 0

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
        with self.send_lock:
            socket = self.socket
            if socket is None:
                raise VoiceRoomProviderError("volc_session_not_started")
            socket.send_binary(frame)

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
            # The provider timeout is useful while establishing the session,
            # but keeping it as a socket read deadline makes an otherwise
            # healthy long-lived room fail after repeated quiet intervals.
            # Once the O2 session is ready, recv must remain blocking. The
            # heartbeat below is responsible for detecting a dead connection.
            set_timeout = getattr(self.socket, "settimeout", None)
            if callable(set_timeout):
                set_timeout(None)
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
        if self.event_handler is not None:
            raise VoiceRoomProviderError("volc_session_is_streaming")
        with self.turn_lock:
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

    def start_receiving(self, event_handler):
        if not callable(event_handler):
            raise VoiceRoomProviderError("volc_event_handler_missing")
        with self.lock:
            if not self.socket:
                raise VoiceRoomProviderError("volc_session_not_started")
            if self.receive_thread and self.receive_thread.is_alive():
                return
            self.event_handler = event_handler
            self.stop_event.clear()
            self.receive_thread = threading.Thread(
                target=self._receive_loop,
                daemon=True,
                name="XiaoyouVolcO2Receive-" + self.session_id[:12],
            )
            self.receive_thread.start()
            self.heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                daemon=True,
                name="XiaoyouVolcO2Heartbeat-" + self.session_id[:12],
            )
            self.heartbeat_thread.start()
        self._ensure_audio_sender()

    def _heartbeat_loop(self):
        # The microphone stream is usually sufficient traffic, but muted or
        # quiet rooms still need an application-level heartbeat so NAT/proxy
        # idle timers cannot silently discard the WebSocket.
        while not self.stop_event.wait(20.0):
            try:
                with self.send_lock:
                    socket = self.socket
                    if socket is None:
                        return
                    ping = getattr(socket, "ping", None)
                    if callable(ping):
                        ping()
                    else:
                        # Test doubles may not expose the WebSocket ping API.
                        # Do not manufacture microphone audio as a keepalive.
                        continue
            except Exception:
                if self.stop_event.is_set():
                    return
                logger.warning(
                    "[VoiceRoom] O2 heartbeat failed session=%s",
                    self.session_id[:48],
                    exc_info=True,
                )
                # Closing the socket wakes the receive loop, which publishes a
                # single error event. The App then recreates the room while
                # preserving the provider dialog id for conversational
                # continuity.
                try:
                    socket.close()
                except Exception:
                    pass
                return

    def _receive_loop(self):
        while not self.stop_event.is_set():
            try:
                frame = self._receive()
            except Exception as exc:
                if self.stop_event.is_set():
                    return
                if exc.__class__.__name__ in (
                    "WebSocketTimeoutException",
                    "TimeoutError",
                ):
                    continue
                handler = self.event_handler
                if callable(handler):
                    try:
                        handler(
                            {
                                "event": SERVER_ERROR,
                                "payload": {
                                    "error": _clean_text(exc, 500),
                                },
                                "payload_bytes": b"",
                            }
                        )
                    except Exception:
                        logger.exception(
                            "[VoiceRoom] realtime error callback failed"
                        )
                logger.exception(
                    "[VoiceRoom] O2 receive loop stopped session=%s",
                    self.session_id[:48],
                )
                return
            handler = self.event_handler
            if callable(handler):
                try:
                    handler(frame)
                except Exception:
                    logger.exception(
                        "[VoiceRoom] realtime event callback failed "
                        "event=%s session=%s",
                        frame.get("event"),
                        self.session_id[:48],
                    )

    def _ensure_audio_sender(self):
        with self.audio_condition:
            if self.stop_event.is_set():
                raise VoiceRoomProviderError("volc_connection_closed")
            if (
                self.audio_sender_thread is not None
                and self.audio_sender_thread.is_alive()
            ):
                return
            if self.socket is None:
                raise VoiceRoomProviderError("volc_session_not_started")
            self.audio_sender_error = None
            self.audio_sender_thread = threading.Thread(
                target=self._audio_send_loop,
                daemon=True,
                name="XiaoyouVolcO2Audio-" + self.session_id[:12],
            )
            self.audio_sender_thread.start()

    def _audio_send_loop(self):
        pending = bytearray()
        while not self.stop_event.is_set():
            with self.audio_condition:
                while (
                    not self.audio_queue
                    and not self.stop_event.is_set()
                ):
                    self.audio_condition.wait(timeout=0.5)
                if self.stop_event.is_set():
                    return
                while self.audio_queue:
                    chunk = self.audio_queue.popleft()
                    self.audio_queue_bytes -= len(chunk)
                    pending.extend(chunk)

            while len(pending) >= 640 and not self.stop_event.is_set():
                chunk = bytes(pending[:640])
                del pending[:640]
                now = time.monotonic()
                if (
                    self.audio_next_send_at <= 0
                    or self.audio_next_send_at < now - 0.25
                ):
                    self.audio_next_send_at = now
                wait_seconds = self.audio_next_send_at - now
                if wait_seconds > 0:
                    time.sleep(wait_seconds)
                try:
                    self._send(
                        TASK_REQUEST,
                        chunk,
                        audio=True,
                        session=True,
                    )
                except Exception as exc:
                    with self.audio_condition:
                        self.audio_sender_error = exc
                    handler = self.event_handler
                    if callable(handler):
                        try:
                            handler(
                                {
                                    "event": SERVER_ERROR,
                                    "payload": {
                                        "error": (
                                            "volc_audio_sender_failed:"
                                            + _clean_text(exc, 300)
                                        ),
                                    },
                                    "payload_bytes": b"",
                                }
                            )
                        except Exception:
                            logger.exception(
                                "[VoiceRoom] microphone error callback failed"
                            )
                    logger.exception(
                        "[VoiceRoom] realtime microphone sender stopped "
                        "session=%s",
                        self.session_id[:48],
                    )
                    return
                first_packet = self.audio_bytes_sent == 0
                self.audio_bytes_sent += len(chunk)
                self.audio_next_send_at += len(chunk) / (16000 * 2)
                if first_packet:
                    logger.info(
                        "[VoiceRoom] realtime microphone stream started "
                        "session=%s",
                        self.session_id[:48],
                    )

    def send_audio(self, pcm_bytes):
        pcm = bytes(pcm_bytes or b"")
        if not pcm:
            return
        if len(pcm) % 2:
            pcm = pcm[:-1]
        if not pcm:
            return

        # The HTTP handler must return immediately. A dedicated sender restores
        # O2.0's 640-byte/20 ms cadence independently, so network round trips
        # never accumulate behind the microphone capture stream.
        self._ensure_audio_sender()
        with self.audio_condition:
            if self.audio_sender_error is not None:
                raise VoiceRoomProviderError(
                    "volc_audio_sender_failed:"
                    + _clean_text(self.audio_sender_error, 300)
                )
            maximum_queue_bytes = 16000 * 2 * 4
            dropped = 0
            while (
                self.audio_queue
                and self.audio_queue_bytes + len(pcm)
                > maximum_queue_bytes
            ):
                dropped_chunk = self.audio_queue.popleft()
                self.audio_queue_bytes -= len(dropped_chunk)
                dropped += len(dropped_chunk)
            if dropped:
                logger.warning(
                    "[VoiceRoom] realtime microphone backlog trimmed "
                    "session=%s dropped_bytes=%s",
                    self.session_id[:48],
                    dropped,
                )
            self.audio_queue.append(pcm)
            self.audio_queue_bytes += len(pcm)
            self.audio_condition.notify()

    def truncate(self, reply_id, audio_end_ms):
        reply_id = _clean_text(reply_id, 128)
        if not reply_id:
            return False
        # Stop the provider's currently generating turn first. Truncating only
        # the conversation history does not guarantee that already scheduled
        # TTS frames stop arriving, which is why barge-in could appear to
        # resume the old answer after the local player had been stopped.
        self._send(
            CLIENT_INTERRUPT,
            {},
            session=True,
        )
        self._send(
            CONVERSATION_TRUNCATE,
            {
                "item_id": reply_id,
                "audio_end_ms": max(0, int(audio_end_ms or 0)),
            },
            session=True,
        )
        return True

    def close(self):
        with self.lock:
            self.stop_event.set()
            with self.audio_condition:
                self.audio_condition.notify_all()
            socket = self.socket
            self.socket = None
            receive_thread = self.receive_thread
            self.receive_thread = None
            audio_sender_thread = self.audio_sender_thread
            self.audio_sender_thread = None
            heartbeat_thread = self.heartbeat_thread
            self.heartbeat_thread = None
            self.event_handler = None
            if socket is not None:
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
                    socket.send_binary(
                        build_client_frame(FINISH_CONNECTION, {})
                    )
                except Exception:
                    pass
                try:
                    socket.close()
                except Exception:
                    pass
        if (
            receive_thread is not None
            and receive_thread is not threading.current_thread()
        ):
            receive_thread.join(timeout=1.0)
        if (
            audio_sender_thread is not None
            and audio_sender_thread is not threading.current_thread()
        ):
            audio_sender_thread.join(timeout=1.0)
        if (
            heartbeat_thread is not None
            and heartbeat_thread is not threading.current_thread()
        ):
            heartbeat_thread.join(timeout=1.0)


class _VoiceRoomLiveRuntime:
    """Bridges one O2 socket to ordered, long-polled App events."""

    def __init__(
        self,
        room,
        provider,
        finalize_callback,
        continuity_callback=None,
    ):
        self.room = dict(room)
        self.provider = provider
        self.finalize_callback = finalize_callback
        self.continuity_callback = continuity_callback
        self.condition = threading.Condition(threading.RLock())
        self.events = deque(maxlen=4096)
        self.sequence = 0
        self.closed = False
        self.user_text = ""
        self.next_user_text = ""
        self.assistant_text = ""
        self.audio = bytearray()
        self.question_id = ""
        self.reply_id = ""
        self.speech_started_at = 0.0
        self.user_duration_ms = 0
        self.played_ms = 0
        self.speaking = False
        self.barge_in = False
        self.sentences = []
        self.active_sentence = None

    @staticmethod
    def _payload_text(payload):
        if not isinstance(payload, dict):
            return ""
        direct = _clean_text(payload.get("text") or payload.get("content"))
        if direct:
            return direct
        for item in payload.get("results") or []:
            if isinstance(item, dict):
                text = _clean_text(item.get("text"))
                if text:
                    return text
        return ""

    def publish(self, event_type, **payload):
        with self.condition:
            if self.closed:
                return
            self.sequence += 1
            event = {
                "sequence": self.sequence,
                "type": str(event_type),
                "created_at": int(time.time() * 1000),
            }
            event.update(payload)
            self.events.append(event)
            self.condition.notify_all()

    def wait(self, after, timeout=20):
        after = max(0, int(after or 0))
        deadline = time.monotonic() + max(0.1, float(timeout or 20))
        with self.condition:
            while not self.closed:
                values = [
                    dict(item)
                    for item in self.events
                    if int(item.get("sequence") or 0) > after
                ]
                if values:
                    return values
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.condition.wait(min(remaining, 1.0))
        return []

    def mark_truncated(self, reply_id, audio_end_ms):
        with self.condition:
            if (
                not reply_id
                or not self.reply_id
                or reply_id != self.reply_id
            ):
                return False
            self.played_ms = max(0, int(audio_end_ms or 0))
            self.barge_in = True
            return True

    def _snapshot_turn(self):
        with self.condition:
            user_text = _clean_text(self.user_text)
            assistant_text = _clean_text(self.assistant_text)
            audio = bytes(self.audio)
            played_ms = max(0, int(self.played_ms or 0))
            delivery_complete = not self.barge_in
            if played_ms and audio:
                maximum_bytes = played_ms * 24000 * 2 // 1000
                audio = audio[:maximum_bytes]
            if delivery_complete:
                delivered_assistant_text = assistant_text
            else:
                played_bytes = len(audio)
                delivered_fragments = []
                for sentence in self.sentences:
                    if not isinstance(sentence, dict):
                        continue
                    end_byte = sentence.get("end_byte")
                    text = _clean_text(sentence.get("text"))
                    if (
                        text
                        and end_byte is not None
                        and int(end_byte) <= played_bytes
                    ):
                        delivered_fragments.append(text)
                delivered_assistant_text = _clean_text(
                    "".join(delivered_fragments)
                )
            snapshot = {
                "turn_id": (
                    _clean_text(self.question_id, 100)
                    or uuid.uuid4().hex
                ),
                "user_text": user_text,
                "assistant_text": delivered_assistant_text,
                "audio_pcm": audio,
                "user_duration_ms": max(0, int(self.user_duration_ms or 0)),
                "reply_id": _clean_text(self.reply_id, 128),
                "delivery_complete": delivery_complete,
                "terminal_status": (
                    "complete" if delivery_complete else "partial"
                ),
            }
            carried_user_text = self.next_user_text
            self.user_text = carried_user_text
            self.next_user_text = ""
            self.assistant_text = ""
            self.audio = bytearray()
            self.question_id = ""
            self.reply_id = ""
            self.user_duration_ms = 0
            self.played_ms = 0
            self.speaking = False
            self.barge_in = False
            self.sentences = []
            self.active_sentence = None
            return snapshot

    def handle_frame(self, frame):
        event = int(frame.get("event") or 0)
        payload = frame.get("payload")
        if event == SERVER_ERROR:
            detail = payload if isinstance(payload, dict) else {}
            self.publish(
                "error",
                error=_clean_text(detail.get("error") or payload, 500),
            )
            return

        if event == ASR_INFO:
            with self.condition:
                interrupted = self.speaking
                if not interrupted:
                    self.user_text = ""
                    self.next_user_text = ""
                self.speech_started_at = time.monotonic()
                self.barge_in = self.barge_in or interrupted
                reply_id = self.reply_id
            self.publish(
                "user_speech_started",
                interrupted=interrupted,
                reply_id=reply_id,
            )
            return

        if event == ASR_RESPONSE:
            text = self._payload_text(payload)
            if text:
                with self.condition:
                    if self.speaking:
                        self.next_user_text = text
                    else:
                        self.user_text = text
                self.publish("user_transcript", text=text, final=False)
            return

        if event == ASR_ENDED:
            text = self._payload_text(payload)
            with self.condition:
                if text:
                    if self.speaking:
                        self.next_user_text = text
                    else:
                        self.user_text = text
                if self.speech_started_at:
                    self.user_duration_ms = int(
                        (time.monotonic() - self.speech_started_at) * 1000
                    )
                self.speech_started_at = 0.0
                final_text = text or (
                    self.next_user_text if self.speaking else self.user_text
                )
            self.publish("user_transcript", text=final_text, final=True)
            self.publish("thinking")
            return

        if event == CHAT_RESPONSE and isinstance(payload, dict):
            text = self._payload_text(payload)
            with self.condition:
                self.assistant_text = VolcO2RealtimeSession._merge_stream_text(
                    self.assistant_text,
                    text,
                )
                self.question_id = _clean_text(
                    payload.get("question_id") or self.question_id,
                    128,
                )
                self.reply_id = _clean_text(
                    payload.get("reply_id") or self.reply_id,
                    128,
                )
            if text:
                self.publish("assistant_transcript", text=self.assistant_text)
            return

        if event in (TTS_SENTENCE_START, TTS_SENTENCE_END):
            if isinstance(payload, dict):
                text = self._payload_text(payload)
                with self.condition:
                    self.assistant_text = (
                        VolcO2RealtimeSession._merge_stream_text(
                            self.assistant_text,
                            text,
                        )
                    )
                    self.question_id = _clean_text(
                        payload.get("question_id") or self.question_id,
                        128,
                    )
                    self.reply_id = _clean_text(
                        payload.get("reply_id") or self.reply_id,
                        128,
                    )
                    if event == TTS_SENTENCE_START and text:
                        self.active_sentence = {
                            "text": text,
                            "start_byte": len(self.audio),
                            "end_byte": None,
                        }
                        self.sentences.append(self.active_sentence)
                    elif event == TTS_SENTENCE_END:
                        if self.active_sentence is None and text:
                            self.active_sentence = {
                                "text": text,
                                "start_byte": 0,
                                "end_byte": len(self.audio),
                            }
                            self.sentences.append(self.active_sentence)
                        elif self.active_sentence is not None:
                            if text:
                                self.active_sentence["text"] = text
                            self.active_sentence["end_byte"] = len(self.audio)
                        self.active_sentence = None
                if text:
                    self.publish(
                        "assistant_transcript",
                        text=self.assistant_text,
                        reply_id=self.reply_id,
                    )
            return

        if event == TTS_RESPONSE:
            audio = bytes(frame.get("payload_bytes") or b"")
            if not audio:
                return
            with self.condition:
                self.audio.extend(audio)
                self.speaking = True
                reply_id = self.reply_id
            self.publish(
                "assistant_audio",
                audio=base64.b64encode(audio).decode("ascii"),
                sample_rate=24000,
                reply_id=reply_id,
            )
            return

        if event == TTS_ENDED:
            if isinstance(payload, dict):
                with self.condition:
                    self.question_id = _clean_text(
                        payload.get("question_id") or self.question_id,
                        128,
                    )
                    self.reply_id = _clean_text(
                        payload.get("reply_id") or self.reply_id,
                        128,
                    )
            snapshot = self._snapshot_turn()
            if (
                snapshot["user_text"]
                and snapshot["assistant_text"]
                and callable(self.continuity_callback)
            ):
                try:
                    self.continuity_callback(snapshot)
                except Exception:
                    logger.exception(
                        "[VoiceRoom] immediate continuity commit failed "
                        "room_id=%s",
                        str(self.room.get("room_id") or "")[:48],
                    )
            self.publish(
                "assistant_audio_ended",
                reply_id=snapshot["reply_id"],
                delivery_complete=snapshot["delivery_complete"],
            )
            if snapshot["user_text"]:
                threading.Thread(
                    target=self.finalize_callback,
                    args=(snapshot,),
                    daemon=True,
                    name="XiaoyouVoiceRoomFinalize",
                ).start()
            return

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()


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
        self.live_sessions = {}
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
        short_records = builder(session_id) if callable(builder) else []
        max_items = _clamp_int(
            os.getenv("XIAOYOU_VOICE_ROOM_CONTEXT_MESSAGES", "20"),
            20,
            2,
            40,
        )
        max_chars = _clamp_int(
            os.getenv("XIAOYOU_VOICE_ROOM_CONTEXT_MAX_CHARS", "7000"),
            7000,
            500,
            10000,
        )
        voice_records = self.store.recent_dialog_context(
            session_id,
            limit_pairs=max_items // 2,
        )
        context = merge_dialog_context_sources(
            short_records,
            voice_records,
            max_items=max_items,
            max_chars=max_chars,
        )
        logger.info(
            "[VoiceRoom] context prepared session=%s short=%s voice=%s "
            "injected=%s",
            str(session_id)[:48],
            len(short_records or []),
            len(voice_records),
            len(context),
        )
        return context

    def _remember_dialog_turn(self, room, snapshot):
        if not isinstance(room, dict) or not isinstance(snapshot, dict):
            return False
        turn_id = (
            _clean_text(snapshot.get("turn_id"), 100)
            or uuid.uuid4().hex
        )
        return self.store.remember_dialog_turn(
            context_id="%s:%s" % (str(room.get("room_id") or ""), turn_id),
            room_id=room.get("room_id"),
            session_id=room.get("session_id"),
            user_text=snapshot.get("user_text"),
            assistant_text=snapshot.get("assistant_text"),
            created_at=int(snapshot.get("created_at") or time.time()),
            terminal_status=str(
                snapshot.get("terminal_status") or "complete"
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
                "input_mod": "keep_alive",
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
        live = _VoiceRoomLiveRuntime(
            room,
            provider,
            lambda snapshot: self._complete_live_turn(
                room["room_id"],
                snapshot,
            ),
            continuity_callback=lambda snapshot: self._remember_dialog_turn(
                room,
                snapshot,
            ),
        )
        with self.lock:
            self.sessions[room["room_id"]] = provider
            self.live_sessions[room["room_id"]] = live
            self.session_last_used[room["room_id"]] = time.monotonic()
        start_receiving = getattr(provider, "start_receiving", None)
        if callable(start_receiving):
            start_receiving(live.handle_frame)
            live.publish("listening")
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

    def _active_runtime(self, room_id, device_id):
        room = self.store.get_room(room_id, device_id)
        if room is None:
            raise VoiceRoomError("voice_room_not_found")
        if room["status"] != "active":
            raise VoiceRoomError("voice_room_closed")
        with self.lock:
            provider = self.sessions.get(str(room_id))
            live = self.live_sessions.get(str(room_id))
            if provider is not None:
                self.session_last_used[str(room_id)] = time.monotonic()
        if provider is None:
            raise VoiceRoomError("voice_room_session_expired")
        return room, provider, live

    def send_audio(self, *, room_id, device_id, audio_bytes, mime_type):
        mime_type = (
            str(mime_type or "").split(";", 1)[0].strip().lower()
        )
        if mime_type not in ("audio/pcm", "audio/l16"):
            raise VoiceRoomError("voice_room_requires_pcm16")
        pcm = bytes(audio_bytes or b"")
        if not pcm or len(pcm) > 128 * 1024:
            raise VoiceRoomError("invalid_voice_room_pcm")
        if len(pcm) % 2:
            pcm = pcm[:-1]
        _, provider, _ = self._active_runtime(room_id, device_id)
        sender = getattr(provider, "send_audio", None)
        if not callable(sender):
            raise VoiceRoomError("voice_room_streaming_unavailable")
        sender(pcm)
        return {"accepted": True, "bytes": len(pcm)}

    def wait_events(
        self,
        *,
        room_id,
        device_id,
        after=0,
        timeout=20,
    ):
        _, _, live = self._active_runtime(room_id, device_id)
        if live is None:
            raise VoiceRoomError("voice_room_streaming_unavailable")
        return live.wait(after, timeout=timeout)

    def truncate(self, *, room_id, device_id, reply_id, audio_end_ms):
        _, provider, live = self._active_runtime(room_id, device_id)
        reply_id = _clean_text(reply_id, 128)
        if not reply_id:
            raise VoiceRoomError("voice_room_reply_id_missing")
        truncator = getattr(provider, "truncate", None)
        if not callable(truncator):
            raise VoiceRoomError("voice_room_truncate_unavailable")
        accepted = bool(truncator(reply_id, audio_end_ms))
        if live is not None:
            if live.mark_truncated(reply_id, audio_end_ms):
                live.publish(
                    "interrupted",
                    reply_id=reply_id,
                    audio_end_ms=max(0, int(audio_end_ms or 0)),
                )
        return {"accepted": accepted}

    def _complete_live_turn(self, room_id, snapshot):
        room = self.store.get_room(room_id)
        if room is None:
            return
        live = None
        with self.lock:
            live = self.live_sessions.get(str(room_id))
        try:
            self._remember_dialog_turn(room, snapshot)
            reply_wav = b""
            media = None
            if snapshot["audio_pcm"]:
                reply_wav = pcm16_to_wav(
                    snapshot["audio_pcm"],
                    sample_rate=24000,
                )
                if self.media_store is None:
                    raise VoiceRoomError(
                        "voice_room_media_store_unavailable"
                    )
                media = self.media_store.save_media_bytes(
                    reply_wav,
                    room["device_id"],
                    "audio/wav",
                )
                if not media:
                    raise VoiceRoomError("voice_room_audio_save_failed")
            turn, inserted = self.store.add_turn(
                turn_id="%s:%s" % (
                    str(room_id),
                    _clean_text(snapshot.get("turn_id"), 100)
                    or uuid.uuid4().hex,
                ),
                room_id=room_id,
                user_text=snapshot["user_text"],
                assistant_text=snapshot["assistant_text"],
                user_duration_ms=snapshot.get("user_duration_ms", 0),
                assistant_duration_ms=(
                    wav_duration_ms(reply_wav) if reply_wav else 0
                ),
                audio_media_id=media["media_id"] if media else "",
                audio_mime_type=media["mime_type"] if media else "",
                delivery_complete=bool(
                    snapshot.get("delivery_complete", True)
                ),
                terminal_status=str(
                    snapshot.get("terminal_status")
                    or "complete"
                ),
            )
            if inserted:
                turn["session_id"] = room["session_id"]
                self.memory.submit(turn)
            if live is not None:
                live.publish(
                    "turn_complete",
                    turn=self.response_payload({"turns": [turn]})["turns"][0],
                    delivery_complete=bool(
                        snapshot.get("delivery_complete", True)
                    ),
                )
        except Exception as exc:
            logger.exception(
                "[VoiceRoom] realtime turn finalization failed room_id=%s",
                str(room_id)[:48],
            )
            if live is not None:
                live.publish(
                    "error",
                    error="voice_room_turn_finalize_failed:"
                    + _clean_text(exc, 300),
                )

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
            self._remember_dialog_turn(
                room,
                {
                    "turn_id": turn_id,
                    "user_text": result["user_text"],
                    "assistant_text": result["assistant_text"],
                    "terminal_status": "complete",
                    "created_at": int(turn.get("created_at") or time.time()),
                },
            )
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
            live = self.live_sessions.pop(str(room_id), None)
            self.session_last_used.pop(str(room_id), None)
        if live is not None:
            live.close()
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
            live_sessions = list(self.live_sessions.values())
            self.sessions.clear()
            self.live_sessions.clear()
            self.session_last_used.clear()
        for live in live_sessions:
            live.close()
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
