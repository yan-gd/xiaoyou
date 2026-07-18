from pathlib import Path
import importlib.util
import sys


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "xiaoyou_memory_schema_under_test",
    ROOT / "plugins" / "xiaoyou_common" / "memory_schema.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
LEGACY = MODULE.LEGACY
normalize_allowed = MODULE.normalize_allowed
normalize_memory_type = MODULE.normalize_memory_type
near_duplicate_text = MODULE.near_duplicate_text


def test_governance_categories_map_to_canonical_memory_types():
    assert normalize_memory_type(category="durable_preference") == "semantic"
    assert normalize_memory_type(category="episodic_event") == "episodic"
    assert normalize_memory_type(category="pending_thread") == "pending"


def test_unknown_old_provider_record_remains_legacy_compatible():
    assert normalize_memory_type() == LEGACY
    assert LEGACY in normalize_allowed(["project", "pending"])


def test_near_duplicate_detection_is_conservative_but_handles_legacy_prefixes():
    assert near_duplicate_text(
        "YoYo明确确认小悠是他的女朋友。",
        "# 用户明确确认小悠是他的女朋友",
    )
    assert not near_duplicate_text(
        "YoYo喜欢自然简短的回复。",
        "YoYo计划继续部署小悠服务器。",
    )
