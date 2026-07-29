import importlib.util
import io
import json
import struct
import sys
import threading
import time
import types
import wave
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_service(monkeypatch, tmp_path):
    class _Logger:
        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    common = types.ModuleType("common")
    common_log = types.ModuleType("common.log")
    common_log.logger = _Logger()
    plugins = types.ModuleType("plugins")
    plugins_common = types.ModuleType("plugins.xiaoyou_common")
    context_service = types.ModuleType(
        "plugins.xiaoyou_common.context_service"
    )
    context_service.build_character_context = (
        lambda **_kwargs: "你是小悠，与 YoYo 延续真实亲密关系。"
    )
    runtime_paths = types.ModuleType(
        "plugins.xiaoyou_common.runtime_paths"
    )
    runtime_paths.runtime_path = (
        lambda *_args, **_kwargs: str(tmp_path / "voice_rooms.db")
    )
    for name, module in {
        "common": common,
        "common.log": common_log,
        "plugins": plugins,
        "plugins.xiaoyou_common": plugins_common,
        "plugins.xiaoyou_common.context_service": context_service,
        "plugins.xiaoyou_common.runtime_paths": runtime_paths,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    spec = importlib.util.spec_from_file_location(
        "xiaoyou_voice_room_service_test_module",
        ROOT / "plugins" / "xiaoyou_common" / "voice_room_service.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _wav(duration_ms=240, sample_rate=16000):
    target = io.BytesIO()
    with wave.open(target, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(
            b"\x01\x00" * int(sample_rate * duration_ms / 1000)
        )
    return target.getvalue()


def _stereo_wav(duration_ms=240, sample_rate=48000):
    target = io.BytesIO()
    with wave.open(target, "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(
            b"\x20\x00\xe0\xff"
            * int(sample_rate * duration_ms / 1000)
        )
    return target.getvalue()


def _server_frame(event, payload, session_id="session-1"):
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    session = session_id.encode("utf-8")
    return (
        bytes((0x11, 0x94, 0x10, 0x00))
        + struct.pack(">I", event)
        + struct.pack(">I", len(session))
        + session
        + struct.pack(">I", len(encoded))
        + encoded
    )


def test_android_wav_is_normalized_to_o2_audio(monkeypatch, tmp_path):
    module = _load_service(monkeypatch, tmp_path)

    pcm = module.wav_to_pcm16(
        _stereo_wav(duration_ms=250, sample_rate=48000)
    )

    assert len(pcm) == 16000 * 2 // 4
    assert pcm == b"\x00\x00" * 4000


def test_o2_protocol_frame_and_context_pairs(monkeypatch, tmp_path):
    module = _load_service(monkeypatch, tmp_path)

    parsed = module.parse_server_frame(
        _server_frame(
            module.SESSION_STARTED,
            {"dialog_id": "dialog-1"},
        )
    )
    assert parsed["event"] == module.SESSION_STARTED
    assert parsed["session_id"] == "session-1"
    assert parsed["payload"]["dialog_id"] == "dialog-1"

    context = module.normalize_dialog_context(
        [
            {"role": "assistant", "content": "orphan"},
            {"role": "user", "content": "昨晚我们说了晚安"},
            {"role": "assistant", "content": "嗯，我记得"},
            {"role": "user", "content": "incomplete"},
        ]
    )
    assert context == [
        {
            "role": "user",
            "text": "昨晚我们说了晚安",
            "timestamp": 0,
        },
        {
            "role": "assistant",
            "text": "嗯，我记得",
            "timestamp": 0,
        },
    ]


def test_o2_steady_state_has_no_read_deadline_and_interrupts_before_truncate(
    monkeypatch,
    tmp_path,
):
    module = _load_service(monkeypatch, tmp_path)

    class _Socket:
        def __init__(self):
            self.frames = []
            self.timeouts = []
            self.responses = [
                _server_frame(module.CONNECTION_STARTED, {}),
                _server_frame(
                    module.SESSION_STARTED,
                    {"dialog_id": "dialog-live"},
                ),
            ]

        def send_binary(self, frame):
            self.frames.append(frame)

        def recv(self):
            return self.responses.pop(0)

        def settimeout(self, value):
            self.timeouts.append(value)

    socket = _Socket()
    session = module.VolcO2RealtimeSession(
        app_id="app-id",
        access_key="access-key",
        session_id="session-id",
        start_payload={"dialog": {}},
        websocket_factory=lambda *_args, **_kwargs: socket,
    )

    assert session.start() == "dialog-live"
    assert socket.timeouts == [None]

    socket.frames.clear()
    assert session.truncate("reply-1", 320) is True
    assert [
        struct.unpack(">I", frame[4:8])[0]
        for frame in socket.frames
    ] == [
        module.CLIENT_INTERRUPT,
        module.CONVERSATION_TRUNCATE,
    ]


def test_late_truncate_cannot_mark_the_next_turn_partial(
    monkeypatch,
    tmp_path,
):
    module = _load_service(monkeypatch, tmp_path)
    live = module._VoiceRoomLiveRuntime(
        {"room_id": "room-1"},
        provider=object(),
        finalize_callback=lambda _snapshot: None,
    )
    live.reply_id = "reply-1"
    live.user_text = "第一轮"
    live.assistant_text = "第一轮回复"

    live._snapshot_turn()

    assert live.mark_truncated("reply-1", 400) is False
    assert live.barge_in is False
    assert live.played_ms == 0


def test_realtime_pcm_is_forwarded_at_twenty_millisecond_cadence(
    monkeypatch,
    tmp_path,
):
    module = _load_service(monkeypatch, tmp_path)
    clock = [100.0]
    sleeps = []

    def _monotonic():
        return clock[0]

    def _sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    class _Socket:
        def __init__(self):
            self.frames = []
            self.complete = threading.Event()

        def send_binary(self, frame):
            self.frames.append(frame)
            if len(self.frames) == 5:
                self.complete.set()

    monkeypatch.setattr(module.time, "monotonic", _monotonic)
    monkeypatch.setattr(module.time, "sleep", _sleep)
    session = module.VolcO2RealtimeSession(
        app_id="app-id",
        access_key="access-key",
        session_id="session-id",
        start_payload={},
    )
    socket = _Socket()
    session.socket = socket

    session.send_audio(b"\x01\x00" * 1600)

    assert socket.complete.wait(timeout=1)
    assert len(socket.frames) == 5
    assert len(sleeps) == 4
    assert all(abs(value - 0.02) < 0.000001 for value in sleeps)
    assert session.audio_bytes_sent == 3200
    session.close()


def test_voice_rooms_are_separate_and_project_memory_async(
    monkeypatch,
    tmp_path,
):
    module = _load_service(monkeypatch, tmp_path)
    monkeypatch.setenv("XIAOYOU_VOICE_ROOM_APP_ID", "app-id")
    monkeypatch.setenv("XIAOYOU_VOICE_ROOM_ACCESS_KEY", "access-token")

    short_calls = []
    long_calls = []

    class _ShortMemory:
        def build_dialog_context_for_external_consumer(self, _session_id):
            return [
                {"role": "user", "content": "你记得昨晚吗", "ts": 10},
                {"role": "assistant", "content": "当然记得", "ts": 11},
            ]

        def append_external_user_message(self, *args, **kwargs):
            short_calls.append(("user", args, kwargs))

        def append_external_assistant_message(self, *args, **kwargs):
            short_calls.append(("assistant", args, kwargs))

    class _LongMemory:
        def append_delivered_assistant_message(self, *args, **kwargs):
            long_calls.append((args, kwargs))

    class _Provider:
        latest = None

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.closed = False
            _Provider.latest = self

        def start(self):
            return "provider-dialog-1"

        def process_turn(self, pcm):
            assert pcm
            return {
                "user_text": "我回来了",
                "assistant_text": "欢迎回来，刚才还在想你。",
                "audio_pcm": b"\x00\x00" * 2400,
            }

        def close(self):
            self.closed = True

    class _MediaStore:
        def save_media_bytes(self, payload, device_id, mime_type):
            assert payload[:4] == b"RIFF"
            assert device_id == "phone"
            assert mime_type == "audio/wav"
            return {
                "media_id": "voice-wav-1",
                "mime_type": "audio/wav",
            }

    instances = {
        "SHORTMEMORY": _ShortMemory(),
        "LONGTERMMEMORY": _LongMemory(),
    }
    store = module.VoiceRoomStore(tmp_path / "rooms.db")
    service = module.VoiceRoomService(
        store=store,
        media_store=_MediaStore(),
        instances_provider=lambda: instances,
        session_factory=_Provider,
    )

    room = service.create_room(session_id="yoyo", device_id="phone")
    assert room["turns"] == []
    payload = _Provider.latest.kwargs["start_payload"]
    assert payload["dialog"]["extra"]["model"] == "1.2.1.1"
    assert payload["dialog"]["dialog_context"][0]["text"] == "你记得昨晚吗"

    result = service.process_turn(
        room_id=room["room_id"],
        device_id="phone",
        turn_id="turn-1",
        audio_bytes=_wav(),
        mime_type="audio/wav",
        duration_ms=240,
    )
    assert result["accepted"] is True
    assert result["turn"]["audio_media_id"] == "voice-wav-1"

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        saved = store.get_room(
            room["room_id"],
            "phone",
            include_turns=True,
        )
        if saved["turns"][0]["memory_status"] == "complete":
            break
        time.sleep(0.02)
    assert saved["turns"][0]["memory_status"] == "complete"
    assert [call[0] for call in short_calls] == ["user", "assistant"]
    assert long_calls[0][1]["source"] == "voice_room"

    finished = service.finish_room(room["room_id"], "phone")
    assert finished["status"] == "complete"
    assert finished["turn_count"] == 1
    assert _Provider.latest.closed is True
    assert service.list_rooms("phone")[0]["room_id"] == room["room_id"]


def test_realtime_room_streams_audio_and_persists_only_delivered_speech(
    monkeypatch,
    tmp_path,
):
    module = _load_service(monkeypatch, tmp_path)
    monkeypatch.setenv("XIAOYOU_VOICE_ROOM_APP_ID", "app-id")
    monkeypatch.setenv("XIAOYOU_VOICE_ROOM_ACCESS_KEY", "access-token")

    memory_calls = []

    class _ShortMemory:
        def build_dialog_context_for_external_consumer(self, _session_id):
            return []

        def append_external_user_message(self, *args, **kwargs):
            memory_calls.append(("short_user", args, kwargs))

        def append_external_assistant_message(self, *args, **kwargs):
            memory_calls.append(("short_assistant", args, kwargs))

    class _LongMemory:
        def append_delivered_assistant_message(self, *args, **kwargs):
            memory_calls.append(("long_assistant", args, kwargs))

        def append_external_user_message(self, *args, **kwargs):
            memory_calls.append(("long_user", args, kwargs))

    class _Provider:
        latest = None

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.handler = None
            self.audio = bytearray()
            self.truncations = []
            self.closed = False
            _Provider.latest = self

        def start(self):
            return "dialog-live"

        def start_receiving(self, handler):
            self.handler = handler

        def send_audio(self, pcm):
            self.audio.extend(pcm)

        def truncate(self, reply_id, audio_end_ms):
            self.truncations.append((reply_id, audio_end_ms))
            return True

        def close(self):
            self.closed = True

    class _MediaStore:
        def save_media_bytes(self, payload, device_id, mime_type):
            assert payload[:4] == b"RIFF"
            assert device_id == "phone"
            assert mime_type == "audio/wav"
            return {
                "media_id": "voice-" + str(len(payload)),
                "mime_type": mime_type,
            }

    store = module.VoiceRoomStore(tmp_path / "live-rooms.db")
    service = module.VoiceRoomService(
        store=store,
        media_store=_MediaStore(),
        instances_provider=lambda: {
            "SHORTMEMORY": _ShortMemory(),
            "LONGTERMMEMORY": _LongMemory(),
        },
        session_factory=_Provider,
    )
    room = service.create_room(session_id="yoyo", device_id="phone")
    provider = _Provider.latest
    assert provider.handler is not None
    assert (
        provider.kwargs["start_payload"]["dialog"]["extra"]["input_mod"]
        == "keep_alive"
    )

    service.send_audio(
        room_id=room["room_id"],
        device_id="phone",
        audio_bytes=b"\x01\x00" * 320,
        mime_type="audio/pcm",
    )
    assert bytes(provider.audio) == b"\x01\x00" * 320

    provider.handler({"event": module.ASR_INFO, "payload": {}})
    provider.handler(
        {
            "event": module.ASR_RESPONSE,
            "payload": {"results": [{"text": "我回来了"}]},
        }
    )
    provider.handler(
        {
            "event": module.ASR_ENDED,
            "payload": {"results": [{"text": "我回来了"}]},
        }
    )
    provider.handler(
        {
            "event": module.TTS_SENTENCE_START,
            "payload": {
                "text": "先抱抱你。",
                "question_id": "question-1",
                "reply_id": "reply-1",
            },
        }
    )
    provider.handler(
        {
            "event": module.TTS_RESPONSE,
            "payload_bytes": b"\x02\x00" * 1200,
        }
    )
    provider.handler(
        {
            "event": module.TTS_SENTENCE_END,
            "payload": {
                "text": "先抱抱你。",
                "question_id": "question-1",
                "reply_id": "reply-1",
            },
        }
    )
    service.truncate(
        room_id=room["room_id"],
        device_id="phone",
        reply_id="reply-1",
        audio_end_ms=50,
    )
    provider.handler(
        {
            "event": module.TTS_SENTENCE_START,
            "payload": {
                "text": "后面这句没有播放完。",
                "question_id": "question-1",
                "reply_id": "reply-1",
            },
        }
    )
    provider.handler(
        {
            "event": module.TTS_RESPONSE,
            "payload_bytes": b"\x03\x00" * 2400,
        }
    )
    provider.handler(
        {
            "event": module.TTS_ENDED,
            "payload": {
                "question_id": "question-1",
                "reply_id": "reply-1",
            },
        }
    )

    deadline = time.monotonic() + 3
    saved = None
    while time.monotonic() < deadline:
        saved = store.get_room(
            room["room_id"],
            "phone",
            include_turns=True,
        )
        if saved["turns"]:
            break
        time.sleep(0.02)
    assert saved is not None
    assert saved["turns"][0]["assistant_text"] == "先抱抱你。"
    assert saved["turns"][0]["delivery_complete"] is False
    assert saved["turns"][0]["terminal_status"] == "partial"
    assert provider.truncations == [("reply-1", 50)]

    events = service.wait_events(
        room_id=room["room_id"],
        device_id="phone",
        after=0,
        timeout=1,
    )
    event_types = [item["type"] for item in events]
    assert "user_speech_started" in event_types
    assert "assistant_audio" in event_types
    assert "interrupted" in event_types
    assert "turn_complete" in event_types

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        delivered = [
            item
            for item in memory_calls
            if item[0] == "long_assistant"
        ]
        if delivered:
            break
        time.sleep(0.02)
    assert delivered[0][2]["delivery_complete"] is False
    assert delivered[0][2]["terminal_status"] == "partial"
