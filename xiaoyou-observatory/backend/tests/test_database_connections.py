from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

import app.database as database_module
from app.database import Database


def test_connections_close_after_success_and_rollback(tmp_path, monkeypatch):
    opened: list[int] = []
    closed: list[int] = []
    original_connect = database_module.sqlite3.connect

    class TrackingConnection(sqlite3.Connection):
        def close(self) -> None:
            closed.append(id(self))
            super().close()

    def tracking_connect(*args, **kwargs):
        kwargs["factory"] = TrackingConnection
        connection = original_connect(*args, **kwargs)
        opened.append(id(connection))
        return connection

    monkeypatch.setattr(database_module.sqlite3, "connect", tracking_connect)

    database = Database(tmp_path / "observatory.db")
    database.initialize()
    assert database.admin_count() == 0
    database.create_admin("yoyo", "hash", "secret", [])

    with pytest.raises(sqlite3.IntegrityError):
        database.create_admin("yoyo", "another-hash", "another-secret", [])

    assert opened
    assert closed == opened


def test_connection_closes_when_sqlite_setup_fails(tmp_path, monkeypatch):
    closed: list[int] = []
    original_connect = database_module.sqlite3.connect

    class FailingPragmaConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if str(sql).startswith("PRAGMA journal_mode"):
                raise sqlite3.OperationalError("simulated pragma failure")
            return super().execute(sql, parameters)

        def close(self) -> None:
            closed.append(id(self))
            super().close()

    def failing_connect(*args, **kwargs):
        kwargs["factory"] = FailingPragmaConnection
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(database_module.sqlite3, "connect", failing_connect)

    with pytest.raises(sqlite3.OperationalError, match="simulated pragma failure"):
        Database(tmp_path / "observatory.db").admin_count()

    assert len(closed) == 1


@pytest.mark.skipif(
    not Path("/proc/self/fd").is_dir(),
    reason="Linux /proc file-descriptor accounting is required",
)
def test_repeated_database_reads_do_not_leak_file_descriptors(tmp_path):
    database = Database(tmp_path / "observatory.db")
    database.initialize()
    before = len(os.listdir("/proc/self/fd"))

    for _ in range(500):
        assert database.admin_count() == 0
        assert database.total_token_usage() == 0

    after = len(os.listdir("/proc/self/fd"))
    assert after <= before + 2
