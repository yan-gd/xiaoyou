import importlib.util
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_service(monkeypatch, model_result):
    class _Logger:
        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    calls = []
    common = types.ModuleType("common")
    common_log = types.ModuleType("common.log")
    common_log.logger = _Logger()
    plugins_common = types.ModuleType("plugins.xiaoyou_common")
    context_service = types.ModuleType(
        "plugins.xiaoyou_common.context_service"
    )
    context_service.build_context_snapshot = (
        lambda **_kwargs: types.SimpleNamespace(
            short_memory="YoYo：那你说给我听吧\n小悠：好呀"
        )
    )
    model_gateway = types.ModuleType(
        "plugins.xiaoyou_common.model_gateway"
    )

    def _chat_completion(**kwargs):
        calls.append(kwargs)
        return model_result

    model_gateway.chat_completion = _chat_completion
    thinking_config = types.ModuleType(
        "plugins.xiaoyou_common.thinking_config"
    )
    thinking_config.build_thinking_payload = (
        lambda *_args, **_kwargs: {"enable_thinking": False}
    )
    for name, module in {
        "common": common,
        "common.log": common_log,
        "plugins.xiaoyou_common": plugins_common,
        "plugins.xiaoyou_common.context_service": context_service,
        "plugins.xiaoyou_common.model_gateway": model_gateway,
        "plugins.xiaoyou_common.thinking_config": thinking_config,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    spec = importlib.util.spec_from_file_location(
        "xiaoyou_app_voice_reply_decision_test_module",
        ROOT
        / "plugins"
        / "xiaoyou_common"
        / "app_voice_reply_decision.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, calls


def test_text_turn_uses_model_voice_decision_without_local_intent_gate(
    monkeypatch,
):
    module, calls = _load_service(
        monkeypatch,
        types.SimpleNamespace(
            ok=True,
            content=(
                '{"medium":"voice","confidence":0.94,'
                '"reason":"当前是在接受小悠亲口表达"}'
            ),
        ),
    )
    service = module.AppVoiceReplyDecisionService()

    decision = service.decide(
        input_kind="text",
        user_text="那你说给我听吧",
        assistant_text="好呀，我最喜欢你了～",
        session_id="yoyo",
        trace_id="trace-1",
        input_id="text-1",
    )

    assert decision.use_voice is True
    assert decision.model_ok is True
    assert decision.forced is False
    assert decision.confidence == 0.94
    assert len(calls) == 1
    assert calls[0]["purpose"] == "choose_reply_medium"
    prompt = calls[0]["payload"]["messages"][1]["content"]
    assert "那你说给我听吧" in prompt
    assert "好呀，我最喜欢你了～" in prompt
    assert "最近对话" in prompt


def test_voice_input_is_forced_to_voice_without_calling_model(monkeypatch):
    module, calls = _load_service(
        monkeypatch,
        types.SimpleNamespace(ok=False, content=""),
    )
    service = module.AppVoiceReplyDecisionService()

    decision = service.decide(
        input_kind="voice",
        user_text="我回来了",
        assistant_text="欢迎回来～",
    )

    assert decision.use_voice is True
    assert decision.forced is True
    assert calls == []


def test_invalid_or_failed_medium_model_safely_falls_back_to_text(
    monkeypatch,
):
    module, _calls = _load_service(
        monkeypatch,
        types.SimpleNamespace(ok=True, content="not-json"),
    )
    service = module.AppVoiceReplyDecisionService()
    invalid = service.decide(
        input_kind="text",
        user_text="陪我聊聊",
        assistant_text="当然好呀",
    )
    assert invalid.use_voice is False
    assert invalid.model_ok is False

    module, _calls = _load_service(
        monkeypatch,
        types.SimpleNamespace(
            ok=False,
            content="",
            error_kind="provider_unavailable",
        ),
    )
    service = module.AppVoiceReplyDecisionService()
    failed = service.decide(
        input_kind="text",
        user_text="陪我聊聊",
        assistant_text="当然好呀",
    )
    assert failed.use_voice is False
    assert failed.model_ok is False
