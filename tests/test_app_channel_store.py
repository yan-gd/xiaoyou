import importlib.util
import sys
import types
from enum import Enum
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_app_channel(monkeypatch, tmp_path):
    class _Logger:
        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    class _ContextType(Enum):
        TEXT = 1

    class _ReplyType(Enum):
        TEXT = 1
        IMAGE = 2
        IMAGE_URL = 3
        VOICE = 4

    class _AppVoiceError(RuntimeError):
        pass

    class _AppVoiceService:
        available = True
        asr_model = "qwen3-asr-flash"
        tts_model = "cosyvoice-v3-flash"

        def transcribe(self, *_args, **_kwargs):
            return "测试语音"

    class _Context(dict):
        def __init__(self, context_type, content):
            super().__init__()
            self.type = context_type
            self.content = content
            self.kwargs = {}

        def __getitem__(self, key):
            if key in self.kwargs:
                return self.kwargs[key]
            return super().__getitem__(key)

        def get(self, key, default=None):
            if key in self.kwargs:
                return self.kwargs.get(key, default)
            return super().get(key, default)

    class _ChatChannel:
        def __init__(self):
            pass

    class _Plugin:
        def __init__(self):
            self.handlers = {}

    plugins_module = types.ModuleType("plugins")
    plugins_module.Plugin = _Plugin
    plugins_module.register = lambda **_kwargs: (lambda value: value)

    modules = {
        "plugins": plugins_module,
        "bridge": types.ModuleType("bridge"),
        "bridge.context": types.ModuleType("bridge.context"),
        "bridge.reply": types.ModuleType("bridge.reply"),
        "channel": types.ModuleType("channel"),
        "channel.chat_channel": types.ModuleType("channel.chat_channel"),
        "common": types.ModuleType("common"),
        "common.log": types.ModuleType("common.log"),
        "plugins.xiaoyou_common": types.ModuleType("plugins.xiaoyou_common"),
        "plugins.xiaoyou_common.app_transport": types.ModuleType(
            "plugins.xiaoyou_common.app_transport"
        ),
        "plugins.xiaoyou_common.app_voice_service": types.ModuleType(
            "plugins.xiaoyou_common.app_voice_service"
        ),
        "plugins.xiaoyou_common.conversation_coordinator": types.ModuleType(
            "plugins.xiaoyou_common.conversation_coordinator"
        ),
        "plugins.xiaoyou_common.outbound_dispatcher": types.ModuleType(
            "plugins.xiaoyou_common.outbound_dispatcher"
        ),
        "plugins.xiaoyou_common.recent_state_service": types.ModuleType(
            "plugins.xiaoyou_common.recent_state_service"
        ),
        "plugins.xiaoyou_common.runtime_paths": types.ModuleType(
            "plugins.xiaoyou_common.runtime_paths"
        ),
        "plugins.xiaoyou_common.trace_service": types.ModuleType(
            "plugins.xiaoyou_common.trace_service"
        ),
    }
    modules["bridge.context"].Context = _Context
    modules["bridge.context"].ContextType = _ContextType
    modules["bridge.reply"].ReplyType = _ReplyType
    modules["channel.chat_channel"].ChatChannel = _ChatChannel
    modules["common.log"].logger = _Logger()

    app_transport = modules["plugins.xiaoyou_common.app_transport"]
    app_transport.app_receiver = lambda device_id: "app:" + str(device_id)
    app_transport.get_app_service = lambda: None
    app_transport.register_app_service = lambda service: service
    app_transport.register_app_store = lambda _store: None
    app_voice = modules["plugins.xiaoyou_common.app_voice_service"]
    app_voice.AppVoiceError = _AppVoiceError
    app_voice.AppVoiceService = _AppVoiceService
    modules[
        "plugins.xiaoyou_common.conversation_coordinator"
    ].note_user_activity = lambda *args, **kwargs: None
    outbound = modules["plugins.xiaoyou_common.outbound_dispatcher"]
    outbound.record_assistant_message = lambda *args, **kwargs: ""
    outbound.record_delivered_assistant_long_memory = lambda *args, **kwargs: True
    modules[
        "plugins.xiaoyou_common.recent_state_service"
    ].get_recent_state_service = lambda: types.SimpleNamespace(
        schedule_update=lambda *args, **kwargs: True
    )
    runtime_paths = modules["plugins.xiaoyou_common.runtime_paths"]
    runtime_paths.appdata_root = lambda: str(tmp_path)
    runtime_paths.runtime_path = (
        lambda *_args, **_kwargs: str(tmp_path / "app_channel" / "app.db")
    )
    trace = modules["plugins.xiaoyou_common.trace_service"]
    trace.attach_input_trace = lambda *args, **kwargs: None
    trace.trace_event = lambda *args, **kwargs: None

    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    spec = importlib.util.spec_from_file_location(
        "xiaoyou_app_channel_test_module",
        ROOT / "plugins" / "app_channel" / "__init__.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_app_inbox_is_idempotent_persistent_and_receipt_driven(
    monkeypatch,
    tmp_path,
):
    module = _load_app_channel(monkeypatch, tmp_path)
    store = module.AppInboxStore(tmp_path / "app_channel" / "app.db")

    assert store.register_device("phone-1", "yoyo", platform="android")
    assert store.preferred_device("yoyo") == "phone-1"

    assert store.accept_input(
        message_id="input-1",
        session_id="yoyo",
        device_id="phone-1",
        text="在吗",
        client_sequence=1,
    )
    assert not store.accept_input(
        message_id="input-1",
        session_id="yoyo",
        device_id="phone-1",
        text="在吗",
        client_sequence=1,
    )
    store.mark_input_status("input-1", "failed")
    assert store.accept_input(
        message_id="input-1",
        session_id="yoyo",
        device_id="phone-1",
        text="在吗",
        client_sequence=1,
    )
    store.mark_input_status("input-1", "queued")

    assert store.queue_action(
        action_id="action-1",
        session_id="yoyo",
        device_id="phone-1",
        source="split_reply",
        parts=["在呀", "怎么啦"],
        input_id="input-1",
        user_text="在吗",
    )
    events = store.events_after("phone-1")
    assert [event["text"] for event in events] == ["在呀", "怎么啦"]
    assert all(event["requested_parts"] == 2 for event in events)

    partial = store.acknowledge(
        "action-1",
        "phone-1",
        "partial",
        [events[0]["event_id"]],
    )
    assert partial["sent_text"] == "在呀"
    assert partial["terminal_status"] == "partial"
    assert not partial["delivery_complete"]

    # A terminal receipt is immutable. A late retry cannot turn partial into
    # complete and cannot rewrite which words Xiaoyou actually delivered.
    repeated = store.acknowledge("action-1", "phone-1", "complete")
    assert repeated["terminal_status"] == "partial"
    assert repeated["sent_text"] == "在呀"

    reopened = module.AppInboxStore(tmp_path / "app_channel" / "app.db")
    assert reopened.events_after("phone-1")[0]["action_id"] == "action-1"
    history = reopened.history("phone-1")
    assert any(item["role"] == "user" and item["text"] == "在吗" for item in history)
    assert any(
        item["role"] == "assistant" and item["text"] == "在呀"
        for item in history
    )
    assert not any(
        item["role"] == "assistant" and item["text"] == "怎么啦"
        for item in history
    )


