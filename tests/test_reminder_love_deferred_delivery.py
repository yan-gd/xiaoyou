import importlib.util
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def _load_module(monkeypatch):
    class _Logger:
        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    class _Plugin:
        def __init__(self):
            self.handlers = {}

    class _JsonStateStore:
        def __init__(self, *_args, **_kwargs):
            pass

    common = types.ModuleType("common")
    common_log = types.ModuleType("common.log")
    common_log.logger = _Logger()
    plugins = types.ModuleType("plugins")
    plugins.__path__ = []
    plugins.Plugin = _Plugin
    plugins.Event = SimpleNamespace(ON_HANDLE_CONTEXT="on_handle_context")
    plugins.EventContext = dict
    plugins.register = lambda **_kwargs: lambda cls: cls

    modules = {
        "common": common,
        "common.log": common_log,
        "plugins": plugins,
        "plugins.xiaoyou_common": types.ModuleType(
            "plugins.xiaoyou_common"
        ),
        "plugins.xiaoyou_common.thinking_config": types.ModuleType(
            "plugins.xiaoyou_common.thinking_config"
        ),
        "plugins.xiaoyou_common.model_gateway": types.ModuleType(
            "plugins.xiaoyou_common.model_gateway"
        ),
        "plugins.xiaoyou_common.outbound_dispatcher": types.ModuleType(
            "plugins.xiaoyou_common.outbound_dispatcher"
        ),
        "plugins.xiaoyou_common.state_store": types.ModuleType(
            "plugins.xiaoyou_common.state_store"
        ),
        "plugins.xiaoyou_common.runtime_paths": types.ModuleType(
            "plugins.xiaoyou_common.runtime_paths"
        ),
        "plugins.xiaoyou_common.conversation_coordinator": types.ModuleType(
            "plugins.xiaoyou_common.conversation_coordinator"
        ),
        "plugins.xiaoyou_common.context_service": types.ModuleType(
            "plugins.xiaoyou_common.context_service"
        ),
        "plugins.xiaoyou_common.intent_fastpath": types.ModuleType(
            "plugins.xiaoyou_common.intent_fastpath"
        ),
        "bridge": types.ModuleType("bridge"),
        "bridge.context": types.ModuleType("bridge.context"),
        "bridge.reply": types.ModuleType("bridge.reply"),
    }
    modules["plugins.xiaoyou_common.thinking_config"].build_thinking_payload = (
        lambda *args, **kwargs: {}
    )
    modules["plugins.xiaoyou_common.model_gateway"].chat_completion = (
        lambda **kwargs: SimpleNamespace(ok=False, content="")
    )
    outbound = modules["plugins.xiaoyou_common.outbound_dispatcher"]
    outbound.resolve_receiver = lambda _session_id, receiver="": receiver
    outbound.send_text = lambda **kwargs: None
    modules["plugins.xiaoyou_common.state_store"].JsonStateStore = (
        _JsonStateStore
    )
    modules["plugins.xiaoyou_common.runtime_paths"].runtime_path = (
        lambda *args, **kwargs: "reminders.json"
    )
    modules[
        "plugins.xiaoyou_common.conversation_coordinator"
    ].claim_action = lambda *args, **kwargs: None
    context_service = modules["plugins.xiaoyou_common.context_service"]
    context_service.build_character_context = lambda *args, **kwargs: ""
    context_service.extract_current_user_text = lambda *args, **kwargs: ""
    context_service.load_long_memory_context = lambda *args, **kwargs: ""
    modules["plugins.xiaoyou_common.intent_fastpath"].might_need_capability = (
        lambda *args, **kwargs: False
    )
    modules["bridge.context"].ContextType = SimpleNamespace(TEXT="text")
    modules["bridge.reply"].Reply = lambda *args, **kwargs: None
    modules["bridge.reply"].ReplyType = SimpleNamespace(TEXT="text")
    for name, value in modules.items():
        monkeypatch.setitem(sys.modules, name, value)

    spec = importlib.util.spec_from_file_location(
        "xiaoyou_reminder_deferred_delivery_test_module",
        ROOT / "plugins" / "reminder_love" / "reminder_love.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_deferred_app_reminder_is_not_requeued(monkeypatch):
    module = _load_module(monkeypatch)
    reminder = object.__new__(module.ReminderLove)
    state = {
        "yoyo": [
            {
                "id": "reminder-1",
                "session_id": "yoyo",
                "receiver": "app:yoyo-phone",
                "task": "起床",
                "due_ts": int(time.time()) - 1,
                "status": "pending",
                "trace_id": "trace-1",
                "input_id": "voice-room:turn-1",
            }
        ]
    }
    sends = []
    completed = []
    cancelled = []

    monkeypatch.setattr(reminder, "_enabled", lambda: True)
    monkeypatch.setattr(reminder, "_load_all", lambda: state)
    monkeypatch.setattr(reminder, "_save_all", lambda _data: True)
    monkeypatch.setattr(
        reminder,
        "_generate_reminder_message",
        lambda _item: "YoYo，起床啦",
    )
    monkeypatch.setattr(
        reminder,
        "_split_message",
        lambda text: [text],
    )
    monkeypatch.setattr(
        module,
        "resolve_receiver",
        lambda _session_id, receiver: receiver,
    )

    class _Lease:
        accepted = True
        finished = False
        token = "lease-1"
        reason = ""

        @staticmethod
        def current():
            return True

        def complete(self, delivered=False, detail=""):
            self.finished = True
            completed.append((delivered, detail))

        def cancel(self, reason=""):
            self.finished = True
            cancelled.append(reason)

    monkeypatch.setattr(module, "claim_action", lambda *args, **kwargs: _Lease())

    def _send_text(**kwargs):
        sends.append(kwargs)
        return SimpleNamespace(
            delivered=False,
            queued=True,
            deferred_delivery=True,
            action_id="action-1",
            sent_text="",
            ok=True,
            error="",
        )

    monkeypatch.setattr(module, "send_text", _send_text)

    reminder._check_due()
    reminder._check_due()

    assert len(sends) == 1
    assert state["yoyo"][0]["status"] == "sent"
    assert state["yoyo"][0]["sent_text"] == "YoYo，起床啦"
    assert completed == [(False, "queued_for_deferred_app_delivery")]
    assert cancelled == []
