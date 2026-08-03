# -*- coding: utf-8 -*-
"""Concurrent prefetch for independent semantic route decisions.

Only model-backed classification runs in the worker pool. Plugin callbacks
still consume the results in their normal priority order, so reminder, photo,
and MCP side effects remain deterministic and single-threaded. App reply
medium prediction is independent and may be consumed after reply generation.
"""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from common.log import logger


PREFETCH_KEY = "_xiaoyou_parallel_route_prefetch"
_EXECUTOR = None
_EXECUTOR_LOCK = threading.Lock()


def _enabled():
    return os.getenv(
        "XIAOYOU_PARALLEL_ROUTE_ENABLED",
        "true",
    ).strip().lower() in ("1", "true", "yes", "on")


def _executor():
    global _EXECUTOR
    if _EXECUTOR is not None:
        return _EXECUTOR
    with _EXECUTOR_LOCK:
        if _EXECUTOR is None:
            workers = max(
                1,
                min(
                    8,
                    int(os.getenv("XIAOYOU_PARALLEL_ROUTE_WORKERS", "4")),
                ),
            )
            _EXECUTOR = ThreadPoolExecutor(
                max_workers=workers,
                thread_name_prefix="XiaoyouRoute",
            )
    return _EXECUTOR


def start_route_prefetch(context, providers):
    """Start every eligible route provider once for this Context."""
    if not _enabled() or context is None:
        return False

    kwargs = getattr(context, "kwargs", None)
    if not isinstance(kwargs, dict):
        return False
    if PREFETCH_KEY in kwargs:
        return False

    submitted_at = time.monotonic()
    futures = {}
    for name, provider in dict(providers or {}).items():
        if not callable(provider):
            continue
        try:
            futures[str(name)] = _executor().submit(provider)
        except Exception:
            logger.exception(
                "[ParallelRoute] failed to submit route=%s",
                name,
            )

    if not futures:
        return False

    kwargs[PREFETCH_KEY] = {
        "submitted_at": submitted_at,
        "futures": futures,
    }
    logger.info(
        "[ParallelRoute] started routes=%s",
        ",".join(sorted(futures)),
    )
    return True


def resolve_route_prefetch(context, name, fallback):
    """Resolve a prefetched decision, preserving the caller's fallback."""
    state = None
    kwargs = getattr(context, "kwargs", None) if context is not None else None
    if isinstance(kwargs, dict):
        state = kwargs.get(PREFETCH_KEY)
    futures = state.get("futures") if isinstance(state, dict) else None
    future = futures.get(str(name)) if isinstance(futures, dict) else None
    if future is None:
        return fallback()

    waited_at = time.monotonic()
    try:
        value = future.result()
        finished_at = time.monotonic()
        logger.info(
            "[ParallelRoute] resolved route=%s wait=%.3fs total=%.3fs",
            name,
            finished_at - waited_at,
            finished_at - float(state.get("submitted_at") or waited_at),
        )
        return value
    except Exception:
        logger.exception(
            "[ParallelRoute] prefetched route failed route=%s; retrying inline",
            name,
        )
        return fallback()
