# -*- coding: utf-8 -*-
"""
统一 thinking 配置。

原则：
- 代码里不写死 enable_thinking=True/False
- 是否开启 thinking 完全由 docker-compose.yml / .env 环境变量决定
- 支持全局开关，也支持每个插件单独覆盖
"""

import os


def _env_bool(key, default=None):
    value = os.getenv(key)

    if value is None:
        return default

    value = str(value).strip().lower()

    if value in ("1", "true", "yes", "y", "on"):
        return True

    if value in ("0", "false", "no", "n", "off"):
        return False

    return default


def _env_int(key, default=None):
    value = os.getenv(key)

    if value is None or str(value).strip() == "":
        return default

    try:
        return int(str(value).strip())
    except Exception:
        return default


def build_thinking_payload(prefix=None, default=False):
    """
    返回可直接合并进 DashScope compatible-mode /chat/completions payload 的 thinking 参数。

    优先级：
    1. {PREFIX}_ENABLE_THINKING
    2. XIAOYOU_ENABLE_THINKING
    3. ENABLE_THINKING
    4. default

    thinking_budget 优先级：
    1. {PREFIX}_THINKING_BUDGET
    2. XIAOYOU_THINKING_BUDGET
    3. THINKING_BUDGET
    """
    keys = []

    if prefix:
        keys.append(f"{prefix}_ENABLE_THINKING")

    keys.extend([
        "XIAOYOU_ENABLE_THINKING",
        "ENABLE_THINKING",
    ])

    enabled = None

    for key in keys:
        enabled = _env_bool(key, None)
        if enabled is not None:
            break

    if enabled is None:
        enabled = bool(default)

    payload = {
        "enable_thinking": bool(enabled)
    }

    if not enabled:
        return payload

    budget = None

    if prefix:
        budget = _env_int(f"{prefix}_THINKING_BUDGET", None)

    if budget is None:
        budget = _env_int("XIAOYOU_THINKING_BUDGET", None)

    if budget is None:
        budget = _env_int("THINKING_BUDGET", None)

    if budget and budget > 0:
        payload["thinking_budget"] = budget

    return payload


def strip_thinking_payload(payload):
    """
    某些接口/模型不支持 thinking 参数时，重试前统一删除。
    """
    if not isinstance(payload, dict):
        return payload

    payload.pop("enable_thinking", None)
    payload.pop("thinking_budget", None)

    return payload
