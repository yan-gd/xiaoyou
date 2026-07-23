# -*- coding: utf-8 -*-
"""App-only speech recognition and synthesis.

The service deliberately stays outside the generic WeChat voice switches.
App voice input is transcribed into the existing text conversation pipeline,
and only contexts explicitly marked by AppChannel receive synthesized audio.
"""

import base64
import io
import os
import time
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
    """Qwen ASR/TTS adapter used exclusively by AppChannel."""

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
        self.tts_model = (
            os.getenv("XIAOYOU_APP_TTS_MODEL", "cosyvoice-v3-flash").strip()
            or "cosyvoice-v3-flash"
        )
        self.tts_voice = (
            os.getenv("XIAOYOU_APP_TTS_VOICE", "longyan_v3").strip()
            or "longyan_v3"
        )
        self.compatible_base_url = (
            os.getenv("XIAOYOU_APP_ASR_BASE_URL")
            or os.getenv("OPEN_AI_API_BASE")
            or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ).rstrip("/")
        self.tts_endpoint = (
            os.getenv("XIAOYOU_APP_TTS_ENDPOINT")
            or "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/"
            "SpeechSynthesizer"
        ).strip()
        self.timeout = max(
            10,
            min(int(os.getenv("XIAOYOU_APP_VOICE_TIMEOUT", "45") or 45), 120),
        )

    @property
    def available(self):
        return bool(self.enabled and self.api_key)

    def transcribe(
        self,
        audio_bytes,
        mime_type,
        *,
        session_id="",
        trace_id="",
        input_id="",
    ):
        if not self.available:
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
        if not self.available:
            raise AppVoiceError("voice_not_configured")
        text = str(text or "").strip()
        if not text:
            raise AppVoiceError("empty_tts_text")

        started = time.monotonic()
        try:
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
            duration_ms = _wav_duration_ms(audio_bytes)
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
                "model": self.tts_model,
                "voice": self.tts_voice,
                "characters": len(text),
                "audio_bytes": len(audio_bytes),
                "duration_ms": duration_ms,
                "elapsed_ms": int(elapsed * 1000),
            },
        )
        logger.info(
            "[AppVoice] TTS completed model=%s voice=%s characters=%s "
            "duration_ms=%s elapsed=%.2fs",
            self.tts_model,
            self.tts_voice,
            len(text),
            duration_ms,
            elapsed,
        )
        return SynthesizedVoice(audio_bytes, mime_type, duration_ms)


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


def _wav_duration_ms(data):
    try:
        with wave.open(io.BytesIO(data), "rb") as source:
            rate = source.getframerate()
            frames = source.getnframes()
        if rate > 0:
            return int(frames * 1000 / rate)
    except (EOFError, wave.Error):
        pass
    return 0
