import ast
import json
import math
import os
import threading
import time
import types
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_compact_persona_keeps_understanding_without_fixed_reaction_rules():
    source = (ROOT / "plugins" / "xiaoyou_chat" / "__init__.py").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    persona = compose.split("CHARACTER_DESC: |", 1)[1].split(
        "# ===== 微信触发与回复前缀 =====",
        1,
    )[0]

    assert "先理解YoYo真正想表达的意思" in persona
    assert "而不是固定人设表演" in persona
    assert "内容复杂时可以自然展开" in persona
    assert "最近六个助手回合" not in source
    assert "默认只回复 1 到 2 行" not in source


def test_compose_caps_bubbles_and_uses_requested_delay():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "SPLIT_REPLY_MAX_PARTS: '3'" in compose
    assert "SPLIT_REPLY_DELAY_PER_CHAR: '0.2'" in compose
    assert "VISION_SPLIT_REPLY_DELAY_PER_CHAR: '0.2'" in compose


def test_long_memory_is_dynamic_and_filters_low_ranked_candidates():
    source = (ROOT / "plugins" / "aliyun_memory" / "aliyun_memory.py").read_text(encoding="utf-8")

    assert "plan_context(" in source
    assert "allowed_memory_types=plan.allowed_memory_types" in source
    assert "if not plan.use_long_memory" in source
    assert "ALIYUN_MEMORY_MIN_RETRIEVAL_SCORE" in source
    assert "dynamic_limit=%s" in source


def test_explicit_remember_request_is_part_of_governance_policy():
    source = (ROOT / "plugins" / "xiaoyou_common" / "memory_governance.py").read_text(encoding="utf-8")

    assert "这是显式记忆授权" in source
    assert "应提高 importance 并优先提取" in source


def test_reminders_are_marked_transient_and_acknowledgement_skips_thinking():
    reminder = (ROOT / "plugins" / "reminder_love" / "reminder_love.py").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert 'kwargs["xiaoyou_skip_long_memory_write"] = True' in reminder
    assert 'self._mark_transient_memory_turn(context, "reminder_request")' in reminder
    assert "REMINDER_ACK_ENABLE_THINKING: 'false'" in compose
