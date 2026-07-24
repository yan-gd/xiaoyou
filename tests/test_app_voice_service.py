import base64
import importlib.util
import io
import json
import struct
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


def test_streaming_wav_placeholder_length_uses_actual_downloaded_bytes(
    monkeypatch,
):
    module = _load_voice_service(monkeypatch)
    payload = bytearray(_wav_payload(1800))
    struct.pack_into("<I", payload, 4, 0x7FFFFFFF)
    struct.pack_into("<I", payload, 40, 0x7FFFFFFF)

    assert 1790 <= module._wav_duration_ms(bytes(payload)) <= 1810


def test_app_voice_uses_qwen_asr_and_volcengine_rouguhunshi_tts(
    monkeypatch,
):
    module = _load_voice_service(monkeypatch)
    monkeypatch.setenv("OPEN_AI_API_KEY", "test-key")
    monkeypatch.setenv("XIAOYOU_APP_TTS_API_KEY", "speech-test-key")
    monkeypatch.delenv("XIAOYOU_APP_TTS_PROVIDER", raising=False)
    monkeypatch.delenv("XIAOYOU_APP_TTS_MODEL", raising=False)
    monkeypatch.delenv("XIAOYOU_APP_TTS_VOICE", raising=False)
    monkeypatch.delenv("XIAOYOU_APP_TTS_ENDPOINT", raising=False)
    service = module.AppVoiceService()

    assert service.transcribe(
        b"audio",
        "audio/mp4",
        session_id="yoyo",
        input_id="voice-1",
    ) == "我想你了"
    assert service.asr_model == "qwen3-asr-flash"
    assert service.tts_provider == "volcengine"
    assert service.tts_model == "seed-tts-2.0"
    assert service.tts_voice == "ICL_uranus_zh_female_rouguhunshi_tob"
    assert service.tts_loudness_rate == 100
    assert service.asr_available is True
    assert service.tts_available is True

    calls = {}
    audio_parts = [b"ID3-rouguhunshi-", b"voice"]

    class _Response:
        def __init__(self):
            self.headers = {"Content-Type": "application/x-ndjson"}

        def raise_for_status(self):
            return None

        def iter_lines(self):
            return iter(
                [
                    json.dumps(
                        {
                            "code": 0,
                            "sequence": 1,
                            "data": base64.b64encode(
                                audio_parts[0]
                            ).decode("ascii"),
                        }
                    ).encode("utf-8"),
                    json.dumps(
                        {
                            "code": 0,
                            "sequence": -1,
                            "data": base64.b64encode(
                                audio_parts[1]
                            ).decode("ascii"),
                            "addition": {"duration": "1250"},
                        }
                    ).encode("utf-8"),
                ]
            )

    def _post(url, headers, json, timeout, stream):
        calls["url"] = url
        calls["headers"] = headers
        calls["payload"] = json
        calls["timeout"] = timeout
        calls["stream"] = stream
        return _Response()

    monkeypatch.setattr(module.requests, "post", _post)

    voice = service.synthesize(
        "我也想你呀",
        session_id="yoyo",
        input_id="voice-1",
    )
    assert calls["url"] == (
        "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
    )
    assert calls["stream"] is True
    assert calls["headers"]["X-Api-Key"] == "speech-test-key"
    assert calls["headers"]["X-Api-Resource-Id"] == "seed-tts-2.0"
    assert "Authorization" not in calls["headers"]
    assert (
        calls["payload"]["req_params"]["speaker"]
        == "ICL_uranus_zh_female_rouguhunshi_tob"
    )
    assert calls["payload"]["req_params"]["audio_params"] == {
        "format": "mp3",
        "sample_rate": 24000,
        "loudness_rate": 100,
    }
    assert voice.data == b"".join(audio_parts)
    assert voice.mime_type == "audio/mpeg"
    assert voice.duration_ms == 1250


def test_volcengine_tts_loudness_rate_is_configurable_and_clamped(
    monkeypatch,
):
    module = _load_voice_service(monkeypatch)
    monkeypatch.setenv("OPEN_AI_API_KEY", "asr-test-key")
    monkeypatch.setenv("XIAOYOU_APP_TTS_API_KEY", "speech-test-key")

    monkeypatch.setenv("XIAOYOU_APP_TTS_LOUDNESS_RATE", "45")
    assert module.AppVoiceService().tts_loudness_rate == 45

    monkeypatch.setenv("XIAOYOU_APP_TTS_LOUDNESS_RATE", "500")
    assert module.AppVoiceService().tts_loudness_rate == 100

    monkeypatch.setenv("XIAOYOU_APP_TTS_LOUDNESS_RATE", "-500")
    assert module.AppVoiceService().tts_loudness_rate == -50

    monkeypatch.setenv(
        "XIAOYOU_APP_TTS_LOUDNESS_RATE",
        "not-an-integer",
    )
    assert module.AppVoiceService().tts_loudness_rate == 100


def test_volcengine_tts_supports_legacy_app_id_access_key_auth(
    monkeypatch,
):
    module = _load_voice_service(monkeypatch)
    monkeypatch.setenv("OPEN_AI_API_KEY", "asr-test-key")
    monkeypatch.delenv("XIAOYOU_APP_TTS_API_KEY", raising=False)
    monkeypatch.delenv("VOLCENGINE_TTS_API_KEY", raising=False)
    monkeypatch.setenv("XIAOYOU_APP_TTS_APP_ID", "speech-app-id")
    monkeypatch.setenv(
        "XIAOYOU_APP_TTS_ACCESS_KEY",
        "speech-access-key",
    )
    service = module.AppVoiceService()
    calls = {}

    class _Response:
        def raise_for_status(self):
            return None

        def iter_lines(self):
            return iter(
                [
                    json.dumps(
                        {
                            "code": 0,
                            "sequence": -1,
                            "data": base64.b64encode(b"mp3").decode("ascii"),
                            "addition": json.dumps({"duration": 420}),
                        }
                    ).encode("utf-8")
                ]
            )

    def _post(_url, headers, json, **_kwargs):
        calls["headers"] = headers
        calls["payload"] = json
        return _Response()

    monkeypatch.setattr(module.requests, "post", _post)
    voice = service.synthesize("好呀")

    assert calls["headers"]["X-Api-App-Id"] == "speech-app-id"
    assert (
        calls["headers"]["X-Api-Access-Key"]
        == "speech-access-key"
    )
    assert "X-Api-Key" not in calls["headers"]
    assert calls["payload"]["user"]["uid"] == "speech-app-id"
    assert voice.duration_ms == 420


def test_missing_volcengine_tts_credentials_keeps_asr_available(
    monkeypatch,
):
    module = _load_voice_service(monkeypatch)
    monkeypatch.setenv("OPEN_AI_API_KEY", "asr-test-key")
    for name in (
        "XIAOYOU_APP_TTS_API_KEY",
        "VOLCENGINE_TTS_API_KEY",
        "XIAOYOU_APP_TTS_APP_ID",
        "VOLCENGINE_TTS_APP_ID",
        "XIAOYOU_APP_TTS_ACCESS_KEY",
        "VOLCENGINE_TTS_ACCESS_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    service = module.AppVoiceService()

    assert service.available is True
    assert service.asr_available is True
    assert service.tts_available is False
    try:
        service.synthesize("这次退回文字")
    except module.AppVoiceError as exc:
        assert str(exc) == "voice_not_configured"
    else:
        raise AssertionError("missing TTS credentials must not call provider")
