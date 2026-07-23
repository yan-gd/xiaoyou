# -*- coding: utf-8 -*-
"""OpenAI-compatible embedding transport used by local semantic memory."""

from __future__ import annotations

import os
import time
import uuid

import requests

from common.log import logger
from plugins.xiaoyou_common.trace_service import ensure_trace, trace_event


class EmbeddingResult:
    def __init__(
        self,
        *,
        ok=False,
        vectors=None,
        error_kind="",
        status_code=0,
        call_id="",
        elapsed=0.0,
    ):
        self.ok = bool(ok)
        self.vectors = vectors if isinstance(vectors, list) else []
        self.error_kind = str(error_kind or "")
        self.status_code = int(status_code or 0)
        self.call_id = str(call_id or "")
        self.elapsed = float(elapsed or 0.0)


def embed_texts(
    texts,
    *,
    component="LongTermMemory",
    purpose="semantic_index",
    model=None,
    dimensions=None,
    timeout=None,
    api_key=None,
    base_url=None,
    session_id="",
    trace_id="",
    input_id="",
):
    values = [str(text or "").strip() for text in (texts or [])]
    if not values or any(not text for text in values):
        return EmbeddingResult(ok=False, error_kind="invalid_input")
    if len(values) > 10:
        return EmbeddingResult(ok=False, error_kind="batch_too_large")

    model = str(
        model
        or os.getenv("LONG_MEMORY_EMBEDDING_MODEL")
        or "text-embedding-v4"
    )
    try:
        dimensions = int(
            dimensions
            or os.getenv("LONG_MEMORY_EMBEDDING_DIMENSIONS")
            or 512
        )
    except Exception:
        dimensions = 512
    dimensions = max(64, min(2048, dimensions))
    try:
        timeout = max(
            1,
            int(timeout or os.getenv("LONG_MEMORY_EMBEDDING_TIMEOUT") or 8),
        )
    except Exception:
        timeout = 8

    api_key = (
        api_key
        or os.getenv("LONG_MEMORY_EMBEDDING_API_KEY")
        or os.getenv("OPEN_AI_API_KEY")
        or os.getenv("DASHSCOPE_API_KEY")
    )
    base_url = (
        base_url
        or os.getenv("LONG_MEMORY_EMBEDDING_API_BASE")
        or os.getenv("OPEN_AI_API_BASE")
        or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ).rstrip("/")
    call_id = uuid.uuid4().hex[:16]
    started = time.monotonic()
    link = ensure_trace(
        session_id=session_id,
        source=component,
        trace_id=trace_id,
        input_id=input_id,
    )
    trace_event(
        "model_call_started",
        status="started",
        link=link,
        model_call_id=call_id,
        attrs={
            "component": component,
            "purpose": purpose,
            "model": model,
        },
    )

    if not api_key:
        return _failure(
            component=component,
            purpose=purpose,
            model=model,
            call_id=call_id,
            started=started,
            link=link,
            error_kind="configuration",
        )

    try:
        response = requests.post(
            base_url + "/embeddings",
            headers={
                "Authorization": "Bearer " + api_key,
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "input": values,
                "dimensions": dimensions,
                "encoding_format": "float",
            },
            timeout=timeout,
        )
    except requests.Timeout:
        return _failure(
            component=component,
            purpose=purpose,
            model=model,
            call_id=call_id,
            started=started,
            link=link,
            error_kind="timeout",
        )
    except requests.RequestException:
        return _failure(
            component=component,
            purpose=purpose,
            model=model,
            call_id=call_id,
            started=started,
            link=link,
            error_kind="network",
        )
    except Exception:
        return _failure(
            component=component,
            purpose=purpose,
            model=model,
            call_id=call_id,
            started=started,
            link=link,
            error_kind="exception",
        )

    if response.status_code >= 400:
        if response.status_code in (401, 403):
            error_kind = "authentication"
        elif response.status_code == 429:
            error_kind = "rate_limit"
        elif response.status_code >= 500:
            error_kind = "provider_unavailable"
        else:
            error_kind = "invalid_request"
        return _failure(
            component=component,
            purpose=purpose,
            model=model,
            call_id=call_id,
            started=started,
            link=link,
            error_kind=error_kind,
            status_code=response.status_code,
        )

    try:
        data = response.json()
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise ValueError("embedding data missing")
        ordered = sorted(items, key=lambda item: int(item.get("index") or 0))
        vectors = [
            [float(value) for value in item.get("embedding", [])]
            for item in ordered
        ]
        if len(vectors) != len(values) or any(not vector for vector in vectors):
            raise ValueError("embedding count mismatch")
    except Exception:
        return _failure(
            component=component,
            purpose=purpose,
            model=model,
            call_id=call_id,
            started=started,
            link=link,
            error_kind="malformed_response",
            status_code=response.status_code,
        )

    elapsed = time.monotonic() - started
    usage = data.get("usage") if isinstance(data, dict) else None
    total_tokens = (
        int(usage.get("total_tokens") or usage.get("prompt_tokens") or 0)
        if isinstance(usage, dict)
        else 0
    )
    if total_tokens:
        logger.info(
            "[TokenUsage] usage_id=%s component=%s total_tokens=%s",
            call_id,
            component,
            total_tokens,
        )
    logger.info(
        "[EmbeddingGateway] completed component=%s purpose=%s call_id=%s "
        "model=%s count=%s dimensions=%s elapsed=%.2fs",
        component,
        purpose,
        call_id,
        model,
        len(vectors),
        len(vectors[0]),
        elapsed,
    )
    trace_event(
        "model_call_completed",
        status="ok",
        link=link,
        model_call_id=call_id,
        attrs={
            "component": component,
            "purpose": purpose,
            "model": model,
            "ok": True,
            "elapsed_ms": int(elapsed * 1000),
        },
    )
    return EmbeddingResult(
        ok=True,
        vectors=vectors,
        status_code=response.status_code,
        call_id=call_id,
        elapsed=elapsed,
    )


def _failure(
    *,
    component,
    purpose,
    model,
    call_id,
    started,
    link,
    error_kind,
    status_code=0,
):
    elapsed = time.monotonic() - started
    logger.warning(
        "[EmbeddingGateway] failed component=%s purpose=%s call_id=%s "
        "model=%s category=%s status=%s elapsed=%.2fs",
        component,
        purpose,
        call_id,
        model,
        error_kind,
        int(status_code or 0),
        elapsed,
    )
    trace_event(
        "model_call_completed",
        status="failed",
        link=link,
        model_call_id=call_id,
        attrs={
            "component": component,
            "purpose": purpose,
            "model": model,
            "ok": False,
            "error_kind": error_kind,
            "status_code": int(status_code or 0),
            "elapsed_ms": int(elapsed * 1000),
        },
    )
    return EmbeddingResult(
        ok=False,
        error_kind=error_kind,
        status_code=status_code,
        call_id=call_id,
        elapsed=elapsed,
    )


__all__ = ["EmbeddingResult", "embed_texts"]
