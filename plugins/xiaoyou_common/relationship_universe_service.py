# -*- coding: utf-8 -*-
"""Durable relationship-universe records for the Xiaoyou mobile App.

The service stores only relationship presentation records: confirmed daily
journals, time-capsule letters and voice-room keepsakes.  It deliberately does
not write long-term memory or participate in reply routing.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime

from common.log import logger
from plugins.xiaoyou_common.model_gateway import chat_completion
from plugins.xiaoyou_common.runtime_paths import runtime_path
from plugins.xiaoyou_common.thinking_config import build_thinking_payload


RELATIONSHIP_DATABASE_PATH = runtime_path(
    "app_channel",
    "relationship.db",
    env_var="XIAOYOU_RELATIONSHIP_DB_PATH",
)

_KINDS = {"journal", "capsule", "voice_memory"}
_STATUSES = {"draft", "confirmed", "sealed", "opened"}


class RelationshipUniverseService:
    """SQLite-backed relationship presentation store."""

    def __init__(self, path=RELATIONSHIP_DATABASE_PATH):
        self.path = str(path)
        self.lock = threading.RLock()
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=8)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=8000")
        return connection

    def _initialize(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with self.lock, self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS relationship_entries (
                    entry_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    body_json TEXT NOT NULL DEFAULT '{}',
                    event_at INTEGER NOT NULL,
                    unlock_at INTEGER,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_relationship_entries_session_time
                    ON relationship_entries(session_id, event_at DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS
                    idx_relationship_journal_day
                    ON relationship_entries(session_id, kind, event_at)
                    WHERE kind='journal';
                """
            )

    def list_entries(self, session_id, *, now=None, include_drafts=True):
        now = int(now or time.time())
        clauses = ["session_id=?"]
        values = [str(session_id or "")]
        if not include_drafts:
            clauses.append("status!='draft'")
        with self.lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM relationship_entries
                WHERE %s
                ORDER BY event_at DESC, created_at DESC
                LIMIT 500
                """
                % " AND ".join(clauses),
                tuple(values),
            ).fetchall()
        return [self._public_row(row, now=now) for row in rows]

    def draft_daily_journal(
        self,
        *,
        session_id,
        day,
        messages,
        mood=None,
    ):
        session_id = str(session_id or "").strip()
        event_at = _day_timestamp(day)
        existing = self._journal_for_day(session_id, event_at)
        if existing and existing["status"] == "confirmed":
            return self._public_row(existing)

        normalized_messages = _messages_for_day(messages, event_at)
        body = self._generate_journal_body(
            session_id=session_id,
            day=day,
            messages=normalized_messages,
            mood=mood or {},
        )
        title = str(body.get("title") or "我们今天")[:80]
        now = int(time.time())
        entry_id = (
            str(existing["entry_id"])
            if existing
            else "journal_" + uuid.uuid4().hex
        )
        with self.lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO relationship_entries(
                    entry_id, session_id, kind, status, title, body_json,
                    event_at, unlock_at, created_at, updated_at
                ) VALUES(?, ?, 'journal', 'draft', ?, ?, ?, NULL, ?, ?)
                ON CONFLICT(entry_id) DO UPDATE SET
                    title=excluded.title,
                    body_json=excluded.body_json,
                    updated_at=excluded.updated_at
                """,
                (
                    entry_id,
                    session_id,
                    title,
                    json.dumps(body, ensure_ascii=False, separators=(",", ":")),
                    event_at,
                    int(existing["created_at"]) if existing else now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM relationship_entries WHERE entry_id=?",
                (entry_id,),
            ).fetchone()
        return self._public_row(row)

    def confirm_journal(self, session_id, entry_id, body=None):
        with self.lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM relationship_entries
                WHERE entry_id=? AND session_id=? AND kind='journal'
                """,
                (str(entry_id or ""), str(session_id or "")),
            ).fetchone()
            if not row:
                return None
            current = _json_object(row["body_json"])
            if isinstance(body, dict):
                current.update(_normalize_journal(body))
            now = int(time.time())
            connection.execute(
                """
                UPDATE relationship_entries
                SET status='confirmed', title=?, body_json=?, updated_at=?
                WHERE entry_id=?
                """,
                (
                    str(current.get("title") or row["title"] or "我们今天")[:80],
                    json.dumps(
                        current,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    now,
                    row["entry_id"],
                ),
            )
            updated = connection.execute(
                "SELECT * FROM relationship_entries WHERE entry_id=?",
                (row["entry_id"],),
            ).fetchone()
        return self._public_row(updated)

    def create_capsule(
        self,
        *,
        session_id,
        title,
        text,
        unlock_at,
        author="user",
    ):
        unlock_at = int(unlock_at or 0)
        now = int(time.time())
        if unlock_at <= now:
            raise ValueError("capsule_unlock_must_be_future")
        text = str(text or "").strip()
        if not text:
            raise ValueError("capsule_text_required")
        entry_id = "capsule_" + uuid.uuid4().hex
        body = {
            "text": text[:12000],
            "author": "assistant" if str(author) == "assistant" else "user",
        }
        with self.lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO relationship_entries(
                    entry_id, session_id, kind, status, title, body_json,
                    event_at, unlock_at, created_at, updated_at
                ) VALUES(?, ?, 'capsule', 'sealed', ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id,
                    str(session_id or ""),
                    str(title or "写给未来")[:80],
                    json.dumps(body, ensure_ascii=False, separators=(",", ":")),
                    now,
                    unlock_at,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM relationship_entries WHERE entry_id=?",
                (entry_id,),
            ).fetchone()
        return self._public_row(row, now=now)

    def open_capsule(self, session_id, entry_id, *, now=None):
        now = int(now or time.time())
        with self.lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM relationship_entries
                WHERE entry_id=? AND session_id=? AND kind='capsule'
                """,
                (str(entry_id or ""), str(session_id or "")),
            ).fetchone()
            if not row:
                return None
            unlock_at = int(row["unlock_at"] or 0)
            if unlock_at > now:
                return self._public_row(row, now=now)
            connection.execute(
                """
                UPDATE relationship_entries
                SET status='opened', updated_at=?
                WHERE entry_id=?
                """,
                (now, row["entry_id"]),
            )
            updated = connection.execute(
                "SELECT * FROM relationship_entries WHERE entry_id=?",
                (row["entry_id"],),
            ).fetchone()
        return self._public_row(updated, now=now)

    def record_voice_memory(
        self,
        *,
        session_id,
        started_at,
        ended_at,
        turn_count,
        duration_ms,
        title="耳边的一会儿",
    ):
        now = int(time.time())
        started_at = max(1, int(started_at or now))
        ended_at = max(started_at, int(ended_at or now))
        entry_id = "voice_" + uuid.uuid4().hex
        body = {
            "turn_count": max(0, int(turn_count or 0)),
            "duration_ms": max(0, int(duration_ms or 0)),
            "started_at": started_at,
            "ended_at": ended_at,
        }
        with self.lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO relationship_entries(
                    entry_id, session_id, kind, status, title, body_json,
                    event_at, unlock_at, created_at, updated_at
                ) VALUES(?, ?, 'voice_memory', 'confirmed', ?, ?, ?, NULL, ?, ?)
                """,
                (
                    entry_id,
                    str(session_id or ""),
                    str(title or "耳边的一会儿")[:80],
                    json.dumps(body, ensure_ascii=False, separators=(",", ":")),
                    ended_at,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM relationship_entries WHERE entry_id=?",
                (entry_id,),
            ).fetchone()
        return self._public_row(row, now=now)

    def _journal_for_day(self, session_id, event_at):
        with self.lock, self._connect() as connection:
            return connection.execute(
                """
                SELECT *
                FROM relationship_entries
                WHERE session_id=? AND kind='journal' AND event_at=?
                LIMIT 1
                """,
                (session_id, event_at),
            ).fetchone()

    def _generate_journal_body(self, *, session_id, day, messages, mood):
        grounded = _fallback_journal(day, messages, mood)
        if not messages:
            return grounded
        transcript = "\n".join(
            "[%s] %s: %s"
            % (
                datetime.fromtimestamp(item["created_at"]).strftime("%H:%M"),
                "YoYo" if item["role"] == "user" else "小悠",
                item["content"],
            )
            for item in messages[-100:]
        )
        prompt = """请把以下一天的真实聊天整理成“我们今天”日记草稿。
