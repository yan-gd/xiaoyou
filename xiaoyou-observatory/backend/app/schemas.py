from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=256)
    otp: str = Field(min_length=6, max_length=32)


class AuthState(BaseModel):
    authenticated: bool
    username: str
    role: Literal["admin", "guest"]
    csrf_token: str
    expires_at: int


class ActionResponse(BaseModel):
    ok: bool
    action: Literal["start", "stop", "restart"]
    message: str


class ResourceState(BaseModel):
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    memory_usage: str = ""


class ContainerState(BaseModel):
    exists: bool = False
    running: bool = False
    status: str = "unknown"
    health: str = "none"
    started_at: str = ""
    finished_at: str = ""
    restart_count: int = 0
    image: str = ""
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    memory_usage: str = ""


class ServicePulse(BaseModel):
    state: Literal["healthy", "idle", "waiting", "degraded", "offline", "unknown"]
    label: str
    detail: str = ""
    last_event_at: str = ""


class RuntimeStatus(BaseModel):
    overall: Literal["online", "waiting_qr", "starting", "stopped", "degraded", "unknown"]
    observed_at: int
    host: ResourceState = Field(default_factory=ResourceState)
    container: ContainerState
    wechat: ServicePulse
    model: ServicePulse
    memory: ServicePulse
    vision: ServicePulse
    last_input_at: str = ""
    last_output_at: str = ""
    total_tokens: int = 0
    today_tokens: int = 0
    token_usage_available: bool = False
    recent_errors: int = 0
    qr_available: bool = False
    plugin_versions: list[str] = []


class MetricPoint(BaseModel):
    observed_at: int
    host_cpu_percent: float = 0.0
    host_memory_percent: float = 0.0
    container_cpu_percent: float = 0.0
    container_memory_percent: float = 0.0
    recent_errors: int = 0
    total_tokens: int = 0
    today_tokens: int = 0
    running: bool = False


class MetricsResponse(BaseModel):
    hours: int
    points: list[MetricPoint]


class QrState(BaseModel):
    available: bool
    login_url: str = ""
    detected_at: str = ""
    status: Literal["waiting", "online", "unavailable"] = "unavailable"


class LogResponse(BaseModel):
    lines: list[str]
    truncated: bool = False


class AuditItem(BaseModel):
    id: int
    action: str
    result: str
    created_at: int
    ip_address: str
