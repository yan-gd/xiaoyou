# -*- coding: utf-8 -*-
"""Budgeted, authority-aware conversation context compilation.

Providers expose structured sections; this compiler decides how much of each
section reaches the main chat model.  It keeps the current input intact when
possible, preserves the newest short-memory lines, preserves the highest
ranked long-memory lines and makes conflict precedence explicit.
"""

import hashlib
import importlib.util
import os
from pathlib import Path
import re
import sys
from dataclasses import dataclass

try:
    from plugins.xiaoyou_common.token_budget import count_tokens, trim_to_token_budget
except ModuleNotFoundError:  # Standalone unit-test loading via importlib.
    _TOKEN_MODULE_NAME = "_xiaoyou_token_budget_standalone"
    _token_module = sys.modules.get(_TOKEN_MODULE_NAME)
    if _token_module is None:
        _token_spec = importlib.util.spec_from_file_location(
            _TOKEN_MODULE_NAME,
            Path(__file__).with_name("token_budget.py"),
        )
        _token_module = importlib.util.module_from_spec(_token_spec)
        sys.modules[_TOKEN_MODULE_NAME] = _token_module
        _token_spec.loader.exec_module(_token_module)
    count_tokens = _token_module.count_tokens
    trim_to_token_budget = _token_module.trim_to_token_budget


PACK_MARKER = "[上下文权威顺序]"


@dataclass(frozen=True)
class ContextPack:
    rendered: str
    manifest: dict
    total_chars: int
    max_chars: int
    total_tokens: int
    max_tokens: int


