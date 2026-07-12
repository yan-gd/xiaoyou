# -*- coding:utf-8 -*-
import ast
import os
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_PATH = os.path.join(ROOT, "plugins", "conversation_followup", "__init__.py")


def load_policy():
    with open(SOURCE_PATH, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=SOURCE_PATH)
    selected = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "ConversationFollowup":
            selected.extend(
                item for item in node.body
                if isinstance(item, ast.FunctionDef) and item.name == "_apply_soft_fallback"
            )
    namespace = {}
    exec(compile(ast.Module(body=selected, type_ignores=[]), SOURCE_PATH, "exec"), namespace)
    return namespace["_apply_soft_fallback"]


class FollowupPolicyTest(unittest.TestCase):
    def setUp(self):
        self.apply = load_policy()

        class Dummy:
            soft_fallback_enabled = True
            soft_fallback_delay = 240
            min_delay = 120
            max_delay = 900

        self.plugin = Dummy()

    def test_soft_no_is_converted_to_one_bounded_followup(self):
        decision, fallback = self.apply(self.plugin, {
            "need_followup": False,
            "hard_silence": False,
            "reason": "轮到对方回复",
        })
        self.assertTrue(fallback)
        self.assertTrue(decision["need_followup"])
        self.assertEqual(240, decision["followup_delay_seconds"])
        self.assertIn("不要责怪", decision["followup_intent"])

    def test_explicit_hard_silence_is_respected(self):
        source = {
            "need_followup": False,
            "hard_silence": True,
            "reason": "用户明确要求安静",
        }
        decision, fallback = self.apply(self.plugin, source)
        self.assertFalse(fallback)
        self.assertFalse(decision["need_followup"])

    def test_classifier_failure_does_not_trigger_fallback(self):
        source = {
            "need_followup": False,
            "hard_silence": False,
            "allow_soft_fallback": False,
            "reason": "classifier timeout",
        }
        decision, fallback = self.apply(self.plugin, source)
        self.assertFalse(fallback)
        self.assertFalse(decision["need_followup"])

    def test_model_yes_is_not_rewritten(self):
        source = {"need_followup": True, "followup_delay_seconds": 240}
        decision, fallback = self.apply(self.plugin, source)
        self.assertFalse(fallback)
        self.assertEqual(source, decision)


if __name__ == "__main__":
    unittest.main()
