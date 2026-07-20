# -*- coding: utf-8 -*-
"""Shared gateway for OpenAI-compatible chat-completion calls.

The gateway owns transport-level concerns only: credentials, endpoint shape,
thinking-parameter compatibility, provider error classification and safe
structured logging.  Prompts and the business response to failures remain in
the capability plugin that initiated the request.
"""

import os
import re
import time
import uuid

import requests

from common.log import logger
from plugins.xiaoyou_common.thinking_config import strip_thinking_payload
from plugins.xiaoyou_common.trace_service import ensure_trace, trace_event


ERROR_CONFIGURATION = "configuration"
ERROR_CONTENT_INSPECTION = "content_inspection"
ERROR_TIMEOUT = "timeout"
ERROR_RATE_LIMIT = "rate_limit"
ERROR_AUTHENTICATION = "authentication"
ERROR_MODEL_UNAVAILABLE = "model_unavailable"
ERROR_INVALID_REQUEST = "invalid_request"
ERROR_PROVIDER_UNAVAILABLE = "provider_unavailable"
ERROR_NETWORK = "network"
ERROR_HTTP = "http_error"
ERROR_MALFORMED_RESPONSE = "malformed_response"
ERROR_EXCEPTION = "exception"


_CONTENT_INSPECTION_MARKERS = (
    "datainspectionfailed",
    "data_inspection_failed",
    "data inspection failed",
    "inappropriate content",
    "content inspection",
    "content_filter",
    "content filter",
)

_THINKING_UNSUPPORTED_MARKERS = (
    "enable_thinking",
    "thinking_budget",
)


class ModelCallResult:
    """Transport-neutral result returned to capability plugins."""

    def __init__(
        self,
        ok=False,
        content="",
        data=None,
        error_kind="",
        status_code=0,
        error_code="",
        error_message="",
        call_id="",
        elapsed=0.0,
        thinking_fallback=False,
        trace_id="",
        input_id="",
    ):
        self.ok = bool(ok)
        self.content = str(content or "")
        self.data = data if isinstance(data, dict) else {}
        self.error_kind = str(error_kind or "")
        self.status_code = int(status_code or 0)
        self.error_code = str(error_code or "")
        self.error_message = str(error_message or "")
        self.call_id = str(call_id or "")
        self.elapsed = float(elapsed or 0.0)
        self.thinking_fallback = bool(thinking_fallback)
        self.trace_id = str(trace_id or "")
        self.input_id = str(input_id or "")


def is_content_inspection_error(value):
    """Return whether a provider response represents content inspection."""
    text = str(value or "").lower()
    return any(marker in text for marker in _CONTENT_INSPECTION_MARKERS)


def classify_provider_error(status_code=0, response_text="", exception=None):
    """Map provider/transport failures to stable internal categories."""
    if isinstance(exception, requests.Timeout):
        return ERROR_TIMEOUT
    if isinstance(exception, requests.ConnectionError):
        return ERROR_NETWORK
    if isinstance(exception, requests.RequestException):
        return ERROR_NETWORK
    if exception is not None:
        return ERROR_EXCEPTION

    status_code = int(status_code or 0)
    text = str(response_text or "").lower()

    if is_content_inspection_error(text):
        return ERROR_CONTENT_INSPECTION
    if status_code in (401, 403):
        return ERROR_AUTHENTICATION
    if status_code == 429:
        return ERROR_RATE_LIMIT
    if status_code in (408, 504):
        return ERROR_TIMEOUT
    if status_code >= 500:
        return ERROR_PROVIDER_UNAVAILABLE
    if status_code == 404 or "modelnotopen" in text or "model_not_found" in text:
        return ERROR_MODEL_UNAVAILABLE
    if status_code in (400, 405, 409, 413, 415, 422):
        return ERROR_INVALID_REQUEST
    return ERROR_HTTP


