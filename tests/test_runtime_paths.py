import importlib.util
import sys
import types
from pathlib import Path


def _load_runtime_paths():
    root = Path(__file__).resolve().parents[1]
    common_module = sys.modules.setdefault("common", types.ModuleType("common"))
    log_module = types.ModuleType("common.log")

    class _Logger:
        def warning(self, *args, **kwargs):
            return None

    log_module.logger = _Logger()
    common_module.log = log_module
    sys.modules["common.log"] = log_module

    spec = importlib.util.spec_from_file_location(
        "runtime_paths_under_test",
        root / "plugins" / "xiaoyou_common" / "runtime_paths.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RUNTIME_PATHS = _load_runtime_paths()
appdata_root = RUNTIME_PATHS.appdata_root
runtime_path = RUNTIME_PATHS.runtime_path


def test_runtime_path_defaults_below_appdata(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA_DIR", str(tmp_path / "runtime"))

    resolved = runtime_path("short_memory", "short_memory.json")

    assert resolved == str(
        (tmp_path / "runtime" / "short_memory" / "short_memory.json").resolve()
    )
    assert appdata_root() == str((tmp_path / "runtime").resolve())


def test_runtime_path_migrates_primary_and_backup_without_deleting_legacy(
    monkeypatch,
    tmp_path,
):
    appdata = tmp_path / "data"
    legacy = tmp_path / "plugins" / "short_memory" / "short_memory.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('{"source":"primary"}', encoding="utf-8")
    Path(str(legacy) + ".backup").write_text(
        '{"source":"backup"}',
        encoding="utf-8",
    )
    monkeypatch.setenv("APPDATA_DIR", str(appdata))

    target = Path(
        runtime_path(
            "short_memory",
            "short_memory.json",
            legacy_paths=(legacy,),
        )
    )

    assert target.read_text(encoding="utf-8") == '{"source":"primary"}'
    assert Path(str(target) + ".backup").read_text(encoding="utf-8") == (
        '{"source":"backup"}'
    )
    assert legacy.exists()
    assert Path(str(legacy) + ".backup").exists()

    target.write_text('{"source":"data"}', encoding="utf-8")
    legacy.write_text('{"source":"changed-legacy"}', encoding="utf-8")
    resolved_again = runtime_path(
        "short_memory",
        "short_memory.json",
        legacy_paths=(legacy,),
    )
    assert resolved_again == str(target)
    assert target.read_text(encoding="utf-8") == '{"source":"data"}'


def test_relative_override_stays_below_appdata(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CUSTOM_STATE_PATH", "custom/state.json")

    resolved = runtime_path(
        "ignored",
        "ignored.json",
        env_var="CUSTOM_STATE_PATH",
    )

    assert resolved == str((tmp_path / "data" / "custom" / "state.json").resolve())


def test_plugin_runtime_state_sources_use_data_resolver():
    root = Path(__file__).resolve().parents[1]
    expected = {
        "plugins/short_memory/short_memory.py": "SHORT_MEMORY_STATE_PATH",
        "plugins/reminder_love/reminder_love.py": "REMINDER_LOVE_STATE_PATH",
        "plugins/proactive_love/proactive_love.py": "PROACTIVE_LOVE_STATE_PATH",
        "plugins/conversation_followup/__init__.py": "CONVERSATION_FOLLOWUP_STATE_PATH",
        "plugins/xiaoyou_identity/__init__.py": "XIAOYOU_IDENTITY_STATE_PATH",
        "plugins/xiaoyou_chat/__init__.py": "XIAOYOU_RECOVERY_STATE_PATH",
        "plugins/xiaoyou_life_photo/__init__.py": "XIAOYOU_LIFE_PHOTO_STATE_PATH",
        "plugins/xiaoyou_common/recent_state_service.py": "XIAOYOU_RECENT_STATE_PATH",
        "plugins/xiaoyou_common/inner_state_service.py": "XIAOYOU_INNER_STATE_PATH",
        "plugins/xiaoyou_common/conversation_archive_service.py": "XIAOYOU_CONVERSATION_ARCHIVE_PATH",
    }

    for relative, env_name in expected.items():
        source = (root / relative).read_text(encoding="utf-8")
        assert "runtime_path(" in source
        assert env_name in source


def test_compose_pins_mutable_state_below_appdata():
    root = Path(__file__).resolve().parents[1]
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    expected = (
        "SHORT_MEMORY_STATE_PATH: '/app/data/short_memory/short_memory.json'",
        "REMINDER_LOVE_STATE_PATH: '/app/data/reminder_love/reminders.json'",
        "PROACTIVE_LOVE_STATE_PATH: '/app/data/proactive_love/proactive_state.json'",
        "CONVERSATION_FOLLOWUP_STATE_PATH: '/app/data/conversation_followup/followup_state.json'",
        "XIAOYOU_IDENTITY_STATE_PATH: '/app/data/xiaoyou_identity/state.json'",
        "XIAOYOU_RECOVERY_STATE_PATH: '/app/data/xiaoyou_chat/recovery_state.json'",
        "XIAOYOU_RECENT_STATE_PATH: '/app/data/xiaoyou_recent_state/state.json'",
        "XIAOYOU_INNER_STATE_PATH: '/app/data/xiaoyou_inner_state/state.json'",
        "XIAOYOU_LIFE_PHOTO_STATE_PATH: '/app/data/xiaoyou_life_photo/state.json'",
        "XIAOYOU_CONVERSATION_ARCHIVE_PATH: '/app/data/xiaoyou_conversation/conversation.db'",
    )
    for item in expected:
        assert item in compose
