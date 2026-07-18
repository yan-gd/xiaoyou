# -*- coding: utf-8 -*-
"""Build provider-native role messages for Xiaoyou's active chat window."""

import os

try:
    from plugins.xiaoyou_common.token_budget import count_tokens
except ModuleNotFoundError:  # Loaded directly by standalone tests.
    import importlib.util
    from pathlib import Path
    import sys

    _NAME = "_xiaoyou_token_budget_for_messages"
    _module = sys.modules.get(_NAME)
    if _module is None:
        _spec = importlib.util.spec_from_file_location(
            _NAME,
            Path(__file__).with_name("token_budget.py"),
        )
        _module = importlib.util.module_from_spec(_spec)
        sys.modules[_NAME] = _module
        _spec.loader.exec_module(_module)
    count_tokens = _module.count_tokens


VALID_ROLES = ("user", "assistant")


def prepare_native_history(
    records,
    *,
    max_messages=None,
    max_tokens=None,
    counter=None,
    current_inputs=None,
):
    """Normalize and bound ActiveWindow records without flattening their roles."""
    max_messages = _positive_int(
        max_messages,
        "XIAOYOU_NATIVE_HISTORY_MAX_MESSAGES",
        400,
    )
    max_tokens = _positive_int(
        max_tokens,
        "XIAOYOU_NATIVE_HISTORY_MAX_TOKENS",
        4500,
    )

    normalized = []
    seen_ids = set()
    for record in records or []:
        if not isinstance(record, dict):
            continue
        role = str(record.get("role") or "").strip().lower()
        content = str(record.get("content") or "").strip()
        message_id = str(record.get("id") or "").strip()
        if role not in VALID_ROLES or not content:
            continue
        if record.get("provider_injection_blocked"):
            continue
        if message_id and message_id in seen_ids:
            continue
        if message_id:
            seen_ids.add(message_id)
        normalized.append({
            "role": role,
            "content": content,
            "_id": message_id,
            "_ts": int(record.get("ts") or 0),
        })

    # ConversationArchive records the current user message before XiaoyouChat
    # compiles the provider payload.  The same text is then present in the
    # final user prompt.  Remove only the matching trailing user records so a
    # current input is not sent twice or weighted twice by content inspection.
    visible_inputs = [
        str(value or "").strip()
        for value in (current_inputs or [])
        if str(value or "").strip()
    ]
    for current in reversed(visible_inputs):
        if not normalized:
            break
        latest = normalized[-1]
        if latest["role"] != "user" or latest["content"] != current:
            break
        normalized.pop()

    if max_messages > 0:
        normalized = normalized[-max_messages:]

    selected = []
    used_tokens = 0
    for message in reversed(normalized):
        message_tokens = count_tokens(message["content"], counter) + 4
        if selected and used_tokens + message_tokens > max_tokens:
            continue
        if not selected and message_tokens > max_tokens:
            # One unusually large record must not evict the entire active
            # window.  It remains available in ShortMemory summaries.
            continue
        selected.append(message)
        used_tokens += message_tokens
    selected.reverse()

    return [
        {"role": message["role"], "content": message["content"]}
        for message in selected
    ]


def build_chat_messages(system_prompt, user_prompt, native_history=None):
    messages = [{"role": "system", "content": str(system_prompt or "").strip()}]
    messages.extend(prepare_native_history(native_history or []))
    messages.append({"role": "user", "content": str(user_prompt or "").strip()})
    return messages


def _positive_int(value, env_key, default):
    try:
        resolved = int(value) if value is not None else int(os.getenv(env_key, str(default)))
    except Exception:
        resolved = int(default)
    return max(1, resolved)