def test_app_channel_configuration_is_safe_by_default():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    app_compose = (ROOT / "docker-compose.app.yml").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    plugins = (ROOT / "plugins" / "plugins.json").read_text(encoding="utf-8")

    assert "'127.0.0.1:8787:8787'" not in compose
    assert "'127.0.0.1:8787:8787'" in app_compose
    assert "XIAOYOU_APP_ENABLED: '${XIAOYOU_APP_ENABLED:-false}'" in compose
    assert "XIAOYOU_APP_DB_PATH: '/app/data/app_channel/app.db'" in compose
    assert (
        "XIAOYOU_APP_DEFAULT_PROACTIVE: "
        "'${XIAOYOU_APP_DEFAULT_PROACTIVE:-false}'"
    ) in compose
    assert "XIAOYOU_APP_VOICE_ENABLED" in compose
    assert "XIAOYOU_APP_TTS_MODEL: 'cosyvoice-v3-flash'" in compose
    assert "XIAOYOU_APP_TTS_VOICE: 'longyan_v3'" in compose
    assert "XIAOYOU_APP_ENABLED=false" in env_example
    assert '"AppChannel"' in plugins


def test_app_voice_messages_keep_audio_transcript_and_receipt_text(
    monkeypatch,
    tmp_path,
):
    module = _load_app_channel(monkeypatch, tmp_path)
    store = module.AppInboxStore(tmp_path / "app_channel" / "app.db")
    store.register_device("phone-voice", "yoyo", platform="android")

    uploaded = store.save_media_bytes(
        b"m4a-test-payload",
        "phone-voice",
        "audio/mp4",
    )
    assert uploaded["media_id"]
    assert store.accept_input(
        message_id="voice-input-1",
        session_id="yoyo",
        device_id="phone-voice",
        kind="voice",
        text="我想你了",
        media_id=uploaded["media_id"],
        mime_type="audio/mp4",
        duration_ms=2300,
        client_sequence=2,
    )

    assert store.queue_action(
        action_id="voice-action-1",
        session_id="yoyo",
        device_id="phone-voice",
        source="chat_channel",
        voice_bytes=b"wav-test-payload",
        voice_mime_type="audio/wav",
        voice_text="我也想你呀",
        voice_duration_ms=1800,
        input_id="voice-input-1",
        user_text="我想你了",
    )
    events = store.events_after("phone-voice")
    assert len(events) == 1
    assert events[0]["kind"] == "voice"
    assert events[0]["text"] == "我也想你呀"
    assert events[0]["duration_ms"] == 1800
    assert events[0]["media_id"]

    receipt = store.acknowledge(
        "voice-action-1",
        "phone-voice",
        "complete",
    )
    assert receipt["sent_text"] == "我也想你呀"
    assert receipt["delivery_complete"]

    history = store.history("phone-voice")
    user_voice = next(item for item in history if item["id"] == "voice-input-1")
    assistant_voice = next(
        item for item in history if item["id"] == events[0]["event_id"]
    )
    assert user_voice["kind"] == "voice"
    assert user_voice["duration_ms"] == 2300
    assert assistant_voice["kind"] == "voice"
    assert assistant_voice["text"] == "我也想你呀"


