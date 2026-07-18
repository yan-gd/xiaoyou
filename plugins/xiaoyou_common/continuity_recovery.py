# -*- coding: utf-8 -*-
"""Provider-inspection recovery views that preserve conversational referents."""

import re


VALID_ROLES = ("user", "assistant")
STOP_CHARS = set(
    "我你您他她它们这那哪还快啦了呢吗吧啊呀嘛就都又也别不没想让"
    "说是的在和跟给去来着个几很真太更被把"
)


def build_recovery_candidates(native_history, current_text, *, recent_limit=12):
    """Return rich-to-minimal recovery histories without mutating records.

    The referent-grounded view keeps YoYo's earlier messages that overlap the
    current ellipsis or follow-up.  It is intentionally user-grounded so a
    playful assistant tangent cannot replace the concrete object or activity.
    """
    history = [
        {
            "role": str(message.get("role") or "").strip().lower(),
            "content": str(message.get("content") or "").strip(),
        }
        for message in (native_history or [])
        if isinstance(message, dict)
        and str(message.get("role") or "").strip().lower() in VALID_ROLES
        and str(message.get("content") or "").strip()
    ]
    recent = history[-max(2, int(recent_limit or 12)):]
    candidates = []
    if recent:
        candidates.append(("recent_exact", recent))

    grounded = _grounded_user_history(recent, current_text)
    if grounded:
        candidates.append(("referent_grounded", grounded))

    compact = recent[-4:]
    if compact:
        candidates.append(("recent_compact", compact))
    candidates.append(("current_only", []))

    unique = []
    seen = set()
    for mode, messages in candidates:
        signature = tuple(
            (message.get("role"), message.get("content"))
            for message in messages
        )
        if signature in seen:
            continue
        seen.add(signature)
        unique.append((mode, messages))
    return unique


def _grounded_user_history(history, current_text):
    query_terms = continuity_terms(current_text)
    ranked = []
    for index, message in enumerate(history or []):
        if message.get("role") != "user":
            continue
        content = str(message.get("content") or "").strip()
        overlap = query_terms & continuity_terms(content)
        if not overlap:
            continue
        ranked.append((len(overlap), index, content))

    if not ranked:
        return []
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    chosen = sorted(ranked[:6], key=lambda item: item[1])
    return [
        {"role": "user", "content": content}
        for _, _, content in chosen
    ]


def continuity_terms(value):
    compact = re.sub(r"\[[^\]]{1,16}\]", "", str(value or "").lower())
    latin = re.findall(r"[a-z0-9]{2,}", compact)
    cjk = re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", compact)
    return set(latin + [char for char in cjk if char not in STOP_CHARS])
