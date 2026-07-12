# -*- coding:utf-8 -*-
import ast
import os
import re
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_PATH = os.path.join(ROOT, "plugins", "short_memory", "short_memory.py")


def load_sanitizer():
    with open(SOURCE_PATH, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=SOURCE_PATH)

    selected = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = [target.id for target in node.targets if isinstance(target, ast.Name)]
            if any(name.startswith("SUMMARY_") and name.endswith("_RE") for name in names):
                selected.append(node)
        if isinstance(node, ast.ClassDef) and node.name == "ShortMemory":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "_sanitize_summary_for_injection":
                    selected.append(item)

    namespace = {"re": re}
    exec(compile(ast.Module(body=selected, type_ignores=[]), SOURCE_PATH, "exec"), namespace)
    return namespace["_sanitize_summary_for_injection"]


class ShortMemoryStyleHygieneTest(unittest.TestCase):
    def setUp(self):
        self.sanitize = load_sanitizer()

        class Dummy:
            @staticmethod
            def _style_hygiene_enabled():
                return True

        self.memory = Dummy()

    def test_keeps_facts_but_removes_future_behavior_directives(self):
        source = (
            "YoYo 已经到家，说明感冒有所缓解。\n"
            "小悠当时有些担心，也有一点生气。\n"
            "需继续执行固定惩罚机制。\n"
            "若他再次开玩笑，后续应保持强硬。\n"
            "这种暧昧拉扯已成为固定互动模式，后续可延续。"
        )
        result = self.sanitize(self.memory, source)
        self.assertIn("YoYo 已经到家", result)
        self.assertIn("小悠当时有些担心", result)
        self.assertNotIn("固定惩罚机制", result)
        self.assertNotIn("保持强硬", result)
        self.assertNotIn("固定互动模式", result)

    def test_trims_inline_strategy_after_factual_clause(self):
        source = "YoYo 已完成服药，需持续监控并继续施压。"
        result = self.sanitize(self.memory, source)
        self.assertEqual("YoYo 已完成服药", result)

    def test_keeps_user_need_as_a_fact(self):
        source = "YoYo 明天需要早起，因此今晚计划早点睡。"
        result = self.sanitize(self.memory, source)
        self.assertEqual(source, result)

    def test_all_directive_summary_can_be_omitted_from_injection(self):
        source = "继续执行原有惩罚。\n保持强硬互动节奏。\n后续可继续调侃。"
        result = self.sanitize(self.memory, source)
        self.assertEqual("", result)


if __name__ == "__main__":
    unittest.main()
