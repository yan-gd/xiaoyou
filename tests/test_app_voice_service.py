import importlib.util
import io
import sys
import types
import wave
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_voice_service(monkeypatch):
    class _Logger:
        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    common = types.ModuleType("common")
    common_log = types.ModuleType("common.log")
    common_log.logger = _Logger()
    plugins_common = types.ModuleType("plugins.xiaoyou_common")
    model_gateway = types.ModuleType(
        "plugins.xiaoyou_common.model_gateway"
    )
    model_gateway.chat_completion = lambda **_kwargs: types.SimpleNamespace(
        ok=True,
        content="我想你了",
        error_kind="",
        error_code="",
    )
    trace_service = types.ModuleType(
        "plugins.xiaoyou_common.trace_service"
    )
    trace_service.trace_event = lambda *args, **kwargs: None
    for name, module in {
        "common": common,
        "common.log": common_log,
        "plugins.xiaoyou_common": plugins_common,
        "plugins.xiaoyou_common.model_gateway": model_gateway,
        "plugins.xiaoyou_common.trace_service": trace_service,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    spec = importlib.util.spec_from_file_location(
        "xiaoyou_app_voice_service_test_module",
        ROOT / "plugins" / "xiaoyou_common" / "app_voice_service.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _wav_payload(duration_ms=1000, sample_rate=24000):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(b"\x00\x00" * int(sample_rate * duration_ms / 1000))
    return buffer.getvalue()


def test_app_voice_uses_qwen_asr_and_requested_longyan_cosyvoice(
    monkeypatch,
):
    module = _load_voice_service(monkeypatch)
    monkeypatch.setenv("OPEN_AI_API_KEY", "test-key")
    monkeypatch.delenv("XIAOYOU_APP_TTS_MODEL", raising=False)
    monkeypatch.delenv("XIAOYOU_APP_TTS_VOICE", raising=False)
    service = module.AppVoiceService()

    assert service.transcribe(
        b"audio",
        "audio/mp4",
        session_id="yoyo",
        input_id="voice-1",
    ) == "我想你了"
    assert service.asr_model == "qwen3-asr-flash"
    assert service.tts_model == "cosyvoice-v3-flash"
    assert service.tts_voice == "longyan_v3"

    calls = {}
    wav = _wav_payload(1250)

    class _Response:
        def __init__(self, *, payload=None, content=b"", mime="application/json"):
            self._payload = payload or {}
            self.content = content
            self.headers = {"Content-Type": mime}

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def _post(url, headers, json, timeout):
        calls["url"] = url
        calls["payload"] = json
        calls["timeout"] = timeout
        return _Response(
            payload={
                "output": {
                    "audio": {"url": "https://example.invalid/voice.wav"}
                }
            }
        )

    monkeypatch.setattr(module.requests, "post", _post)
    monkeypatch.setattr(
        module.requests,
        "get",
        lambda *_args, **_kwargs: _Response(
            content=wav,
            mime="audio/wav",
        ),
    )

    voice = service.synthesize(
        "我也想你呀",
        session_id="yoyo",
        input_id="voice-1",
    )
    assert calls["url"].endswith("/audio/tts/SpeechSynthesizer")
    assert calls["payload"]["model"] == "cosyvoice-v3-flash"
    assert calls["payload"]["input"]["voice"] == "longyan_v3"
    assert calls["payload"]["input"]["format"] == "wav"
    assert voice.mime_type == "audio/wav"
    assert 1240 <= voice.duration_ms <= 1260
