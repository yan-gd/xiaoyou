from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


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

                CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);
                CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_log(created_at DESC);
                """
            )

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
