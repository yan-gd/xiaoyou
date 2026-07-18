# -*- coding:utf-8 -*-
import ast
import difflib
import json
import os
import re
import types


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "plugins", "xiaoyou_common", "selective_critic.py")


class Logger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None


def load_critic():
    with open(SOURCE, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=SOURCE)
    selected = [
        node for node in tree.body
        if isinstance(node, (ast.Assign, ast.ClassDef, ast.FunctionDef))
    ]
    namespace = {
        "__name__": "critic_test",
        "__file__": SOURCE,
        "difflib": difflib,
        "json": json,
        "os": os,
        "re": re,
        "logger": Logger(),
        "build_thinking_payload": lambda prefix: {},
        "chat_completion": None,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), SOURCE, "exec"), namespace)
    return namespace


def test_casual_low_risk_turn_skips_model_call():
    module = load_critic()
    calls = []
    module["chat_completion"] = lambda **kwargs: calls.append(kwargs)
    critic = module["SelectiveCritic"]()

    reply, manifest = critic.review_if_needed(
        current_text="抱抱我",
        draft="来呀，抱紧一点",
        native_history=[],
        context_plan={"mode": "emotional", "use_long_memory": False},
    )

    assert reply == "来呀，抱紧一点"
    assert manifest["status"] == "skipped"
    assert calls == []


def test_user_correction_triggers_minimal_replacement():
    module = load_critic()
    module["chat_completion"] = lambda **kwargs: types.SimpleNamespace(
        ok=True,
        content=json.dumps({
            "action": "replace",
            "issues": ["仍沿用了被纠正的旧事实"],
            "reply": "好，是明天出发\n这次我记清楚啦",
        }, ensure_ascii=False),
        error_kind="",
    )
    critic = module["SelectiveCritic"]()

    reply, manifest = critic.review_if_needed(
        current_text="纠正一下，不是今天，是明天出发",
        draft="那今天出发前记得吃饭呀",
        native_history=[],
        context_plan={"mode": "correction", "use_long_memory": True},
    )

    assert reply.startswith("好，是明天出发")
    assert manifest["status"] == "replaced"
    assert "user_correction" in manifest["risk_reasons"]


def test_repeated_recent_reply_is_auditable_risk():
    module = load_critic()
    critic = module["SelectiveCritic"]()
    reasons = critic.risk_reasons(
        current_text="嗯嗯",
        draft="唔，笨蛋，不许再闹啦",
        native_history=[
            {"role": "assistant", "content": "唔，笨蛋，不许再闹啦"},
            {"role": "user", "content": "知道啦"},
        ],
        context_plan={"mode": "continuation", "use_long_memory": False},
    )

    assert "near_duplicate" in reasons


def test_explicit_noun_question_does_not_trigger_ambiguous_reference_review():
    module = load_critic()
    critic = module["SelectiveCritic"]()
    reasons = critic.risk_reasons(
        current_text="这个多轮消息架构现在都在用吗",
        draft="主流聊天接口基本都会保留角色历史",
        native_history=[{"role": "assistant", "content": "我们刚聊到上下文架构"}],
        context_plan={"mode": "general", "use_long_memory": False},
    )

    assert "reference_resolution" not in reasons
