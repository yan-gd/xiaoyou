import importlib.util
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "plugins"
    / "xiaoyou_common"
    / "continuity_recovery.py"
)
SPEC = importlib.util.spec_from_file_location("continuity_recovery_under_test", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _candidate(candidates, name):
    return next(messages for mode, messages in candidates if mode == name)


def test_referent_grounded_recovery_keeps_the_named_food_for_elliptical_wash():
    history = [
        {"role": "user", "content": "悠悠鲍鱼还没洗呢"},
        {"role": "assistant", "content": "我这就去洗"},
        {"role": "user", "content": "多洗几个好好补补身子"},
        {"role": "assistant", "content": "又想逗我是不是"},
        {"role": "user", "content": "今天腿都软了"},
        {"role": "assistant", "content": "坏蛋"},
    ]

    candidates = MODULE.build_recovery_candidates(history, "快洗啦…别贫嘴了")
    grounded = _candidate(candidates, "referent_grounded")

    assert grounded[0]["content"] == "悠悠鲍鱼还没洗呢"
    assert all(message["role"] == "user" for message in grounded)
    assert not any("坏蛋" in message["content"] for message in grounded)


def test_referent_grounded_recovery_keeps_meal_context():
    history = [
        {"role": "user", "content": "饭还没有煮好"},
        {"role": "assistant", "content": "那我等你呀"},
        {"role": "user", "content": "先切菜"},
        {"role": "assistant", "content": "好嘛"},
    ]

    candidates = MODULE.build_recovery_candidates(history, "还吃不吃饭啦")
    grounded = _candidate(candidates, "referent_grounded")

    assert grounded == [{"role": "user", "content": "饭还没有煮好"}]


def test_recovery_ladder_always_ends_with_current_only():
    candidates = MODULE.build_recovery_candidates(
        [{"role": "assistant", "content": "上一条回复"}],
        "继续",
    )

    assert candidates[-1] == ("current_only", [])
