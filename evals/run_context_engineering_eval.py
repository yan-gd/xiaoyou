# -*- coding: utf-8 -*-
"""Offline regression checks for Xiaoyou context planning and token budgets."""

import argparse
import importlib.util
import json
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = Path(__file__).with_name("context_engineering_cases.json")


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_cases(path=DEFAULT_CASES):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("context evaluation file must use schema_version 1")
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("context evaluation file contains no cases")
    seen = set()
    for case in cases:
        case_id = str(case.get("id") or "").strip()
        if not case_id or case_id in seen:
            raise ValueError("invalid or duplicate case id: %r" % case_id)
        seen.add(case_id)
        if not isinstance(case.get("messages"), list) or not case["messages"]:
            raise ValueError("case %s contains no messages" % case_id)
        if not isinstance(case.get("expected"), dict):
            raise ValueError("case %s contains no expected plan" % case_id)
    return cases


def evaluate_case(case, planner, compiler):
    messages = [str(item or "").strip() for item in case["messages"] if str(item or "").strip()]
    plan = planner.plan_context(messages[-1], messages, thinking_enabled=False)
    expected = case["expected"]
    failures = []
    checks = {
        "mode": plan.mode,
        "use_long_memory": plan.use_long_memory,
        "retrieval_mode": plan.retrieval_mode,
        "max_results": plan.long_memory_max_results,
    }
    for key in ("mode", "use_long_memory", "retrieval_mode", "max_results"):
        if key in expected and checks[key] != expected[key]:
            failures.append("%s expected=%r actual=%r" % (key, expected[key], checks[key]))
    missing_types = sorted(
        set(expected.get("types_include") or []) - set(plan.allowed_memory_types)
    )
    if missing_types:
        failures.append("missing memory types: " + ",".join(missing_types))

    long_memory = ""
    if plan.use_long_memory:
        long_memory = "\n".join(
            "- [%s] 合成测试记忆 %s" % (memory_type, index)
            for index, memory_type in enumerate(plan.allowed_memory_types, 1)
        )
    pack = compiler.compile_context_pack(
        current_user_text=messages[-1],
        input_messages=messages,
        short_memory="YoYo：这里是最近真实对话\n小悠：这里是上一轮回复",
        episodic_memory=(
            "[合成历史情节]\nYoYo：这里是与本轮相关的历史原始片段"
            if plan.use_episodic_memory else ""
        ),
        long_memory=long_memory,
        max_chars=10000,
        max_tokens=plan.context_max_tokens,
        section_token_caps=plan.section_token_caps,
    )
    if pack.total_tokens > plan.context_max_tokens:
        failures.append("compiled pack exceeded token budget")
    if messages[-1] not in pack.rendered:
        failures.append("latest visible input was not retained")
    if not plan.use_long_memory and "合成测试记忆" in pack.rendered:
        failures.append("long memory leaked into a no-retrieval plan")
    return {
        "id": case["id"],
        "passed": not failures,
        "failures": failures,
        "plan": plan.as_dict(),
        "context": {
            "total_chars": pack.total_chars,
            "max_chars": pack.max_chars,
            "total_tokens": pack.total_tokens,
            "max_tokens": pack.max_tokens,
        },
    }


def run(cases_path=DEFAULT_CASES):
    planner = _load_module(
        "xiaoyou_context_eval_planner",
        ROOT / "plugins" / "xiaoyou_common" / "context_planner.py",
    )
    compiler = _load_module(
        "xiaoyou_context_eval_compiler",
        ROOT / "plugins" / "xiaoyou_common" / "context_compiler.py",
    )
    results = [evaluate_case(case, planner, compiler) for case in load_cases(cases_path)]
    return {
        "schema_version": 1,
        "created_at": int(time.time()),
        "summary": {
            "cases": len(results),
            "passed": sum(1 for item in results if item["passed"]),
            "failed": sum(1 for item in results if not item["passed"]),
        },
        "results": results,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run offline Xiaoyou context-engineering evals")
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--output", default="")
    args = parser.parse_args(argv)
    report = run(args.cases)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    for item in report["results"]:
        if not item["passed"]:
            print("FAIL %s: %s" % (item["id"], "; ".join(item["failures"])))
    return 0 if report["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
