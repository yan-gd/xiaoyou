# -*- coding:utf-8 -*-
import ast
import os
import threading
import types
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHAT_CHANNEL_PATH = os.path.join(ROOT, "patches", "chat_channel.py")
VISION_PATH = os.path.join(ROOT, "plugins", "qwen_vision", "qwen_vision.py")


def load_class_methods(path, class_name, method_names, namespace=None):
    with open(path, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)
    selected = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            selected.extend(
                item
                for item in node.body
                if isinstance(item, ast.FunctionDef) and item.name in method_names
            )
    namespace = dict(namespace or {})
    exec(compile(ast.Module(body=selected, type_ignores=[]), path, "exec"), namespace)
    return {name: namespace[name] for name in method_names}


class Context(dict):
    def __init__(self, content="", context_type=None, **values):
        super().__init__(values)
        self.kwargs = dict(values)
        self.content = content
        self.type = context_type


class InputVersionAndVisionStaleTest(unittest.TestCase):
    def test_version_check_uses_immutable_pre_identity_session_key(self):
        methods = load_class_methods(
            CHAT_CHANNEL_PATH,
            "ChatChannel",
            {"_set_context_input_metadata", "is_context_current"},
        )
        channel = types.SimpleNamespace(
            lock=threading.Lock(),
            input_versions={"@wechat-session": 2, "yoyo": 99},
        )
        channel._set_context_input_metadata = types.MethodType(
            methods["_set_context_input_metadata"], channel
        )
        channel.is_context_current = types.MethodType(
            methods["is_context_current"], channel
        )

        context = Context(session_id="@wechat-session")
        channel._set_context_input_metadata(
            context,
            version=2,
            session_key="@wechat-session",
        )
        context["session_id"] = "yoyo"
        context.kwargs["session_id"] = "yoyo"

        self.assertTrue(channel.is_context_current(context))
        context.kwargs["xiaoyou_input_version"] = 1
        self.assertFalse(channel.is_context_current(context))

    def test_text_input_is_committed_immediately_with_stale_protection(self):
        class _ContextType:
            TEXT = "text"
            IMAGE = "image"
            VOICE = "voice"

        trace_calls = []
        methods = load_class_methods(
            CHAT_CHANNEL_PATH,
            "ChatChannel",
            {
                "_set_context_input_metadata",
                "_prepare_immediate_input_context",
                "_should_merge_input",
                "is_context_current",
            },
            namespace={
                "ContextType": _ContextType,
                "os": os,
                "time": __import__("time"),
                "trace_event": lambda *args, **kwargs: trace_calls.append(
                    (args, kwargs)
                ),
                "activate_context_trace": lambda context: context,
            },
        )
        channel = types.SimpleNamespace(
            lock=threading.Lock(),
            input_versions={"yoyo": 1},
        )
        for name, method in methods.items():
            setattr(channel, name, types.MethodType(method, channel))

        context = Context(
            "第一条消息",
            _ContextType.TEXT,
            session_id="yoyo",
            xiaoyou_trace_id="trace-1",
            xiaoyou_input_id="message-1",
            xiaoyou_input_version=1,
            xiaoyou_input_session_key="yoyo",
            xiaoyou_input_immediate=True,
        )

        self.assertFalse(channel._should_merge_input(context))
        channel._prepare_immediate_input_context(context)
        self.assertEqual(context.kwargs["xiaoyou_input_messages"], ["第一条消息"])
        self.assertEqual(context.kwargs["xiaoyou_input_batch_size"], 1)
        self.assertEqual(
            context.kwargs["xiaoyou_source_message_ids"],
            ["message-1"],
        )
        self.assertEqual(trace_calls[-1][0], ("input_merged",))
        self.assertEqual(trace_calls[-1][1]["status"], "immediate")
        self.assertEqual(trace_calls[-1][1]["attrs"]["settle_ms"], 0)

        channel.input_versions["yoyo"] = 2
        self.assertFalse(channel.is_context_current(context))

    def test_unchanged_stale_vision_snapshot_is_not_requeued(self):
        with open(VISION_PATH, "r", encoding="utf-8") as handle:
            source = handle.read()
        method = source[
            source.index("    def _send_pending_response"):
            source.index("    def _vision_snapshot_current")
        ]
        self.assertNotIn("keep_pending", method)
        self.assertIn('current_revision != revision or current.get("dirty")', method)
        self.assertIn("retry=False", method)


if __name__ == "__main__":
    unittest.main()
