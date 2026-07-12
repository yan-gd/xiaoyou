from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from .controller import ContainerController, ControllerError
from .schemas import ContainerState, QrState, RuntimeStatus, ServicePulse


TIMESTAMP_RE = re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})]")
LOGIN_URL_RE = re.compile(r"https://login\.weixin\.qq\.com/l/[A-Za-z0-9_\-=/+%]+")
PLUGIN_RE = re.compile(r"Plugin\s+([A-Za-z0-9_.-]+)\s+registered")

SENSITIVE_LINE_RE = re.compile(
    r"(?:api[_ -]?key|access[_ -]?key|secret|authorization|bearer\s+|character_desc|"
    r"override config by environ args.*(?:key|token|secret)|ALIYUN_MEMORY_LIBRARY_ID)",
    re.I,
)
SECRET_VALUE_PATTERNS = (
    re.compile(r"(?i)(api[_ -]?key|access[_ -]?key|secret|token|password)\s*[:=]\s*[^\s,}\]]+"),
    re.compile(r"(?i)Bearer\s+[A-Za-z0-9._~+\-/=]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
)


def _timestamp(line: str) -> str:
    match = TIMESTAMP_RE.search(line)
    return match.group(1) if match else ""


def _last_line(lines: list[str], predicate) -> tuple[int, str]:
    for index in range(len(lines) - 1, -1, -1):
        if predicate(lines[index]):
            return index, lines[index]
    return -1, ""


def _handled_background_failure(line: str) -> bool:
    value = str(line or "").lower()
    return (
        "component=shortmemory" in value
        and "purpose=summary" in value
        and ("error_kind=content_inspection" in value or "category=content_inspection" in value)
    )


def redact_log_line(line: str) -> str:
    line = str(line or "").replace("\x00", "")
    if LOGIN_URL_RE.search(line):
        return LOGIN_URL_RE.sub("[微信登录地址已隐藏，请使用重连之门]", line)
    if SENSITIVE_LINE_RE.search(line):
        timestamp = _timestamp(line)
        prefix = f"[{timestamp}] " if timestamp else ""
        return prefix + "[敏感启动配置已隐藏]"
    for pattern in SECRET_VALUE_PATTERNS:
        line = pattern.sub(lambda match: match.group(1) + "=***" if match.lastindex else "***", line)
    return line[:1200]


def redact_logs(raw: str, limit: int = 240) -> list[str]:
    lines = [redact_log_line(line) for line in str(raw or "").splitlines()]
    lines = [line for line in lines if line.strip()]
    return lines[-max(1, min(int(limit), 500)):]


@dataclass(slots=True)
class LogAnalysis:
    qr: QrState
    wechat: ServicePulse
    model: ServicePulse
    memory: ServicePulse
    vision: ServicePulse
    last_input_at: str
    last_output_at: str
    recent_errors: int
    plugin_versions: list[str]


def analyze_logs(raw: str, container_running: bool) -> LogAnalysis:
    lines = str(raw or "").splitlines()
    qr_index, qr_line = _last_line(lines, lambda value: bool(LOGIN_URL_RE.search(value)))
    online_index, online_line = _last_line(
        lines,
        lambda value: "Wechat login success" in value or "Start auto replying" in value,
    )
    waiting_index, waiting_line = _last_line(
        lines,
        lambda value: "Ready to login" in value or "Getting uuid of QR code" in value,
    )

    qr_match = LOGIN_URL_RE.search(qr_line) if qr_line else None
    qr_waiting = container_running and qr_match is not None and qr_index > online_index
    if qr_waiting:
        qr = QrState(
            available=True,
            login_url=qr_match.group(0),
            detected_at=_timestamp(qr_line),
            status="waiting",
        )
    elif container_running and online_index >= 0 and online_index > waiting_index:
        qr = QrState(available=False, status="online")
    else:
        qr = QrState(available=False, status="unavailable")

    if not container_running:
        wechat = ServicePulse(state="offline", label="灵魂连接已沉寂", detail="容器未运行")
    elif qr_waiting or waiting_index > online_index:
        wechat = ServicePulse(
            state="waiting",
            label="等待重新连接",
            detail="微信需要扫描新的登录二维码",
            last_event_at=_timestamp(qr_line or waiting_line),
        )
    elif online_index >= 0:
        wechat = ServicePulse(
            state="healthy",
            label="微信连接稳定",
            detail="小悠已经登录并开始接收消息",
            last_event_at=_timestamp(online_line),
        )
    else:
        wechat = ServicePulse(state="unknown", label="正在辨认连接状态", detail="等待更多启动日志")

    model_ok_index, model_ok_line = _last_line(
        lines,
        lambda value: "stage=model_call_completed" in value and "status=ok" in value,
    )
    model_fail_index, model_fail_line = _last_line(
        lines,
        lambda value: "model_call" in value
        and ("status=failed" in value or "status=error" in value)
        and not _handled_background_failure(value),
    )
    if not container_running:
        model = ServicePulse(state="offline", label="思维回路休眠")
    elif model_ok_index >= model_fail_index and model_ok_index >= 0:
        model = ServicePulse(
            state="healthy",
            label="思维回路清晰",
            detail="最近一次模型调用成功",
            last_event_at=_timestamp(model_ok_line),
        )
    elif model_fail_index >= 0:
        model = ServicePulse(
            state="degraded",
            label="思维回路出现波动",
            detail="最近一次模型调用失败",
            last_event_at=_timestamp(model_fail_line),
        )
    else:
        model = ServicePulse(state="idle", label="思维回路静候唤醒", detail="本次启动后还没有模型调用")

    memory_saved_index, memory_saved_line = _last_line(
        lines,
        lambda value: "long_memory_recorded" in value and "status=saved" in value,
    )
    memory_fail_index, memory_fail_line = _last_line(
        lines,
        lambda value: "long_memory" in value and ("status=failed" in value or "add failed" in value),
    )
    short_memory_loaded = any("[ShortMemory] inited" in line for line in lines)
    if not container_running:
        memory = ServicePulse(state="offline", label="记忆星海休眠")
    elif memory_fail_index > memory_saved_index:
        memory = ServicePulse(
            state="degraded",
            label="记忆星海出现涟漪",
            detail="最近一次长期记忆操作失败，短期记忆仍独立保存",
            last_event_at=_timestamp(memory_fail_line),
        )
    elif memory_saved_index >= 0 or short_memory_loaded:
        memory = ServicePulse(
            state="healthy",
            label="记忆星海稳定",
            detail="短期记忆已载入，长期记忆链路最近无异常",
            last_event_at=_timestamp(memory_saved_line),
        )
    else:
        memory = ServicePulse(state="unknown", label="正在确认记忆星海")

    vision_loaded = any("[QwenVision] inited" in line for line in lines)
    photo_loaded = any("[XiaoyouLifePhoto] inited" in line for line in lines)
    if not container_running:
        vision = ServicePulse(state="offline", label="生活映像休眠")
    elif vision_loaded or photo_loaded:
        vision = ServicePulse(
            state="healthy",
            label="生活映像已就绪",
            detail="视觉理解与生活照能力已经加载",
        )
    else:
        vision = ServicePulse(state="unknown", label="正在确认生活映像")

    _, input_line = _last_line(lines, lambda value: "stage=input_received" in value)
    _, output_line = _last_line(lines, lambda value: "stage=outbound_completed" in value and "status=ok" in value)
    error_lines = [
        line
        for line in lines[-500:]
        if (
            "[ERROR]" in line
            or "Traceback (most recent call last)" in line
            or ("status=failed" in line and not _handled_background_failure(line))
        )
    ]
    plugins: list[str] = []
    for line in lines:
        match = PLUGIN_RE.search(line)
        if match and match.group(1) not in plugins:
            plugins.append(match.group(1))

    return LogAnalysis(
        qr=qr,
        wechat=wechat,
        model=model,
        memory=memory,
        vision=vision,
        last_input_at=_timestamp(input_line),
        last_output_at=_timestamp(output_line),
        recent_errors=len(error_lines),
        plugin_versions=plugins[-20:],
    )


class RuntimeService:
    def __init__(self, controller: ContainerController):
        self.controller = controller
        self._snapshot_lock = threading.Lock()
        self._cached_snapshot: RuntimeStatus | None = None
        self._cached_at = 0.0

    def snapshot(self) -> RuntimeStatus:
        now = time.monotonic()
        if self._cached_snapshot is not None and now - self._cached_at < 1.5:
            return self._cached_snapshot.model_copy(deep=True)
        with self._snapshot_lock:
            now = time.monotonic()
            if self._cached_snapshot is not None and now - self._cached_at < 1.5:
                return self._cached_snapshot.model_copy(deep=True)
            snapshot = self._collect_snapshot()
            self._cached_snapshot = snapshot
            self._cached_at = time.monotonic()
            return snapshot.model_copy(deep=True)

    def invalidate(self) -> None:
        with self._snapshot_lock:
            self._cached_snapshot = None
            self._cached_at = 0.0

    def _collect_snapshot(self) -> RuntimeStatus:
        controller_available = True
        try:
            raw_status = self.controller.status()
        except ControllerError:
            controller_available = False
            raw_status = {"exists": False, "running": False, "status": "unavailable"}

        try:
            raw_stats = self.controller.stats() if raw_status.get("running") else {}
        except ControllerError:
            raw_stats = {}

        container = ContainerState(
            exists=bool(raw_status.get("exists")),
            running=bool(raw_status.get("running")),
            status=str(raw_status.get("status") or "unknown"),
            health=str(raw_status.get("health") or "none"),
            started_at=str(raw_status.get("started_at") or ""),
            finished_at=str(raw_status.get("finished_at") or ""),
            restart_count=int(raw_status.get("restart_count") or 0),
            image=str(raw_status.get("image") or ""),
            cpu_percent=float(raw_stats.get("cpu_percent") or 0.0),
            memory_percent=float(raw_stats.get("memory_percent") or 0.0),
            memory_usage=str(raw_stats.get("memory_usage") or ""),
        )
        try:
            raw_logs = self.controller.logs() if container.exists else ""
        except ControllerError:
            raw_logs = ""
        analysis = analyze_logs(raw_logs, container.running)

        if not controller_available:
            unavailable = ServicePulse(
                state="unknown",
                label="观测链路暂时不可用",
                detail="无法确认小悠当前状态，请检查观测台服务",
            )
            analysis.wechat = unavailable
            analysis.model = unavailable.model_copy(deep=True)
            analysis.memory = unavailable.model_copy(deep=True)
            analysis.vision = unavailable.model_copy(deep=True)
            overall = "degraded"
        elif not container.running:
            overall = "stopped"
        elif analysis.qr.available or analysis.wechat.state == "waiting":
            overall = "waiting_qr"
        elif analysis.wechat.state == "healthy" and analysis.recent_errors == 0:
            overall = "online"
        elif analysis.wechat.state in ("unknown", "idle"):
            overall = "starting"
        else:
            overall = "degraded"

        return RuntimeStatus(
            overall=overall,
            observed_at=int(time.time()),
            container=container,
            wechat=analysis.wechat,
            model=analysis.model,
            memory=analysis.memory,
            vision=analysis.vision,
            last_input_at=analysis.last_input_at,
            last_output_at=analysis.last_output_at,
            recent_errors=analysis.recent_errors,
            qr_available=analysis.qr.available,
            plugin_versions=analysis.plugin_versions,
        )

    def qr_state(self) -> QrState:
        try:
            status = self.controller.status()
            raw_logs = self.controller.logs() if status.get("exists") else ""
        except ControllerError:
            return QrState(available=False, status="unavailable")
        return analyze_logs(raw_logs, bool(status.get("running"))).qr

    def logs(self, limit: int = 240) -> list[str]:
        try:
            return redact_logs(self.controller.logs(), limit=limit)
        except ControllerError as exc:
            return [f"[观测台] 无法读取容器日志：{str(exc)[:180]}"]