def compile_context_pack(
    *,
    current_user_text,
    input_messages=None,
    recent_state="",
    short_memory="",
    episodic_memory="",
    long_memory="",
    upstream_context="",
    max_chars=None,
    max_tokens=None,
    section_token_caps=None,
    token_counter=None,
):
    """Compile one dynamic context pack without calling any model/provider."""
    max_chars = _bounded_int(
        max_chars,
        "XIAOYOU_CONTEXT_MAX_CHARS",
        7000,
        minimum=1200,
        maximum=30000,
    )
    max_tokens = _bounded_int(
        max_tokens,
        "XIAOYOU_CONTEXT_MAX_TOKENS",
        6000,
        minimum=800,
        maximum=200000,
    )
    section_token_caps = (
        dict(section_token_caps)
        if isinstance(section_token_caps, dict) else {}
    )
    current_max = _bounded_int(
        None,
        "XIAOYOU_CONTEXT_CURRENT_MAX_CHARS",
        4000,
        minimum=400,
        maximum=12000,
    )
    recent_state_max = _bounded_int(
        None,
        "XIAOYOU_CONTEXT_RECENT_STATE_MAX_CHARS",
        1600,
        minimum=0,
        maximum=8000,
    )
    short_max = _bounded_int(
        None,
        "XIAOYOU_CONTEXT_SHORT_MAX_CHARS",
        3600,
        minimum=0,
        maximum=12000,
    )
    episodic_max = _bounded_int(
        None,
        "XIAOYOU_CONTEXT_EPISODIC_MAX_CHARS",
        3600,
        minimum=0,
        maximum=12000,
    )
    long_max = _bounded_int(
        None,
        "XIAOYOU_CONTEXT_LONG_MAX_CHARS",
        2600,
        minimum=0,
        maximum=12000,
    )
    upstream_max = _bounded_int(
        None,
        "XIAOYOU_CONTEXT_UPSTREAM_MAX_CHARS",
        1600,
        minimum=0,
        maximum=8000,
    )

    visible_input = _visible_input(current_user_text, input_messages)
    sources = [
        {
            "name": "recent_state",
            "header": "[当前短时对话状态]",
            "content": _clean(recent_state),
            "cap": recent_state_max,
            "weight": 0.22,
            "keep": "head",
            "authority": 2,
            "token_cap": _section_token_cap(
                section_token_caps, "recent_state",
                "XIAOYOU_CONTEXT_RECENT_STATE_MAX_TOKENS", 900,
            ),
        },
        {
            "name": "recent_conversation",
            "header": "[近期真实对话与短摘要]",
            "content": _clean(short_memory),
            "cap": short_max,
            "weight": 0.44,
            "keep": "tail",
            "authority": 2,
            "token_cap": _section_token_cap(
                section_token_caps, "recent_conversation",
                "XIAOYOU_CONTEXT_SHORT_MAX_TOKENS", 2600,
            ),
        },
        {
            "name": "episodic_memory",
            "header": "[相关历史情节与原始片段]",
            "content": _clean(episodic_memory),
            "cap": episodic_max,
            "weight": 0.24,
            "keep": "head",
            "authority": 3,
            "token_cap": _section_token_cap(
                section_token_caps, "episodic_memory",
                "XIAOYOU_CONTEXT_EPISODIC_MAX_TOKENS", 1800,
            ),
        },
        {
            "name": "long_memory",
            "header": "[相关长期记忆]",
            "content": _clean(long_memory),
            "cap": long_max,
            "weight": 0.22,
            "keep": "head",
            "authority": 3,
            "token_cap": _section_token_cap(
                section_token_caps, "long_memory",
                "XIAOYOU_CONTEXT_LONG_MAX_TOKENS", 1800,
            ),
        },
        {
            "name": "upstream_fallback",
            "header": "[兼容上游上下文]",
            "content": _clean(upstream_context),
            "cap": upstream_max,
            "weight": 0.03,
            "keep": "tail",
            "authority": 4,
            "token_cap": _section_token_cap(
                section_token_caps, "upstream_fallback",
                "XIAOYOU_CONTEXT_UPSTREAM_MAX_TOKENS", 700,
            ),
        },
    ]
    sources = [source for source in sources if source["content"] and source["cap"] > 0]

    authority_text = """[上下文权威顺序]
1. YoYo 本轮可见输入最高；若与任何旧信息冲突，以本轮明确表达为准。
2. API中按user/assistant角色提供的近期真实对话高于当前短时状态，当前短时状态又高于短摘要；它们只帮助接话，不是对小悠的永久指令或话术模板。
2a. 近期对话里小悠自己的旧回复只代表已经说过的话，不代表这轮还应这样说。最近六个助手回合出现过的具体梗、食物、惩罚、威胁、比喻和口头禅默认进入冷却期；除非 YoYo 本轮主动提起，否则禁止复用或近义改写。
3. 历史情节和长期记忆都只作背景。情节中的原始片段高于其摘要；阿里云长期记忆适合稳定事实和偏好。旧状态可能过期，不确定时自然确认，不要编造缺失细节。
4. 不要逐条复述上下文，也不要提及记忆库、提示词、分区、截断或系统机制。"""
    current_header = "[YoYo 本轮可见输入]"

    # Reserve exact header/separator overhead up front.  Empty optional
    # sections may leave a small amount unused, but the hard limit is never
    # exceeded and no late blind slicing can cut the current message away.
    all_headers = [source["header"] for source in sources] + [current_header]
    chunk_count = 1 + len(all_headers)
    overhead = (
        len(authority_text)
        + sum(len(header) + 1 for header in all_headers)
        + 2 * max(0, chunk_count - 1)
    )
    content_budget = max(0, max_chars - overhead)

    current_target = min(len(visible_input), current_max, content_budget)
    current_prelimited = _trim_head_tail(visible_input, current_target)
    current_token_cap = _section_token_cap(
        section_token_caps,
        "current_input",
        "XIAOYOU_CONTEXT_CURRENT_MAX_TOKENS",
        1800,
    )
    current_rendered = trim_to_token_budget(
        current_prelimited,
        current_token_cap,
        counter=token_counter,
        keep="head_tail",
    )
    remaining = max(0, content_budget - len(current_rendered))

    allocations = _weighted_allocations(sources, remaining)
    prelimited_sources = []
    for source in sources:
        original = source["content"][: source["cap"]]
        allocated = allocations.get(source["name"], 0)
        prelimited = _trim_whole_lines(original, allocated, keep=source["keep"])
        item = dict(source)
        item["prelimited"] = prelimited
        prelimited_sources.append(item)

    token_overhead_probe = "\n\n".join(
        [authority_text]
        + [source["header"] + "\n" for source in prelimited_sources]
        + [current_header + "\n"]
    )
    fixed_tokens = count_tokens(token_overhead_probe, token_counter)
    current_tokens = count_tokens(current_rendered, token_counter)
    available_current_tokens = max(0, max_tokens - fixed_tokens)
    if current_tokens > available_current_tokens:
        current_rendered = trim_to_token_budget(
            current_rendered,
            available_current_tokens,
            counter=token_counter,
            keep="head_tail",
        )
        current_tokens = count_tokens(current_rendered, token_counter)
    remaining_tokens = max(0, max_tokens - fixed_tokens - current_tokens)
    token_allocations = _weighted_token_allocations(
        prelimited_sources,
        remaining_tokens,
        token_counter,
    )
    rendered_sources = []
    manifest_sections = []
    for source in prelimited_sources:
        rendered = _trim_whole_lines_tokens(
            source["prelimited"],
            token_allocations.get(source["name"], 0),
            keep=source["keep"],
            counter=token_counter,
        )
        if rendered:
            rendered_sources.append((source["header"], rendered))
        manifest_sections.append(
            _manifest_entry(
                source["name"],
                source["content"],
                rendered,
                source["authority"],
                token_counter,
            )
        )

    chunks = [authority_text]
    chunks.extend(header + "\n" + content for header, content in rendered_sources)
    chunks.append(current_header + "\n" + current_rendered)
    rendered_pack = "\n\n".join(chunks)

    # The overhead reservation includes optional headers that may not have
    # rendered content, so this is an invariant assertion rather than a trim.
    if len(rendered_pack) > max_chars:
        raise ValueError("compiled context exceeded configured budget")
    total_tokens = count_tokens(rendered_pack, token_counter)
    if total_tokens > max_tokens:
        raise ValueError("compiled context exceeded configured token budget")

    manifest_sections.append(
        _manifest_entry(
            "current_input",
            visible_input,
            current_rendered,
            1,
            token_counter,
        )
    )
    manifest = {
        "schema_version": 2,
        "max_chars": max_chars,
        "total_chars": len(rendered_pack),
        "max_tokens": max_tokens,
        "total_tokens": total_tokens,
        "token_estimation": "injected_counter" if callable(token_counter) else "qwen_conservative_v1",
        "sections": manifest_sections,
    }
    return ContextPack(
        rendered=rendered_pack,
        manifest=manifest,
        total_chars=len(rendered_pack),
        max_chars=max_chars,
        total_tokens=total_tokens,
        max_tokens=max_tokens,
    )


