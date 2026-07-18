# -*- coding:utf-8 -*-
import ast
import json
import os
import types
import unittest
from dataclasses import dataclass


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_PATH = os.path.join(
    ROOT,
    "plugins",
    "xiaoyou_common",
    "photo_intent_service.py",
)


def load_service():
    with open(SOURCE_PATH, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=SOURCE_PATH)
    selected = [
        node
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.ClassDef, ast.FunctionDef))
    ]

    class Logger:
        def info(self, *args, **kwargs):
            return None

    namespace = {
        "__name__": "photo_intent_service_test",
        "dataclass": dataclass,
        "json": json,
        "os": os,
        "logger": Logger(),
        "build_context_snapshot": lambda **kwargs: types.SimpleNamespace(
            short_memory="最近对话上下文"
        ),
        "build_thinking_payload": lambda prefix: {},
        "might_need_capability": lambda *args, **kwargs: True,
        "chat_completion": None,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), SOURCE_PATH, "exec"), namespace)
    return namespace


class DummyContext:
    def __init__(self):
        self.kwargs = {}


class PhotoSemanticRouteTest(unittest.TestCase):
    def setUp(self):
        self.service = load_service()

    def _result(self, payload, ok=True):
        return types.SimpleNamespace(
            ok=ok,
            content=json.dumps(payload, ensure_ascii=False) if ok else "",
        )

    def test_immediate_generation_route_is_accepted(self):
        self.service["chat_completion"] = lambda **kwargs: self._result({
            "route": "generate_xiaoyou_photo",
            "time_scope": "now",
            "subject": "xiaoyou",
            "confidence": 0.96,
            "reason": "完整语义要求现在生成",
        })
        route = self.service["classify_photo_semantics"](
            text="按我们刚才商量的那样来吧",
            session_id="yoyo",
            context=DummyContext(),
        )
        self.assertTrue(route.should_generate)

    def test_future_photo_plan_never_generates_now(self):
        self.service["chat_completion"] = lambda **kwargs: self._result({
            "route": "generate_xiaoyou_photo",
            "time_scope": "future",
            "subject": "xiaoyou",
            "confidence": 0.93,
            "reason": "这是稍后的安排",
        })
        route = self.service["classify_photo_semantics"](
            text="进去以后再做刚才说的事",
            session_id="yoyo",
            context=DummyContext(),
        )
        self.assertFalse(route.should_generate)
        self.assertEqual("independent_text", route.route)
        self.assertEqual("future", route.time_scope)

    def test_pending_image_followup_can_be_implicit(self):
        self.service["chat_completion"] = lambda **kwargs: self._result({
            "route": "image_followup",
            "time_scope": "now",
            "subject": "xiaoyou",
            "confidence": 0.91,
            "reason": "承接刚才图片",
        })
        route = self.service["classify_photo_semantics"](
            text="美吧",
            session_id="yoyo",
            pending_user_image=True,
            context=DummyContext(),
        )
        self.assertTrue(route.is_image_followup)

    def test_router_failure_never_triggers_generation(self):
        self.service["chat_completion"] = lambda **kwargs: self._result({}, ok=False)
        route = self.service["classify_photo_semantics"](
            text="任何表达",
            session_id="yoyo",
            context=DummyContext(),
        )
        self.assertFalse(route.should_generate)
        self.assertEqual("independent_text", route.route)

    def test_same_context_reuses_one_semantic_decision(self):
        calls = []

        def complete(**kwargs):
            calls.append(kwargs)
            return self._result({
                "route": "generate_xiaoyou_photo",
                "time_scope": "now",
                "subject": "xiaoyou",
                "confidence": 0.9,
                "reason": "立即请求",
            })

        self.service["chat_completion"] = complete
        context = DummyContext()
        classify = self.service["classify_photo_semantics"]
        first = classify(text="按刚才说的来", session_id="yoyo", context=context)
        second = classify(
            text="按刚才说的来",
            session_id="yoyo",
            pending_user_image=True,
            context=context,
        )
        self.assertEqual(first, second)
        self.assertEqual(1, len(calls))

    def test_source_contains_no_keyword_or_regex_router(self):
        with open(SOURCE_PATH, "r", encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("re.compile", source)
        self.assertNotIn("re.search", source)


if __name__ == "__main__":
    unittest.main()