def chat_completion(
    *,
    component,
    purpose,
    payload,
    timeout=45,
    api_key=None,
    base_url=None,
    session_id="",
    call_id="",
    trace_id="",
    input_id="",
    retry_without_thinking=True,
):
    """Call an OpenAI-compatible ``/chat/completions`` endpoint.

    No prompt or content fallback is generated here.  The only compatibility
    retry is the behavior the project already used: when a provider explicitly
    rejects thinking parameters, retry once without those parameters.
    """
    component = str(component or "unknown")
    purpose = str(purpose or "unknown")
    session_id = str(session_id or "")
    call_id = str(call_id or uuid.uuid4().hex[:16])
    timeout = int(timeout or 45)
    model = str((payload or {}).get("model") or "unknown")
    started = time.monotonic()
    trace_link = ensure_trace(
        session_id=session_id,
        source=component,
        trace_id=trace_id,
        input_id=input_id,
    )
    trace_event(
        "model_call_started",
        status="started",
        link=trace_link,
        model_call_id=call_id,
        attrs={
            "component": component,
            "purpose": purpose,
            "model": model,
        },
    )

    api_key = api_key or os.getenv("OPEN_AI_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
    base_url = (
        base_url
        or os.getenv("OPEN_AI_API_BASE")
        or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ).rstrip("/")

    if not api_key:
        return _failure_result(
            component=component,
            purpose=purpose,
            model=model,
            session_id=session_id,
            call_id=call_id,
            started=started,
            error_kind=ERROR_CONFIGURATION,
            error_message="api key missing",
            trace_id=trace_link.trace_id,
            input_id=trace_link.input_id,
        )

    if not isinstance(payload, dict):
        return _failure_result(
            component=component,
            purpose=purpose,
            model=model,
            session_id=session_id,
            call_id=call_id,
            started=started,
            error_kind=ERROR_CONFIGURATION,
            error_message="payload must be a dict",
            trace_id=trace_link.trace_id,
            input_id=trace_link.input_id,
        )

    # Copy the mutable containers used by the compatibility retry so callers
    # can safely reuse their original request for a later recovery decision.
    request_payload = dict(payload)
    if isinstance(payload.get("messages"), list):
        request_payload["messages"] = list(payload["messages"])

    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
    }
    url = base_url + "/chat/completions"
    thinking_fallback = False

    try:
        response = requests.post(
            url,
            headers=headers,
            json=request_payload,
            timeout=timeout,
        )

        if (
            retry_without_thinking
            and response.status_code >= 400
            and _thinking_parameters_rejected(response.text)
        ):
            strip_thinking_payload(request_payload)
            thinking_fallback = True
            logger.info(
                "[ModelGateway] thinking compatibility retry component=%s purpose=%s "
                "call_id=%s model=%s",
                component,
                purpose,
                call_id,
                model,
            )
            response = requests.post(
                url,
                headers=headers,
                json=request_payload,
                timeout=timeout,
            )

        if response.status_code >= 400:
            error_code, error_message = _extract_provider_error(response)
            return _failure_result(
                component=component,
                purpose=purpose,
                model=model,
                session_id=session_id,
                call_id=call_id,
                started=started,
                error_kind=classify_provider_error(response.status_code, response.text),
                status_code=response.status_code,
                error_code=error_code,
                error_message=error_message,
                thinking_fallback=thinking_fallback,
                trace_id=trace_link.trace_id,
                input_id=trace_link.input_id,
            )

        try:
            data = response.json()
            message = data.get("choices", [{}])[0].get("message", {})
            content = message.get("content", "")
        except Exception as exc:
            return _failure_result(
                component=component,
                purpose=purpose,
                model=model,
                session_id=session_id,
                call_id=call_id,
                started=started,
                error_kind=ERROR_MALFORMED_RESPONSE,
                status_code=response.status_code,
                error_message=str(exc),
                thinking_fallback=thinking_fallback,
                trace_id=trace_link.trace_id,
                input_id=trace_link.input_id,
            )

        elapsed = time.monotonic() - started
        content = str(content or "")
        usage = data.get("usage", {}) if isinstance(data, dict) else {}
        total_tokens = int(usage.get("total_tokens") or 0) if isinstance(usage, dict) else 0
        if total_tokens > 0:
            logger.info(
                "[TokenUsage] usage_id=%s component=%s total_tokens=%s "
                "prompt_tokens=%s completion_tokens=%s",
                call_id,
                component,
                total_tokens,
                int(usage.get("prompt_tokens") or 0),
                int(usage.get("completion_tokens") or 0),
            )
        logger.info(
            "[ModelGateway] completed component=%s purpose=%s call_id=%s model=%s "
            "session=%s elapsed=%.2fs has_content=%s thinking_fallback=%s",
            component,
            purpose,
            call_id,
            model,
            session_id or "-",
            elapsed,
            bool(content.strip()),
            thinking_fallback,
        )
        trace_event(
            "model_call_completed",
            status="ok",
            link=trace_link,
            model_call_id=call_id,
            attrs={
                "component": component,
                "purpose": purpose,
                "model": model,
                "ok": True,
                "elapsed_ms": int(elapsed * 1000),
                "thinking_fallback": thinking_fallback,
            },
        )
        return ModelCallResult(
            ok=True,
            content=content,
            data=data,
            status_code=response.status_code,
            call_id=call_id,
            elapsed=elapsed,
            thinking_fallback=thinking_fallback,
            trace_id=trace_link.trace_id,
            input_id=trace_link.input_id,
        )

    except Exception as exc:
        return _failure_result(
            component=component,
            purpose=purpose,
            model=model,
            session_id=session_id,
            call_id=call_id,
            started=started,
            error_kind=classify_provider_error(exception=exc),
            error_message=str(exc),
            thinking_fallback=thinking_fallback,
            trace_id=trace_link.trace_id,
            input_id=trace_link.input_id,
        )


