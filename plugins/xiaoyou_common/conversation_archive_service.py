# -*- coding: utf-8 -*-
"""Lossless conversation archive, active window and episodic retrieval.

The WeChat UI is one lifelong thread, while model context is necessarily
bounded.  This service separates durable retention from selective recall:

* every real user/assistant message is appended to SQLite with its original ID;
* an ActiveWindow returns exact recent role messages by wall-clock time;
* idle gaps close invisible episodes that are summarized in the background;
* retrieval combines time intent, character-level semantic overlap, recency,
  unfinished-thread relevance and importance, then expands hits back to raw
  neighboring messages.

The archive never replaces ShortMemory or LongTermMemory. All failures are
fail-open for chat delivery, and assistant messages remain conversation
evidence rather than proof of user facts.
"""

import json
import math
import os
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta

from common.log import logger
from plugins.xiaoyou_common.model_gateway import chat_completion
from plugins.xiaoyou_common.runtime_paths import runtime_path
from plugins.xiaoyou_common.thinking_config import build_thinking_payload


ARCHIVE_FILE = runtime_path(
    "xiaoyou_conversation",
    "conversation.db",
    env_var="XIAOYOU_CONVERSATION_ARCHIVE_PATH",
    legacy_paths=(
        os.path.join(os.path.dirname(__file__), "xiaoyou_conversation", "conversation.db"),
    ),
)
VALID_ROLES = ("user", "assistant")
SENSITIVE_RE = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{12,}|密码|验证码|银行卡|身份证|api[_ -]?key|access[_ -]?token)",
    re.I,
)
GENERIC_CONTINUE_RE = re.compile(
    r"^(?:我们)?(?:继续|接着)(?:吧|呀|聊|做|说)?[。！!？?]*$"
)


