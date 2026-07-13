# -*- coding:utf-8 -*-
import ast
import json
import math
import os
import threading
import time
import types
import unittest
from datetime import datetime


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "plugins", "xiaoyou_common", "inner_state_service.py")


class MemoryStore:
    def __init__(self, *args, **kwargs):
        self.data = {"schema_version": 1, "sessions": {}}

    def load(self):
        return json.loads(json.dumps(self.data))

    def save(self, data):
        self.data = json.loads(json.dumps(data))
        return True


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
        if isinstance(node, (ast.Assign, ast.FunctionDef, ast.ClassDef))
        and not (isinstance(node, ast.FunctionDef) and node.name == "get_inner_state_service")
    ]
    namespace = {
        "__name__": "inner_state_test",
        "__file__": SOURCE,
        "json": json,
        "math": math,
        "os": os,
        "threading": threading,
        "time": time,
        "datetime": datetime,
        "logger": Logger(),
        "JsonStateStore": MemoryStore,
        "build_context_snapshot": lambda **kwargs: types.SimpleNamespace(
            time_context="当前时间",
            character_context="小悠人格",
            short_memory="近期聊天",
        ),
        "build_thinking_payload": lambda prefix: {},
        "chat_completion": None,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), SOURCE, "exec"), namespace)
    return namespace


class InnerStateServiceTest(unittest.TestCase):
    def setUp(self):
        self.module = load_service()
        self.service = self.module["InnerStateService"]("unused.json")

    def test_exchange_updates_bounded_state_and_model_schedule(self):
        self.module["chat_completion"] = lambda **kwargs: types.SimpleNamespace(
            ok=True,
            content=json.dumps({
                "deltas": {
                    "mood_valence": 0.2,
                    "sharing_drive": 0.3,
                    "longing": -0.1,
                },
                "confidence": 1.0,
                "emotion_note": "亲密而轻松",
                "next_evaluation_seconds": 73,
                "reason": "本轮气氛自然",
            }, ensure_ascii=False),
        )
        before = self.service.get("yoyo")["mood_valence"]
        result = self.service.update_from_exchange(
            "yoyo",
            user_text="本轮用户内容",
            assistant_text="本轮小悠回复",
            last_user_ts=int(time.time()),
        )
        self.assertGreater(result["state"]["mood_valence"], before)
        self.assertGreater(result["state"]["sharing_drive"], 0.34)
        self.assertEqual(73, result["next_evaluation_seconds"])
        for key in self.module["STATE_KEYS"]:
            self.assertGreaterEqual(result["state"][key], 0.0)
            self.assertLessEqual(result["state"][key], 1.0)

    def test_model_failure_does_not_block_or_invent_delta(self):
        self.module["chat_completion"] = lambda **kwargs: types.SimpleNamespace(ok=False, content="")
        before = self.service.get("yoyo")
        result = self.service.update_from_exchange(
            "yoyo",
            user_text="任意语义",
            assistant_text="正常回复",
            last_user_ts=int(time.time()),
        )
        self.assertEqual(before["mood_valence"], result["state"]["mood_valence"])
        self.assertGreaterEqual(result["next_evaluation_seconds"], 60)

    def test_state_is_separate_from_long_term_memory(self):
        with open(SOURCE, "r", encoding="utf-8") as handle:
            source = handle.read().lower()
        self.assertIn("xiaoyou_inner_state", source)
        self.assertNotIn("aliyun_memory", source)
        self.assertNotIn("record_assistant_message", source)


if __name__ == "__main__":
    unittest.main()
