import importlib.util
import json
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "plugins"
    / "xiaoyou_common"
    / "long_memory_store.py"
)
SPEC = importlib.util.spec_from_file_location("long_memory_store_under_test", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
LongMemoryStore = MODULE.LongMemoryStore


def _candidate(
    content,
    *,
    key="preference.color",
    memory_type="semantic",
    subject="user",
    source_role="user",
    **temporal,
):
    return {
        "memory_key": key,
        "category": "durable_preference",
        "memory_type": memory_type,
        "subject": subject,
        "source_role": source_role,
        "content": content,
        "confidence": 0.95,
        "importance": 0.9,
        "source_turn_sequence": 3,
        "source_input_id": "input-3",
        "source_session_id": "yoyo",
        **temporal,
    }


def test_sqlite_upsert_replaces_one_stable_key_and_persists(tmp_path):
    path = tmp_path / "data" / "long_memory" / "memories.db"
    store = LongMemoryStore(path)

    first = store.upsert(
        user_id="yoyo",
        candidate=_candidate("YoYo喜欢红色。"),
        embedding=[1.0, 0.0],
        embedding_model="test:2",
    )
    second = store.upsert(
        user_id="yoyo",
        candidate=_candidate("YoYo现在喜欢蓝色。"),
        embedding=[0.0, 1.0],
        embedding_model="test:2",
    )

    assert first["ok"] is True
    assert second["ok"] is True
    assert second["operation"] == "update"
    assert second["memory_id"] == first["memory_id"]

    reopened = LongMemoryStore(path)
    memories = reopened.list_memories(user_id="yoyo")
    assert reopened.count(user_id="yoyo") == 1
    assert memories[0]["content"] == "YoYo现在喜欢蓝色。"
    assert memories[0]["embedding"] == [0.0, 1.0]
    assert memories[0]["source_turn_sequence"] == 3


def test_type_filter_is_applied_by_sqlite_before_semantic_ranking(tmp_path):
    store = LongMemoryStore(tmp_path / "memories.db")
    store.upsert(
        user_id="yoyo",
        candidate=_candidate("偏好", key="preference.style", memory_type="semantic"),
    )
    store.upsert(
        user_id="yoyo",
        candidate=_candidate("项目", key="project.xiaoyou", memory_type="project"),
    )

    memories = store.list_memories(
        user_id="yoyo",
        allowed_types=("project",),
    )

    assert [memory["memory_key"] for memory in memories] == ["project.xiaoyou"]


def test_subject_and_event_time_survive_sqlite_reopen(tmp_path):
    path = tmp_path / "memories.db"
    store = LongMemoryStore(path)
    store.upsert(
        user_id="yoyo",
        candidate=_candidate(
            "双方约好在困难时先听完对方再表达判断。",
            key="relationship.agreement.listen_first",
            memory_type="relationship",
            subject="relationship",
            source_role="joint",
            occurred_at="2026-07-22T22:15:00+08:00",
            temporal_precision="exact",
            valid_from="2026-07-22",
            timezone="Asia/Shanghai",
            time_evidence="昨天晚上十点十五分",
        ),
    )

    memory = LongMemoryStore(path).list_memories(user_id="yoyo")[0]

    assert memory["subject"] == "relationship"
    assert memory["source_role"] == "joint"
    assert memory["occurred_at"] == "2026-07-22T22:15:00+08:00"
    assert memory["temporal_precision"] == "exact"
    assert memory["valid_from"] == "2026-07-22"
    assert memory["timezone"] == "Asia/Shanghai"
    assert memory["time_evidence"] == "昨天晚上十点十五分"
    assert memory["created_at"] > 0
    assert memory["updated_at"] >= memory["created_at"]


def test_existing_v1_database_is_migrated_without_losing_memories(tmp_path):
    import sqlite3

    path = tmp_path / "memories.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE memories (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            memory_key TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT '',
            memory_type TEXT NOT NULL DEFAULT 'legacy',
            content TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 0,
            importance REAL NOT NULL DEFAULT 0,
            source_turn_sequence INTEGER NOT NULL DEFAULT 0,
            source_input_id TEXT NOT NULL DEFAULT '',
            source_session_id TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            embedding TEXT,
            embedding_model TEXT NOT NULL DEFAULT '',
            UNIQUE(user_id, memory_key)
        );
        INSERT INTO memories (
            id, user_id, memory_key, content, created_at, updated_at
        ) VALUES (
            'old-1', 'yoyo', 'user.preference.color', 'YoYo 喜欢蓝色。', 1, 1
        );
        """
    )
    connection.commit()
    connection.close()

    memory = LongMemoryStore(path).list_memories(user_id="yoyo")[0]

    assert memory["content"] == "YoYo 喜欢蓝色。"
    assert memory["subject"] == "user"
    assert memory["source_role"] == "user"
    assert memory["occurred_at"] == ""


def test_older_turn_cannot_overwrite_newer_fact_for_the_same_session(tmp_path):
    store = LongMemoryStore(tmp_path / "memories.db")
    newer = _candidate("YoYo现在喜欢蓝色。")
    newer["source_turn_sequence"] = 19
    older = _candidate("YoYo喜欢红色。")
    older["source_turn_sequence"] = 18

    store.upsert(user_id="yoyo", candidate=newer)
    result = store.upsert(user_id="yoyo", candidate=older)

    assert result["ok"] is True
    assert result["operation"] == "stale"
    assert store.list_memories(user_id="yoyo")[0]["content"] == "YoYo现在喜欢蓝色。"


def test_governance_ledger_is_imported_once_without_rewriting_existing_rows(tmp_path):
    path = tmp_path / "memories.db"
    ledger = tmp_path / "memory_governance.json"
    ledger.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        **_candidate("有效事实"),
                        "status": "written",
                        "updated_at": 100,
                    },
                    {
                        **_candidate("失败事实", key="preference.failed"),
                        "status": "failed",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    store = LongMemoryStore(path)

    assert store.import_governance_ledger(ledger, user_id="yoyo") == 1
    before = store.list_memories(user_id="yoyo")
    assert store.import_governance_ledger(ledger, user_id="yoyo") == 0
    after = store.list_memories(user_id="yoyo")

    assert [memory["content"] for memory in before] == ["有效事实"]
    assert before == after
