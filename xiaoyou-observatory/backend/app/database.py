from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(slots=True)
class AdminRecord:
    id: int
    username: str
    password_hash: str
    totp_secret_encrypted: str


@dataclass(slots=True)
class SessionRecord:
    token_hash: str
    admin_id: int
    username: str
    csrf_token: str
    expires_at: int
    role: str = "admin"


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS admins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    totp_secret_encrypted TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS recovery_codes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    admin_id INTEGER NOT NULL REFERENCES admins(id) ON DELETE CASCADE,
                    code_hash TEXT NOT NULL UNIQUE,
                    used_at INTEGER,
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    admin_id INTEGER NOT NULL REFERENCES admins(id) ON DELETE CASCADE,
                    csrf_token TEXT NOT NULL,
                    ip_address TEXT NOT NULL,
                    user_agent TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    last_seen_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    admin_id INTEGER REFERENCES admins(id) ON DELETE SET NULL,
                    action TEXT NOT NULL,
                    result TEXT NOT NULL,
                    ip_address TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS token_usage_events (
                    event_id TEXT PRIMARY KEY,
                    component TEXT NOT NULL,
                    total_tokens INTEGER NOT NULL CHECK(total_tokens >= 0),
                    observed_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS persistent_counters (
                    name TEXT PRIMARY KEY,
                    value INTEGER NOT NULL CHECK(value >= 0),
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS metric_snapshots (
                    observed_at INTEGER PRIMARY KEY,
                    cpu_percent REAL NOT NULL DEFAULT 0,
                    memory_percent REAL NOT NULL DEFAULT 0,
                    host_cpu_percent REAL NOT NULL DEFAULT 0,
                    host_memory_percent REAL NOT NULL DEFAULT 0,
                    container_cpu_percent REAL NOT NULL DEFAULT 0,
                    container_memory_percent REAL NOT NULL DEFAULT 0,
                    recent_errors INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    today_tokens INTEGER NOT NULL DEFAULT 0,
                    running INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);
                CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_log(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_token_usage_observed_at ON token_usage_events(observed_at DESC);
                CREATE INDEX IF NOT EXISTS idx_metric_snapshots_observed_at ON metric_snapshots(observed_at DESC);
                """
            )

            metric_columns = {
                str(row["name"])
                for row in db.execute("PRAGMA table_info(metric_snapshots)").fetchall()
            }
            newly_added_metric_columns: set[str] = set()
            for name in (
                "host_cpu_percent",
                "host_memory_percent",
                "container_cpu_percent",
                "container_memory_percent",
            ):
                if name not in metric_columns:
                    db.execute(
                        f"ALTER TABLE metric_snapshots ADD COLUMN {name} REAL NOT NULL DEFAULT 0"
                    )
                    newly_added_metric_columns.add(name)

            # Older releases stored only the container values in cpu_percent /
            # memory_percent. Preserve those points as Xiaoyou-container history
            # while host history starts from the first new sample.
            if "container_cpu_percent" in newly_added_metric_columns:
                db.execute(
                    "UPDATE metric_snapshots SET container_cpu_percent = cpu_percent"
                )
            if "container_memory_percent" in newly_added_metric_columns:
                db.execute(
                    "UPDATE metric_snapshots SET container_memory_percent = memory_percent"
                )

            # Migrate older installations without ever allowing the cumulative
            # total to move backwards. Existing event rows and historical
            # snapshots are both valid recovery sources for the initial value.
            event_total_row = db.execute(
                "SELECT COALESCE(SUM(total_tokens), 0) AS total FROM token_usage_events"
            ).fetchone()
            snapshot_total_row = db.execute(
                "SELECT COALESCE(MAX(total_tokens), 0) AS total FROM metric_snapshots"
            ).fetchone()
            recovered_total = max(
                int(event_total_row["total"] or 0),
                int(snapshot_total_row["total"] or 0),
            )
            db.execute(
                """INSERT INTO persistent_counters(name, value, updated_at)
                   VALUES ('total_tokens', ?, ?)
                   ON CONFLICT(name) DO UPDATE SET
                       value = MAX(persistent_counters.value, excluded.value),
                       updated_at = CASE
                           WHEN excluded.value > persistent_counters.value THEN excluded.updated_at
                           ELSE persistent_counters.updated_at
                       END""",
                (recovered_total, int(time.time())),
            )

    def record_token_usage(
        self,
        event_id: str,
        component: str,
        total_tokens: int,
        observed_at: int | None = None,
    ) -> bool:
        return self.record_token_usage_batch(
            [(event_id, component, total_tokens, observed_at)]
        ) == 1

    def record_token_usage_batch(
        self,
        events: Iterable[tuple[str, str, int, int | None]],
    ) -> int:
        normalized: list[tuple[str, str, int, int]] = []
        seen_event_ids: set[str] = set()
        fallback_now = int(time.time())
        for event_id, component, total_tokens, observed_at in events:
            safe_event_id = str(event_id or "").strip()[:160]
            safe_component = str(component or "unknown").strip()[:80] or "unknown"
            try:
                safe_total = int(total_tokens or 0)
                safe_observed_at = int(observed_at or fallback_now)
            except (TypeError, ValueError, OverflowError):
                continue
            if (
                not safe_event_id
                or safe_event_id in seen_event_ids
                or safe_total <= 0
                or safe_total > 999_999_999_999
            ):
                continue
            seen_event_ids.add(safe_event_id)
            normalized.append(
                (safe_event_id, safe_component, safe_total, safe_observed_at)
            )

        if not normalized:
            return 0

        inserted_count = 0
        inserted_total = 0
        with self._connect() as db:
            # A snapshot can contain hundreds of already-seen log events. Keep
            # all dedupe inserts and the cumulative-counter update in one
            # connection and one transaction rather than reopening SQLite for
            # every line in the Docker log tail.
            for event in normalized:
                cursor = db.execute(
                    """INSERT OR IGNORE INTO token_usage_events(
                        event_id, component, total_tokens, observed_at
                    ) VALUES (?, ?, ?, ?)""",
                    event,
                )
                if cursor.rowcount == 1:
                    inserted_count += 1
                    inserted_total += event[2]
            if inserted_total:
                db.execute(
                    """INSERT INTO persistent_counters(name, value, updated_at)
                       VALUES ('total_tokens', ?, ?)
                       ON CONFLICT(name) DO UPDATE SET
                           value = persistent_counters.value + excluded.value,
                           updated_at = excluded.updated_at""",
                    (inserted_total, fallback_now),
                )
        return inserted_count

    def total_token_usage(self) -> int:
        with self._connect() as db:
            row = db.execute(
                "SELECT value FROM persistent_counters WHERE name = 'total_tokens'"
            ).fetchone()
            if row is not None:
                return max(0, int(row["value"] or 0))

            # Defensive fallback for a partially migrated database. initialize()
            # normally creates this row, but callers still receive the old sum
            # instead of a misleading zero if startup migration was interrupted.
            fallback = db.execute(
                "SELECT COALESCE(SUM(total_tokens), 0) AS total FROM token_usage_events"
            ).fetchone()
            return max(0, int(fallback["total"] or 0))

    def today_token_usage(self, now: int | None = None) -> int:
        observed_at = int(now or time.time())
        start = int(
            time.mktime(
                time.localtime(observed_at)[:3] + (0, 0, 0, 0, 0, -1)
            )
        )
        with self._connect() as db:
            row = db.execute(
                "SELECT COALESCE(SUM(total_tokens), 0) AS total FROM token_usage_events WHERE observed_at >= ?",
                (start,),
            ).fetchone()
        return int(row["total"] or 0)

    def record_metric_snapshot(
        self,
        *,
        observed_at: int,
        host_cpu_percent: float,
        host_memory_percent: float,
        container_cpu_percent: float,
        container_memory_percent: float,
        recent_errors: int,
        total_tokens: int,
        today_tokens: int,
        running: bool,
    ) -> None:
        bucket = max(0, int(observed_at)) // 60 * 60
        safe_host_cpu = max(0.0, float(host_cpu_percent or 0.0))
        safe_host_memory = max(0.0, float(host_memory_percent or 0.0))
        safe_container_cpu = max(0.0, float(container_cpu_percent or 0.0))
        safe_container_memory = max(0.0, float(container_memory_percent or 0.0))
        with self._connect() as db:
            db.execute(
                """INSERT INTO metric_snapshots(
                    observed_at, cpu_percent, memory_percent,
                    host_cpu_percent, host_memory_percent,
                    container_cpu_percent, container_memory_percent,
                    recent_errors, total_tokens, today_tokens, running
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(observed_at) DO UPDATE SET
                    cpu_percent=excluded.cpu_percent,
                    memory_percent=excluded.memory_percent,
                    host_cpu_percent=excluded.host_cpu_percent,
                    host_memory_percent=excluded.host_memory_percent,
                    container_cpu_percent=excluded.container_cpu_percent,
                    container_memory_percent=excluded.container_memory_percent,
                    recent_errors=excluded.recent_errors,
                    total_tokens=excluded.total_tokens,
                    today_tokens=excluded.today_tokens,
                    running=excluded.running""",
                (
                    bucket,
                    safe_container_cpu,
                    safe_container_memory,
                    safe_host_cpu,
                    safe_host_memory,
                    safe_container_cpu,
                    safe_container_memory,
                    max(0, int(recent_errors or 0)),
                    max(0, int(total_tokens or 0)),
                    max(0, int(today_tokens or 0)),
                    1 if running else 0,
                ),
            )

    def metric_history(self, hours: int = 24) -> list[dict]:
        safe_hours = max(1, min(int(hours), 168))
        since = int(time.time()) - safe_hours * 3600
        with self._connect() as db:
            rows = db.execute(
                """SELECT observed_at, host_cpu_percent, host_memory_percent,
                          container_cpu_percent, container_memory_percent,
                          recent_errors, total_tokens, today_tokens, running
                   FROM metric_snapshots
                   WHERE observed_at >= ?
                   ORDER BY observed_at ASC""",
                (since,),
            ).fetchall()
        return [dict(row) for row in rows]

    def prune_metric_history(self, retention_hours: int = 168) -> int:
        safe_hours = max(24, min(int(retention_hours), 2160))
        cutoff = int(time.time()) - safe_hours * 3600
        with self._connect() as db:
            cursor = db.execute(
                "DELETE FROM metric_snapshots WHERE observed_at < ?",
                (cutoff,),
            )
            return max(0, int(cursor.rowcount or 0))

    def admin_count(self) -> int:
        with self._connect() as db:
            row = db.execute("SELECT COUNT(*) AS count FROM admins").fetchone()
            return int(row["count"])

    def create_admin(
        self,
        username: str,
        password_hash: str,
        totp_secret_encrypted: str,
        recovery_code_hashes: list[str],
    ) -> int:
        now = int(time.time())
        with self._connect() as db:
            cursor = db.execute(
                "INSERT INTO admins(username, password_hash, totp_secret_encrypted, created_at) VALUES (?, ?, ?, ?)",
                (username, password_hash, totp_secret_encrypted, now),
            )
            admin_id = int(cursor.lastrowid)
            db.executemany(
                "INSERT INTO recovery_codes(admin_id, code_hash, created_at) VALUES (?, ?, ?)",
                [(admin_id, value, now) for value in recovery_code_hashes],
            )
            return admin_id

    def get_admin(self, username: str) -> AdminRecord | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT id, username, password_hash, totp_secret_encrypted FROM admins WHERE username = ?",
                (username,),
            ).fetchone()
        if not row:
            return None
        return AdminRecord(
            id=int(row["id"]),
            username=str(row["username"]),
            password_hash=str(row["password_hash"]),
            totp_secret_encrypted=str(row["totp_secret_encrypted"]),
        )

    def use_recovery_code(self, admin_id: int, code_hash: str) -> bool:
        now = int(time.time())
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE recovery_codes SET used_at = ? WHERE admin_id = ? AND code_hash = ? AND used_at IS NULL",
                (now, admin_id, code_hash),
            )
            return cursor.rowcount == 1

    def create_session(
        self,
        token_hash: str,
        admin_id: int,
        csrf_token: str,
        ip_address: str,
        user_agent: str,
        expires_at: int,
    ) -> None:
        now = int(time.time())
        with self._connect() as db:
            db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            db.execute(
                """INSERT INTO sessions(
                    token_hash, admin_id, csrf_token, ip_address, user_agent,
                    created_at, last_seen_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    token_hash,
                    admin_id,
                    csrf_token,
                    ip_address[:128],
                    user_agent[:300],
                    now,
                    now,
                    expires_at,
                ),
            )

    def get_session(self, token_hash: str) -> SessionRecord | None:
        now = int(time.time())
        with self._connect() as db:
            row = db.execute(
                """SELECT s.token_hash, s.admin_id, a.username, s.csrf_token, s.expires_at
                   FROM sessions s JOIN admins a ON a.id = s.admin_id
                   WHERE s.token_hash = ? AND s.expires_at > ?""",
                (token_hash, now),
            ).fetchone()
            if row:
                db.execute(
                    "UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?",
                    (now, token_hash),
                )
        if not row:
            return None
        return SessionRecord(
            token_hash=str(row["token_hash"]),
            admin_id=int(row["admin_id"]),
            username=str(row["username"]),
            csrf_token=str(row["csrf_token"]),
            expires_at=int(row["expires_at"]),
            role="admin",
        )

    def delete_session(self, token_hash: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def delete_admin_sessions(self, admin_id: int) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM sessions WHERE admin_id = ?", (admin_id,))

    def add_audit(
        self,
        admin_id: int | None,
        action: str,
        result: str,
        ip_address: str,
        detail: str = "",
    ) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO audit_log(admin_id, action, result, ip_address, detail, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    admin_id,
                    action[:80],
                    result[:40],
                    ip_address[:128],
                    detail[:500],
                    int(time.time()),
                ),
            )

    def recent_audit(self, limit: int = 12) -> list[dict]:
        limit = max(1, min(int(limit), 50))
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, action, result, created_at, ip_address FROM audit_log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
