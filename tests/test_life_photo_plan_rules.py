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
    def test_explicit_third_person_wins(self):
        mode = RULES.normalize_capture_mode(
            "front_camera_selfie",
            user_text="用第三人称视角拍一张，我双手托脸做个pose",
        )
        self.assertEqual("third_person_camera", mode)

    def test_two_free_hands_cannot_be_front_selfie(self):
        mode = RULES.normalize_capture_mode(
            "front_camera_selfie",
            user_text="给我拍张照片报备",
            visual_prompt="小悠双手捧脸，开心地看着镜头",
        )
        self.assertEqual("timer_camera", mode)

    def test_report_intent_respects_planner_timer_choice(self):
        mode = RULES.normalize_capture_mode(
            "timer_camera",
            user_text="自己拍张照片给我报备一下",
        )
        self.assertEqual("timer_camera", mode)

    def test_report_intent_can_be_front_selfie(self):
        mode = RULES.normalize_capture_mode(
            "front_camera_selfie",
            user_text="自己拍张照片给我报备一下",
        )
        self.assertEqual("front_camera_selfie", mode)

    def test_explicit_selfie_stays_front_camera(self):
        mode = RULES.normalize_capture_mode(
            "timer_camera",
            user_text="用前置自拍一张给我看看",
        )
        self.assertEqual("front_camera_selfie", mode)

    def test_negated_third_person_does_not_override_selfie(self):
        mode = RULES.normalize_capture_mode(
            "third_person_camera",
            user_text="不要用第三人称，改成前置自拍",
        )
        self.assertEqual("front_camera_selfie", mode)

    def test_mirror_full_body_is_valid(self):
        mode = RULES.normalize_capture_mode(
            "front_camera_selfie",
            user_text="对镜自拍一张全身穿搭照",
        )
        self.assertEqual("mirror_selfie", mode)

    def test_full_body_front_selfie_falls_back_to_timer(self):
        mode = RULES.normalize_capture_mode(
            "front_camera_selfie",
            user_text="拍一张从头到脚的完整全身照",
        )
        self.assertEqual("timer_camera", mode)

    def test_third_person_operator_does_not_invent_yoyo(self):
        operator = RULES.normalize_camera_operator(
            "third_person_camera",
            requested_operator="",
            user_text="用第三人称视角拍一张",
        )
        self.assertEqual("unspecified_third_person", operator)

    def test_seedream_prompt_uses_profile_age_and_semantic_camera(self):
        builder = load_seedream_prompt_builder()

        class Dummy:
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


if __name__ == "__main__":
    unittest.main()
