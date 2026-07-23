# -*- coding: utf-8 -*-
"""SQLite persistence for governed long-term memories."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid


class LongMemoryStore:
    """Store durable memory facts and their semantic vectors locally."""

    def __init__(self, path, *, timeout=8):
        self.path = os.path.abspath(os.fspath(path))
        self.timeout = max(1, int(timeout or 8))
        self._lock = threading.RLock()
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._initialize()

    def upsert(self, *, user_id, candidate, embedding=None, embedding_model=""):
        if not isinstance(candidate, dict):
            return {"ok": False, "error": "invalid_candidate"}
        user_id = str(user_id or "").strip()
        memory_key = str(candidate.get("memory_key") or "").strip()
        content = str(candidate.get("content") or "").strip()
        if not user_id or not memory_key or not content:
            return {"ok": False, "error": "missing_required_field"}

        now = int(time.time())
        serialized_embedding = self._serialize_embedding(embedding)
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                """
                SELECT id, content, embedding, embedding_model, created_at,
                       source_turn_sequence, source_session_id, subject,
                       source_role, occurred_at, temporal_precision,
                       valid_from, valid_until, timezone, time_evidence
                FROM memories
                WHERE user_id = ? AND memory_key = ?
                """,
                (user_id, memory_key),
            ).fetchone()
            if existing:
                memory_id = str(existing["id"])
                incoming_sequence = self._positive_int(
                    candidate.get("source_turn_sequence")
                )
                existing_sequence = self._positive_int(
                    existing["source_turn_sequence"]
                )
                incoming_session = str(
                    candidate.get("source_session_id") or ""
                )[:160]
                existing_session = str(existing["source_session_id"] or "")[:160]
                if (
                    incoming_sequence
                    and existing_sequence
                    and incoming_session == existing_session
                    and incoming_sequence < existing_sequence
                ):
                    return {
                        "ok": True,
                        "memory_id": memory_id,
                        "operation": "stale",
                    }
                same_content = str(existing["content"]) == content
                if same_content and serialized_embedding is None:
                    serialized_embedding = existing["embedding"]
                    embedding_model = str(existing["embedding_model"] or "")
                subject = candidate.get("subject")
                source_role = candidate.get("source_role")
                occurred_at = candidate.get("occurred_at")
                temporal_precision = candidate.get("temporal_precision")
                valid_from = candidate.get("valid_from")
                valid_until = candidate.get("valid_until")
                timezone = candidate.get("timezone")
                time_evidence = candidate.get("time_evidence")
                if same_content:
                    subject = subject or existing["subject"]
                    source_role = source_role or existing["source_role"]
                    occurred_at = occurred_at or existing["occurred_at"]
                    temporal_precision = (
                        temporal_precision or existing["temporal_precision"]
                    )
                    valid_from = valid_from or existing["valid_from"]
                    valid_until = valid_until or existing["valid_until"]
                    timezone = timezone or existing["timezone"]
                    time_evidence = time_evidence or existing["time_evidence"]
                connection.execute(
                    """
                    UPDATE memories
                    SET category = ?, memory_type = ?, subject = ?,
                        source_role = ?, content = ?,
                        confidence = ?, importance = ?,
                        source_turn_sequence = ?, source_input_id = ?,
                        source_session_id = ?, updated_at = ?,
                        occurred_at = ?, temporal_precision = ?,
                        valid_from = ?, valid_until = ?, timezone = ?,
                        time_evidence = ?, embedding = ?, embedding_model = ?
                    WHERE id = ?
                    """,
                    (
                        str(candidate.get("category") or ""),
                        str(candidate.get("memory_type") or ""),
                        self._subject(subject),
                        self._source_role(source_role),
                        content,
                        self._unit_float(candidate.get("confidence")),
                        self._unit_float(candidate.get("importance")),
                        self._positive_int(candidate.get("source_turn_sequence")),
                        str(candidate.get("source_input_id") or "")[:160],
                        str(candidate.get("source_session_id") or "")[:160],
                        now,
                        str(occurred_at or "")[:64],
                        str(temporal_precision or "")[:32],
                        str(valid_from or "")[:64],
                        str(valid_until or "")[:64],
                        str(timezone or "")[:80],
                        str(time_evidence or "")[:240],
                        serialized_embedding,
                        str(embedding_model or "")[:120],
                        memory_id,
                    ),
                )
                operation = "update"
            else:
                memory_id = "local_" + uuid.uuid4().hex
                connection.execute(
                    """
                    INSERT INTO memories (
                        id, user_id, memory_key, category, memory_type,
                        subject, source_role, content, confidence, importance,
                        source_turn_sequence,
                        source_input_id, source_session_id, created_at, updated_at,
                        occurred_at, temporal_precision, valid_from, valid_until,
                        timezone, time_evidence, embedding, embedding_model
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        memory_id,
                        user_id,
                        memory_key,
                        str(candidate.get("category") or ""),
                        str(candidate.get("memory_type") or ""),
                        self._subject(candidate.get("subject")),
                        self._source_role(candidate.get("source_role")),
                        content,
                        self._unit_float(candidate.get("confidence")),
                        self._unit_float(candidate.get("importance")),
                        self._positive_int(candidate.get("source_turn_sequence")),
                        str(candidate.get("source_input_id") or "")[:160],
                        str(candidate.get("source_session_id") or "")[:160],
                        now,
                        now,
                        str(candidate.get("occurred_at") or "")[:64],
                        str(candidate.get("temporal_precision") or "")[:32],
                        str(candidate.get("valid_from") or "")[:64],
                        str(candidate.get("valid_until") or "")[:64],
                        str(candidate.get("timezone") or "")[:80],
                        str(candidate.get("time_evidence") or "")[:240],
                        serialized_embedding,
                        str(embedding_model or "")[:120],
                    ),
                )
                operation = "insert"
            connection.commit()
        return {
            "ok": True,
            "memory_id": memory_id,
            "operation": operation,
        }

    def list_memories(self, *, user_id, allowed_types=None, limit=2000):
        user_id = str(user_id or "").strip()
        if not user_id:
            return []
        try:
            limit = max(1, min(10000, int(limit)))
        except Exception:
            limit = 2000

        normalized_types = sorted(
            {
                str(memory_type or "").strip().lower()
                for memory_type in (allowed_types or ())
                if str(memory_type or "").strip()
            }
        )
        statement = """
            SELECT id, user_id, memory_key, category, memory_type, content,
                   confidence, importance, source_turn_sequence,
                   source_input_id, source_session_id, created_at, updated_at,
                   subject, source_role, occurred_at, temporal_precision,
                   valid_from, valid_until, timezone, time_evidence,
                   embedding, embedding_model
            FROM memories
            WHERE user_id = ?
        """
        parameters = [user_id]
        if normalized_types:
            placeholders = ",".join("?" for _ in normalized_types)
            statement += " AND memory_type IN (%s)" % placeholders
            parameters.extend(normalized_types)
        statement += " ORDER BY updated_at DESC, id DESC LIMIT ?"
        parameters.append(limit)

        with self._lock, self._connect() as connection:
            rows = connection.execute(statement, parameters).fetchall()
        return [self._row_to_memory(row) for row in rows]

    def update_embedding(self, memory_id, embedding, embedding_model):
        serialized = self._serialize_embedding(embedding)
        if not serialized:
            return False
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE memories
                SET embedding = ?, embedding_model = ?
                WHERE id = ?
                """,
                (
                    serialized,
                    str(embedding_model or "")[:120],
                    str(memory_id or ""),
                ),
            )
            connection.commit()
            return cursor.rowcount > 0

    def import_governance_ledger(self, path, *, user_id):
        """Import active governed facts when switching away from cloud storage."""
        path = os.path.abspath(os.fspath(path))
        if not os.path.isfile(path):
            return 0
        try:
            with open(path, "r", encoding="utf-8") as handle:
                state = json.load(handle)
        except Exception:
            return 0
        entries = state.get("entries") if isinstance(state, dict) else None
        if not isinstance(entries, list):
            return 0

        newest_by_key = {}
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("status") or "") != "written":
                continue
            memory_key = str(entry.get("memory_key") or "").strip()
            content = str(entry.get("content") or "").strip()
            if not memory_key or not content:
                continue
            newest_by_key[memory_key] = entry

        imported = 0
        for entry in newest_by_key.values():
            if self._contains_key(
                user_id=str(user_id or ""),
                memory_key=str(entry.get("memory_key") or ""),
            ):
                continue
            result = self.upsert(
                user_id=user_id,
                candidate=entry,
                embedding=None,
                embedding_model="",
            )
            if result.get("ok") and result.get("operation") == "insert":
                imported += 1
        return imported

    def _contains_key(self, *, user_id, memory_key):
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM memories
                WHERE user_id = ? AND memory_key = ?
                LIMIT 1
                """,
                (str(user_id or ""), str(memory_key or "")),
            ).fetchone()
        return row is not None

    def count(self, *, user_id):
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM memories WHERE user_id = ?",
                (str(user_id or ""),),
            ).fetchone()
        return int(row["total"] or 0)

    def _initialize(self):
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    memory_key TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT '',
                    memory_type TEXT NOT NULL DEFAULT 'legacy',
                    subject TEXT NOT NULL DEFAULT 'user',
                    source_role TEXT NOT NULL DEFAULT 'user',
                    content TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0,
                    importance REAL NOT NULL DEFAULT 0,
                    source_turn_sequence INTEGER NOT NULL DEFAULT 0,
                    source_input_id TEXT NOT NULL DEFAULT '',
                    source_session_id TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    occurred_at TEXT NOT NULL DEFAULT '',
                    temporal_precision TEXT NOT NULL DEFAULT '',
                    valid_from TEXT NOT NULL DEFAULT '',
                    valid_until TEXT NOT NULL DEFAULT '',
                    timezone TEXT NOT NULL DEFAULT '',
                    time_evidence TEXT NOT NULL DEFAULT '',
                    embedding TEXT,
                    embedding_model TEXT NOT NULL DEFAULT '',
                    UNIQUE(user_id, memory_key)
                );
                CREATE INDEX IF NOT EXISTS idx_memories_user_updated
                ON memories(user_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_memories_user_type_updated
                ON memories(user_id, memory_type, updated_at DESC);
                """
            )
            self._ensure_columns(connection)
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memories_user_subject_updated
                ON memories(user_id, subject, updated_at DESC)
                """
            )
            connection.execute("PRAGMA user_version = 2")
            connection.commit()

    @staticmethod
    def _ensure_columns(connection):
        existing = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(memories)").fetchall()
        }
        additions = {
            "subject": "TEXT NOT NULL DEFAULT 'user'",
            "source_role": "TEXT NOT NULL DEFAULT 'user'",
            "occurred_at": "TEXT NOT NULL DEFAULT ''",
            "temporal_precision": "TEXT NOT NULL DEFAULT ''",
            "valid_from": "TEXT NOT NULL DEFAULT ''",
            "valid_until": "TEXT NOT NULL DEFAULT ''",
            "timezone": "TEXT NOT NULL DEFAULT ''",
            "time_evidence": "TEXT NOT NULL DEFAULT ''",
        }
        for name, definition in additions.items():
            if name not in existing:
                connection.execute(
                    "ALTER TABLE memories ADD COLUMN %s %s" % (name, definition)
                )

    def _connect(self):
        connection = sqlite3.connect(
            self.path,
            timeout=self.timeout,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA busy_timeout=%d" % (self.timeout * 1000))
        return connection

    def _row_to_memory(self, row):
        memory = dict(row)
        raw_embedding = memory.get("embedding")
        try:
            parsed = json.loads(raw_embedding) if raw_embedding else None
            memory["embedding"] = (
                [float(value) for value in parsed]
                if isinstance(parsed, list)
                else None
            )
        except Exception:
            memory["embedding"] = None
        memory["memory_id"] = str(memory.pop("id", "") or "")
        return memory

    @staticmethod
    def _serialize_embedding(value):
        if not isinstance(value, (list, tuple)) or not value:
            return None
        try:
            vector = [float(item) for item in value]
        except Exception:
            return None
        return json.dumps(vector, ensure_ascii=True, separators=(",", ":"))

    @staticmethod
    def _unit_float(value):
        try:
            return max(0.0, min(1.0, float(value)))
        except Exception:
            return 0.0

    @staticmethod
    def _positive_int(value):
        try:
            return max(0, int(value))
        except Exception:
            return 0

    @staticmethod
    def _subject(value):
        value = str(value or "").strip().lower()
        return value if value in ("user", "xiaoyou", "relationship") else "user"

    @staticmethod
    def _source_role(value):
        value = str(value or "").strip().lower()
        return (
            value
            if value in ("user", "assistant_delivered", "joint")
            else "user"
        )


__all__ = ["LongMemoryStore"]