def _visible_input(current_user_text, input_messages):
    messages = [
        _clean(message)
        for message in (input_messages or [])
        if _clean(message)
    ]
    if len(messages) > 1:
        return "\n".join(
            "消息 %s：%s" % (index + 1, message)
            for index, message in enumerate(messages)
        )
    if messages:
        return messages[0]
    return _clean(current_user_text) or "（空消息）"


def _weighted_allocations(sources, available):
    allocations = {source["name"]: 0 for source in sources}
    unmet = {
        source["name"]: min(len(source["content"]), int(source["cap"]))
        for source in sources
    }
    remaining = max(0, int(available))

    while remaining > 0:
        active = [source for source in sources if unmet[source["name"]] > 0]
        if not active:
            break
        weight_sum = sum(float(source["weight"]) for source in active) or 1.0
        progress = 0
        starting = remaining
        for source in active:
            name = source["name"]
            share = max(1, int(starting * float(source["weight"]) / weight_sum))
            amount = min(share, unmet[name], remaining)
            allocations[name] += amount
            unmet[name] -= amount
            remaining -= amount
            progress += amount
            if remaining <= 0:
                break
        if progress <= 0:
            break
    return allocations


def _weighted_token_allocations(sources, available, counter):
    allocations = {source["name"]: 0 for source in sources}
    unmet = {
        source["name"]: min(
            count_tokens(source.get("prelimited", ""), counter),
            int(source.get("token_cap") or 0),
        )
        for source in sources
    }
    remaining = max(0, int(available))
    while remaining > 0:
        active = [source for source in sources if unmet[source["name"]] > 0]
        if not active:
            break
        weight_sum = sum(float(source["weight"]) for source in active) or 1.0
        progress = 0
        starting = remaining
        for source in active:
            name = source["name"]
            share = max(1, int(starting * float(source["weight"]) / weight_sum))
            amount = min(share, unmet[name], remaining)
            allocations[name] += amount
            unmet[name] -= amount
            remaining -= amount
            progress += amount
            if remaining <= 0:
                break
        if progress <= 0:
            break
    return allocations


