from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import Settings, get_settings
from .controller import ContainerController, ControllerError
from .database import Database, SessionRecord
from .runtime import RuntimeService
from .schemas import ActionResponse, AuditItem, AuthState, LoginRequest, LogResponse, QrState, RuntimeStatus
from .security import AuthenticationError, RateLimitError, SecurityService


def client_ip(request: Request, settings: Settings) -> str:
    if settings.trusted_proxy:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",", 1)[0].strip()[:128]
    return (request.client.host if request.client else "unknown")[:128]


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    database = Database(settings.database_path)
    database.initialize()
    app.state.settings = settings
    app.state.database = database
    app.state.security = SecurityService(settings, database)
    app.state.controller = ContainerController(settings)
    app.state.runtime = RuntimeService(app.state.controller)
    app.state.action_lock = asyncio.Lock()
    yield


app = FastAPI(
    title="小悠 · 命轨观测台",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def security_dep(request: Request) -> SecurityService:
    return request.app.state.security


def database_dep(request: Request) -> Database:
    return request.app.state.database


def runtime_dep(request: Request) -> RuntimeService:
    return request.app.state.runtime


def current_session(
    request: Request,
    security_service: Annotated[SecurityService, Depends(security_dep)],
    settings: Annotated[Settings, Depends(settings_dep)],
    session_cookie: Annotated[str | None, Cookie(alias="xiaoyou_observatory_session")] = None,
) -> SessionRecord:
    token = request.cookies.get(settings.cookie_name) or session_cookie
    session = security_service.resolve_session(token)
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请重新登录命轨观测台")
    return session


def csrf_session(
    session: Annotated[SessionRecord, Depends(current_session)],
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> SessionRecord:
    if not csrf_token or not secrets_compare(csrf_token, session.csrf_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="操作凭证已失效，请刷新页面")
    return session


def admin_session(
    session: Annotated[SessionRecord, Depends(current_session)],
) -> SessionRecord:
    if session.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="访客只能观测公开命轨")
    return session


def admin_csrf_session(
    session: Annotated[SessionRecord, Depends(csrf_session)],
) -> SessionRecord:
    if session.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="访客无权执行容器命仪")
    return session


def secrets_compare(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(str(left), str(right))


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True, "service": "xiaoyou-observatory", "time": int(time.time())}


@app.post("/api/auth/login", response_model=AuthState)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(settings_dep)],
    database: Annotated[Database, Depends(database_dep)],
    security_service: Annotated[SecurityService, Depends(security_dep)],
):
    ip_address = client_ip(request, settings)
    try:
        admin = security_service.authenticate(
            payload.username.strip(),
            payload.password,
            payload.otp.strip(),
            ip_address,
        )
    except RateLimitError as exc:
        database.add_audit(None, "login", "rate_limited", ip_address)
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    except AuthenticationError as exc:
        database.add_audit(None, "login", "failed", ip_address)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    created = security_service.create_session(
        admin,
        ip_address=ip_address,
        user_agent=request.headers.get("user-agent", ""),
    )
    database.add_audit(admin.id, "login", "success", ip_address)
    response.set_cookie(
        key=settings.cookie_name,
        value=created.token,
        max_age=settings.session_minutes * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )
    return AuthState(
        authenticated=True,
        username=admin.username,
        role="admin",
        csrf_token=created.csrf_token,
        expires_at=created.expires_at,
    )


@app.post("/api/auth/guest", response_model=AuthState)
async def guest_login(
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(settings_dep)],
    security_service: Annotated[SecurityService, Depends(security_dep)],
):
    try:
        created = security_service.create_guest_session(client_ip(request, settings))
    except RateLimitError as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    response.set_cookie(
        key=settings.cookie_name,
        value=created.token,
        max_age=settings.session_minutes * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )
    return AuthState(
        authenticated=True,
        username="访客",
        role="guest",
        csrf_token=created.csrf_token,
        expires_at=created.expires_at,
    )


@app.get("/api/auth/me", response_model=AuthState)
async def me(session: Annotated[SessionRecord, Depends(current_session)]):
    return AuthState(
        authenticated=True,
        username=session.username,
        role=session.role,
        csrf_token=session.csrf_token,
        expires_at=session.expires_at,
    )


