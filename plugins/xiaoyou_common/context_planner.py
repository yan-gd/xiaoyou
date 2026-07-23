# -*- coding: utf-8 -*-
"""Local query planner for Xiaoyou's conversation context.

It decides which memory families and budgets are useful before retrieval.  It
does not call a model, so better context selection adds no conversational
latency and remains auditable in tests and traces.
"""

import os
import re
import importlib.util
from dataclasses import dataclass
from pathlib import Path
import sys

try:
    from plugins.xiaoyou_common.memory_schema import (
        CORRECTION, EPISODIC, LEGACY, PENDING, PROJECT, RELATIONSHIP, SEMANTIC,
    )
    from plugins.xiaoyou_common.token_budget import build_model_token_budget
except ModuleNotFoundError:  # Standalone unit-test/evaluator loading.
    def _load_sibling(module_name, filename):
        module = sys.modules.get(module_name)
        if module is not None:
            return module
        spec = importlib.util.spec_from_file_location(
            module_name,
            Path(__file__).with_name(filename),
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module

    _schema = _load_sibling("_xiaoyou_memory_schema_standalone", "memory_schema.py")
    _tokens = _load_sibling("_xiaoyou_token_budget_standalone", "token_budget.py")
    CORRECTION = _schema.CORRECTION
    EPISODIC = _schema.EPISODIC
    LEGACY = _schema.LEGACY
    PENDING = _schema.PENDING
    PROJECT = _schema.PROJECT
    RELATIONSHIP = _schema.RELATIONSHIP
    SEMANTIC = _schema.SEMANTIC
    build_model_token_budget = _tokens.build_model_token_budget


@dataclass(frozen=True)
class ContextPlan:
    mode: str
    use_long_memory: bool
    retrieval_mode: str
    long_memory_max_results: int
    use_episodic_memory: bool
    episodic_max_results: int
    allowed_memory_types: tuple
    context_max_tokens: int
    section_token_caps: dict
    reason: str

    def as_dict(self):
        return {
            "schema_version": 1,
            "mode": self.mode,
            "use_long_memory": self.use_long_memory,
            "retrieval_mode": self.retrieval_mode,
            "long_memory_max_results": self.long_memory_max_results,
            "use_episodic_memory": self.use_episodic_memory,
            "episodic_max_results": self.episodic_max_results,
            "allowed_memory_types": list(self.allowed_memory_types),
            "context_max_tokens": self.context_max_tokens,
            "section_token_caps": dict(self.section_token_caps),
            "reason": self.reason,
        }


def plan_context(current_user_text, input_messages=None, *, thinking_enabled=False):
    messages = [str(item or "").strip() for item in (input_messages or []) if str(item or "").strip()]
    text = "\n".join(messages) if messages else str(current_user_text or "").strip()
    compact = _semantic_compact(text)

    if _matches(compact, ("我改主意", "纠正一下", "不是这样的", "以后不要", "从现在起", "现在改成")):
        return _plan(
            "correction", True, "normal", _result_limit("LONG_MEMORY_RECALL_RESULTS", 5),
            (CORRECTION, SEMANTIC, RELATIONSHIP, PROJECT, PENDING, LEGACY),
            4400, {"current_input": 1200, "recent_state": 700, "recent_conversation": 1800, "long_memory": 1500, "upstream_fallback": 300},
            "latest user correction may supersede durable memory", thinking_enabled,
        )
    if _matches(compact, ("还记得", "你记得", "以前", "之前", "上次", "当时", "曾经", "哪天", "什么时候")):
        return _plan(
            "recall", True, "recovery", _result_limit("LONG_MEMORY_RECALL_RESULTS", 5),
            (EPISODIC, SEMANTIC, RELATIONSHIP, PENDING, LEGACY),
            4600, {"current_input": 900, "recent_state": 700, "recent_conversation": 1700, "long_memory": 1900, "upstream_fallback": 300},
            "explicit recall benefits from episodic and semantic memory", thinking_enabled,
        )
    if _matches(compact, ("记住", "要记得", "我喜欢", "我不喜欢", "我讨厌", "我的习惯", "以后叫我", "偏好")):
        return _plan(
            "preference", True, "normal", _result_limit("LONG_MEMORY_RECALL_RESULTS", 5),
            (SEMANTIC, RELATIONSHIP, CORRECTION, LEGACY),
            4000, {"current_input": 1100, "recent_state": 650, "recent_conversation": 1550, "long_memory": 1400, "upstream_fallback": 300},
            "preference or explicit remember request may update semantic memory", thinking_enabled,
        )
    if _matches(compact, ("项目", "代码", "架构", "部署", "服务器", "容器", "方案", "报错", "测试", "继续做")):
        return _plan(
            "project", True, "normal", _result_limit("LONG_MEMORY_RECALL_RESULTS", 5),
            (PROJECT, PENDING, EPISODIC, SEMANTIC, LEGACY),
            5200, {"current_input": 1600, "recent_state": 900, "recent_conversation": 2200, "long_memory": 1500, "upstream_fallback": 500},
            "project work needs active thread and prior decisions", True,
        )
    if _is_emotional_chat(compact):
        return _plan(
            "emotional", False, "normal", 0,
            (), 3000, {"current_input": 900, "recent_state": 700, "recent_conversation": 1900, "long_memory": 0, "upstream_fallback": 200},
            "current emotion and recent dialogue are sufficient", thinking_enabled,
        )
    if _is_continuation(compact):
        return _plan(
            "continuation", False, "normal", 0,
            (), 3200, {"current_input": 700, "recent_state": 700, "recent_conversation": 2100, "long_memory": 0, "upstream_fallback": 200},
            "short continuation should rely on working memory", thinking_enabled,
        )
    return _plan(
        "general", True, "normal", _result_limit("LONG_MEMORY_NORMAL_CHAT_RESULTS", 3),
        (EPISODIC, SEMANTIC, RELATIONSHIP, PROJECT, PENDING, CORRECTION, LEGACY),
        4000, {"current_input": 1200, "recent_state": 700, "recent_conversation": 1800, "long_memory": 1100, "upstream_fallback": 300},
        "general turn receives a small mixed-memory budget", thinking_enabled,
    )


def _plan(mode, use_long, retrieval_mode, max_results, allowed, context_tokens, caps, reason, thinking):
    global_cap = int(os.getenv("XIAOYOU_CONTEXT_MAX_TOKENS", "6000"))
    requested = min(global_cap, int(context_tokens))
    model_budget = build_model_token_budget(
        requested_context_tokens=requested,
        thinking_enabled=bool(thinking),
    )
    episodic_results = {
        "correction": 3,
        "recall": 4,
        "preference": 2,
        "project": 3,
        "emotional": 0,
        "continuation": 1,
        "general": 2,
    }.get(mode, 0)
    caps = dict(caps)
    caps.setdefault(
        "episodic_memory",
        {
            "recall": 1800,
            "project": 1500,
            "correction": 1400,
            "preference": 1100,
            "continuation": 800,
            "general": 1000,
        }.get(mode, 0),
    )
    return ContextPlan(
        mode=mode,
        use_long_memory=bool(use_long),
        retrieval_mode=retrieval_mode,
        long_memory_max_results=max(0, int(max_results)),
        use_episodic_memory=episodic_results > 0,
        episodic_max_results=episodic_results,
        allowed_memory_types=tuple(allowed),
        context_max_tokens=model_budget.context_max_tokens,
        section_token_caps=caps,
        reason=reason,
    )


def _matches(text, phrases):
    return any(phrase in text for phrase in phrases)


def _result_limit(key, default):
    try:
        maximum = max(1, int(os.getenv("LONG_MEMORY_MAX_RESULTS", "5")))
        wanted = max(1, int(os.getenv(key, str(default))))
    except Exception:
        maximum, wanted = 5, int(default)
    return min(maximum, wanted)


def _is_emotional_chat(text):
    if len(text) > 80:
        return False
    return _matches(text, (
        "想你", "爱你", "喜欢你", "抱抱", "亲亲", "不分开", "陪我",
        "难过", "委屈", "焦虑", "害怕", "累了", "困了", "晚安", "早安",
        "一起靠着", "摸摸", "撒娇", "羞羞", "老婆", "老公", "亲爱的",
        "睡觉", "睡啦", "睡着", "抱紧", "好好休息", "哄我睡", "哄你睡",
    ))


def _semantic_compact(value):
    text = re.sub(r"\s+", "", str(value or ""))
    # WeChat textual emoji names describe tone but must not turn a short
    # continuation into a long standalone query merely by adding characters.
    text = re.sub(r"\[[^\]]{1,16}\]", "", text)
    return text


def _is_continuation(text):
    if len(text) <= 18:
        return True
    if len(text) > 36:
        return False
    if "还" in text and re.search(r"(?:吗|呢|啦|呀|嘛|[？?！!])$", text):
        return True
    return text.startswith((
        "那", "然后", "所以", "可是", "不过", "而且", "再", "继续", "接着",
        "快", "嗯", "好", "对", "行", "哎", "诶",
    ))
