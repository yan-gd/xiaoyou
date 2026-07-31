import importlib.util
import json
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_service(monkeypatch):
    class _Logger:
        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    common = types.ModuleType("common")
    common_log = types.ModuleType("common.log")
    common_log.logger = _Logger()
    plugins = types.ModuleType("plugins")
    plugins_common = types.ModuleType("plugins.xiaoyou_common")
    app_transport = types.ModuleType(
        "plugins.xiaoyou_common.app_transport"
    )
    app_transport.app_receiver = (
        lambda device_id: "app:" + str(device_id or "")
    )
    model_gateway = types.ModuleType(
        "plugins.xiaoyou_common.model_gateway"
    )
    model_gateway.chat_completion = lambda **_kwargs: types.SimpleNamespace(
        ok=False,
        content="",
        error_kind="configuration",
    )
    outbound = types.ModuleType(
        "plugins.xiaoyou_common.outbound_dispatcher"
    )
    outbound.send_action = lambda **_kwargs: None
    thinking = types.ModuleType(
        "plugins.xiaoyou_common.thinking_config"
    )
    thinking.build_thinking_payload = (
        lambda *_args, **_kwargs: {"enable_thinking": False}
    )
    for name, module in {
        "common": common,
        "common.log": common_log,
        "plugins": plugins,
        "plugins.xiaoyou_common": plugins_common,
        "plugins.xiaoyou_common.app_transport": app_transport,
        "plugins.xiaoyou_common.model_gateway": model_gateway,
        "plugins.xiaoyou_common.outbound_dispatcher": outbound,
        "plugins.xiaoyou_common.thinking_config": thinking,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    spec = importlib.util.spec_from_file_location(
        "xiaoyou_voice_room_capability_test_module",
        ROOT
        / "plugins"
        / "xiaoyou_common"
        / "voice_room_capability_service.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, model_gateway


def test_planner_returns_structured_reminder_and_photo_without_local_routing(
    monkeypatch,
):
    module, gateway = _load_service(monkeypatch)

    def _chat_completion(**kwargs):
        assert kwargs["purpose"] == "completed_turn_capability_plan"
        payload_text = kwargs["payload"]["messages"][1]["content"]
        assert "明早记得叫我" in payload_text
        return types.SimpleNamespace(
            ok=True,
            content=json.dumps(
                {
                    "actions": [
                        {
                            "type": "create_reminder",
                            "confidence": 0.96,
                            "due_at": "2099-08-01T07:30:00+08:00",
                            "task": "叫 YoYo 起床",
                            "reason": "明确的未来提醒委托",
                        },
                        {
                            "type": "generate_life_photo",
                            "confidence": 0.93,
                            "subject": "xiaoyou",
                            "request_text": "现在也拍一张你的生活照给我",
                            "reason": "明确的即时照片请求",
                        },
                    ]
                },
                ensure_ascii=False,
            ),
            error_kind="",
        )

    gateway.chat_completion = _chat_completion
    module.chat_completion = _chat_completion
    actions = module.plan_voice_room_actions(
        {
            "session_id": "yoyo",
            "turn_id": "room-1:turn-1",
            "user_text": "明早记得叫我，现在也拍一张你的生活照给我",
            "assistant_text": "好呀，我记着，也拍给你看。",
        }
    )

    assert [item["type"] for item in actions] == [
        "create_reminder",
        "generate_life_photo",
    ]
    assert actions[0]["due_at"] == "2099-08-01T07:30:00+08:00"
    assert actions[1]["subject"] == "xiaoyou"


def test_capabilities_use_existing_reminder_photo_and_app_delivery(
    monkeypatch,
):
    module, _gateway = _load_service(monkeypatch)
    reminder_calls = []
    photo_calls = []
    delivery_calls = []

    class _Reminder:
        def create_voice_reminder(self, **kwargs):
            reminder_calls.append(kwargs)
            return {"id": "vr-1", "due_text": "2099-08-01 07:30"}

    class _Photo:
        def create_voice_share(self, **kwargs):
            photo_calls.append(("create", kwargs))
            return {
                "path": "generated.jpg",
                "caption": "刚拍好，给你看～",
            }

        def mark_voice_sent(self, session_id, share):
            photo_calls.append(("sent", session_id, share))

        def discard_share(self, share):
            photo_calls.append(("discard", share))

    def _dispatcher(**kwargs):
        delivery_calls.append(kwargs)
        return types.SimpleNamespace(
            ok=True,
            queued=True,
            action_id="action-1",
        )

    service = module.VoiceRoomCapabilityService(
        instances_provider=lambda: {
            "REMINDERLOVE": _Reminder(),
            "XIAOYOULIFEPHOTO": _Photo(),
        },
        planner=lambda _exchange: [
            {
                "type": "create_reminder",
                "due_at": "2099-08-01T07:30:00+08:00",
                "task": "叫 YoYo 起床",
            },
            {
                "type": "generate_life_photo",
                "request_text": "拍一张现在的生活照",
                "subject": "xiaoyou",
            },
        ],
        dispatcher=_dispatcher,
        start_worker=False,
    )
    results = service.process(
        {
            "session_id": "yoyo",
            "device_id": "phone",
            "room_id": "room-1",
            "turn_id": "room-1:turn-1",
            "user_text": "明早叫我，再拍张照片给我",
            "assistant_text": "好，我都记住啦。",
        }
    )

    assert [item["ok"] for item in results] == [True, True]
    assert reminder_calls[0]["receiver"] == "app:phone"
    assert reminder_calls[0]["turn_id"] == "room-1:turn-1"
    assert photo_calls[0][0] == "create"
    assert photo_calls[1][0] == "sent"
    assert delivery_calls[0]["receiver"] == "app:phone"
    assert delivery_calls[0]["record_memory"] is False


def test_submit_is_non_blocking_and_deduplicates_turns(monkeypatch):
    module, _gateway = _load_service(monkeypatch)
    service = module.VoiceRoomCapabilityService(start_worker=False)
    exchange = {
        "turn_id": "room-1:turn-1",
        "user_text": "随便一段完整语音",
        "assistant_text": "我听到了。",
    }

    assert service.submit(exchange) is True
    assert service.submit(exchange) is False
    assert service.jobs.qsize() == 1
