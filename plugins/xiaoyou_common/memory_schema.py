# -*- coding: utf-8 -*-
"""Canonical memory taxonomy shared by governance, retrieval and prompts."""

from difflib import SequenceMatcher
import re


WORKING = "working"
EPISODIC = "episodic"
SEMANTIC = "semantic"
RELATIONSHIP = "relationship"
PROJECT = "project"
PENDING = "pending"
CORRECTION = "correction"
LEGACY = "legacy"

MEMORY_TYPES = {
    WORKING, EPISODIC, SEMANTIC, RELATIONSHIP,
    PROJECT, PENDING, CORRECTION, LEGACY,
}

CATEGORY_TO_TYPE = {
    "user_profile": SEMANTIC,
    "durable_preference": SEMANTIC,
    "response_preference": SEMANTIC,
    "relationship": RELATIONSHIP,
    "project_direction": PROJECT,
    "correction": CORRECTION,
    "episodic_event": EPISODIC,
    "pending_thread": PENDING,
}

DISPLAY_NAMES = {
    EPISODIC: "情节记忆",
    SEMANTIC: "语义记忆",
    RELATIONSHIP: "关系记忆",
    PROJECT: "项目记忆",
    PENDING: "未完事项",
    CORRECTION: "纠正记录",
    LEGACY: "旧版记忆",
    WORKING: "工作记忆",
}


def normalize_memory_type(value="", category=""):
    memory_type = str(value or "").strip().lower()
    if memory_type in MEMORY_TYPES:
        return memory_type
    return CATEGORY_TO_TYPE.get(str(category or "").strip().lower(), LEGACY)


def display_name(memory_type):
    normalized = normalize_memory_type(memory_type)
    return DISPLAY_NAMES.get(normalized, DISPLAY_NAMES[LEGACY])


def normalize_allowed(values):
    if values is None:
        return None
    normalized = {
        normalize_memory_type(value)
        for value in values
        if str(value or "").strip()
    }
    # Old provider records carry no type.  Always retain them as a compatible
    # fallback until governed memories gradually replace them.
    normalized.add(LEGACY)
    return normalized


def near_duplicate_text(left, right):
    """Conservatively detect exact or strongly overlapping memory facts."""
    first = _fact_text(left)
    second = _fact_text(right)
    if not first or not second:
        return False
    if first == second:
        return True
    shorter, longer = sorted((first, second), key=len)
    if len(shorter) >= 8 and shorter in longer:
        return True
    if min(len(first), len(second)) < 8:
        return False
    if SequenceMatcher(None, first, second).ratio() >= 0.84:
        return True
    first_pairs = {first[index:index + 2] for index in range(len(first) - 1)}
    second_pairs = {second[index:index + 2] for index in range(len(second) - 1)}
    union = first_pairs | second_pairs
    overlap = len(first_pairs & second_pairs) / float(len(union) or 1)
    return overlap >= 0.68


def _fact_text(value):
    text = str(value or "").lower()
    text = re.sub(r"^[#\-*\s]+", "", text)
    text = re.sub(r"^(?:用户|yoyo|yo yo)[:：]?(?:明确)?", "", text)
    return re.sub(r"[^a-z0-9\u3400-\u9fff]+", "", text)