class ConversationArchiveService:
    def __init__(self, path=None, *, start_worker=True):
        self.path = os.path.abspath(path or ARCHIVE_FILE)
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.stop_event = threading.Event()
        self.worker = None
        self.last_backup_at = 0
        self._initialize()
        try:
            self.last_backup_at = int(os.path.getmtime(self.path + ".backup"))
        except OSError:
            self.last_backup_at = 0
        self._recover_interrupted_jobs()
        if start_worker and self.enabled():
            self.worker = threading.Thread(
                target=self._worker_loop,
                daemon=True,
                name="XiaoyouEpisodeBuilder",
            )
            self.worker.start()

    def enabled(self):
        return os.getenv("XIAOYOU_CONVERSATION_ARCHIVE_ENABLED", "true").strip().lower() in (
            "1", "true", "yes", "on"
        )

    def record_message(
        self,
        *,
        message_id,
        session_id,
        role,
        content,
        ts=None,
        source="event",
        trace_id="",
        input_id="",
        action_id="",
    ):
        if not self.enabled():
            return ""
        message_id = str(message_id or "").strip()
        session_id = str(session_id or "").strip()
        role = str(role or "").strip().lower()
        # Keep the conversation evidence itself intact.  Normalization belongs
        # to summaries/search indexes, never to the durable raw-message row.
        content = str(content or "").strip()
        ts = int(ts or time.time())
        if not message_id or not session_id or role not in VALID_ROLES or not content:
            return ""

        closed_episode = ""
        with self.lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT episode_id FROM messages WHERE id = ?",
                    (message_id,),
                ).fetchone()
                if existing:
                    connection.commit()
                    return str(existing["episode_id"] or "")

                episode = connection.execute(
                    """SELECT * FROM episodes
                       WHERE session_id = ? AND status = 'open' AND excluded = 0
                       ORDER BY last_message_at DESC LIMIT 1""",
                    (session_id,),
                ).fetchone()
                if episode and self._should_roll_episode(episode, ts):
                    closed_episode = str(episode["id"])
                    self._close_episode(
                        connection,
                        closed_episode,
                        ended_at=int(episode["last_message_at"] or ts),
                        reason=self._roll_reason(episode, ts),
                    )
                    episode = None

                if not episode:
                    episode_id = uuid.uuid4().hex
                    connection.execute(
                        """INSERT INTO episodes
                           (id, session_id, started_at, last_message_at, status,
                            message_count, created_at, updated_at)
                           VALUES (?, ?, ?, ?, 'open', 0, ?, ?)""",
                        (episode_id, session_id, ts, ts, int(time.time()), int(time.time())),
                    )
                else:
                    episode_id = str(episode["id"])

                connection.execute(
                    """INSERT INTO messages
                       (id, session_id, episode_id, role, content, ts, source,
                        trace_id, input_id, action_id, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        message_id,
                        session_id,
                        episode_id,
                        role,
                        content,
                        ts,
                        str(source or "")[:80],
                        str(trace_id or "")[:80],
                        str(input_id or "")[:80],
                        str(action_id or "")[:80],
                        int(time.time()),
                    ),
                )
                connection.execute(
                    """UPDATE episodes
                       SET last_message_at = ?, message_count = message_count + 1,
                           updated_at = ?
                       WHERE id = ?""",
                    (ts, int(time.time()), episode_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                logger.exception(
                    "[ConversationArchive] append failed session=%s role=%s",
                    session_id,
                    role,
                )
                return ""
            finally:
                connection.close()

        if closed_episode:
            self.wake.set()
        logger.info(
            "[ConversationArchive] message archived session=%s role=%s episode=%s",
            session_id,
            role,
            episode_id[:12],
        )
        return episode_id

    def backfill_messages(self, session_id, records):
        inserted = 0
        for record in sorted(
            [value for value in (records or []) if isinstance(value, dict)],
            # Python's stable sort preserves the original role order when
            # ShortMemory records share the same one-second timestamp.
            key=lambda value: int(value.get("ts") or 0),
        ):
            if self.record_message(
                message_id=record.get("id"),
                session_id=session_id,
                role=record.get("role"),
                content=record.get("content"),
                ts=record.get("ts"),
                source=record.get("source", "short_memory_backfill"),
            ):
                inserted += 1
        self.close_stale_episodes()
        return inserted

    def build_active_history(self, session_id, *, now=None):
        if not self.enabled():
            return []
        session_id = str(session_id or "").strip()
        if not session_id:
            return []
        now = int(now or time.time())
        hours = _env_float("XIAOYOU_ACTIVE_WINDOW_HOURS", 6.0, 0.5, 48.0)
        cutoff = now - int(hours * 3600)
        limit = _env_int("XIAOYOU_ACTIVE_WINDOW_MAX_MESSAGES", 400, 20, 5000)
        with self.lock:
            connection = self._connect()
            try:
                rows = connection.execute(
                    """SELECT id, role, content, ts, episode_id
                       FROM messages
                       WHERE session_id = ? AND ts >= ? AND excluded = 0
                         AND provider_injection_blocked = 0
                       ORDER BY ts DESC, rowid DESC LIMIT ?""",
                    (session_id, cutoff, limit),
                ).fetchall()
            finally:
                connection.close()
        rows = list(reversed(rows))
        return [
            {
                "id": row["id"],
                "role": row["role"],
                "content": _context_safe_content(row["content"]),
                "ts": int(row["ts"] or 0),
                "episode_id": row["episode_id"],
            }
            for row in rows
        ]

    def block_injected_messages(self, message_ids, reason="provider_content_inspection"):
        ids = list(dict.fromkeys(
            str(value or "").strip() for value in (message_ids or []) if str(value or "").strip()
        ))
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self.lock:
            connection = self._connect()
            try:
                cursor = connection.execute(
                    "UPDATE messages SET provider_injection_blocked = 1, block_reason = ? "
                    "WHERE id IN (%s)" % placeholders,
                    [str(reason or "")[:120]] + ids,
                )
                connection.commit()
                return int(cursor.rowcount or 0)
            finally:
                connection.close()

    def exclude_recent_session(self, session_id, *, now=None):
        """Forget recent derived context without deleting older durable history."""
        session_id = str(session_id or "").strip()
        if not session_id:
            return 0
        now = int(now or time.time())
        cutoff = now - int(_env_float("XIAOYOU_ACTIVE_WINDOW_HOURS", 6.0, 0.5, 48.0) * 3600)
        with self.lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    "UPDATE messages SET excluded = 1 WHERE session_id = ? AND ts >= ?",
                    (session_id, cutoff),
                )
                connection.execute(
                    """UPDATE episodes SET excluded = 1, status = 'excluded', updated_at = ?
                       WHERE session_id = ? AND last_message_at >= ?""",
                    (now, session_id, cutoff),
                )
                connection.commit()
                return int(cursor.rowcount or 0)
            except Exception:
                connection.rollback()
                logger.exception(
                    "[ConversationArchive] recent exclusion failed session=%s",
                    session_id,
                )
                return 0
            finally:
                connection.close()

    def close_stale_episodes(self, *, now=None):
        now = int(now or time.time())
        idle_seconds = _env_int("XIAOYOU_EPISODE_IDLE_SECONDS", 2700, 300, 86400)
        cutoff = now - idle_seconds
        closed = []
        with self.lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                rows = connection.execute(
                    """SELECT id, last_message_at FROM episodes
                       WHERE status = 'open' AND excluded = 0 AND last_message_at <= ?""",
                    (cutoff,),
                ).fetchall()
                for row in rows:
                    episode_id = str(row["id"])
                    self._close_episode(
                        connection,
                        episode_id,
                        ended_at=int(row["last_message_at"] or now),
                        reason="idle_gap",
                    )
                    closed.append(episode_id)
                connection.commit()
            except Exception:
                connection.rollback()
                logger.exception("[ConversationArchive] stale episode close failed")
            finally:
                connection.close()
        if closed:
            self.wake.set()
            logger.info("[EpisodeBuilder] closed stale episodes count=%s", len(closed))
        return closed

    def build_episodic_context(
        self,
        session_id,
        query,
        *,
        mode="general",
        max_results=2,
        max_chars=None,
    ):
        if not self.enabled() or max_results <= 0:
            return "", {"episode_ids": [], "scores": [], "message_ids": []}
        session_id = str(session_id or "").strip()
        query = _clean_text(query, 1600)
        if not session_id or not query:
            return "", {"episode_ids": [], "scores": [], "message_ids": []}

        candidates = self._candidate_episodes(session_id, query)
        ranked = self._rank_episodes(candidates, query, mode=mode)
        selected = ranked[: max(1, int(max_results))]
        if not selected:
            return "", {"episode_ids": [], "scores": [], "message_ids": []}

        sections = []
        used_message_ids = []
        for candidate in selected:
            raw_messages = self._episode_messages(candidate["id"])
            span = _select_relevant_span(raw_messages, query)
            span = [
                dict(message, content=_context_safe_content(message.get("content")))
                for message in span
            ]
            used_message_ids.extend(message.get("id") for message in span if message.get("id"))
            sections.append(self._format_episode(candidate, span))

        max_chars = int(
            max_chars
            if max_chars is not None
            else os.getenv("XIAOYOU_EPISODIC_CONTEXT_MAX_CHARS", "3600")
        )
        rendered = _trim_sections(sections, max(400, max_chars))
        manifest = {
            "schema_version": 1,
            "episode_ids": [candidate["id"] for candidate in selected],
            "scores": [round(float(candidate["score"]), 4) for candidate in selected],
            "message_ids": used_message_ids,
        }
        logger.info(
            "[EpisodicMemory] retrieved episodes=%s mode=%s",
            len(selected),
            mode,
        )
        return rendered, manifest

    def backup_now(self):
        """Create a consistent rotating SQLite backup without stopping chat."""
        if not self.enabled():
            return ""
        temporary = self.path + ".backup.tmp"
        primary = self.path + ".backup"
        generations = _env_int("XIAOYOU_ARCHIVE_BACKUP_GENERATIONS", 3, 1, 10)
        with self.lock:
            source = self._connect()
            target = None
            try:
                if os.path.exists(temporary):
                    os.remove(temporary)
                target = sqlite3.connect(temporary)
                source.backup(target)
                target.commit()
                target.close()
                target = None
                for index in range(generations - 1, 0, -1):
                    older = primary if index == 1 else primary + ".%s" % (index - 1)
                    newer = primary + ".%s" % index
                    if os.path.exists(older):
                        os.replace(older, newer)
                os.replace(temporary, primary)
                _restrict_private_file(primary)
                self.last_backup_at = int(time.time())
            except Exception:
                logger.exception("[ConversationArchive] online backup failed")
                return ""
            finally:
                if target is not None:
                    target.close()
                source.close()
                if os.path.exists(temporary):
                    try:
                        os.remove(temporary)
                    except OSError:
                        pass
        logger.info(
            "[ConversationArchive] backup completed generations=%s",
            generations,
        )
        return primary

    def _maybe_backup(self):
        interval = _env_int(
            "XIAOYOU_ARCHIVE_BACKUP_INTERVAL_SECONDS",
            86400,
            300,
            2592000,
        )
        if int(time.time()) - int(self.last_backup_at or 0) >= interval:
            return self.backup_now()
        return ""

    def _candidate_episodes(self, session_id, query):
        terms = _query_terms(query)[:8]
        time_range = _time_range(query)
        with self.lock:
            connection = self._connect()
            try:
                base_where = (
                    "session_id = ? AND excluded = 0 "
                    "AND status IN ('ready','pending','retry')"
                )
                params = [session_id]
                if time_range:
                    base_where += " AND last_message_at >= ? AND started_at <= ?"
                    params.extend([time_range[0], time_range[1]])
                recent = connection.execute(
                    "SELECT * FROM episodes WHERE %s ORDER BY last_message_at DESC LIMIT ?" % base_where,
                    params + [_env_int("XIAOYOU_EPISODIC_RECENT_CANDIDATES", 120, 20, 1000)],
                ).fetchall()

                matched = []
                if terms:
                    clauses = " OR ".join("search_text LIKE ?" for _ in terms)
                    matched = connection.execute(
                        "SELECT * FROM episodes WHERE %s AND (%s) "
                        "ORDER BY last_message_at DESC LIMIT ?" % (base_where, clauses),
                        params
                        + ["%%%s%%" % term for term in terms]
                        + [_env_int("XIAOYOU_EPISODIC_MATCH_CANDIDATES", 200, 20, 2000)],
                    ).fetchall()
            finally:
                connection.close()

        merged = {}
        for row in list(matched) + list(recent):
            merged[str(row["id"])] = dict(row)
        return list(merged.values())

    def _rank_episodes(self, candidates, query, *, mode):
        now = int(time.time())
        query_terms = set(_query_terms(query))
        generic_continue = bool(GENERIC_CONTINUE_RE.match(re.sub(r"\s+", "", query)))
        explicit_time = bool(_time_range(query))
        explicit_recall = mode == "recall" and bool(
            re.search(r"还记得|记不记得|上次|以前|之前|当时|哪天|什么时候", query)
        )
        half_life_days = _env_float("XIAOYOU_EPISODIC_HALF_LIFE_DAYS", 21.0, 1.0, 3650.0)
        ranked = []
        for candidate in candidates:
            search_terms = set(str(candidate.get("search_text") or "").split())
            semantic = _set_overlap(query_terms, search_terms)
            age_days = max(0.0, (now - int(candidate.get("last_message_at") or now)) / 86400.0)
            recency = math.exp(-math.log(2) * age_days / half_life_days)
            importance = max(0.0, min(1.0, float(candidate.get("importance") or 0.3)))
            summary_json = _parse_json(candidate.get("summary_json")) or {}
            open_text = " ".join(
                str(item.get("text") or "")
                for item in summary_json.get("open_loops", [])
                if isinstance(item, dict)
            )
            open_overlap = _set_overlap(query_terms, set(_query_terms(open_text)))
            continuity = max(
                open_overlap,
                0.9 if generic_continue and open_text else 0.72 if generic_continue else 0.0,
            )

            if mode == "recall":
                score = semantic * 0.62 + recency * 0.16 + continuity * 0.12 + importance * 0.10
            elif mode in ("project", "correction"):
                score = semantic * 0.48 + recency * 0.20 + continuity * 0.22 + importance * 0.10
            elif generic_continue:
                score = semantic * 0.20 + recency * 0.38 + continuity * 0.34 + importance * 0.08
            else:
                score = semantic * 0.50 + recency * 0.30 + continuity * 0.10 + importance * 0.10

            candidate = dict(candidate)
            candidate["score"] = score
            threshold = _env_float("XIAOYOU_EPISODIC_MIN_SCORE", 0.16, 0.0, 1.0)
            relevant = semantic > 0 or continuity > 0 or explicit_time or explicit_recall
            if (score >= threshold and relevant) or (generic_continue and continuity > 0):
                ranked.append(candidate)
        ranked.sort(key=lambda value: (float(value["score"]), int(value.get("last_message_at") or 0)), reverse=True)
        return ranked

    def _episode_messages(self, episode_id):
        with self.lock:
            connection = self._connect()
            try:
                rows = connection.execute(
                    """SELECT id, role, content, ts FROM messages
                       WHERE episode_id = ? AND excluded = 0
                         AND provider_injection_blocked = 0
                       ORDER BY ts, rowid""",
                    (episode_id,),
                ).fetchall()
            finally:
                connection.close()
        return [dict(row) for row in rows]

    def _format_episode(self, episode, span):
        started = _format_ts(episode.get("started_at"))
        ended = _format_ts(episode.get("ended_at") or episode.get("last_message_at"))
        title = _clean_text(episode.get("title"), 100) or "一段近期聊天"
        summary = _clean_text(episode.get("summary_text"), 1800)
        lines = ["[情节 %s～%s｜%s]" % (started, ended, title)]
        if summary:
            lines.append(summary)
        if span:
            lines.append("相关原始片段：")
            for message in span:
                name = "YoYo" if message.get("role") == "user" else "小悠"
                lines.append("[%s] %s：%s" % (
                    _format_ts(message.get("ts"), include_year=False),
                    name,
                    _clean_text(message.get("content"), 500),
                ))
        return "\n".join(lines)

    def _worker_loop(self):
        interval = _env_int("XIAOYOU_EPISODE_WORKER_INTERVAL", 30, 5, 3600)
        while not self.stop_event.is_set():
            try:
                self.close_stale_episodes()
                while self._summarize_one_pending():
                    pass
                self._maybe_backup()
            except Exception:
                logger.exception("[EpisodeBuilder] worker iteration failed")
            self.wake.wait(interval)
            self.wake.clear()

    def _summarize_one_pending(self):
        episode = self._claim_pending_episode()
        if not episode:
            return False
        episode_id = str(episode["id"])
        messages = self._episode_messages(episode_id)
        if not messages:
            self._finish_episode_fallback(episode, messages, reason="empty_episode")
            return True

        if not _env_bool("XIAOYOU_EPISODE_SUMMARY_ENABLED", True):
            self._finish_episode_fallback(episode, messages, reason="summary_disabled")
            return True

        prompt = self._episode_summary_prompt(episode, messages)
        payload = {
            "model": os.getenv("XIAOYOU_EPISODE_SUMMARY_MODEL", "qwen3.7-plus"),
            "messages": [
                {
                    "role": "system",
                    "content": "你只整理一段微信聊天情节，只输出合法JSON。",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.12,
            "max_tokens": 1400,
            **build_thinking_payload("XIAOYOU_EPISODE_SUMMARY"),
        }
        result = chat_completion(
            component="XiaoyouEpisodeBuilder",
            purpose="summarize_closed_episode",
            payload=payload,
            timeout=_env_int("XIAOYOU_EPISODE_SUMMARY_TIMEOUT", 45, 10, 180),
            session_id=str(episode.get("session_id") or ""),
        )
        if not result.ok:
            self._retry_episode(
                episode_id,
                getattr(result, "error_kind", "model_failed"),
            )
            return True

        data = _parse_json(result.content)
        normalized = self._normalize_episode_summary(data, messages)
        if not normalized:
            self._retry_episode(episode_id, "invalid_or_ungrounded_summary")
            return True
        self._finish_episode(episode_id, normalized)
        return True

    def _claim_pending_episode(self):
        now = int(time.time())
        with self.lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """SELECT * FROM episodes
                       WHERE excluded = 0
                         AND status IN ('pending','retry')
                         AND next_retry_at <= ?
                       ORDER BY ended_at ASC LIMIT 1""",
                    (now,),
                ).fetchone()
                if not row:
                    connection.commit()
                    return None
                connection.execute(
                    "UPDATE episodes SET status = 'summarizing', updated_at = ? WHERE id = ?",
                    (now, row["id"]),
                )
                connection.commit()
                return dict(row)
            except Exception:
                connection.rollback()
                logger.exception("[EpisodeBuilder] claim failed")
                return None
            finally:
                connection.close()

    def _episode_summary_prompt(self, episode, messages):
        max_chars = _env_int("XIAOYOU_EPISODE_SUMMARY_INPUT_MAX_CHARS", 26000, 4000, 100000)
        transcript = "\n".join(
            "[%s] %s：%s" % (
                _format_ts(message.get("ts")),
                "YoYo" if message.get("role") == "user" else "小悠",
                _context_safe_content(message.get("content", "")),
            )
            for message in messages
        )
        if len(transcript) > max_chars:
            head = int(max_chars * 0.45)
            tail = max_chars - head
            transcript = transcript[:head] + "\n…中间原文仍保存在档案中…\n" + transcript[-tail:]
        return """请把下面一段已结束的微信聊天整理成可供以后回忆的情节记录。详细保留事件顺序、当时状态、明确约定和未完成事项，但不要把普通调情、助手猜测或玩笑升级为YoYo的稳定事实。

key_user_quotes和key_assistant_quotes必须逐字来自对应角色原话。open_loops中的evidence也必须逐字存在，并标明source。不要记录密码、令牌、验证码等秘密。summary描述发生了什么，不向未来的小悠下达行为指令。

聊天时间：%s～%s
原始消息：
%s

只输出合法JSON：
{
  "title":"不超过30字的情节标题",
  "detailed_summary":"按时间顺序的详细情节摘要",
  "topics":["话题"],
  "key_user_quotes":["逐字原话"],
  "key_assistant_quotes":["逐字原话"],
  "open_loops":[{"text":"未完成事项","evidence":"逐字证据","source":"user|assistant"}],
  "entities":["相关人物、项目、物品或地点"],
  "importance":0.0
}""" % (
            _format_ts(episode.get("started_at")),
            _format_ts(episode.get("ended_at") or episode.get("last_message_at")),
            transcript,
        )

    def _normalize_episode_summary(self, data, messages):
        if not isinstance(data, dict):
            return None
        title = _safe_memory_text(data.get("title"), 100)
        detailed = _safe_memory_text(data.get("detailed_summary"), 1800)
        if not title or not detailed:
            return None

        user_texts = [str(item.get("content") or "") for item in messages if item.get("role") == "user"]
        assistant_texts = [str(item.get("content") or "") for item in messages if item.get("role") == "assistant"]
        user_quotes = _grounded_quotes(data.get("key_user_quotes"), user_texts)
        assistant_quotes = _grounded_quotes(data.get("key_assistant_quotes"), assistant_texts)
        open_loops = []
        for raw in data.get("open_loops", []) if isinstance(data.get("open_loops"), list) else []:
            if not isinstance(raw, dict):
                continue
            text = _safe_memory_text(raw.get("text"), 180)
            evidence = _clean_text(raw.get("evidence"), 220)
            source = str(raw.get("source") or "").strip().lower()
            corpus = user_texts if source == "user" else assistant_texts if source == "assistant" else []
            if text and evidence and any(evidence in value for value in corpus):
                open_loops.append({"text": text, "evidence": evidence, "source": source})
        topics = _safe_list(data.get("topics"), 8, 80)
        entities = _safe_list(data.get("entities"), 12, 80)
        try:
            importance = max(0.0, min(1.0, float(data.get("importance") or 0.3)))
        except Exception:
            importance = 0.3
        value = {
            "title": title,
            "detailed_summary": detailed,
            "topics": topics,
            "key_user_quotes": user_quotes,
            "key_assistant_quotes": assistant_quotes,
            "open_loops": open_loops[:6],
            "entities": entities,
            "importance": importance,
        }
        search_source = " ".join(
            [title, detailed]
            + topics
            + entities
            + user_quotes
            + [item["text"] for item in open_loops]
        )
        value["search_text"] = " ".join(_query_terms(search_source))
        return value

    def _finish_episode(self, episode_id, value):
        summary_text = _render_episode_summary(value)
        now = int(time.time())
        with self.lock:
            connection = self._connect()
            try:
                connection.execute(
                    """UPDATE episodes
                       SET status = 'ready', title = ?, summary_text = ?, summary_json = ?,
                           search_text = ?, importance = ?, updated_at = ?, last_error = ''
                       WHERE id = ?""",
                    (
                        value["title"],
                        summary_text,
                        json.dumps(value, ensure_ascii=False),
                        value["search_text"],
                        value["importance"],
                        now,
                        episode_id,
                    ),
                )
                connection.commit()
            finally:
                connection.close()
        logger.info("[EpisodeBuilder] episode ready id=%s", episode_id[:12])

    def _finish_episode_fallback(self, episode, messages, reason):
        user_quotes = [
            _safe_memory_text(item.get("content"), 260)
            for item in messages
            if item.get("role") == "user" and _safe_memory_text(item.get("content"), 260)
        ][-4:]
        assistant_quotes = [
            _safe_memory_text(item.get("content"), 260)
            for item in messages
            if item.get("role") == "assistant" and _safe_memory_text(item.get("content"), 260)
        ][-2:]
        detail = "；".join(user_quotes) or "该情节原文仍保存在聊天档案中。"
        value = {
            "title": "近期聊天片段",
            "detailed_summary": detail,
            "topics": [],
            "key_user_quotes": user_quotes,
            "key_assistant_quotes": assistant_quotes,
            "open_loops": [],
            "entities": [],
            "importance": 0.2,
        }
        value["search_text"] = " ".join(_query_terms(" ".join(user_quotes + assistant_quotes)))
        self._finish_episode(str(episode["id"]), value)
        logger.info(
            "[EpisodeBuilder] deterministic fallback id=%s reason=%s",
            str(episode["id"])[:12],
            reason,
        )

    def _retry_episode(self, episode_id, error):
        now = int(time.time())
        fallback_episode = None
        with self.lock:
            connection = self._connect()
            try:
                row = connection.execute(
                    "SELECT retry_count FROM episodes WHERE id = ?",
                    (episode_id,),
                ).fetchone()
                retry_count = int(row["retry_count"] or 0) + 1 if row else 1
                maximum = _env_int("XIAOYOU_EPISODE_SUMMARY_MAX_RETRIES", 4, 1, 20)
                if retry_count >= maximum:
                    episode = connection.execute(
                        "SELECT * FROM episodes WHERE id = ?",
                        (episode_id,),
                    ).fetchone()
                    fallback_episode = (
                        dict(episode) if episode else {"id": episode_id}
                    )
                else:
                    delay = min(86400, 300 * (2 ** (retry_count - 1)))
                    connection.execute(
                        """UPDATE episodes SET status = 'retry', retry_count = ?,
                           next_retry_at = ?, last_error = ?, updated_at = ? WHERE id = ?""",
                        (retry_count, now + delay, str(error or "")[:200], now, episode_id),
                    )
                    connection.commit()
            finally:
                connection.close()
        if fallback_episode is not None:
            self._finish_episode_fallback(
                fallback_episode,
                self._episode_messages(episode_id),
                reason="max_retries",
            )
            return
        logger.warning(
            "[EpisodeBuilder] summary retry id=%s error=%s",
            episode_id[:12],
            str(error or "")[:80],
        )

    def _close_episode(self, connection, episode_id, *, ended_at, reason):
        connection.execute(
            """UPDATE episodes
               SET status = 'pending', ended_at = ?, close_reason = ?,
                   next_retry_at = 0, updated_at = ? WHERE id = ?""",
            (ended_at, str(reason or "")[:80], int(time.time()), episode_id),
        )

    def _should_roll_episode(self, episode, ts):
        gap = ts - int(episode["last_message_at"] or ts)
        duration = ts - int(episode["started_at"] or ts)
        if gap >= _env_int("XIAOYOU_EPISODE_IDLE_SECONDS", 2700, 300, 86400):
            return True
        if int(episode["message_count"] or 0) >= _env_int(
            "XIAOYOU_EPISODE_MAX_MESSAGES", 160, 20, 2000
        ):
            return True
        if duration >= _env_int("XIAOYOU_EPISODE_MAX_SECONDS", 21600, 1800, 172800):
            return True
        previous_day = datetime.fromtimestamp(int(episode["last_message_at"] or ts)).date()
        current_day = datetime.fromtimestamp(ts).date()
        return previous_day != current_day and gap >= 300

    def _roll_reason(self, episode, ts):
        gap = ts - int(episode["last_message_at"] or ts)
        if gap >= _env_int("XIAOYOU_EPISODE_IDLE_SECONDS", 2700, 300, 86400):
            return "idle_gap"
        if int(episode["message_count"] or 0) >= _env_int(
            "XIAOYOU_EPISODE_MAX_MESSAGES", 160, 20, 2000
        ):
            return "message_limit"
        return "time_boundary"

    def _initialize(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        _restrict_private_file(os.path.dirname(self.path) or ".", directory=True)
        restored = 0
        with self.lock:
            connection = self._connect()
            try:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS episodes (
                        id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        started_at INTEGER NOT NULL,
                        last_message_at INTEGER NOT NULL,
                        ended_at INTEGER,
                        status TEXT NOT NULL DEFAULT 'open',
                        message_count INTEGER NOT NULL DEFAULT 0,
                        close_reason TEXT NOT NULL DEFAULT '',
                        title TEXT NOT NULL DEFAULT '',
                        summary_text TEXT NOT NULL DEFAULT '',
                        summary_json TEXT NOT NULL DEFAULT '',
                        search_text TEXT NOT NULL DEFAULT '',
                        importance REAL NOT NULL DEFAULT 0.3,
                        retry_count INTEGER NOT NULL DEFAULT 0,
                        next_retry_at INTEGER NOT NULL DEFAULT 0,
                        last_error TEXT NOT NULL DEFAULT '',
                        excluded INTEGER NOT NULL DEFAULT 0,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_episode_session_time
                        ON episodes(session_id, last_message_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_episode_status_retry
                        ON episodes(status, next_retry_at);

                    CREATE TABLE IF NOT EXISTS messages (
                        id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        episode_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        ts INTEGER NOT NULL,
                        source TEXT NOT NULL DEFAULT '',
                        trace_id TEXT NOT NULL DEFAULT '',
                        input_id TEXT NOT NULL DEFAULT '',
                        action_id TEXT NOT NULL DEFAULT '',
                        provider_injection_blocked INTEGER NOT NULL DEFAULT 0,
                        block_reason TEXT NOT NULL DEFAULT '',
                        excluded INTEGER NOT NULL DEFAULT 0,
                        created_at INTEGER NOT NULL,
                        FOREIGN KEY(episode_id) REFERENCES episodes(id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_message_session_time
                        ON messages(session_id, ts DESC);
                    CREATE INDEX IF NOT EXISTS idx_message_episode_time
                        ON messages(episode_id, ts ASC);
                    """
                )
                # Versions before continuity recovery treated one rejected
                # combined prompt as proof that every ActiveWindow message was
                # unsafe.  Restore those over-broad batch blocks.  Rows blocked
                # because the current message itself failed inspection keep
                # their distinct reason and remain isolated.
                restored = connection.execute(
                    """UPDATE messages
                       SET provider_injection_blocked = 0, block_reason = ''
                       WHERE provider_injection_blocked = 1
                         AND block_reason = 'chat_data_inspection_failed'"""
                ).rowcount
                connection.commit()
            finally:
                connection.close()
        if restored:
            logger.info(
                "[ConversationArchive] restored over-broad legacy context blocks count=%s",
                int(restored),
            )
        _restrict_private_file(self.path)

    def _recover_interrupted_jobs(self):
        with self.lock:
            connection = self._connect()
            try:
                connection.execute(
                    """UPDATE episodes SET status = 'retry', next_retry_at = 0,
                       last_error = 'process_restarted', updated_at = ?
                       WHERE status = 'summarizing'""",
                    (int(time.time()),),
                )
                connection.commit()
            finally:
                connection.close()

    def _connect(self):
        connection = sqlite3.connect(
            self.path,
            timeout=_env_float("XIAOYOU_ARCHIVE_SQLITE_TIMEOUT", 8.0, 1.0, 60.0),
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=8000")
        return connection


def _select_relevant_span(messages, query):
    if not messages:
        return []
    query_terms = set(_query_terms(query))
    best_index = len(messages) - 1
    best_score = 0.0
    for index, message in enumerate(messages):
        score = _set_overlap(query_terms, set(_query_terms(message.get("content", ""))))
        if score > best_score:
            best_score = score
            best_index = index
    radius_before = _env_int("XIAOYOU_EPISODIC_SPAN_BEFORE", 3, 1, 10)
    radius_after = _env_int("XIAOYOU_EPISODIC_SPAN_AFTER", 4, 1, 10)
    start = max(0, best_index - radius_before)
    end = min(len(messages), best_index + radius_after + 1)
    return messages[start:end]


def _query_terms(value):
    text = re.sub(r"\s+", "", str(value or "").lower())
    text = re.sub(r"[^0-9a-z\u3400-\u4dbf\u4e00-\u9fff]+", "", text)
    stop = {
        "我们", "你们", "这个", "那个", "一下", "什么", "怎么", "可以",
        "还是", "就是", "已经", "现在", "然后", "继续", "记得", "之前",
    }
    terms = []
    for token in re.findall(r"[a-z0-9]{2,}|[\u3400-\u4dbf\u4e00-\u9fff]{2,}", text):
        if token in stop:
            continue
        if re.fullmatch(r"[\u3400-\u4dbf\u4e00-\u9fff]+", token):
            if len(token) <= 3:
                terms.append(token)
            else:
                terms.extend(token[index:index + 2] for index in range(len(token) - 1))
        else:
            terms.append(token)
    return list(dict.fromkeys(term for term in terms if term and term not in stop))


def _set_overlap(left, right):
    if not left or not right:
        return 0.0
    intersection = len(set(left) & set(right))
    return intersection / float(max(1, min(len(set(left)), len(set(right)))))


def _time_range(query, now=None):
    now_dt = datetime.fromtimestamp(int(now or time.time()))
    compact = re.sub(r"\s+", "", str(query or ""))
    if "刚才" in compact or "刚刚" in compact:
        return int((now_dt - timedelta(hours=6)).timestamp()), int(now_dt.timestamp())
    if "今天" in compact:
        start = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        return int(start.timestamp()), int((start + timedelta(days=1)).timestamp() - 1)
    if "昨天" in compact:
        end = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        start = end - timedelta(days=1)
        return int(start.timestamp()), int(end.timestamp() - 1)
    if "前天" in compact:
        end = now_dt.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        start = end - timedelta(days=1)
        return int(start.timestamp()), int(end.timestamp() - 1)
    if "前几天" in compact or "最近几天" in compact:
        return int((now_dt - timedelta(days=14)).timestamp()), int(now_dt.timestamp())
    if "这周" in compact or "本周" in compact:
        start = (now_dt - timedelta(days=now_dt.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return int(start.timestamp()), int(now_dt.timestamp())
    if "上周" in compact:
        this_week = (now_dt - timedelta(days=now_dt.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        start = this_week - timedelta(days=7)
        return int(start.timestamp()), int(this_week.timestamp() - 1)
    if "这个月" in compact or "本月" in compact:
        start = now_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return int(start.timestamp()), int(now_dt.timestamp())
    if "上个月" in compact:
        current = now_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        previous_end = current - timedelta(seconds=1)
        start = previous_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return int(start.timestamp()), int(current.timestamp() - 1)
    if "去年" in compact:
        start = datetime(now_dt.year - 1, 1, 1)
        end = datetime(now_dt.year, 1, 1)
        return int(start.timestamp()), int(end.timestamp() - 1)
    days_ago = re.search(r"(\d{1,3})天前", compact)
    if days_ago:
        target = now_dt - timedelta(days=int(days_ago.group(1)))
        start = target.replace(hour=0, minute=0, second=0, microsecond=0)
        return int(start.timestamp()), int((start + timedelta(days=1)).timestamp() - 1)
    match = re.search(r"(20\d{2})年(?:(\d{1,2})月)?(?:(\d{1,2})日)?", compact)
    if match:
        year = int(match.group(1))
        month = int(match.group(2) or 1)
        day = int(match.group(3) or 1)
        start = datetime(year, month, day)
        if match.group(3):
            end = start + timedelta(days=1)
        elif match.group(2):
            end = datetime(year + (1 if month == 12 else 0), 1 if month == 12 else month + 1, 1)
        else:
            end = datetime(year + 1, 1, 1)
        return int(start.timestamp()), int(end.timestamp() - 1)
    month_day = re.search(r"(?<!\d)(\d{1,2})月(\d{1,2})日", compact)
    if month_day:
        month = int(month_day.group(1))
        day = int(month_day.group(2))
        try:
            start = datetime(now_dt.year, month, day)
        except ValueError:
            return None
        if start.timestamp() > now_dt.timestamp() + 86400:
            start = datetime(now_dt.year - 1, month, day)
        end = start + timedelta(days=1)
        return int(start.timestamp()), int(end.timestamp() - 1)
    return None


def _render_episode_summary(value):
    parts = [value.get("detailed_summary", "")]
    if value.get("key_user_quotes"):
        parts.append("YoYo关键原话：" + "｜".join(value["key_user_quotes"]))
    if value.get("open_loops"):
        parts.append("未完事项：" + "；".join(
            item.get("text", "") for item in value["open_loops"] if item.get("text")
        ))
    return "\n".join(part for part in parts if part)


def _grounded_quotes(values, corpus):
    if not isinstance(values, list):
        return []
    result = []
    for value in values[:12]:
        quote = _safe_memory_text(value, 300)
        if quote and any(quote in source for source in corpus):
            result.append(quote)
    return list(dict.fromkeys(result))[:6]


def _safe_list(values, limit, item_limit):
    if not isinstance(values, list):
        return []
    result = []
    for value in values:
        text = _safe_memory_text(value, item_limit)
        if text:
            result.append(text)
    return list(dict.fromkeys(result))[:limit]


def _safe_memory_text(value, limit):
    text = _clean_text(value, limit)
    return "" if SENSITIVE_RE.search(text) else text


def _context_safe_content(value):
    text = str(value or "")
    if SENSITIVE_RE.search(text):
        return "[该消息含密码、密钥或其他敏感信息，原文仅保存在本地档案中]"
    return text


def _clean_text(value, limit):
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _format_ts(value, include_year=True):
    try:
        moment = datetime.fromtimestamp(int(value or 0))
    except Exception:
        return "时间未知"
    return moment.strftime("%Y-%m-%d %H:%M" if include_year else "%m-%d %H:%M")


def _trim_sections(sections, budget):
    selected = []
    used = 0
    for section in sections:
        section = str(section or "").strip()
        if not section:
            continue
        size = len(section) + (2 if selected else 0)
        if used + size <= budget:
            selected.append(section)
            used += size
            continue
        remaining = budget - used - (2 if selected else 0)
        if remaining >= 300:
            selected.append(section[:remaining].rstrip() + "…")
        break
    return "\n\n".join(selected)


def _parse_json(raw):
    text = str(raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except Exception:
            return None


def _env_bool(key, default):
    raw = os.getenv(key, "true" if default else "false")
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _env_int(key, default, minimum, maximum):
    try:
        value = int(os.getenv(key, str(default)))
    except Exception:
        value = int(default)
    return max(minimum, min(maximum, value))


def _env_float(key, default, minimum, maximum):
    try:
        value = float(os.getenv(key, str(default)))
    except Exception:
        value = float(default)
    return max(minimum, min(maximum, value))


def _restrict_private_file(path, directory=False):
    try:
        os.chmod(path, 0o700 if directory else 0o600)
    except OSError:
        # Some bind-mount/filesystem combinations do not expose POSIX modes.
        pass


_SERVICE = None
_SERVICE_LOCK = threading.Lock()


def get_conversation_archive_service():
    global _SERVICE
    if _SERVICE is None:
        with _SERVICE_LOCK:
            if _SERVICE is None:
                _SERVICE = ConversationArchiveService()
    return _SERVICE
