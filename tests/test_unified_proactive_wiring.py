# -*- coding:utf-8 -*-
import os
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), "r", encoding="utf-8") as handle:
        return handle.read()


class UnifiedProactiveWiringTest(unittest.TestCase):
    def test_old_followup_loop_is_disabled_in_unified_mode(self):
        source = read("plugins", "conversation_followup", "__init__.py")
        self.assertIn("self.enabled and not self.unified_enabled", source)
        self.assertIn("_delegate_completed_reply", source)
        self.assertIn("_delegate_user_context", source)

    def test_user_activity_is_deduplicated_by_input_id(self):
        source = read("plugins", "proactive_love", "proactive_love.py")
        self.assertIn("last_observed_input_id", source)
        self.assertIn("conversation_revision", source)
        self.assertIn("source_revision", source)

    def test_unified_scheduler_uses_model_due_time_not_old_idle_gates(self):
        source = read("plugins", "proactive_love", "proactive_love.py")
        method = source[source.index("    def _check_and_send"):source.index("    def _generate_message")]
        self.assertIn("next_evaluation_ts", method)
        self.assertNotIn("PROACTIVE_IDLE_SECONDS", method)
        self.assertNotIn("PROACTIVE_COOLDOWN_SECONDS", method)
        self.assertNotIn("PROACTIVE_DECISION_COOLDOWN_SECONDS", method)

    def test_photo_decision_uses_real_image_path(self):
        source = read("plugins", "proactive_love", "proactive_love.py")
        self.assertIn('decision.action == "photo"', source)
        self.assertIn('image_path=(photo_share or {}).get("path", "")', source)
        self.assertIn("real photo unavailable; no placeholder sent", source)

    def test_unified_photo_mode_bypasses_legacy_six_hour_gate(self):
        source = read("plugins", "xiaoyou_life_photo", "__init__.py")
        method = source[source.index("    def _can_send_proactive"):source.index("    def _format_recent_shares")]
        unified_return = method.index("return True")
        legacy_interval = method.index("XIAOYOU_LIFE_PHOTO_PROACTIVE_MIN_INTERVAL_SECONDS")
        self.assertLess(unified_return, legacy_interval)


if __name__ == "__main__":
    unittest.main()
