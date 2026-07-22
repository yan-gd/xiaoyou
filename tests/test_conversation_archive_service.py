import sqlite3
import sys
import time
import types
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
class _Logger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def exception(self, *args, **kwargs):
        return None


common_module = types.ModuleType("common")
common_log_module = types.ModuleType("common.log")
common_log_module.logger = _Logger()
plugins_module = types.ModuleType("plugins")
plugins_common_module = types.ModuleType("plugins.xiaoyou_common")
gateway_module = types.ModuleType("plugins.xiaoyou_common.model_gateway")
runtime_paths_module = types.ModuleType("plugins.xiaoyou_common.runtime_paths")
thinking_module = types.ModuleType("plugins.xiaoyou_common.thinking_config")
gateway_module.chat_completion = lambda **kwargs: None
runtime_paths_module.runtime_path = (
    lambda namespace, filename, **kwargs: str(ROOT / "data" / namespace / filename)
)
thinking_module.build_thinking_payload = lambda prefix: {"enable_thinking": False}
_stub_names = (
    "common",
    "common.log",
    "plugins",
    "plugins.xiaoyou_common",
    "plugins.xiaoyou_common.model_gateway",
    "plugins.xiaoyou_common.runtime_paths",
    "plugins.xiaoyou_common.thinking_config",
)
_previous_modules = {name: sys.modules.get(name) for name in _stub_names}
sys.modules["common"] = common_module
sys.modules["common.log"] = common_log_module
sys.modules["plugins"] = plugins_module
sys.modules["plugins.xiaoyou_common"] = plugins_common_module
sys.modules["plugins.xiaoyou_common.model_gateway"] = gateway_module
sys.modules["plugins.xiaoyou_common.runtime_paths"] = runtime_paths_module
sys.modules["plugins.xiaoyou_common.thinking_config"] = thinking_module

SPEC = importlib.util.spec_from_file_location(
    "conversation_archive_service_under_test",
    ROOT / "plugins" / "xiaoyou_common" / "conversation_archive_service.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
ConversationArchiveService = MODULE.ConversationArchiveService
time_range = MODULE._time_range
for _name, _previous in _previous_modules.items():
    if _previous is None:
        sys.modules.pop(_name, None)
    else:
        sys.modules[_name] = _previous


def _rows(path, sql, args=()):
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(sql, args).fetchall()]
    finally:
        connection.close()


