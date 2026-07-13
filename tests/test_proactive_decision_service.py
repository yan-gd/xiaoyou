# -*- coding:utf-8 -*-
import ast
import json
import os
import types
import unittest
from dataclasses import dataclass, field


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "plugins", "xiaoyou_common", "proactive_decision_service.py")


class Logger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None


def load_service():
    with open(SOURCE, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=SOURCE)
    selected = [
        node for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.ClassDef, ast.FunctionDef))
    ]
    namespace = {
        "__name__": "proactive_decision_test",
        "dataclass": dataclass,
        "field": field,
        "json": json,
        "os": os,
        "logger": Logger(),
        "build_context_snapshot": lambda **kwargs: types.SimpleNamespace(
            time_context="当前时间",
            character_context="小悠人格",
            short_memory="近期聊天",
            long_memory="相关记忆",
        ),
        "build_thinking_payload": lambda prefix: {},
        "chat_completion": None,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), SOURCE, "exec"), namespace)
    return namespace


class ProactiveDecisionServiceTest(unittest.TestCase):
    def setUp(self):
        self.module = load_service()

    def decide(self, payload, ok=True):
        self.module["chat_completion"] = lambda **kwargs: types.SimpleNamespace(
            ok=ok,
            content=json.dumps(payload, ensure_ascii=False) if ok else "",
        )
        return self.module["decide_proactive_action"](
            session_id="yoyo",
            activity={
                "last_user_text": "最近原话",
                "last_assistant_text": "最近回复",
                "recent_proactive_texts": [],
            },
            inner_state={"sharing_drive": 0.8},
            normalize_delay=lambda value: max(30, min(604800, int(value))),
        )

    def test_structured_photo_action_never_becomes_text_placeholder(self):
        decision = self.decide({
            "action": "photo",
            "text": "[图片：假的占位描述]",
            "photo_intent": "此刻很想分享刚刚的轻松和调皮",
            "next_evaluation_seconds": 180,
            "confidence": 0.9,
            "state_deltas": {"sharing_drive": -0.1},
            "reason": "照片比文字自然",
        })
        self.assertEqual("photo", decision.action)
        self.assertEqual("", decision.text)
        self.assertTrue(decision.photo_intent)

    def test_text_action_contains_only_real_message(self):
        decision = self.decide({
            "action": "text",
            "text": "刚刚忽然又想起你了",
            "photo_intent": "不应使用",
            "next_evaluation_seconds": 7200,
            "confidence": 0.8,
            "state_deltas": {},
            "reason": "此刻文字更自然",
        })
        self.assertEqual("text", decision.action)
        self.assertEqual("", decision.photo_intent)

    def test_none_can_choose_its_own_next_evaluation(self):
        decision = self.decide({
            "action": "none",
            "text": "",
            "photo_intent": "",
            "next_evaluation_seconds": 9876,
            "confidence": 0.7,
            "state_deltas": {},
            "reason": "现在安静更自然",
        })
        self.assertEqual("none", decision.action)
        self.assertEqual(9876, decision.next_evaluation_seconds)

    def test_model_failure_is_silent(self):
        decision = self.decide({}, ok=False)
        self.assertEqual("none", decision.action)
        self.assertFalse(decision.model_ok)

    def test_source_has_no_keyword_or_regex_media_router(self):
        with open(SOURCE, "r", encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("re.compile", source)
        self.assertNotIn("re.search", source)


if __name__ == "__main__":
    unittest.main()
