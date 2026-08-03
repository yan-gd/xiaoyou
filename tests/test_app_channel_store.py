import importlib.util
import sys
import threading
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
        IMAGE = 2

    class _ReplyType(Enum):
        TEXT = 1
        IMAGE = 2
        IMAGE_URL = 3
        VOICE = 4

    class _Event(Enum):
        ON_SEND_REPLY = 1

    class _AppVoiceError(RuntimeError):
        pass

    class _AppVoiceService:
        available = True
        asr_model = "qwen3-asr-flash"
        tts_model = "seed-tts-2.0"
        tts_provider = "volcengine"
        tts_available = True

        def transcribe(self, *_args, **_kwargs):
            return "测试语音"

        def synthesize(self, *_args, **_kwargs):
            return types.SimpleNamespace(
                data=b"mp3-test-payload",
                mime_type="audio/mpeg",
                duration_ms=1200,
            )

    class _AppVoiceReplyDecision:
        def __init__(
            self,
            medium="text",
            confidence=0.9,
            reason="test",
            model_ok=True,
        ):
            self.medium = medium
            self.confidence = confidence
            self.reason = reason
            self.model_ok = model_ok

        @property
        def use_voice(self):
            return self.medium == "voice"

    class _AppVoiceReplyDecisionService:
        def decide(self, **_kwargs):
            return _AppVoiceReplyDecision()

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
        futures = {}
        sessions = {}
        lock = threading.Lock()
        input_batches = {}
        input_batch_workers = set()
        input_versions = {}

        def __init__(self):
            pass

    class _Plugin:
        def __init__(self):
            self.handlers = {}

    plugins_module = types.ModuleType("plugins")
    plugins_module.Event = _Event
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
        "plugins.xiaoyou_common.app_voice_reply_decision": types.ModuleType(
            "plugins.xiaoyou_common.app_voice_reply_decision"
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
        "plugins.xiaoyou_common.route_prefetch": types.ModuleType(
            "plugins.xiaoyou_common.route_prefetch"
        ),
        "plugins.xiaoyou_common.runtime_paths": types.ModuleType(
            "plugins.xiaoyou_common.runtime_paths"
        ),
        "plugins.xiaoyou_common.system_push_service": types.ModuleType(
            "plugins.xiaoyou_common.system_push_service"
        ),
        "plugins.xiaoyou_common.trace_service": types.ModuleType(
            "plugins.xiaoyou_common.trace_service"
        ),
        "plugins.xiaoyou_common.voice_room_service": types.ModuleType(
            "plugins.xiaoyou_common.voice_room_service"
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
    app_voice_decision = modules[
        "plugins.xiaoyou_common.app_voice_reply_decision"
    ]
    app_voice_decision.AppVoiceReplyDecisionService = (
        _AppVoiceReplyDecisionService
    )
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
    def _resolve_route_prefetch(context, name, fallback):
        values = getattr(context, "kwargs", {}).get(
            "_test_route_prefetch",
            {},
        )
        if name in values:
            return values[name]
        return fallback()

    modules[
        "plugins.xiaoyou_common.route_prefetch"
    ].resolve_route_prefetch = _resolve_route_prefetch
    runtime_paths = modules["plugins.xiaoyou_common.runtime_paths"]
    runtime_paths.appdata_root = lambda: str(tmp_path)
    runtime_paths.runtime_path = (
        lambda *_args, **_kwargs: str(tmp_path / "app_channel" / "app.db")
    )
    modules[
        "plugins.xiaoyou_common.system_push_service"
    ].SystemPushDispatcher = lambda: types.SimpleNamespace(
        enabled=False,
        enqueue=lambda **_kwargs: False,
    )
    trace = modules["plugins.xiaoyou_common.trace_service"]
    trace.attach_input_trace = lambda *args, **kwargs: None
    trace.trace_event = lambda *args, **kwargs: None
    voice_rooms = modules["plugins.xiaoyou_common.voice_room_service"]
    voice_rooms.VoiceRoomError = type(
        "VoiceRoomError",
        (RuntimeError,),
        {},
    )
    voice_rooms.VoiceRoomProviderError = type(
        "VoiceRoomProviderError",
        (voice_rooms.VoiceRoomError,),
        {},
    )
    voice_rooms.VoiceRoomService = lambda **_kwargs: types.SimpleNamespace(
        available=False,
        close_all=lambda: None,
    )

    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    spec = importlib.util.spec_from_file_location(
        "xiaoyou_app_channel_test_module",
        ROOT / "plugins" / "app_channel" / "__init__.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_app_runtime_owns_work_queues_but_shares_input_version_clock(
    monkeypatch,
    tmp_path,
):
    module = _load_app_channel(monkeypatch, tmp_path)

    assert module.AppRuntimeChannel.sessions is not module.ChatChannel.sessions
    assert module.AppRuntimeChannel.futures is not module.ChatChannel.futures
    assert (
        module.AppRuntimeChannel.input_batches
        is not module.ChatChannel.input_batches
    )
    assert (
        module.AppRuntimeChannel.input_batch_workers
        is not module.ChatChannel.input_batch_workers
    )
    assert (
        module.AppRuntimeChannel.input_versions
        is module.ChatChannel.input_versions
    )
    assert module.AppRuntimeChannel.lock is module.ChatChannel.lock


def test_app_text_input_requests_immediate_dispatch(monkeypatch, tmp_path):
    module = _load_app_channel(monkeypatch, tmp_path)
    produced = []
    runtime = object.__new__(module.AppRuntimeChannel)
    runtime.canonical_session_id = "yoyo"
    runtime.produce = produced.append
    runtime.store = types.SimpleNamespace(
        mark_input_status=lambda *_args, **_kwargs: None
    )

    runtime._produce_text(
        text="我先说第一句",
        message_id="message-immediate-1",
        device_id="phone-immediate",
        voice_reply=False,
    )

    assert len(produced) == 1
    assert produced[0]["xiaoyou_input_immediate"] is True


def test_device_reconnect_preserves_system_push_registration(
    monkeypatch,
    tmp_path,
):
    module = _load_app_channel(monkeypatch, tmp_path)
    store = module.AppInboxStore(tmp_path / "app_channel" / "app.db")

    assert store.register_device(
        "phone-push",
        "yoyo",
        platform="android",
        push_provider="vivo",
        push_token="reg-id-1",
        push_enabled=True,
        push_preview=False,
        push_sound=True,
        push_vibration=False,
    )
    # Normal App reconnects do not include push fields.
    assert store.register_device(
        "phone-push",
        "yoyo",
        platform="android",
    )

    assert store.push_target("phone-push") == {
        "provider": "vivo",
        "token": "reg-id-1",
        "preview": False,
        "sound": True,
        "vibration": False,
    }


def test_committed_action_enqueues_system_push_once(
    monkeypatch,
    tmp_path,
):
    module = _load_app_channel(monkeypatch, tmp_path)
    queued = []
    dispatcher = types.SimpleNamespace(
        enqueue=lambda **kwargs: queued.append(kwargs) or True,
    )
    store = module.AppInboxStore(
        tmp_path / "app_channel" / "app.db",
        push_dispatcher=dispatcher,
    )
    store.register_device(
        "phone-push",
        "yoyo",
        platform="android",
        push_provider="vivo",
        push_token="reg-id-1",
        push_enabled=True,
    )

    arguments = {
        "action_id": "push-action-1",
        "session_id": "yoyo",
        "device_id": "phone-push",
        "source": "proactive_love",
        "parts": ["记得吃午饭呀"],
    }
    assert store.queue_action(**arguments)
    assert store.queue_action(**arguments)

    assert len(queued) == 1
    assert queued[0]["action_id"] == "push-action-1"
    assert queued[0]["text"] == "记得吃午饭呀"
    assert queued[0]["reg_id"] == "reg-id-1"


def test_store_exposes_recent_push_delivery_outcomes(
    monkeypatch,
    tmp_path,
):
    module = _load_app_channel(monkeypatch, tmp_path)
    dispatcher = types.SimpleNamespace(
        outcomes=lambda action_ids: {
            action_id: {"state": "accepted", "updated_at": 1, "error": ""}
            for action_id in action_ids
        },
    )
    store = module.AppInboxStore(
        tmp_path / "app_channel" / "app.db",
        push_dispatcher=dispatcher,
    )

    assert store.push_delivery_outcomes(["action-1"]) == {
        "action-1": {
            "state": "accepted",
            "updated_at": 1,
            "error": "",
        },
    }


def test_app_text_reply_uses_model_selected_voice_medium(
    monkeypatch,
    tmp_path,
):
    module = _load_app_channel(monkeypatch, tmp_path)
    store = module.AppInboxStore(tmp_path / "app_channel" / "app.db")
    store.register_device("phone-text-voice", "yoyo", platform="android")
    decision_calls = []
    synthesis_calls = []

    class _DecisionService:
        def decide(self, **kwargs):
            decision_calls.append(kwargs)
            return types.SimpleNamespace(
                medium="voice",
                confidence=0.96,
                reason="YoYo wants to hear Xiaoyou",
                model_ok=True,
                use_voice=True,
            )

    class _VoiceService:
        tts_available = True

        def synthesize(self, text, **kwargs):
            synthesis_calls.append((text, kwargs))
            return types.SimpleNamespace(
                data=b"model-selected-mp3",
                mime_type="audio/mpeg",
                duration_ms=1450,
            )

    runtime = object.__new__(module.AppRuntimeChannel)
    runtime.store = store
    runtime.canonical_session_id = "yoyo"
    runtime.voice_service = _VoiceService()
    runtime.voice_reply_decision = _DecisionService()
    context = module.Context(module.ContextType.TEXT, "用声音和我说嘛")
    context.kwargs = {
        "session_id": "yoyo",
        "receiver": "app:phone-text-voice",
        "xiaoyou_app_device_id": "phone-text-voice",
        "xiaoyou_input_id": "text-voice-1",
        "xiaoyou_input_kind": "text",
        "xiaoyou_input_messages": ["用声音和我说嘛"],
    }
    reply = types.SimpleNamespace(
        type=module.ReplyType.TEXT,
        content="好呀，那我就亲口告诉你～",
    )

    assert runtime.send(reply, context)
    events = store.events_after("phone-text-voice")
    assert len(events) == 1
    assert events[0]["kind"] == "voice"
    assert events[0]["text"] == "好呀，那我就亲口告诉你～"
    assert events[0]["duration_ms"] == 1450
    assert decision_calls[0]["input_kind"] == "text"
    assert decision_calls[0]["user_text"] == "用声音和我说嘛"
    assert synthesis_calls[0][0] == "好呀，那我就亲口告诉你～"
    assert context.kwargs["xiaoyou_app_voice_requested_by"] == "model"


def test_app_text_reply_consumes_prefetched_medium_without_second_model(
    monkeypatch,
    tmp_path,
):
    module = _load_app_channel(monkeypatch, tmp_path)
    store = module.AppInboxStore(tmp_path / "app_channel" / "app.db")
    store.register_device("phone-prefetch", "yoyo", platform="android")

    class _DecisionService:
        def decide(self, **_kwargs):
            raise AssertionError("prefetched medium must avoid a second model")

    class _VoiceService:
        tts_available = True

        def synthesize(self, *_args, **_kwargs):
            return types.SimpleNamespace(
                data=b"prefetched-voice",
                mime_type="audio/mpeg",
                duration_ms=1100,
            )

    runtime = object.__new__(module.AppRuntimeChannel)
    runtime.store = store
    runtime.canonical_session_id = "yoyo"
    runtime.voice_service = _VoiceService()
    runtime.voice_reply_decision = _DecisionService()
    context = module.Context(module.ContextType.TEXT, "陪我说说话")
    context.kwargs = {
        "session_id": "yoyo",
        "receiver": "app:phone-prefetch",
        "xiaoyou_app_device_id": "phone-prefetch",
        "xiaoyou_input_id": "prefetch-1",
        "xiaoyou_input_kind": "text",
        "_test_route_prefetch": {
            "APPVOICEREPLYDECISION": types.SimpleNamespace(
                medium="voice",
                confidence=0.93,
                reason="voice suits this turn",
                model_ok=True,
                use_voice=True,
            ),
        },
    }
    reply = types.SimpleNamespace(
        type=module.ReplyType.TEXT,
        content="好呀，我陪你聊一会儿。",
    )

    assert runtime.send(reply, context)
    events = store.events_after("phone-prefetch")
    assert len(events) == 1
    assert events[0]["kind"] == "voice"
    assert context.kwargs["xiaoyou_app_voice_decision"]["medium"] == "voice"


def test_app_runtime_publishes_voice_event_before_synthesis_finishes(
    monkeypatch,
    tmp_path,
):
    module = _load_app_channel(monkeypatch, tmp_path)
    store = module.AppInboxStore(tmp_path / "app_channel" / "app.db")
    store.register_device("phone-live-tts", "yoyo", platform="android")
    observed = {}

    class _VoiceService:
        tts_available = True

        def synthesize(self, text, on_audio_chunk=None, **_kwargs):
            on_audio_chunk(b"first-frame", "audio/mpeg")
            event = store.events_after("phone-live-tts")[0]
            observed["streaming"] = event["streaming"]
            observed["token"] = event["stream_token"]
            on_audio_chunk(b"last-frame", "audio/mpeg")
            return types.SimpleNamespace(
                data=b"first-framelast-frame",
                mime_type="audio/mpeg",
                duration_ms=840,
            )

    runtime = object.__new__(module.AppRuntimeChannel)
    runtime.store = store
    runtime.canonical_session_id = "yoyo"
    runtime.voice_service = _VoiceService()
    runtime.voice_reply_decision = types.SimpleNamespace()
    context = module.Context(module.ContextType.TEXT, "voice")
    context.kwargs = {
        "session_id": "yoyo",
        "receiver": "app:phone-live-tts",
        "xiaoyou_app_device_id": "phone-live-tts",
        "xiaoyou_input_id": "live-tts-1",
        "xiaoyou_input_kind": "text",
        "xiaoyou_app_voice_medium_decided": True,
        "xiaoyou_app_voice_reply": True,
    }
    reply = types.SimpleNamespace(
        type=module.ReplyType.TEXT,
        content="I can speak before synthesis finishes.",
    )

    assert runtime.send(reply, context)
    assert observed["streaming"] is True
    assert observed["token"]
    event = store.events_after("phone-live-tts")[0]
    assert event["streaming"] is False
    assert event["duration_ms"] == 840
    media = store.media(
        event["media_id"],
        "phone-live-tts",
        session_id="yoyo",
    )
    assert media[0].read_bytes() == b"first-framelast-frame"


def test_app_text_reply_stays_text_when_model_selects_text(
    monkeypatch,
    tmp_path,
):
    module = _load_app_channel(monkeypatch, tmp_path)
    store = module.AppInboxStore(tmp_path / "app_channel" / "app.db")
    store.register_device("phone-text", "yoyo", platform="android")

    class _DecisionService:
        def decide(self, **_kwargs):
            return types.SimpleNamespace(
                medium="text",
                confidence=0.88,
                reason="text is the natural medium",
                model_ok=True,
                use_voice=False,
            )

    class _VoiceService:
        tts_available = True

        def synthesize(self, *_args, **_kwargs):
            raise AssertionError("text decision must not synthesize audio")

    runtime = object.__new__(module.AppRuntimeChannel)
    runtime.store = store
    runtime.canonical_session_id = "yoyo"
    runtime.voice_service = _VoiceService()
    runtime.voice_reply_decision = _DecisionService()
    context = module.Context(module.ContextType.TEXT, "今天吃什么")
    context.kwargs = {
        "session_id": "yoyo",
        "receiver": "app:phone-text",
        "xiaoyou_app_device_id": "phone-text",
        "xiaoyou_input_id": "text-1",
        "xiaoyou_input_kind": "text",
        "short_memory_current_user_text": "今天吃什么",
    }
    reply = types.SimpleNamespace(
        type=module.ReplyType.TEXT,
        content="想吃点热乎的～",
    )

    assert runtime.send(reply, context)
    events = store.events_after("phone-text")
    assert len(events) == 1
    assert events[0]["kind"] == "text"
    assert events[0]["text"] == "想吃点热乎的～"
    assert context.kwargs["xiaoyou_app_voice_decision"]["medium"] == "text"


def test_app_voice_input_keeps_voice_reply_without_medium_model(
    monkeypatch,
    tmp_path,
):
    module = _load_app_channel(monkeypatch, tmp_path)
    store = module.AppInboxStore(tmp_path / "app_channel" / "app.db")
    store.register_device("phone-voice", "yoyo", platform="android")

    class _DecisionService:
        def decide(self, **_kwargs):
            raise AssertionError("voice input must not need a medium model")

    class _VoiceService:
        tts_available = True

        def synthesize(self, *_args, **_kwargs):
            return types.SimpleNamespace(
                data=b"voice-input-mp3",
                mime_type="audio/mpeg",
                duration_ms=900,
            )

    runtime = object.__new__(module.AppRuntimeChannel)
    runtime.store = store
    runtime.canonical_session_id = "yoyo"
    runtime.voice_service = _VoiceService()
    runtime.voice_reply_decision = _DecisionService()
    context = module.Context(module.ContextType.TEXT, "我回来啦")
    context.kwargs = {
        "session_id": "yoyo",
        "receiver": "app:phone-voice",
        "xiaoyou_app_device_id": "phone-voice",
        "xiaoyou_input_id": "voice-1",
        "xiaoyou_input_kind": "voice",
        "xiaoyou_app_voice_reply": True,
    }
    reply = types.SimpleNamespace(
        type=module.ReplyType.TEXT,
        content="终于回来啦～",
    )

    assert runtime.send(reply, context)
    events = store.events_after("phone-voice")
    assert len(events) == 1
    assert events[0]["kind"] == "voice"
    assert context.kwargs["xiaoyou_app_voice_requested_by"] == "voice_input"


def test_app_plugin_decides_medium_before_split_reply_phase(
    monkeypatch,
    tmp_path,
):
    module = _load_app_channel(monkeypatch, tmp_path)
    calls = []

    class _Runtime:
        def _should_use_voice_reply(self, *, content, context, kwargs):
            calls.append((content, context, kwargs))
            kwargs["xiaoyou_app_voice_requested_by"] = "model"
            context.kwargs = kwargs
            return True

    plugin = object.__new__(module.AppChannel)
    plugin.enabled = True
    plugin.runtime = _Runtime()
    context = module.Context(module.ContextType.TEXT, "想听你说")
    context.kwargs = {
        "xiaoyou_transport": "app",
        "xiaoyou_input_kind": "text",
        "xiaoyou_input_id": "pre-send-1",
    }
    reply = types.SimpleNamespace(
        type=module.ReplyType.TEXT,
        content="那我说给你听～",
    )

    plugin.on_send_reply({"context": context, "reply": reply})

    assert len(calls) == 1
    assert context.kwargs["xiaoyou_app_voice_medium_decided"] is True
    assert context.kwargs["xiaoyou_app_voice_reply"] is True
    assert context.kwargs["xiaoyou_app_voice_requested_by"] == "model"


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
    assert "XIAOYOU_APP_IMAGE_MAX_BYTES: '8388608'" in compose
    assert "XIAOYOU_APP_TTS_PROVIDER: 'volcengine'" in compose
    assert "XIAOYOU_APP_TTS_MODEL: 'seed-tts-2.0'" in compose
    assert (
        "XIAOYOU_APP_TTS_VOICE: "
        "'${XIAOYOU_APP_TTS_VOICE:-ICL_uranus_zh_female_rouguhunshi_tob}'"
    ) in compose
    assert "XIAOYOU_APP_TTS_API_KEY:" in compose
    assert "XIAOYOU_APP_TTS_ACCESS_KEY:" in compose
    assert (
        "XIAOYOU_APP_TEXT_VOICE_DECISION_ENABLED: 'true'"
    ) in compose
    assert "XIAOYOU_APP_VOICE_ROUTE_MODEL: 'qwen3.7-plus'" in compose
    assert (
        "XIAOYOU_APP_VOICE_ROUTE_ENABLE_THINKING: 'false'"
    ) in compose
    assert "XIAOYOU_APP_ENABLED=false" in env_example
    assert '"AppChannel"' in plugins


def test_app_image_is_stored_under_data_with_chat_metadata(
    monkeypatch,
    tmp_path,
):
    module = _load_app_channel(monkeypatch, tmp_path)
    store = module.AppInboxStore(tmp_path / "app_channel" / "app.db")
    store.register_device("phone-image", "yoyo", platform="android")

    uploaded = store.save_media_bytes(
        b"\x89PNG\r\n\x1a\nimage-payload",
        "phone-image",
        "image/png",
    )
    assert uploaded["media_id"]
    assert Path(uploaded["local_path"]).is_file()
    assert store.accept_input(
        message_id="image-input-1",
        session_id="yoyo",
        device_id="phone-image",
        kind="sticker",
        text="[YoYo 发来了一张表情包]",
        media_id=uploaded["media_id"],
        mime_type="image/png",
        client_sequence=3,
    )

    history = store.history("phone-image")
    image = next(item for item in history if item["id"] == "image-input-1")
    assert image["kind"] == "sticker"
    assert image["mime_type"] == "image/png"
    assert image["media_id"] == uploaded["media_id"]

    produced = []
    runtime = object.__new__(module.AppRuntimeChannel)
    runtime.store = store
    runtime.canonical_session_id = "yoyo"
    runtime.produce = produced.append
    result = runtime.submit_image(
        image_bytes=b"\x89PNG\r\n\x1a\nsecond-image",
        mime_type="image/png",
        kind="image",
        message_id="image-input-2",
        device_id="phone-image",
        client_sequence=4,
    )

    assert result["accepted"]
    assert len(produced) == 1
    assert produced[0].type is module.ContextType.IMAGE
    assert Path(produced[0].content).is_file()
    assert produced[0]["xiaoyou_transport"] == "app"
    assert produced[0]["xiaoyou_input_kind"] == "image"
    assert store.input_by_id("image-input-2", "phone-image")["status"] == "queued"


def test_app_history_and_media_restore_across_reinstalled_devices(
    monkeypatch,
    tmp_path,
):
    module = _load_app_channel(monkeypatch, tmp_path)
    store = module.AppInboxStore(tmp_path / "app_channel" / "app.db")
    store.register_device("phone-old", "yoyo", platform="android")
    store.register_device("phone-new", "yoyo", platform="android")
    store.register_device("phone-other", "other", platform="android")

    uploaded = store.save_media_bytes(
        b"\x89PNG\r\n\x1a\nrestorable-image",
        "phone-old",
        "image/png",
    )
    for index in range(7):
        assert store.accept_input(
            message_id="history-input-%d" % index,
            session_id="yoyo",
            device_id="phone-old",
            kind="image" if index == 3 else "text",
            text="[image]" if index == 3 else "message-%d" % index,
            media_id=uploaded["media_id"] if index == 3 else "",
            mime_type="image/png" if index == 3 else "",
            client_sequence=index + 1,
        )

    restored_ids = []
    cursor = ""
    while True:
        page = store.history_page("yoyo", cursor=cursor, limit=2)
        restored_ids.extend(message["id"] for message in page["messages"])
        if not page["has_more"]:
            break
        assert page["next_cursor"]
        cursor = page["next_cursor"]

    assert set(restored_ids) == {
        "history-input-%d" % index
        for index in range(7)
    }
    assert len(restored_ids) == len(set(restored_ids))
    assert store.history_page("other")["messages"] == []

    restored_media = store.media(
        uploaded["media_id"],
        "phone-new",
        session_id="yoyo",
    )
    assert restored_media is not None
    assert restored_media[0].read_bytes().endswith(b"restorable-image")
    assert (
        store.media(
            uploaded["media_id"],
            "phone-other",
            session_id="other",
        )
        is None
    )


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


def test_streaming_voice_event_is_visible_on_first_chunk_and_then_sealed(
    monkeypatch,
    tmp_path,
):
    module = _load_app_channel(monkeypatch, tmp_path)
    store = module.AppInboxStore(tmp_path / "app_channel" / "app.db")
    store.register_device("phone-stream", "yoyo", platform="android")

    voice_media = store.start_media_stream(
        b"first-frame",
        "phone-stream",
        "audio/mpeg",
    )
    assert voice_media is not None
    assert store.queue_action(
        action_id="stream-action-1",
        session_id="yoyo",
        device_id="phone-stream",
        source="chat_channel",
        voice_media=voice_media,
        voice_text="stream this reply",
    )

    first_event = store.events_after("phone-stream")[0]
    assert first_event["streaming"] is True
    assert first_event["stream_token"] == voice_media["stream_token"]
    assert store.append_media_stream(
        voice_media["media_id"],
        b"second-frame",
    )
    assert store.finish_media_stream(
        voice_media["media_id"],
        duration_ms=980,
    )

    completed_event = store.events_after("phone-stream")[0]
    assert completed_event["streaming"] is False
    assert completed_event["stream_token"] == ""
    assert completed_event["duration_ms"] == 980
    media = store.media(
        voice_media["media_id"],
        "phone-stream",
        session_id="yoyo",
    )
    assert media is not None
    assert media[0].read_bytes() == b"first-framesecond-frame"


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


def test_profile_mood_is_derived_from_affect_state_not_message_text(
    monkeypatch,
    tmp_path,
):
    module = _load_app_channel(monkeypatch, tmp_path)

    calm = module._mood_descriptor(
        {
            "mood_valence": 0.62,
            "energy": 0.55,
            "security": 0.76,
            "longing": 0.28,
            "playfulness": 0.48,
            "sensitivity": 0.24,
            "expression_drive": 0.42,
            "sharing_drive": 0.34,
            "interruption_caution": 0.36,
            "last_updated_at": 123,
        }
    )
    happy = module._mood_descriptor(
        {
            "mood_valence": 0.96,
            "energy": 0.90,
            "security": 0.88,
            "longing": 0.10,
            "playfulness": 0.94,
            "sensitivity": 0.08,
            "expression_drive": 0.72,
            "sharing_drive": 0.78,
            "interruption_caution": 0.08,
        }
    )

    assert calm == {
        "key": "calm",
        "label": "平静",
        "asset": "平静.png",
        "updated_at": 123,
    }
    assert happy["key"] == "happy"
    assert happy["asset"] == "开心.png"
    model_selected = module._mood_descriptor(
        {
            "display_mood": "shy",
            "display_mood_updated_at": 789,
        }
    )
    assert model_selected == {
        "key": "shy",
        "label": "有点害羞",
        "asset": "害羞.png",
        "updated_at": 789,
    }


def test_profile_mood_supports_shy_crying_and_afraid_states(
    monkeypatch,
    tmp_path,
):
    module = _load_app_channel(monkeypatch, tmp_path)
    base = {
        "sharing_drive": 0.40,
        "last_updated_at": 456,
    }

    shy = module._mood_descriptor(
        {
            **base,
            "mood_valence": 0.77,
            "energy": 0.25,
            "security": 0.95,
            "longing": 0.83,
            "playfulness": 0.92,
            "sensitivity": 0.67,
            "expression_drive": 0.55,
            "interruption_caution": 0.95,
        }
    )
    crying = module._mood_descriptor(
        {
            **base,
            "mood_valence": 0.36,
            "energy": 0.16,
            "security": 0.12,
            "longing": 0.22,
            "playfulness": 0.05,
            "sensitivity": 0.90,
            "expression_drive": 0.09,
            "interruption_caution": 0.14,
        }
    )
    afraid = module._mood_descriptor(
        {
            **base,
            "mood_valence": 0.87,
            "energy": 0.16,
            "security": 0.03,
            "longing": 0.73,
            "playfulness": 0.46,
            "sensitivity": 0.14,
            "expression_drive": 0.11,
            "interruption_caution": 0.70,
        }
    )

    assert (shy["key"], shy["asset"]) == ("shy", "害羞.png")
    assert (crying["key"], crying["asset"]) == ("crying", "大哭.png")
    assert (afraid["key"], afraid["asset"]) == ("afraid", "害怕.png")
