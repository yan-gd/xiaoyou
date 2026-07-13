# -*- coding:utf-8 -*-
import ast
import importlib.util
import json
import os
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_PATH = os.path.join(
    ROOT,
    "plugins",
    "xiaoyou_life_photo",
    "plan_rules.py",
)
SPEC = importlib.util.spec_from_file_location("xiaoyou_life_photo_plan_rules", RULES_PATH)
RULES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RULES)


def load_seedream_prompt_builder():
    plugin_path = os.path.join(
        ROOT,
        "plugins",
        "xiaoyou_life_photo",
        "__init__.py",
    )
    with open(plugin_path, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=plugin_path)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "XiaoyouLifePhoto":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "_build_seedream_prompt":
                    module = ast.Module(body=[item], type_ignores=[])
                    namespace = {"json": json}
                    exec(compile(module, plugin_path, "exec"), namespace)
                    return namespace["_build_seedream_prompt"]
    raise AssertionError("_build_seedream_prompt not found")


class LifePhotoPlanRulesTest(unittest.TestCase):
    def test_structured_third_person_choice_is_preserved(self):
        mode = RULES.normalize_capture_mode(
            "third_person_camera",
            user_text="这段原话不参与本地语义判断",
        )
        self.assertEqual("third_person_camera", mode)

    def test_structured_free_hands_cannot_use_front_selfie(self):
        mode = RULES.normalize_capture_mode(
            "front_camera_selfie",
            hands_free_required=True,
        )
        self.assertEqual("timer_camera", mode)

    def test_structured_distant_full_body_cannot_use_front_selfie(self):
        mode = RULES.normalize_capture_mode(
            "front_camera_selfie",
            distant_camera_required=True,
        )
        self.assertEqual("timer_camera", mode)

    def test_planner_timer_choice_is_preserved(self):
        mode = RULES.normalize_capture_mode(
            "timer_camera",
        )
        self.assertEqual("timer_camera", mode)

    def test_planner_front_selfie_choice_is_preserved(self):
        mode = RULES.normalize_capture_mode(
            "front_camera_selfie",
        )
        self.assertEqual("front_camera_selfie", mode)

    def test_user_wording_never_overrides_structured_mode(self):
        mode = RULES.normalize_capture_mode(
            "timer_camera",
            user_text="任意自然语言，包括镜头、自拍或否定表达",
        )
        self.assertEqual("timer_camera", mode)

    def test_structured_mirror_choice_is_valid(self):
        mode = RULES.normalize_capture_mode(
            "mirror_selfie",
            distant_camera_required=True,
        )
        self.assertEqual("mirror_selfie", mode)

    def test_third_person_operator_uses_structured_value(self):
        operator = RULES.normalize_camera_operator(
            "third_person_camera",
            requested_operator="friend",
            user_text="原话不会覆盖拍摄者",
        )
        self.assertEqual("friend", operator)

    def test_invalid_third_person_operator_is_conservative(self):
        operator = RULES.normalize_camera_operator(
            "third_person_camera",
            requested_operator="invented_person",
        )
        self.assertEqual("unspecified_third_person", operator)

    def test_constraints_are_enum_validated_without_text_parsing(self):
        constraints = RULES.normalize_constraints([
            "hands_free_pose",
            "distant_full_body",
            "unknown_constraint",
        ])
        self.assertEqual(["hands_free_pose", "distant_full_body"], constraints)

    def test_rule_module_contains_no_regex_intent_engine(self):
        with open(RULES_PATH, "r", encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("re.compile", source)
        self.assertNotIn("re.search", source)

    def test_seedream_prompt_uses_profile_age_and_semantic_camera(self):
        builder = load_seedream_prompt_builder()

        class Dummy:
            relationship_profile = type("Relationship", (), {
                "xiaoyou_current_age": lambda self: 22,
                "yoyo_visual_profile": lambda self: {},
                "yoyo_reference_path": lambda self: "",
            })()
            profile = {
                "identity_age": "22岁的成年女性",
                "face": {"eyes": "灰紫色"},
                "hair": {"color": "黑色"},
                "body": {"height": "约165厘米"},
                "reference_images": [
                    {
                        "role": "正面人脸参考",
                        "preserve": "人物身份",
                        "do_not_copy_by_default": "中性表情",
                    }
                ],
            }

        prompt = builder(
            Dummy(),
            {
                "aspect_ratio": "portrait",
                "capture_mode": "third_person_camera",
                "share_intent": "requested_pose",
                "emotion": "被夸奖后的开心和一点害羞",
                "expression": "眼神明亮，嘴角自然上扬",
                "gaze": "看向镜头",
                "pose": "双手自然放在身前",
                "caption": "只给你看一下哦",
                "visual_prompt": "街边自然站立的半身生活照",
                "pose_constraints": ["hands_free_pose"],
            },
        )
        self.assertIn("第三人称拍摄者", prompt)
        self.assertIn("被夸奖后的开心和一点害羞", prompt)
        self.assertIn("22岁的成年女性", prompt)
        self.assertNotIn("人物明确为24岁", prompt)

    def test_couple_prompt_keeps_two_reference_identities_separate(self):
        builder = load_seedream_prompt_builder()

        class Relationship:
            def xiaoyou_current_age(self):
                return 22

            def yoyo_visual_profile(self):
                return {"name": "YoYo", "current_age": 21, "face": {"hair": "黑色短发"}}

            def yoyo_reference_path(self):
                return "private-yoyo-reference.jpg"

        class Dummy:
            relationship_profile = Relationship()
            profile = {
                "identity_age": "动态年龄",
                "face": {"eyes": "灰紫色"},
                "hair": {"color": "黑色长发"},
                "body": {"height": "约165厘米"},
                "reference_images": [],
            }

        prompt = builder(Dummy(), {
            "aspect_ratio": "portrait",
            "capture_mode": "third_person_camera",
            "include_yoyo": True,
            "visual_prompt": "两人自然同框",
            "pose_constraints": [],
        })
        self.assertIn("最后的YoYo自拍只定义YoYo", prompt)
        self.assertIn("严禁换脸、融脸", prompt)
        self.assertIn("21", prompt)


if __name__ == "__main__":
    unittest.main()