@app.post("/api/auth/logout")
async def logout(
    request: Request,
    response: Response,
    session: Annotated[SessionRecord, Depends(csrf_session)],
    settings: Annotated[Settings, Depends(settings_dep)],
    security_service: Annotated[SecurityService, Depends(security_dep)],
    database: Annotated[Database, Depends(database_dep)],
):
    token = request.cookies.get(settings.cookie_name)
    security_service.delete_session(token)
    if session.role == "admin":
        database.add_audit(session.admin_id, "logout", "success", client_ip(request, settings))
    response.delete_cookie(settings.cookie_name, path="/")
    return {"ok": True}


@app.get("/api/status", response_model=RuntimeStatus)
async def runtime_status(
    session: Annotated[SessionRecord, Depends(current_session)],
    runtime: Annotated[RuntimeService, Depends(runtime_dep)],
):
    snapshot = await asyncio.to_thread(runtime.snapshot)
    if session.role == "guest":
        snapshot.qr_available = False
    return snapshot


@app.get("/api/qr", response_model=QrState)
async def qr_state(
    _: Annotated[SessionRecord, Depends(admin_session)],
    runtime: Annotated[RuntimeService, Depends(runtime_dep)],
):
    return await asyncio.to_thread(runtime.qr_state)


@app.get("/api/logs", response_model=LogResponse)
async def recent_logs(
    _: Annotated[SessionRecord, Depends(admin_session)],
    runtime: Annotated[RuntimeService, Depends(runtime_dep)],
    limit: int = 220,
):
    lines = await asyncio.to_thread(runtime.logs, max(20, min(limit, 500)))
    return LogResponse(lines=lines, truncated=len(lines) >= limit)


@app.get("/api/audit", response_model=list[AuditItem])
async def recent_audit(
    _: Annotated[SessionRecord, Depends(admin_session)],
    database: Annotated[Database, Depends(database_dep)],
):
    return [AuditItem(**item) for item in database.recent_audit(12)]


@app.post("/api/container/{action}", response_model=ActionResponse)
async def container_action(
    action: Literal["start", "stop", "restart"],
    request: Request,
    session: Annotated[SessionRecord, Depends(admin_csrf_session)],
    settings: Annotated[Settings, Depends(settings_dep)],
    database: Annotated[Database, Depends(database_dep)],
):
    controller: ContainerController = request.app.state.controller
    lock: asyncio.Lock = request.app.state.action_lock
    ip_address = client_ip(request, settings)
    if lock.locked():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="另一个命轨操作正在进行")

    async with lock:
        try:
            await asyncio.to_thread(controller.invoke, action)
        except ControllerError as exc:
            database.add_audit(session.admin_id, f"container_{action}", "failed", ip_address, str(exc))
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="容器操作失败，请查看脱敏日志") from exc
        database.add_audit(session.admin_id, f"container_{action}", "success", ip_address)
        request.app.state.runtime.invalidate()

    messages = {
        "start": "唤醒指令已送达，小悠正在重新连接命轨",
        "stop": "容器已经停止，小悠进入休眠",
        "restart": "重启指令已送达，请留意新的微信登录二维码",
    }
    return ActionResponse(ok=True, action=action, message=messages[action])


@app.get("/api/events")
async def events(
    request: Request,
    session: Annotated[SessionRecord, Depends(current_session)],
    settings: Annotated[Settings, Depends(settings_dep)],
    runtime: Annotated[RuntimeService, Depends(runtime_dep)],
):
    async def stream():
        previous = ""
        while not await request.is_disconnected():
            snapshot = await asyncio.to_thread(runtime.snapshot)
            if session.role == "guest":
                snapshot.qr_available = False
            payload = snapshot.model_dump_json()
            if payload != previous:
                yield f"event: status\ndata: {payload}\n\n"
                previous = payload
            else:
                yield ": heartbeat\n\n"
            await asyncio.sleep(settings.status_poll_seconds)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"detail": "命轨观测台发生内部错误，请查看服务日志"},
    )


try:
    configured = get_settings()
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=configured.allowed_host_list)
    if configured.static_dir and Path(configured.static_dir).is_dir():
        app.mount("/", StaticFiles(directory=configured.static_dir, html=True), name="frontend")
except Exception:
    # Configuration validation is deliberately completed again during lifespan,
    # where Uvicorn can report a clear startup error without exposing secrets.
    pass
