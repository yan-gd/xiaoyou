import importlib.util
import json
import sys
import time
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_service(monkeypatch, tmp_path, model_content="{}"):
    logger = types.SimpleNamespace(
        info=lambda *_args, **_kwargs: None,
        warning=lambda *_args, **_kwargs: None,
    )
    modules = {
        "common": types.ModuleType("common"),
        "common.log": types.ModuleType("common.log"),
        "plugins": types.ModuleType("plugins"),
        "plugins.xiaoyou_common": types.ModuleType(
            "plugins.xiaoyou_common"
        ),
        "plugins.xiaoyou_common.model_gateway": types.ModuleType(
            "plugins.xiaoyou_common.model_gateway"
        ),
        "plugins.xiaoyou_common.runtime_paths": types.ModuleType(
            "plugins.xiaoyou_common.runtime_paths"
        ),
        "plugins.xiaoyou_common.thinking_config": types.ModuleType(
            "plugins.xiaoyou_common.thinking_config"
        ),
    }
    modules["common.log"].logger = logger
    modules[
        "plugins.xiaoyou_common.model_gateway"
    ].chat_completion = lambda **_kwargs: types.SimpleNamespace(
        ok=True,
        content=model_content,
    )
    modules[
        "plugins.xiaoyou_common.runtime_paths"
    ].runtime_path = lambda *_args, **_kwargs: str(
        tmp_path / "relationship.db"
    )
    modules[
        "plugins.xiaoyou_common.thinking_config"
    ].build_thinking_payload = lambda *_args, **_kwargs: {}
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    spec = importlib.util.spec_from_file_location(
        "xiaoyou_relationship_universe_test_module",
        ROOT
        / "plugins"
        / "xiaoyou_common"
        / "relationship_universe_service.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_capsule_hides_text_until_unlock_and_is_session_isolated(
    monkeypatch,
    tmp_path,
):
    module = _load_service(monkeypatch, tmp_path)
    service = module.RelationshipUniverseService()
    now = int(time.time())
    future = service.create_capsule(
        session_id="yoyo",
        title="写给未来",
        text="只在未来展示",
        unlock_at=now + 20_000,
        author="user",
    )
    locked = service.open_capsule(
        "yoyo",
        future["entry_id"],
        now=now + 10_000,
    )
    assert locked["locked"] is True
    assert "text" not in locked["body"]
    assert (
        service.open_capsule(
            "other",
            future["entry_id"],
            now=now + 30_000,
        )
        is None
    )

    opened = service.open_capsule(
        "yoyo",
        future["entry_id"],
        now=now + 30_000,
    )
    assert opened["status"] == "opened"
    assert opened["body"]["text"] == "只在未来展示"


def test_ungrounded_journal_is_rejected_then_confirmed(
    monkeypatch,
    tmp_path,
):
    hallucinated = json.dumps(
        {
            "title": "我们今天",
            "summary": "去海边看了日落",
            "mood_changes": ["开心"],
            "representative_media_id": "not-in-transcript",
            "saved_quote": {
                "speaker": "assistant",
                "text": "不存在的原话",
            },
            "tomorrow_wish": "明天去旅行",
        },
        ensure_ascii=False,
    )
    module = _load_service(monkeypatch, tmp_path, hallucinated)
    service = module.RelationshipUniverseService()
    day = "2026-07-26"
    event_at = module._day_timestamp(day)
    messages = [
        {
            "role": "user",
            "kind": "text",
            "text": "今天在实验室做实验",
            "created_at": event_at + 3600,
        },
        {
            "role": "assistant",
            "kind": "text",
            "text": "辛苦啦，记得吃饭",
            "created_at": event_at + 3610,
        },
    ]
    draft = service.draft_daily_journal(
        session_id="yoyo",
        day=day,
        messages=messages,
        mood={"label": "平静"},
    )

    assert draft["status"] == "draft"
    assert "海边" not in draft["body"]["summary"]
    assert draft["body"]["saved_quote"]["text"] in {
        "今天在实验室做实验",
        "辛苦啦，记得吃饭",
    }

    confirmed = service.confirm_journal(
        "yoyo",
        draft["entry_id"],
        body={
            "summary": "今天一起聊了实验和吃饭。",
            "tomorrow_wish": "继续好好生活",
        },
    )
    assert confirmed["status"] == "confirmed"
    assert confirmed["body"]["summary"] == "今天一起聊了实验和吃饭。"
    assert len(service.list_entries("other")) == 0


def test_voice_room_keeps_only_summary_metadata(monkeypatch, tmp_path):
    module = _load_service(monkeypatch, tmp_path)
    service = module.RelationshipUniverseService()
    entry = service.record_voice_memory(
        session_id="yoyo",
        started_at=1_000,
        ended_at=1_120,
        turn_count=5,
        duration_ms=120_000,
    )

    assert entry["kind"] == "voice_memory"
    assert entry["body"] == {
        "turn_count": 5,
        "duration_ms": 120_000,
        "started_at": 1_000,
        "ended_at": 1_120,
    }