只能引用或概括已经发生的内容，不允许补写不存在的约定、活动、情绪或照片。
saved_quote 必须逐字来自 transcript，并标明 speaker。
tomorrow_wish 只能来自已经明确提到的未完成事项；没有证据就返回空字符串。
representative_media_id 只能从 transcript 中出现的 media_id 选择；没有就返回空字符串。
只输出合法 JSON：
{
  "title":"不超过18字",
  "summary":"80到180字",
  "mood_changes":["最多3项，每项简短"],
  "representative_media_id":"",
  "saved_quote":{"speaker":"user|assistant","text":""},
  "tomorrow_wish":""
}

日期：%s
当前连续状态（仅供描述变化，不可当作聊天事实）：%s
transcript：
%s""" % (
            str(day),
            json.dumps(mood or {}, ensure_ascii=False),
            transcript,
        )
        payload = {
            "model": os.getenv(
                "XIAOYOU_APP_JOURNAL_MODEL",
                os.getenv("XIAOYOU_EPISODE_SUMMARY_MODEL", "qwen3.7-plus"),
            ),
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是小悠和YoYo共同日记的事实整理者。"
                        "只整理已发生事实，只输出JSON。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 900,
            **build_thinking_payload("XIAOYOU_APP_JOURNAL"),
        }
        result = chat_completion(
            component="RelationshipUniverse",
            purpose="daily_journal_draft",
            payload=payload,
            timeout=_env_int("XIAOYOU_APP_JOURNAL_TIMEOUT", 45, 10, 120),
            session_id=session_id,
        )
        if not result.ok:
            logger.warning(
                "[RelationshipUniverse] journal model failed session=%s kind=%s",
                session_id,
                getattr(result, "error_kind", "unknown"),
            )
            return grounded
        parsed = _parse_json_object(result.content)
        normalized = _normalize_journal(parsed)
        if not _journal_is_grounded(normalized, messages):
            logger.warning(
                "[RelationshipUniverse] rejected ungrounded journal session=%s",
                session_id,
            )
            return grounded
        return normalized

    @staticmethod
    def _public_row(row, *, now=None):
        now = int(now or time.time())
        payload = _json_object(row["body_json"])
        unlock_at = int(row["unlock_at"] or 0)
        is_locked = bool(
            row["kind"] == "capsule"
            and row["status"] == "sealed"
            and unlock_at > now
        )
        if is_locked:
            payload = {
                "author": str(payload.get("author") or "user"),
            }
        return {
            "entry_id": str(row["entry_id"]),
            "kind": str(row["kind"]),
            "status": str(row["status"]),
            "title": str(row["title"]),
            "body": payload,
            "event_at": int(row["event_at"]),
            "unlock_at": unlock_at,
            "locked": is_locked,
            "created_at": int(row["created_at"]),
            "updated_at": int(row["updated_at"]),
        }


def _messages_for_day(messages, event_at):
    start = int(event_at)
    end = start + 86400
    normalized = []
    for item in messages or []:
        if not isinstance(item, dict):
            continue
        created_at = int(item.get("created_at") or 0)
        if created_at < start or created_at >= end:
            continue
        role = "user" if str(item.get("role")) == "user" else "assistant"
        kind = str(item.get("kind") or "text")
        text = str(item.get("text") or "").strip()
        media_id = str(item.get("media_id") or "").strip()
        if kind in ("image", "sticker"):
            content = "[%s media_id=%s] %s" % (
                kind,
                media_id,
                text,
            )
        elif kind == "voice":
            content = "[voice media_id=%s] %s" % (media_id, text)
        else:
            content = text
        if not content.strip():
            continue
        normalized.append(
            {
                "role": role,
                "kind": kind,
                "content": content[:4000],
                "media_id": media_id,
                "created_at": created_at,
            }
        )
    normalized.sort(key=lambda item: item["created_at"])
    return normalized


def _fallback_journal(day, messages, mood):
    user_messages = [
        item for item in messages if item.get("role") == "user"
    ]
    assistant_messages = [
        item for item in messages if item.get("role") == "assistant"
    ]
    selected = (
        max(messages, key=lambda item: len(item.get("content", "")))
        if messages
        else None
    )
    media = next(
        (
            item
            for item in reversed(messages)
            if item.get("kind") in ("image", "sticker")
            and item.get("media_id")
        ),
        None,
    )
    mood_label = str((mood or {}).get("label") or "").strip()
    if messages:
        summary = "今天一共留下了%s条对话，YoYo说了%s条，小悠回应了%s条。" % (
            len(messages),
            len(user_messages),
            len(assistant_messages),
        )
    else:
        summary = "今天还没有足够的聊天内容，等有了真正发生的日常再收藏。"
    return {
        "title": "我们今天",
        "summary": summary,
        "mood_changes": [mood_label] if mood_label else [],
        "representative_media_id": str(
            (media or {}).get("media_id") or ""
        ),
        "saved_quote": {
            "speaker": str((selected or {}).get("role") or ""),
            "text": str((selected or {}).get("content") or "")[:400],
        },
        "tomorrow_wish": "",
        "date": str(day),
    }


def _normalize_journal(value):
    value = value if isinstance(value, dict) else {}
    quote = value.get("saved_quote")
    quote = quote if isinstance(quote, dict) else {}
    changes = value.get("mood_changes")
    if not isinstance(changes, list):
        changes = []
    return {
        "title": str(value.get("title") or "我们今天")[:80],
        "summary": str(value.get("summary") or "")[:3000],
        "mood_changes": [
            str(item)[:120] for item in changes[:3] if str(item).strip()
        ],
        "representative_media_id": str(
            value.get("representative_media_id") or ""
        )[:128],
        "saved_quote": {
            "speaker": (
                "user"
                if str(quote.get("speaker")) == "user"
                else "assistant"
            ),
            "text": str(quote.get("text") or "")[:800],
        },
        "tomorrow_wish": str(value.get("tomorrow_wish") or "")[:500],
    }


def _journal_is_grounded(journal, messages):
    transcript = "\n".join(item.get("content", "") for item in messages)
    quote = str((journal.get("saved_quote") or {}).get("text") or "")
    if quote and quote not in transcript:
        return False
    available_media = {
        str(item.get("media_id") or "")
        for item in messages
        if item.get("media_id")
    }
    media_id = str(journal.get("representative_media_id") or "")
    return not media_id or media_id in available_media


def _parse_json_object(value):
    text = str(value or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_object(value):
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _day_timestamp(value):
    text = str(value or "").strip()
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("invalid_journal_day") from exc
    return int(parsed.timestamp())


def _env_int(name, default, minimum, maximum):
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = int(default)
    return max(int(minimum), min(value, int(maximum)))


__all__ = [
    "RELATIONSHIP_DATABASE_PATH",
    "RelationshipUniverseService",
]
