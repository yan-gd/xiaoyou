from pathlib import Path
import importlib.util
import sys


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "xiaoyou_token_budget_under_test",
    ROOT / "plugins" / "xiaoyou_common" / "token_budget.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
build_model_token_budget = MODULE.build_model_token_budget
estimate_tokens = MODULE.estimate_tokens
trim_to_token_budget = MODULE.trim_to_token_budget


def test_estimator_counts_chinese_and_mixed_text_conservatively():
    assert estimate_tokens("小悠你好") >= 4
    assert estimate_tokens("deploy xiaoyou_container 2026") >= 7


def test_model_budget_reserves_system_output_and_thinking(monkeypatch):
    monkeypatch.setenv("XIAOYOU_MODEL_CONTEXT_TOKENS", "4096")
    monkeypatch.setenv("XIAOYOU_SYSTEM_RESERVED_TOKENS", "1200")
    monkeypatch.setenv("XIAOYOU_OUTPUT_RESERVED_TOKENS", "600")
    monkeypatch.setenv("XIAOYOU_THINKING_RESERVED_TOKENS", "800")

    casual = build_model_token_budget(requested_context_tokens=3000, thinking_enabled=False)
    complex_turn = build_model_token_budget(requested_context_tokens=3000, thinking_enabled=True)

    assert casual.context_max_tokens == 2296
    assert complex_turn.context_max_tokens == 1496


def test_trim_honors_an_exact_injected_counter():
    counter = len
    value = "A" * 400
    trimmed = trim_to_token_budget(value, 80, counter=counter, keep="head_tail")

    assert counter(trimmed) <= 80
    assert trimmed.startswith("A")
    assert trimmed.endswith("A")


def test_model_budget_also_reserves_native_role_history(monkeypatch):
    monkeypatch.setenv("XIAOYOU_MODEL_CONTEXT_TOKENS", "8192")
    monkeypatch.setenv("XIAOYOU_SYSTEM_RESERVED_TOKENS", "2000")
    monkeypatch.setenv("XIAOYOU_OUTPUT_RESERVED_TOKENS", "600")
    monkeypatch.setenv("XIAOYOU_NATIVE_HISTORY_RESERVED_TOKENS", "2400")

    budget = build_model_token_budget(
        requested_context_tokens=5000,
        thinking_enabled=False,
    )

    assert budget.context_max_tokens == 3192
    assert budget.native_history_reserved_tokens == 2400