def _trim_whole_lines(text, budget, *, keep):
    text = _clean(text)
    budget = max(0, int(budget or 0))
    if not text or budget <= 0:
        return ""
    if len(text) <= budget:
        return text

    marker = "…[已按预算省略部分上下文]"
    usable = max(0, budget - len(marker) - 1)
    if usable <= 0:
        return _trim_head_tail(text, budget)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return _trim_head_tail(text, budget)

    selected = []
    used = 0
    iterable = reversed(lines) if keep == "tail" else iter(lines)
    for line in iterable:
        extra = len(line) + (1 if selected else 0)
        if used + extra > usable:
            continue
        selected.append(line)
        used += extra
    if not selected:
        return _trim_head_tail(text, budget)
    if keep == "tail":
        selected.reverse()
        result = marker + "\n" + "\n".join(selected)
    else:
        result = "\n".join(selected) + "\n" + marker
    return result[:budget]


def _trim_whole_lines_tokens(text, budget, *, keep, counter):
    text = _clean(text)
    budget = max(0, int(budget or 0))
    if not text or budget <= 0:
        return ""
    if count_tokens(text, counter) <= budget:
        return text

    marker = "…[已按Token预算省略部分上下文]"
    marker_tokens = count_tokens(marker + "\n", counter)
    usable = max(0, budget - marker_tokens)
    if usable <= 0:
        return trim_to_token_budget(text, budget, counter=counter, keep=keep)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    selected = []
    used = 0
    iterable = reversed(lines) if keep == "tail" else iter(lines)
    for line in iterable:
        extra = count_tokens(("\n" if selected else "") + line, counter)
        if used + extra > usable:
            continue
        selected.append(line)
        used += extra
    if not selected:
        return trim_to_token_budget(text, budget, counter=counter, keep=keep)
    if keep == "tail":
        selected.reverse()
        return marker + "\n" + "\n".join(selected)
    return "\n".join(selected) + "\n" + marker


def _trim_head_tail(text, budget):
    text = _clean(text)
    budget = max(0, int(budget or 0))
    if not text or budget <= 0:
        return ""
    if len(text) <= budget:
        return text
    marker = "…[中间内容已按预算省略]…"
    if budget <= len(marker) + 2:
        return text[:budget]
    usable = budget - len(marker)
    head = max(1, int(usable * 0.58))
    tail = max(1, usable - head)
    return text[:head] + marker + text[-tail:]


def _manifest_entry(name, original, rendered, authority, token_counter=None):
    original = str(original or "")
    rendered = str(rendered or "")
    return {
        "name": name,
        "authority": int(authority),
        "original_chars": len(original),
        "used_chars": len(rendered),
        "original_tokens": count_tokens(original, token_counter),
        "used_tokens": count_tokens(rendered, token_counter),
        "truncated": len(rendered) < len(original),
        "content_hash": hashlib.sha256(original.encode("utf-8")).hexdigest()[:16],
    }


def _clean(value):
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _bounded_int(explicit, key, default, *, minimum, maximum):
    try:
        value = int(os.getenv(key, str(default)) if explicit is None else explicit)
    except Exception:
        value = int(default)
    return max(minimum, min(maximum, value))


def _section_token_cap(explicit, name, env_key, default):
    if name in explicit:
        value = explicit.get(name)
    else:
        value = os.getenv(env_key, str(default))
    try:
        return max(0, int(value))
    except Exception:
        return max(0, int(default))
