# -*- coding: utf-8 -*-
"""Model-aware token budgeting without a mandatory tokenizer dependency.

Qwen-compatible deployments do not expose a local tokenizer in the runtime
image.  The estimator is deliberately conservative for Chinese, emoji and
punctuation, while callers may inject an exact tokenizer callback later.
"""

import math
import os
import re
from dataclasses import dataclass


_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_ASCII_WORD_RE = re.compile(r"[A-Za-z0-9_]+")


def estimate_tokens(text, model=""):
    """Return a conservative token estimate for mixed Chinese chat text."""
    del model
    value = str(text or "")
    if not value:
        return 0
    cjk = len(_CJK_RE.findall(value))
    ascii_words = _ASCII_WORD_RE.findall(value)
    ascii_tokens = sum(max(1, math.ceil(len(word) / 4.0)) for word in ascii_words)
    remainder = _ASCII_WORD_RE.sub("", _CJK_RE.sub("", value))
    non_space = sum(1 for char in remainder if not char.isspace())
    # Emoji and punctuation are frequently one or more tokens.  Counting each
    # visible symbol as one is safer than a byte/4 approximation.
    structural = value.count("\n") // 2
    return max(1, int(math.ceil(cjk * 1.08 + ascii_tokens + non_space + structural)))


def count_tokens(text, counter=None, model=""):
    if callable(counter):
        try:
            return max(0, int(counter(str(text or ""))))
        except Exception:
            pass
    return estimate_tokens(text, model=model)


@dataclass(frozen=True)
class ModelTokenBudget:
    model_window_tokens: int
    system_reserved_tokens: int
    output_reserved_tokens: int
    thinking_reserved_tokens: int
    native_history_reserved_tokens: int
    context_max_tokens: int

    def as_dict(self):
        return {
            "model_window_tokens": self.model_window_tokens,
            "system_reserved_tokens": self.system_reserved_tokens,
            "output_reserved_tokens": self.output_reserved_tokens,
            "thinking_reserved_tokens": self.thinking_reserved_tokens,
            "native_history_reserved_tokens": self.native_history_reserved_tokens,
            "context_max_tokens": self.context_max_tokens,
        }


def build_model_token_budget(*, requested_context_tokens=None, thinking_enabled=False):
    window = _env_int("XIAOYOU_MODEL_CONTEXT_TOKENS", 16384, 4096, 1000000)
    system = _env_int("XIAOYOU_SYSTEM_RESERVED_TOKENS", 2600, 256, window)
    output = _env_int("XIAOYOU_OUTPUT_RESERVED_TOKENS", 600, 64, window)
    thinking = (
        _env_int("XIAOYOU_THINKING_RESERVED_TOKENS", 1200, 0, window)
        if thinking_enabled else 0
    )
    native_history = _env_int(
        "XIAOYOU_NATIVE_HISTORY_RESERVED_TOKENS",
        0,
        0,
        window,
    )
    configured = _env_int("XIAOYOU_CONTEXT_MAX_TOKENS", 6000, 512, window)
    if requested_context_tokens is not None:
        try:
            configured = max(256, int(requested_context_tokens))
        except Exception:
            pass
    available = max(256, window - system - output - thinking - native_history)
    return ModelTokenBudget(
        model_window_tokens=window,
        system_reserved_tokens=system,
        output_reserved_tokens=output,
        thinking_reserved_tokens=thinking,
        native_history_reserved_tokens=native_history,
        context_max_tokens=min(configured, available),
    )


def trim_to_token_budget(text, budget, *, counter=None, keep="head"):
    value = str(text or "")
    budget = max(0, int(budget or 0))
    if not value or budget <= 0:
        return ""
    if count_tokens(value, counter) <= budget:
        return value

    marker = "…[按Token预算省略]…"
    marker_tokens = count_tokens(marker, counter)
    if budget <= marker_tokens:
        return _binary_prefix(value, budget, counter)

    usable = budget - marker_tokens
    if keep == "tail":
        tail = _binary_suffix(value, usable, counter)
        return marker + tail
    if keep == "head_tail":
        head_budget = max(1, int(usable * 0.58))
        tail_budget = max(1, usable - head_budget)
        return (
            _binary_prefix(value, head_budget, counter)
            + marker
            + _binary_suffix(value, tail_budget, counter)
        )
    return _binary_prefix(value, usable, counter) + marker


def _binary_prefix(text, budget, counter):
    low, high, best = 0, len(text), ""
    while low <= high:
        middle = (low + high) // 2
        candidate = text[:middle]
        if count_tokens(candidate, counter) <= budget:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    return best


def _binary_suffix(text, budget, counter):
    low, high, best = 0, len(text), ""
    while low <= high:
        middle = (low + high) // 2
        candidate = text[len(text) - middle:]
        if count_tokens(candidate, counter) <= budget:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    return best


def _env_int(key, default, minimum, maximum):
    try:
        value = int(os.getenv(key, str(default)))
    except Exception:
        value = int(default)
    return max(minimum, min(maximum, value))
