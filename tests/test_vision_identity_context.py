# -*- coding:utf-8 -*-
import ast
import hashlib
import json
import os
import types
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VISION_PATH = os.path.join(ROOT, "plugins", "qwen_vision", "qwen_vision.py")
LIFE_PHOTO_PATH = os.path.join(ROOT, "plugins", "xiaoyou_life_photo", "__init__.py")


def load_class_methods(path, class_name, method_names, namespace):
    with open(path, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)
    selected = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            selected.extend(
                item
                for item in node.body
                if isinstance(item, ast.FunctionDef) and item.name in method_names
            )
    exec(compile(ast.Module(body=selected, type_ignores=[]), path, "exec"), namespace)
    return {name: namespace[name] for name in method_names}


class Logger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def exception(self, *args, **kwargs):
        return None


class VisionIdentityContextTest(unittest.TestCase):
    def test_image_fingerprint_and_hamming_distance_are_available(self):
        methods = load_class_methods(
            LIFE_PHOTO_PATH,
            "XiaoyouLifePhoto",
            {"_image_fingerprints", "_hash_distance"},
            {"os": os, "hashlib": hashlib, "logger": Logger()},
        )

        class Dummy:
            pass

        dummy = Dummy()
        dummy._image_fingerprints = types.MethodType(methods["_image_fingerprints"], dummy)
        dummy._hash_distance = types.MethodType(methods["_hash_distance"], dummy)

        reference_path = os.path.join(
            ROOT,
            "plugins",
            "xiaoyou_life_photo",
            "assets",
            "xiaoyou_face_front.png",
        )
        fingerprint = dummy._image_fingerprints(reference_path)
        self.assertEqual(64, len(fingerprint["sha256"]))
        self.assertEqual(16, len(fingerprint["dhash"]))

        original_value = int(fingerprint["dhash"], 16)
        six_pixel_differences = "%016x" % (original_value ^ 0b111111)
        distance = dummy._hash_distance(fingerprint["dhash"], six_pixel_differences)
        self.assertEqual(6, distance)

    def test_vision_prompt_contains_prior_context_and_confirmed_self_identity(self):
        snapshot = types.SimpleNamespace(
            character_context="小悠核心人格与现实时间",
            short_memory="刚才小悠分享了一张餐厅门口的生活照",
            long_memory="今天两人刚领证并准备吃饭",
        )
        methods = load_class_methods(
            VISION_PATH,
            "QwenVision",
            {"_build_prompt"},
            {
                "os": os,
                "json": json,
                "build_context_snapshot": lambda **kwargs: snapshot,
                "build_time_context": lambda: "当前时间",
            },
        )

        class Dummy:
            pass

        prompt = methods["_build_prompt"](
            Dummy(),
            "yoyo",
            ["美吧"],
            {
                "profile": {"hair": "黑色长发", "eyes": "灰紫色眼睛"},
                "yoyo_profile": {"hair": "黑色短发", "glasses": "可变化"},
                "recent_photos": [{"caption": "到餐厅啦"}],
                "provenance": {
                    "matched": True,
                    "caption": "到餐厅啦",
                    "visual_prompt": "餐厅门口的全身生活照",
                    "created_at": 123,
                },
            },
        )
        self.assertIn("刚才小悠分享了一张餐厅门口的生活照", prompt)
        self.assertIn("今天两人刚领证并准备吃饭", prompt)
        self.assertIn("图中主体就是小悠本人", prompt)
        self.assertIn("黑色长发", prompt)
        self.assertIn("黑色短发", prompt)
        self.assertIn("美吧", prompt)

    def test_unmatched_image_prompt_can_recognize_yoyo_without_identity_mixup(self):
        snapshot = types.SimpleNamespace(
            character_context="关系与时间事实",
            short_memory="近期聊天",
            long_memory="相关记忆",
        )
        methods = load_class_methods(
            VISION_PATH,
            "QwenVision",
            {"_build_prompt"},
            {
                "os": os,
                "json": json,
                "build_context_snapshot": lambda **kwargs: snapshot,
                "build_time_context": lambda: "当前时间",
            },
        )
        prompt = methods["_build_prompt"](
            object(),
            "yoyo",
            [],
            {
                "profile": {"identity_name": "小悠"},
                "yoyo_profile": {"name": "YoYo", "face": "真实脸部档案"},
                "recent_photos": [],
                "provenance": {"matched": False},
            },
        )
        self.assertIn("YoYo参考图只用于判断图片中的男性是否是YoYo本人", prompt)
        self.assertIn("绝不能互相混淆", prompt)
        self.assertIn("真实脸部档案", prompt)

    def test_removed_keyword_routes_and_sensitive_message_dump(self):
        with open(VISION_PATH, "r", encoding="utf-8") as handle:
            vision_source = handle.read()
        with open(LIFE_PHOTO_PATH, "r", encoding="utf-8") as handle:
            life_source = handle.read()
        self.assertNotIn("_looks_like_xiaoyou_photo_request", vision_source)
        self.assertNotIn("_looks_like_photo_candidate", life_source)
        self.assertNotIn("wrapped msg dict", vision_source)
        self.assertNotIn("kwargs=%r", vision_source)


if __name__ == "__main__":
    unittest.main()