def test_archive_preserves_exact_roles_and_uses_wall_clock_active_window(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("XIAOYOU_CONVERSATION_ARCHIVE_ENABLED", "true")
    monkeypatch.setenv("XIAOYOU_ACTIVE_WINDOW_HOURS", "6")
    path = tmp_path / "conversation.db"
    service = ConversationArchiveService(path, start_worker=False)
    now = int(time.time())

    service.record_message(
        message_id="too-old",
        session_id="yoyo",
        role="user",
        content="七小时前的消息",
        ts=now - 7 * 3600,
    )
    service.record_message(
        message_id="recent-user",
        session_id="yoyo",
        role="user",
        content="第一行\n第二行",
        ts=now - 60,
    )
    service.record_message(
        message_id="recent-assistant",
        session_id="yoyo",
        role="assistant",
        content="我接住这句话了",
        ts=now - 50,
    )

    history = service.build_active_history("yoyo", now=now)
    assert [item["id"] for item in history] == ["recent-user", "recent-assistant"]
    assert [item["role"] for item in history] == ["user", "assistant"]
    assert history[0]["content"] == "第一行\n第二行"


def test_same_second_messages_keep_insertion_role_order(tmp_path, monkeypatch):
    monkeypatch.setenv("XIAOYOU_CONVERSATION_ARCHIVE_ENABLED", "true")
    service = ConversationArchiveService(tmp_path / "conversation.db", start_worker=False)
    now = int(time.time())
    records = [
        {"id": "z-user", "role": "user", "content": "同秒用户", "ts": now},
        {"id": "a-assistant", "role": "assistant", "content": "同秒回复", "ts": now},
    ]
    service.backfill_messages("yoyo", records)

    history = service.build_active_history("yoyo", now=now)
    assert [item["id"] for item in history] == ["z-user", "a-assistant"]
    assert [item["role"] for item in history] == ["user", "assistant"]


def test_idle_gap_creates_episode_and_retrieval_expands_to_raw_neighbors(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("XIAOYOU_CONVERSATION_ARCHIVE_ENABLED", "true")
    monkeypatch.setenv("XIAOYOU_EPISODE_IDLE_SECONDS", "300")
    monkeypatch.setenv("XIAOYOU_EPISODIC_MIN_SCORE", "0.05")
    path = tmp_path / "conversation.db"
    service = ConversationArchiveService(path, start_worker=False)
    now = int(time.time())

    old_episode = service.record_message(
        message_id="old-user",
        session_id="yoyo",
        role="user",
        content="服务器部署时容器出现权限错误",
        ts=now - 1000,
    )
    service.record_message(
        message_id="old-assistant",
        session_id="yoyo",
        role="assistant",
        content="先检查 plugins 目录权限",
        ts=now - 990,
    )
    new_episode = service.record_message(
        message_id="new-user",
        session_id="yoyo",
        role="user",
        content="换个话题",
        ts=now,
    )
    assert old_episode != new_episode

    service._finish_episode(
        old_episode,
        {
            "title": "修复容器权限",
            "detailed_summary": "YoYo部署服务器时遇到了容器权限错误。",
            "topics": ["服务器部署", "容器权限"],
            "key_user_quotes": ["服务器部署时容器出现权限错误"],
            "key_assistant_quotes": ["先检查 plugins 目录权限"],
            "open_loops": [],
            "entities": ["plugins"],
            "importance": 0.7,
            "search_text": "服务器 部署 容器 权限 错误 plugins",
        },
    )

    context, manifest = service.build_episodic_context(
        "yoyo",
        "之前服务器部署的权限错误怎么处理",
        mode="project",
        max_results=3,
    )
    assert old_episode in manifest["episode_ids"]
    assert "修复容器权限" in context
    assert "服务器部署时容器出现权限错误" in context
    assert "先检查 plugins 目录权限" in context
    assert {"old-user", "old-assistant"}.issubset(set(manifest["message_ids"]))


def test_summary_grounding_rejects_role_swaps_and_unsupported_open_loops(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("XIAOYOU_CONVERSATION_ARCHIVE_ENABLED", "true")
    service = ConversationArchiveService(tmp_path / "conversation.db", start_worker=False)
    messages = [
        {"role": "user", "content": "明天上午九点叫醒我"},
        {"role": "assistant", "content": "好呀，我记住啦"},
    ]
    normalized = service._normalize_episode_summary(
        {
            "title": "明早提醒",
            "detailed_summary": "YoYo提出明早叫醒的请求。",
            "topics": ["提醒"],
            "key_user_quotes": ["明天上午九点叫醒我", "好呀，我记住啦"],
            "key_assistant_quotes": ["好呀，我记住啦", "明天上午九点叫醒我"],
            "open_loops": [
                {
                    "text": "明早叫醒",
                    "evidence": "明天上午九点叫醒我",
                    "source": "user",
                },
                {
                    "text": "不存在的安排",
                    "evidence": "下午去机场",
                    "source": "user",
                },
            ],
            "importance": 0.8,
        },
        messages,
    )

    assert normalized["key_user_quotes"] == ["明天上午九点叫醒我"]
    assert normalized["key_assistant_quotes"] == ["好呀，我记住啦"]
    assert [item["text"] for item in normalized["open_loops"]] == ["明早叫醒"]


def test_provider_block_and_short_memory_clear_hide_context_without_deleting_raw_rows(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("XIAOYOU_CONVERSATION_ARCHIVE_ENABLED", "true")
    monkeypatch.setenv("XIAOYOU_ACTIVE_WINDOW_HOURS", "6")
    path = tmp_path / "conversation.db"
    service = ConversationArchiveService(path, start_worker=False)
    now = int(time.time())

    service.record_message(
        message_id="blocked",
        session_id="yoyo",
        role="user",
        content="会触发供应商审查的原话",
        ts=now - 20,
    )
    service.record_message(
        message_id="normal",
        session_id="yoyo",
        role="assistant",
        content="正常回复",
        ts=now - 10,
    )
    assert service.block_injected_messages(["blocked"]) == 1
    assert [item["id"] for item in service.build_active_history("yoyo", now=now)] == [
        "normal"
    ]

    assert service.exclude_recent_session("yoyo", now=now) == 2
    assert service.build_active_history("yoyo", now=now) == []
    stored = _rows(path, "SELECT id, content, excluded FROM messages ORDER BY ts")
    assert [item["id"] for item in stored] == ["blocked", "normal"]
    assert all(item["excluded"] == 1 for item in stored)


def test_restart_restores_only_legacy_whole_context_blocks(tmp_path, monkeypatch):
    monkeypatch.setenv("XIAOYOU_CONVERSATION_ARCHIVE_ENABLED", "true")
    path = tmp_path / "conversation.db"
    service = ConversationArchiveService(path, start_worker=False)
    now = int(time.time())
    for message_id in ("batch-blocked", "current-blocked"):
        service.record_message(
            message_id=message_id,
            session_id="yoyo",
            role="user",
            content=message_id,
            ts=now,
        )

    connection = service._connect()
    try:
        connection.execute(
            "UPDATE messages SET provider_injection_blocked = 1, block_reason = ? WHERE id = ?",
            ("chat_data_inspection_failed", "batch-blocked"),
        )
        connection.execute(
            "UPDATE messages SET provider_injection_blocked = 1, block_reason = ? WHERE id = ?",
            ("current_turn_data_inspection_failed", "current-blocked"),
        )
        connection.commit()
    finally:
        connection.close()

    restarted = ConversationArchiveService(path, start_worker=False)
    rows = _rows(
        path,
        "SELECT id, provider_injection_blocked, block_reason FROM messages ORDER BY id",
    )

    assert rows[0]["id"] == "batch-blocked"
    assert rows[0]["provider_injection_blocked"] == 0
    assert rows[0]["block_reason"] == ""
    assert rows[1]["id"] == "current-blocked"
    assert rows[1]["provider_injection_blocked"] == 1
    assert restarted.build_active_history("yoyo", now=now)[0]["id"] == "batch-blocked"


def test_closed_episode_has_deterministic_fallback_when_model_summary_is_disabled(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("XIAOYOU_CONVERSATION_ARCHIVE_ENABLED", "true")
    monkeypatch.setenv("XIAOYOU_EPISODE_IDLE_SECONDS", "300")
    monkeypatch.setenv("XIAOYOU_EPISODE_SUMMARY_ENABLED", "false")
    path = tmp_path / "conversation.db"
    service = ConversationArchiveService(path, start_worker=False)
    now = int(time.time())
    episode_id = service.record_message(
        message_id="fallback-user",
        session_id="yoyo",
        role="user",
        content="这段原文必须留下",
        ts=now - 1000,
    )
    service.close_stale_episodes(now=now)

    assert service._summarize_one_pending() is True
    episode = _rows(path, "SELECT status, summary_text FROM episodes WHERE id = ?", (episode_id,))[0]
    assert episode["status"] == "ready"
    assert "这段原文必须留下" in episode["summary_text"]


def test_online_backup_contains_archived_messages(tmp_path, monkeypatch):
    monkeypatch.setenv("XIAOYOU_CONVERSATION_ARCHIVE_ENABLED", "true")
    path = tmp_path / "conversation.db"
    service = ConversationArchiveService(path, start_worker=False)
    service.record_message(
        message_id="backup-message",
        session_id="yoyo",
        role="user",
        content="需要进入一致性备份",
        ts=int(time.time()),
    )

    backup_path = service.backup_now()
    assert backup_path == str(path) + ".backup"
    stored = _rows(
        backup_path,
        "SELECT content FROM messages WHERE id = ?",
        ("backup-message",),
    )
    assert stored[0]["content"] == "需要进入一致性备份"


def test_secret_is_preserved_locally_but_redacted_from_active_model_context(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("XIAOYOU_CONVERSATION_ARCHIVE_ENABLED", "true")
    path = tmp_path / "conversation.db"
    service = ConversationArchiveService(path, start_worker=False)
    now = int(time.time())
    secret = "临时密钥 sk-abcdefghijklmnopqrstuvwxyz123456"
    service.record_message(
        message_id="secret",
        session_id="yoyo",
        role="user",
        content=secret,
        ts=now,
    )

    stored = _rows(path, "SELECT content FROM messages WHERE id = 'secret'")
    assert stored[0]["content"] == secret
    active = service.build_active_history("yoyo", now=now)
    assert secret not in active[0]["content"]
    assert "原文仅保存在本地档案" in active[0]["content"]
    prompt = service._episode_summary_prompt(
        {"started_at": now, "ended_at": now},
        [{"role": "user", "content": secret, "ts": now}],
    )
    assert secret not in prompt
    assert "原文仅保存在本地档案" in prompt


def test_time_intent_supports_wechat_style_relative_and_month_day_queries():
    now = int(time.mktime((2026, 7, 18, 15, 0, 0, 0, 0, -1)))

    last_week = time_range("上周我们聊了什么", now=now)
    assert time.localtime(last_week[0])[:3] == (2026, 7, 6)
    assert time.localtime(last_week[1])[:3] == (2026, 7, 12)

    last_month = time_range("上个月那个项目", now=now)
    assert time.localtime(last_month[0])[:3] == (2026, 6, 1)
    assert time.localtime(last_month[1])[:3] == (2026, 6, 30)

    past_month_day = time_range("还记得9月20日吗", now=now)
    assert time.localtime(past_month_day[0])[:3] == (2025, 9, 20)
