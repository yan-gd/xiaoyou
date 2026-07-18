import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "evals" / "run_conversation_quality_eval.py"
CASES = ROOT / "evals" / "conversation_quality_cases.json"


def _load_module():
    spec = importlib.util.spec_from_file_location("conversation_eval_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_synthetic_conversation_suite_is_valid_and_covers_core_failure_modes():
    module = _load_module()
    suite = module.load_cases(CASES)
    cases = suite["cases"]
    categories = {case["category"] for case in cases}

    assert len(cases) >= 10
    assert {
        "context_conflict",
        "reference_resolution",
        "multi_input",
        "continuity",
        "natural_memory_use",
        "stale_memory",
        "memory_provenance",
        "style_repetition",
        "cross_session_continuity",
        "no_fabrication",
    }.issubset(categories)


def test_eval_suite_contains_no_credentials_or_runtime_private_paths():
    text = CASES.read_text(encoding="utf-8").lower()

    assert "sk-" not in text
    assert "api_key" not in text
    assert "@chatroom" not in text
    assert "/app/data/xiaoyou_profile" not in text


def test_eval_dry_run_validates_without_requiring_network_or_api_key(capsys):
    module = _load_module()

    result = module.main(["--cases", str(CASES), "--dry-run", "--limit", "2"])

    assert result == 0
    output = capsys.readouterr().out
    assert "validated 2 cases" in output
    assert "generate_model=" in output


def test_judge_json_parser_accepts_fenced_provider_output():
    module = _load_module()
    value = module._parse_json("```json\n{\"scores\": {\"naturalness\": 5}}\n```")

    assert value == {"scores": {"naturalness": 5}}
