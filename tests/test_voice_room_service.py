import importlib.util
import io
import json
import struct
import sys
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
