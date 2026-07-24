# -*- coding: utf-8 -*-
"""App-only speech recognition and synthesis.

The service deliberately stays outside the generic WeChat voice switches.
App voice input is transcribed into the existing text conversation pipeline,
and only contexts explicitly marked by AppChannel receive synthesized audio.
"""

import base64
import io
import json
import os
import time
import uuid
import wave

import requests

from common.log import logger
from plugins.xiaoyou_common.model_gateway import chat_completion
from plugins.xiaoyou_common.trace_service import trace_event


class AppVoiceError(RuntimeError):
    """A stable, non-secret error surfaced to the App transport."""


class SynthesizedVoice:
    def __init__(self, data, mime_type="audio/wav", duration_ms=0):
        self.data = bytes(data or b"")
        self.mime_type = str(mime_type or "audio/wav")
        self.duration_ms = max(0, int(duration_ms or 0))


class AppVoiceService:
    """Qwen ASR and provider-selectable TTS used only by AppChannel."""

    def __init__(self):
        self.enabled = _truthy(os.getenv("XIAOYOU_APP_VOICE_ENABLED", "true"))
        self.api_key = (
            os.getenv("XIAOYOU_APP_VOICE_API_KEY")
            or os.getenv("OPEN_AI_API_KEY")
            or os.getenv("DASHSCOPE_API_KEY")
            or ""
        ).strip()
        self.asr_model = (
            os.getenv("XIAOYOU_APP_ASR_MODEL", "qwen3-asr-flash").strip()
            or "qwen3-asr-flash"
        )
        self.tts_provider = (
            os.getenv("XIAOYOU_APP_TTS_PROVIDER", "volcengine").strip().lower()
            or "volcengine"
        )
        self.tts_model = (
            os.getenv("XIAOYOU_APP_TTS_MODEL", "seed-tts-2.0").strip()
            or "seed-tts-2.0"
        )
        self.tts_voice = (
            os.getenv(
                "XIAOYOU_APP_TTS_VOICE",
                "zh_female_xiaohe_uranus_bigtts",
            ).strip()
            or "zh_female_xiaohe_uranus_bigtts"
        )
        self.compatible_base_url = (
            os.getenv("XIAOYOU_APP_ASR_BASE_URL")
            or os.getenv("OPEN_AI_API_BASE")
            or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ).rstrip("/")
        default_tts_endpoint = (
            "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/"
            "SpeechSynthesizer"
            if self.tts_provider in ("dashscope", "aliyun")
            else "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
        )
        self.tts_endpoint = (
            os.getenv("XIAOYOU_APP_TTS_ENDPOINT")
            or default_tts_endpoint
        ).strip()
        self.tts_api_key = (
            os.getenv("XIAOYOU_APP_TTS_API_KEY")
            or os.getenv("VOLCENGINE_TTS_API_KEY")
            or ""
        ).strip()
        self.tts_app_id = (
            os.getenv("XIAOYOU_APP_TTS_APP_ID")
            or os.getenv("VOLCENGINE_TTS_APP_ID")
            or ""
        ).strip()
        self.tts_access_key = (
            os.getenv("XIAOYOU_APP_TTS_ACCESS_KEY")
            or os.getenv("VOLCENGINE_TTS_ACCESS_KEY")
            or ""
        ).strip()
        self.timeout = max(
            10,
            min(int(os.getenv("XIAOYOU_APP_VOICE_TIMEOUT", "45") or 45), 120),
        )

    @property
    def available(self):
        return self.asr_available

    @property
    def asr_available(self):
        return bool(self.enabled and self.api_key)

    @property
    def tts_available(self):
        if not self.enabled:
            return False
        if self.tts_provider in ("dashscope", "aliyun"):
            return bool(self.api_key)
        return bool(
            self.tts_api_key
            or (self.tts_app_id and self.tts_access_key)
        )

    def transcribe(
        self,
        audio_bytes,
        mime_type,
        *,
        session_id="",
        trace_id="",
        input_id="",
    ):
        if not self.asr_available:
            raise AppVoiceError("voice_not_configured")
        audio_bytes = bytes(audio_bytes or b"")
        if not audio_bytes:
            raise AppVoiceError("empty_audio")

        data_uri = "data:%s;base64,%s" % (
            _safe_audio_mime(mime_type),
            base64.b64encode(audio_bytes).decode("ascii"),
        )
        result = chat_completion(
            component="AppVoice",
            purpose="speech_to_text",
            payload={
                "model": self.asr_model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_audio",
                                "input_audio": {"data": data_uri},
                            }
                        ],
                    }
                ],
                "stream": False,
                "asr_options": {"enable_itn": True},
            },
            timeout=self.timeout,
            api_key=self.api_key,
            base_url=self.compatible_base_url,
            session_id=session_id,
            trace_id=trace_id,
            input_id=input_id,
            retry_without_thinking=False,
        )
        text = str(result.content or "").strip()
        if not result.ok or not text:
            logger.warning(
                "[AppVoice] ASR failed model=%s input_id=%s kind=%s code=%s",
                self.asr_model,
                str(input_id or "-"),
                str(result.error_kind or "empty_result"),
                str(result.error_code or "-"),
            )
            raise AppVoiceError("speech_recognition_failed")
        return text

    def synthesize(
        self,
        text,
        *,
        session_id="",
        trace_id="",
        input_id="",
    ):
        if not self.tts_available:
            raise AppVoiceError("voice_not_configured")
        text = str(text or "").strip()
        if not text:
            raise AppVoiceError("empty_tts_text")

        started = time.monotonic()
        try:
            if self.tts_provider in ("dashscope", "aliyun"):
                audio_bytes, mime_type, duration_ms = (
                    self._synthesize_dashscope(text)
                )
            elif self.tts_provider in ("volcengine", "volc"):
                audio_bytes, mime_type, duration_ms = (
                    self._synthesize_volcengine(text)
                )
            else:
                raise AppVoiceError("unsupported_tts_provider")
        except AppVoiceError:
            raise
        except Exception as exc:
            logger.warning(
                "[AppVoice] TTS failed model=%s input_id=%s error=%s",
                self.tts_model,
                str(input_id or "-"),
                type(exc).__name__,
            )
            raise AppVoiceError("speech_synthesis_failed") from exc

        elapsed = time.monotonic() - started
        trace_event(
            "app_voice_synthesized",
            status="ok",
            trace_id=trace_id,
            input_id=input_id,
            session_id=session_id,
            attrs={
                "provider": self.tts_provider,
                "model": self.tts_model,
                "voice": self.tts_voice,
                "characters": len(text),
                "audio_bytes": len(audio_bytes),
                "duration_ms": duration_ms,
                "elapsed_ms": int(elapsed * 1000),
            },
        )
        logger.info(
            "[AppVoice] TTS completed provider=%s model=%s voice=%s "
            "characters=%s "
            "duration_ms=%s elapsed=%.2fs",
            self.tts_provider,
            self.tts_model,
            self.tts_voice,
            len(text),
            duration_ms,
            elapsed,
        )
        return SynthesizedVoice(audio_bytes, mime_type, duration_ms)

    def _synthesize_volcengine(self, text):
        headers = {
            "Content-Type": "application/json",
            "X-Api-Resource-Id": self.tts_model,
            "X-Api-Request-Id": uuid.uuid4().hex,
        }
        if self.tts_api_key:
            headers["X-Api-Key"] = self.tts_api_key
        else:
            headers["X-Api-App-Id"] = self.tts_app_id
            headers["X-Api-Access-Key"] = self.tts_access_key

        response = requests.post(
            self.tts_endpoint,
            headers=headers,
            json={
                "user": {"uid": self.tts_app_id or "xiaoyou-app"},
                "req_params": {
                    "text": text,
                    "speaker": self.tts_voice,
                    "audio_params": {
                        "format": "mp3",
                        "sample_rate": 24000,
                    },
                },
            },
            timeout=self.timeout,
            stream=True,
        )
        response.raise_for_status()

        chunks = []
        duration_ms = 0
        for raw_line in response.iter_lines():
            raw_line = bytes(raw_line or b"").strip()
            if not raw_line:
                continue
            if raw_line.startswith(b"data:"):
                raw_line = raw_line[5:].strip()
            try:
                payload = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise AppVoiceError("speech_synthesis_invalid_response") from exc

            code = payload.get("code")
            if code not in (None, 0, 3000, 20000000):
                logger.warning(
                    "[AppVoice] Volcengine TTS rejected code=%s message=%s",
                    str(code),
                    str(payload.get("message") or "")[:160],
                )
                raise AppVoiceError("speech_synthesis_rejected")
            encoded = str(payload.get("data") or "").strip()
            if encoded:
                try:
                    chunks.append(base64.b64decode(encoded, validate=True))
                except ValueError as exc:
                    raise AppVoiceError(
                        "speech_synthesis_invalid_audio"
                    ) from exc
            duration_ms = max(
                duration_ms,
                _volcengine_duration_ms(payload.get("addition")),
            )

        audio_bytes = b"".join(chunks)
        if not audio_bytes or len(audio_bytes) > 12 * 1024 * 1024:
            raise AppVoiceError("speech_synthesis_invalid_audio")
        return audio_bytes, "audio/mpeg", duration_ms

    def _synthesize_dashscope(self, text):
        response = requests.post(
            self.tts_endpoint,
            headers={
                "Authorization": "Bearer " + self.api_key,
                "Content-Type": "application/json",
            },
            json={
                "model": self.tts_model,
                "input": {
                    "text": text,
                    "voice": self.tts_voice,
                    "format": "wav",
                    "sample_rate": 24000,
                },
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        audio = ((payload.get("output") or {}).get("audio") or {})
        audio_url = str(audio.get("url") or "").strip()
        if not audio_url.startswith(("https://", "http://")):
            raise AppVoiceError("speech_synthesis_missing_audio")

        download = requests.get(audio_url, timeout=self.timeout)
        download.raise_for_status()
        audio_bytes = bytes(download.content or b"")
        if not audio_bytes or len(audio_bytes) > 12 * 1024 * 1024:
            raise AppVoiceError("speech_synthesis_invalid_audio")
        mime_type = str(
            download.headers.get("Content-Type") or "audio/wav"
        ).split(";", 1)[0].strip()
        return audio_bytes, mime_type, _wav_duration_ms(audio_bytes)


def _truthy(value):
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _safe_audio_mime(value):
    value = str(value or "").split(";", 1)[0].strip().lower()
    if value in {
        "audio/aac",
        "audio/flac",
        "audio/m4a",
        "audio/mp4",
        "audio/mpeg",
        "audio/ogg",
        "audio/opus",
        "audio/wav",
        "audio/webm",
        "audio/x-m4a",
    }:
        return value
    return "audio/mp4"


def _volcengine_duration_ms(addition):
    if isinstance(addition, str):
        try:
            addition = json.loads(addition)
        except ValueError:
            return 0
    if not isinstance(addition, dict):
        return 0
    for key in ("duration", "duration_ms", "audio_duration"):
        try:
            value = int(float(addition.get(key) or 0))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0


def _wav_duration_ms(data):
    try:
        with wave.open(io.BytesIO(data), "rb") as source:
            rate = source.getframerate()
            channels = source.getnchannels()
            sample_width = source.getsampwidth()
            declared_frames = source.getnframes()
            raw_frames = source.readframes(declared_frames)
        bytes_per_frame = channels * sample_width
        actual_frames = (
            len(raw_frames) // bytes_per_frame
            if bytes_per_frame > 0
            else 0
        )
        # Streaming WAV responses may use 0x7fffffff as a placeholder data
        # length. Trust the bytes that were actually downloaded instead of
        # turning that sentinel into a 12-hour voice message.
        frames = min(declared_frames, actual_frames) if actual_frames else 0
        if rate > 0 and frames > 0:
            return int(frames * 1000 / rate)
    except (EOFError, wave.Error):
        pass
    return 0
