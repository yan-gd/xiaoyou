from __future__ import annotations

import json
import subprocess
import threading
from dataclasses import dataclass

from .config import Settings


class ControllerError(RuntimeError):
    pass


@dataclass(slots=True)
class ControllerResult:
    ok: bool
    stdout: str
    stderr: str = ""


class ContainerController:
    ALLOWED_ACTIONS = {"status", "stats", "logs", "start", "stop", "restart"}

    def __init__(self, settings: Settings):
        self.settings = settings
        self._mock_running = True
        self._mock_lock = threading.Lock()

    def invoke(self, action: str) -> ControllerResult:
        if action not in self.ALLOWED_ACTIONS:
            raise ControllerError("unsupported controller action")
        if self.settings.mock_mode:
            return self._invoke_mock(action)

        command = ["sudo", "-n", str(self.settings.controller_path), action]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.settings.controller_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ControllerError(f"controller timeout during {action}") from exc
        except OSError as exc:
            raise ControllerError(f"controller unavailable: {exc}") from exc

        result = ControllerResult(
            ok=completed.returncode == 0,
            stdout=completed.stdout.strip(),
            stderr=completed.stderr.strip(),
        )
        if not result.ok:
            raise ControllerError(result.stderr or result.stdout or f"{action} failed")
        return result

    def status(self) -> dict:
        result = self.invoke("status")
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ControllerError("invalid status response from controller") from exc
        if not isinstance(value, dict):
            raise ControllerError("invalid status payload")
        return value

    def stats(self) -> dict:
        result = self.invoke("stats")
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def logs(self) -> str:
        return self.invoke("logs").stdout

    def _invoke_mock(self, action: str) -> ControllerResult:
        with self._mock_lock:
            if action == "start":
                self._mock_running = True
                return ControllerResult(True, "mock container started")
            if action == "stop":
                self._mock_running = False
                return ControllerResult(True, "mock container stopped")
            if action == "restart":
                self._mock_running = True
                return ControllerResult(True, "mock container restarted")
            if action == "status":
                payload = {
                    "exists": True,
                    "running": self._mock_running,
                    "status": "running" if self._mock_running else "exited",
                    "health": "none",
                    "started_at": "2026-07-12T10:18:22+08:00" if self._mock_running else "",
                    "finished_at": "" if self._mock_running else "2026-07-12T11:46:08+08:00",
                    "restart_count": 1,
                    "image": "cow-legacy-local:vision-no-think",
                }
                return ControllerResult(True, json.dumps(payload, ensure_ascii=False))
            if action == "stats":
                payload = {
                    "host_cpu_percent": 7.5,
                    "host_memory_percent": 46.8,
                    "host_memory_usage": "958.5MiB / 2.00GiB",
                    "container_cpu_percent": 2.4 if self._mock_running else 0.0,
                    "container_memory_percent": 18.7 if self._mock_running else 0.0,
                    "container_memory_usage": "382.6MiB / 2GiB" if self._mock_running else "0B / 2GiB",
                    # Backwards-compatible aliases for older backends.
                    "cpu_percent": 2.4 if self._mock_running else 0.0,
                    "memory_percent": 18.7 if self._mock_running else 0.0,
                    "memory_usage": "382.6MiB / 2GiB" if self._mock_running else "0B / 2GiB",
                }
                return ControllerResult(True, json.dumps(payload, ensure_ascii=False))
            if action == "logs":
                if not self._mock_running:
                    return ControllerResult(True, "")
                logs = """
[INFO][2026-07-12 10:18:24][plugin_manager.py:41] - Plugin XiaoyouIdentity_v0.4-trace-runtime registered
[INFO][2026-07-12 10:18:24][plugin_manager.py:41] - Plugin ShortMemory_v0.9-style-hygiene registered
[INFO][2026-07-12 10:18:24][plugin_manager.py:41] - Plugin XiaoyouLifePhoto_v0.7-semantic-camera registered
[INFO][2026-07-12 10:18:25][wechat_channel.py:134] - Wechat login success, nickname: 小悠
Start auto replying.
[INFO][2026-07-12 11:42:18][trace_service.py:385] - [Trace] stage=input_received status=accepted trace_id=mock
[INFO][2026-07-12 11:42:20][trace_service.py:385] - [Trace] stage=model_call_completed status=ok trace_id=mock elapsed_ms=1840
[INFO][2026-07-12 11:42:20][chat_gpt_bot.py:94] - [TokenUsage] usage_id=mock-chat-001 component=xiaoyouchat total_tokens=4286 prompt_tokens=3921 completion_tokens=365
[INFO][2026-07-12 11:42:21][trace_service.py:385] - [Trace] stage=outbound_completed status=ok trace_id=mock delivered=True
[INFO][2026-07-12 11:42:21][trace_service.py:385] - [Trace] stage=long_memory_recorded status=saved trace_id=mock
""".strip()
                return ControllerResult(True, logs)
        raise ControllerError("unsupported mock action")