def test_outbound_dispatcher_queues_app_without_claiming_delivery(
    monkeypatch,
):
    monkeypatch.delenv("XIAOYOU_APP_DEFAULT_PROACTIVE", raising=False)

    class _Logger:
        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    plugins_module = types.ModuleType("plugins")
    plugins_module.instance = types.SimpleNamespace(instances={})
    plugins_common_module = types.ModuleType("plugins.xiaoyou_common")
    common_module = types.ModuleType("common")
    common_log_module = types.ModuleType("common.log")
    common_log_module.logger = _Logger()
    trace_module = types.ModuleType("plugins.xiaoyou_common.trace_service")
    trace_module.ensure_trace = lambda **kwargs: types.SimpleNamespace(
        trace_id=kwargs.get("trace_id") or "trace-1",
        input_id=kwargs.get("input_id") or "input-1",
    )
    trace_module.trace_event = lambda *args, **kwargs: None

    for name, module in {
        "plugins": plugins_module,
        "plugins.xiaoyou_common": plugins_common_module,
        "common": common_module,
        "common.log": common_log_module,
        "plugins.xiaoyou_common.trace_service": trace_module,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    transport_spec = importlib.util.spec_from_file_location(
        "plugins.xiaoyou_common.app_transport",
        ROOT / "plugins" / "xiaoyou_common" / "app_transport.py",
    )
    transport = importlib.util.module_from_spec(transport_spec)
    monkeypatch.setitem(
        sys.modules,
        "plugins.xiaoyou_common.app_transport",
        transport,
    )
    transport_spec.loader.exec_module(transport)

    queued = []

    class _Store:
        def queue_action(self, **kwargs):
            queued.append(kwargs)
            return True

        def preferred_device(self, _session_id):
            return "phone-1"

    transport.register_app_store(_Store())

    dispatcher_spec = importlib.util.spec_from_file_location(
        "xiaoyou_outbound_dispatcher_test_module",
        ROOT / "plugins" / "xiaoyou_common" / "outbound_dispatcher.py",
    )
    dispatcher = importlib.util.module_from_spec(dispatcher_spec)
    dispatcher_spec.loader.exec_module(dispatcher)

    receipt = dispatcher.send_text(
        session_id="yoyo",
        source="split_reply",
        receiver="app:phone-1",
        parts=["第一句", "第二句"],
        trace_id="trace-1",
        input_id="input-1",
    )

    assert receipt.ok
    assert receipt.queued
    assert receipt.deferred_delivery
    assert not receipt.delivered
    assert receipt.sent_parts == []
    assert queued[0]["parts"] == ["第一句", "第二句"]
    assert queued[0]["device_id"] == "phone-1"
    assert dispatcher.resolve_receiver("yoyo", "") == ""
