# -*- coding:utf-8 -*-
import ast
import copy
import json
import os
import sys
import threading
import unittest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "plugins", "xiaoyou_common", "relationship_profile_service.py")


class Logger:
    def warning(self, *args, **kwargs):
        return None

    def exception(self, *args, **kwargs):
        return None


def load_service():
    vendor_path = os.path.join(ROOT, "plugins", "xiaoyou_common", "vendor")
    if vendor_path not in sys.path:
        sys.path.insert(0, vendor_path)
    from lunar_python import Solar

    with open(SOURCE, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=SOURCE)
    selected = [
        node for node in tree.body
        if isinstance(node, (ast.Assign, ast.FunctionDef, ast.ClassDef))
        and not (isinstance(node, ast.FunctionDef) and node.name == "get_relationship_profile_service")
    ]
    namespace = {
        "__name__": "relationship_profile_test",
        "__file__": SOURCE,
        "copy": copy,
        "json": json,
        "os": os,
        "threading": threading,
        "date": date,
        "datetime": datetime,
        "timedelta": timedelta,
        "ZoneInfo": ZoneInfo,
        "Solar": Solar,
        "logger": Logger(),
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), SOURCE, "exec"), namespace)
    return namespace


class RelationshipProfileServiceTest(unittest.TestCase):
    def setUp(self):
        module = load_service()
        self.service = module["RelationshipProfileService"](
            os.path.join(ROOT, "data", "nonexistent-test-profile.json")
        )
        self.service._profile = {
            "timezone": "Asia/Shanghai",
            "people": {
                "yoyo": {
                    "name": "Partner",
                    "gender": "male",
                    "birth_date": "2000-03-12",
                    "height_cm": 180,
                    "weight": "private",
                    "face_reference": "missing-reference.jpg",
                },
                "xiaoyou": {
                    "name": "Companion",
                    "gender": "female",
                    "birth_date": "2001-07-07",
                    "height_cm": 165,
                    "appearance": {
                        "face": "柔和鹅蛋脸",
                        "eyes": "灰紫色眼睛",
                        "hair": "自然黑色长发",
                    },
                    "self_identity": {
                        "adult": True,
                        "relationship_to_yoyo": "共同居住的恋人",
                        "birthday_meaning": "生日也是首次相见日",
                    },
                },
            },
            "relationship": {
                "cohabiting": True,
                "home_city": "示例城市",
                "first_met_date": "2020-07-07",
            },
            "special_dates": [
                {"id": "sample_day", "name": "示例日", "month": 1, "day": 1, "proactive": True}
            ],
        }

    def test_real_age_and_relationship_day_are_calculated(self):
        facts = self.service.facts(datetime(2026, 7, 13, 12, 0))
        self.assertEqual(26, facts["yoyo"]["current_age"])
        self.assertEqual(25, facts["xiaoyou"]["current_age"])
        self.assertEqual((date(2026, 7, 13) - date(2020, 7, 7)).days, facts["relationship"]["days_since_first_met"])
        self.assertEqual(facts["relationship"]["days_since_first_met"] + 1, facts["relationship"]["known_day_number"])
        self.assertEqual(6, facts["relationship"]["completed_anniversary_years"])

    def test_age_and_anniversary_really_advance_next_year(self):
        facts = self.service.facts(datetime(2027, 7, 7, 9, 0))
        self.assertEqual(27, facts["yoyo"]["current_age"])
        self.assertEqual(26, facts["xiaoyou"]["current_age"])
        self.assertEqual(7, facts["relationship"]["completed_anniversary_years"])
        event_ids = {event["id"] for event in facts["today_events"]}
        self.assertIn("xiaoyou_birthday", event_ids)
        self.assertIn("first_met_anniversary", event_ids)

    def test_special_day_only_creates_attention_opportunity_after_configured_hour(self):
        old = os.environ.get("XIAOYOU_CALENDAR_ATTENTION_HOUR")
        os.environ["XIAOYOU_CALENDAR_ATTENTION_HOUR"] = "8"
        try:
            self.assertEqual("", self.service.calendar_attention_key(datetime(2027, 7, 7, 7, 59)))
            key = self.service.calendar_attention_key(datetime(2027, 7, 7, 8, 0))
            self.assertIn("2027-07-07", key)
            self.assertIn("xiaoyou_birthday", key)
            self.assertIn("first_met_anniversary", key)
        finally:
            if old is None:
                os.environ.pop("XIAOYOU_CALENDAR_ATTENTION_HOUR", None)
            else:
                os.environ["XIAOYOU_CALENDAR_ATTENTION_HOUR"] = old

    def test_missing_private_face_reference_fails_closed(self):
        self.assertEqual("", self.service.yoyo_reference_path())

    def test_context_marks_weight_as_private(self):
        context = self.service.build_context(datetime(2026, 7, 13, 12, 0))
        self.assertIn("当前26岁", context)
        self.assertIn("当前25岁", context)
        self.assertIn("体重属于其保密信息", context)
        self.assertIn("示例城市", context)
        self.assertIn("当前农历日期", context)
        self.assertIn("身高165厘米", context)
        self.assertIn("脸型：柔和鹅蛋脸", context)
        self.assertIn("眼睛：灰紫色眼睛", context)
        self.assertIn("头发：自然黑色长发", context)
        self.assertIn("明确是成年女性", context)
        self.assertIn("与YoYo的关系：共同居住的恋人", context)
        self.assertIn("生日意义：生日也是首次相见日", context)

    def test_traditional_festivals_follow_lunar_calendar(self):
        cases = {
            date(2026, 2, 17): "spring_festival",
            date(2026, 3, 3): "lantern_festival",
            date(2026, 6, 19): "dragon_boat_festival",
            date(2026, 8, 19): "qixi_festival",
            date(2026, 9, 25): "mid_autumn_festival",
        }
        for solar_date, event_id in cases.items():
            event_ids = {event["id"] for event in self.service.traditional_events_on(solar_date)}
            self.assertIn(event_id, event_ids, solar_date)

    def test_lunar_new_year_eve_is_last_day_before_spring_festival(self):
        eve_ids = {
            event["id"]
            for event in self.service.traditional_events_on(date(2026, 2, 16))
        }
        self.assertIn("lunar_new_year_eve", eve_ids)
        previous_ids = {
            event["id"]
            for event in self.service.traditional_events_on(date(2026, 2, 15))
        }
        self.assertNotIn("lunar_new_year_eve", previous_ids)

    def test_qingming_and_winter_solstice_are_detected_from_solar_terms(self):
        self.assertIn(
            "qingming_festival",
            {event["id"] for event in self.service.traditional_events_on(date(2026, 4, 5))},
        )
        self.assertIn(
            "winter_solstice",
            {event["id"] for event in self.service.traditional_events_on(date(2026, 12, 22))},
        )


if __name__ == "__main__":
    unittest.main()
