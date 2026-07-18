import importlib.util
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "plugins"
    / "xiaoyou_common"
    / "context_compiler.py"
)
SPEC = importlib.util.spec_from_file_location("context_compiler_under_test", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
compile_context_pack = MODULE.compile_context_pack


def test_context_pack_declares_authority_and_keeps_current_input_last():
    pack = compile_context_pack(
        current_user_text="我现在不喜欢以前那种简短回复了，请详细一点。",
        recent_state="当前话题：回复长度偏好",
        short_memory="[昨天] YoYo说以前偏好简短回复。",
        long_memory="- [一个月前] YoYo偏好简短回复。",
        max_chars=1800,
    )

    assert pack.rendered.startswith("[上下文权威顺序]")
    assert "若与任何旧信息冲突，以本轮明确表达为准" in pack.rendered
    assert "[当前短时对话状态]" in pack.rendered
    assert pack.rendered.endswith("我现在不喜欢以前那种简短回复了，请详细一点。")
    assert pack.total_chars == len(pack.rendered)
    assert pack.total_chars <= pack.max_chars


def test_budget_pressure_keeps_newest_short_memory_and_top_ranked_long_memory():
    short_lines = ["较早短期%02d-%s" % (index, "甲" * 50) for index in range(20)]
    short_lines.append("最新短期事实-必须留下")
    long_lines = ["最高相关长期事实-必须留下"]
    long_lines.extend("低相关长期%02d-%s" % (index, "乙" * 50) for index in range(20))

    pack = compile_context_pack(
        current_user_text="接着聊刚才的事。",
        short_memory="\n".join(short_lines),
        long_memory="\n".join(long_lines),
        max_chars=1200,
    )

    assert pack.total_chars <= 1200
    assert "最新短期事实-必须留下" in pack.rendered
    assert "最高相关长期事实-必须留下" in pack.rendered
    assert "已按预算省略部分上下文" in pack.rendered
    section_map = {item["name"]: item for item in pack.manifest["sections"]}
    assert section_map["recent_conversation"]["truncated"] is True
    assert section_map["long_memory"]["truncated"] is True
    assert section_map["current_input"]["truncated"] is False


def test_multi_message_input_is_ordered_once_and_not_duplicated_by_context():
    pack = compile_context_pack(
        current_user_text="第二条",
        input_messages=["第一条", "第二条", "第三条补充"],
        short_memory="上一轮聊天",
        long_memory="一条长期记忆",
        max_chars=1600,
    )

    assert "消息 1：第一条\n消息 2：第二条\n消息 3：第三条补充" in pack.rendered
    assert pack.rendered.count("消息 1：第一条") == 1
    assert pack.rendered.rfind("消息 3：第三条补充") > pack.rendered.find("[相关长期记忆]")


def test_manifest_is_auditable_without_copying_context_contents():
    secret_phrase = "这是一段只应存在于上下文正文里的私密原话"
    pack = compile_context_pack(
        current_user_text=secret_phrase,
        short_memory="近期内容",
        long_memory="长期内容",
        max_chars=1500,
    )

    assert secret_phrase in pack.rendered
    assert secret_phrase not in repr(pack.manifest)
    assert pack.manifest["schema_version"] == 2
    assert pack.manifest["total_tokens"] <= pack.manifest["max_tokens"]
    assert all(len(item["content_hash"]) == 16 for item in pack.manifest["sections"])


def test_legacy_upstream_context_has_lowest_authority_but_remains_compatible():
    pack = compile_context_pack(
        current_user_text="当前消息",
        upstream_context="旧插件提供的上下文",
        max_chars=1400,
    )

    assert "[兼容上游上下文]" in pack.rendered
    assert "旧插件提供的上下文" in pack.rendered
    sections = {item["name"]: item for item in pack.manifest["sections"]}
    assert sections["upstream_fallback"]["authority"] == 4
    assert sections["current_input"]["authority"] == 1


def test_exact_token_counter_enforces_a_hard_pack_budget():
    pack = compile_context_pack(
        current_user_text="当前输入" * 100,
        short_memory="近期对话" * 200,
        long_memory="长期记忆" * 200,
        max_chars=10000,
        max_tokens=800,
        token_counter=len,
    )

    assert pack.total_tokens <= 800
    assert pack.manifest["token_estimation"] == "injected_counter"
    assert "[YoYo 本轮可见输入]" in pack.rendered


def test_episodic_context_keeps_summary_and_raw_evidence_separate_from_durable_memory():
    pack = compile_context_pack(
        current_user_text="上次服务器部署到哪里了",
        episodic_memory=(
            "[情节 2026-07-17 18:00～2026-07-17 19:00｜修复容器权限]\n"
            "相关原始片段：\n[07-17 18:24] YoYo：容器提示 Permission denied"
        ),
        long_memory="- [project] 小悠工程部署在 /opt/cow-legacy",
        max_chars=2400,
    )

    assert "[相关历史情节与原始片段]" in pack.rendered
    assert "Permission denied" in pack.rendered
    assert "[相关长期记忆]" in pack.rendered
    sections = {item["name"]: item for item in pack.manifest["sections"]}
    assert sections["episodic_memory"]["authority"] == 3
    assert sections["long_memory"]["authority"] == 3
