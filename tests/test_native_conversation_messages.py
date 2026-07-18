import importlib.util
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "plugins"
    / "xiaoyou_common"
    / "conversation_messages.py"
)
SPEC = importlib.util.spec_from_file_location("conversation_messages_under_test", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_native_history_preserves_roles_and_keeps_current_user_last():
    history = [
        {"id": "1", "role": "user", "content": "我刚吃完饭", "ts": 1},
        {"id": "2", "role": "assistant", "content": "那就歇一会儿呀", "ts": 2},
        {"id": "3", "role": "user", "content": "还是有点困", "ts": 3},
    ]

    messages = MODULE.build_chat_messages("人格规则", "[YoYo 本轮可见输入]\n陪我睡会儿", history)

    assert [item["role"] for item in messages] == [
        "system", "user", "assistant", "user", "user"
    ]
    assert messages[1]["content"] == "我刚吃完饭"
    assert messages[-1]["content"].endswith("陪我睡会儿")
    assert all(set(item) == {"role", "content"} for item in messages)


def test_native_history_is_bounded_and_deduplicates_record_ids():
    history = [
        {"id": "same", "role": "user", "content": "旧消息"},
        {"id": "same", "role": "user", "content": "重复记录"},
        {"id": "2", "role": "assistant", "content": "中间消息"},
        {"id": "3", "role": "user", "content": "最新消息"},
    ]

    result = MODULE.prepare_native_history(history, max_messages=2, max_tokens=100)

    assert result == [
        {"role": "assistant", "content": "中间消息"},
        {"role": "user", "content": "最新消息"},
    ]


def test_provider_blocked_record_never_reaches_native_history():
    result = MODULE.prepare_native_history([
        {
            "id": "blocked",
            "role": "user",
            "content": "不能再次注入",
            "provider_injection_blocked": True,
        },
        {"id": "ok", "role": "assistant", "content": "正常历史"},
    ])

    assert result == [{"role": "assistant", "content": "正常历史"}]


def test_current_archived_user_input_is_not_sent_twice():
    result = MODULE.prepare_native_history(
        [
            {"id": "1", "role": "user", "content": "鲍鱼还没洗呢"},
            {"id": "2", "role": "assistant", "content": "我去洗"},
            {"id": "3", "role": "user", "content": "快洗啦"},
        ],
        current_inputs=["快洗啦"],
    )

    assert result == [
        {"role": "user", "content": "鲍鱼还没洗呢"},
        {"role": "assistant", "content": "我去洗"},
    ]