def _thinking_parameters_rejected(response_text):
    text = str(response_text or "").lower()
    return any(marker in text for marker in _THINKING_UNSUPPORTED_MARKERS)


def _extract_provider_error(response):
    response_text = str(getattr(response, "text", "") or "")
    code = ""
    message = ""
    try:
        data = response.json()
        error = data.get("error", data) if isinstance(data, dict) else {}
        if isinstance(error, dict):
            code = str(error.get("code") or error.get("type") or "")
            message = str(error.get("message") or "")
    except Exception:
        pass

    if not message:
        message = response_text
    return code[:120], _redact_detail(message)[:500]


def _redact_detail(value):
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    text = re.sub(r"(?i)bearer\s+[a-z0-9._-]+", "Bearer ***", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "sk-***", text)
    return re.sub(r"\s+", " ", text).strip()


def _failure_result(
    *,
    component,
    purpose,
    model,
    session_id,
    call_id,
    started,
    error_kind,
    status_code=0,
    error_code="",
    error_message="",
    thinking_fallback=False,
    trace_id="",
    input_id="",
):
    elapsed = time.monotonic() - started
    safe_message = _redact_detail(error_message)
    # Content-inspection bodies may echo sensitive material; category and code
    # are sufficient for diagnosis, so never include their message in logs.
    if error_kind == ERROR_CONTENT_INSPECTION:
        safe_message = ""
    log_message = safe_message[:300]
    log_failure = (
        logger.info
        if error_kind == ERROR_CONTENT_INSPECTION
        and str(component or "").lower() == "shortmemory"
        and str(purpose or "").lower() == "summary"
        else logger.warning
    )
    log_failure(
        "[ModelGateway] failed component=%s purpose=%s call_id=%s model=%s "
        "session=%s category=%s status=%s code=%s elapsed=%.2fs detail=%s",
        component,
        purpose,
        call_id,
        model,
        session_id or "-",
        error_kind,
        int(status_code or 0),
        error_code or "-",
        elapsed,
        log_message or "-",
    )
    trace_link = ensure_trace(
        session_id=session_id,
        source=component,
        trace_id=trace_id,
        input_id=input_id,
    )
    trace_event(
        "model_call_completed",
        status="failed",
        link=trace_link,
        model_call_id=call_id,
        attrs={
            "component": component,
            "purpose": purpose,
            "model": model,
            "ok": False,
            "error_kind": error_kind,
            "status_code": int(status_code or 0),
            "elapsed_ms": int(elapsed * 1000),
            "thinking_fallback": thinking_fallback,
        },
    )
    return ModelCallResult(
        ok=False,
        error_kind=error_kind,
        status_code=status_code,
        error_code=error_code,
        error_message=safe_message,
        call_id=call_id,
        elapsed=elapsed,
        thinking_fallback=thinking_fallback,
        trace_id=trace_link.trace_id,
        input_id=trace_link.input_id,
    )
