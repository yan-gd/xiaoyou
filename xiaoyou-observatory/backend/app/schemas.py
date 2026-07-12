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
    container: ContainerState
    wechat: ServicePulse
    model: ServicePulse
    memory: ServicePulse
    vision: ServicePulse
    last_input_at: str = ""
    last_output_at: str = ""
    recent_errors: int = 0
    qr_available: bool = False
    plugin_versions: list[str] = []


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
