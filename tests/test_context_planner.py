from pathlib import Path
import importlib.util
import sys


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "xiaoyou_context_planner_under_test",
    ROOT / "plugins" / "xiaoyou_common" / "context_planner.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
plan_context = MODULE.plan_context


def test_emotional_turn_uses_working_memory_without_cloud_retrieval():
    plan = plan_context("今天好累，抱抱我")

    assert plan.mode == "emotional"
    assert plan.use_long_memory is False
    assert plan.long_memory_max_results == 0
    assert plan.section_token_caps["recent_conversation"] > 0
    assert plan.use_episodic_memory is False


def test_affectionate_bedtime_turn_does_not_fall_back_to_general_memory():
    plan = plan_context("嗯嗯，我轻抚着小悠悠的背，快睡啦老婆")

    assert plan.mode == "emotional"
    assert plan.use_long_memory is False


def test_explicit_recall_requests_recovery_and_typed_memories():
    plan = plan_context("你还记得我们上次去哪里了吗")

    assert plan.mode == "recall"
    assert plan.retrieval_mode == "recovery"
    assert plan.long_memory_max_results == 5
    assert "episodic" in plan.allowed_memory_types
    assert "relationship" in plan.allowed_memory_types
    assert plan.use_episodic_memory is True
    assert plan.episodic_max_results == 4


def test_project_turn_requests_project_and_pending_memory():
    plan = plan_context("继续处理小悠服务器容器部署的报错")

    assert plan.mode == "project"
    assert plan.use_long_memory is True
    assert "project" in plan.allowed_memory_types
    assert "pending" in plan.allowed_memory_types
    assert plan.episodic_max_results == 3


def test_batch_input_can_change_plan_even_if_current_fragment_is_short():
    plan = plan_context("继续吧", ["刚才服务器部署报错了", "继续吧"])

    assert plan.mode == "project"


def test_textual_emojis_do_not_turn_a_daily_followup_into_general_recall():
    plan = plan_context("笨蛋悠悠…还吃不吃饭啦[笑脸][笑脸]")

    assert plan.mode == "continuation"
    assert plan.use_long_memory is False


def test_elliptical_daily_action_relies_on_working_memory():
    plan = plan_context("快洗啦…别贫嘴了")

    assert plan.mode == "continuation"
    assert plan.use_long_memory is False


def test_correction_has_precedence_over_generic_project_terms():
    plan = plan_context("纠正一下，以后不要把这个项目叫旧名字")

    assert plan.mode == "correction"
    assert "correction" in plan.allowed_memory_types


def test_plan_never_exceeds_model_available_window(monkeypatch):
    monkeypatch.setenv("XIAOYOU_MODEL_CONTEXT_TOKENS", "4096")
    monkeypatch.setenv("XIAOYOU_SYSTEM_RESERVED_TOKENS", "1500")
    monkeypatch.setenv("XIAOYOU_OUTPUT_RESERVED_TOKENS", "700")
    monkeypatch.setenv("XIAOYOU_THINKING_RESERVED_TOKENS", "900")

    plan = plan_context("分析这个项目架构", thinking_enabled=True)

    assert plan.context_max_tokens == 996
