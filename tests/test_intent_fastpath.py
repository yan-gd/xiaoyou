import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "plugins" / "xiaoyou_common" / "intent_fastpath.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("intent_fastpath_under_test", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_photo_capability_always_uses_model_while_legacy_gates_remain(monkeypatch):
    monkeypatch.setenv("XIAOYOU_FAST_CHAT_ROUTE_ENABLED", "true")
    module = _load_module()

    for text in ("我想你了嘛", "我休息一会儿", "你得等等", "爱你悠悠"):
        assert module.might_need_capability(text, "reminder") is False
        assert module.might_need_capability(text, "photo") is True
        assert module.might_need_capability(text, "external") is False
    assert module.might_need_capability("嗯嗯好～", "photo") is True


def test_domain_shaped_text_keeps_semantic_router(monkeypatch):
    monkeypatch.setenv("XIAOYOU_FAST_CHAT_ROUTE_ENABLED", "true")
    module = _load_module()

    assert module.might_need_capability("晚上九点提醒我下班", "reminder") is True
    assert module.might_need_capability("现在发张自拍给我看看", "photo") is True
    assert module.might_need_capability("帮我查一下今晚天气", "external") is True
    assert module.might_need_capability(
        "这张更好看", "photo", pending_user_image=True
    ) is True


def test_disabling_fast_route_preserves_legacy_semantic_classification(monkeypatch):
    monkeypatch.setenv("XIAOYOU_FAST_CHAT_ROUTE_ENABLED", "false")
    module = _load_module()

    assert module.might_need_capability("我想你了", "external") is True


def test_adaptive_thinking_keeps_casual_chat_fast_and_complex_turns_deep(monkeypatch):
    monkeypatch.setenv("XIAOYOU_CHAT_ADAPTIVE_THINKING_ENABLED", "true")
    module = _load_module()

    assert module.should_use_chat_thinking("我想你了嘛") is False
    assert module.should_use_chat_thinking("请分析一下这套记忆架构为什么会重复") is True
    assert module.should_use_chat_thinking("补充", ["第一点", "第二点", "第三点"]) is True
