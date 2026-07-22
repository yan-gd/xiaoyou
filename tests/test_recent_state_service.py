# -*- coding:utf-8 -*-
import ast
import json
import os
import re
import threading
import time
import types
from datetime import datetime


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "plugins", "xiaoyou_common", "recent_state_service.py")


class MemoryStore:
    def __init__(self, *args, **kwargs):
        self.data = {"schema_version": 1, "sessions": {}}

    def load(self):
        return json.loads(json.dumps(self.data))

    def save(self, data):
        self.data = json.loads(json.dumps(data))
        return True


class Logger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def exception(self, *args, **kwargs):
        return None


def load_service():
    with open(SOURCE, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=SOURCE)
    selected = [
        node for node in tree.body
        if isinstance(node, (ast.Assign, ast.ClassDef, ast.FunctionDef))
        and not (isinstance(node, ast.FunctionDef) and node.name == "get_recent_state_service")
    ]
    namespace = {
        "__name__": "recent_state_test",
        "__file__": SOURCE,
        "json": json,
        "os": os,
        "re": re,
        "threading": threading,
        "time": time,
        "datetime": datetime,
        "logger": Logger(),
        "JsonStateStore": MemoryStore,
        "runtime_path": lambda namespace, filename, **kwargs: os.path.join(
            ROOT, "data", namespace, filename
        ),
        "build_context_snapshot": lambda **kwargs: types.SimpleNamespace(
            time_context="当前时间",
            short_memory="YoYo：我刚吃完饭，但还是没睡够\n小悠：那就陪你歇一会儿",
        ),
        "build_thinking_payload": lambda prefix: {},
        "chat_completion": None,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), SOURCE, "exec"), namespace)
    return namespace


def test_recent_state_accepts_only_grounded_temporary_state():
    module = load_service()
    service = module["RecentStateService"]("unused.json")
    module["chat_completion"] = lambda **kwargs: types.SimpleNamespace(
        ok=True,
        content=json.dumps({
            "topic": {
                "key": "topic",
                "text": "吃完饭后想补觉",
                "evidence": "睡个回笼觉",
                "source": "user",
                "ttl_seconds": 3600,
            },
            "user_states": [{
                "key": "energy",
                "text": "还没有睡够",
                "evidence": "没睡够",
                "source": "user",
                "ttl_seconds": 1800,
            }],
            "xiaoyou_stance": {
                "key": "stance",
                "text": "愿意陪YoYo休息",
                "evidence": "陪你歇一会儿",
                "source": "assistant",
                "ttl_seconds": 1800,
            },
            "open_loops": [],
            "referents": [],
            "temporary_facts": [{
                "key": "meal_status",
                "text": "YoYo刚吃完饭",
                "evidence": "吃完了",
                "source": "user",
                "ttl_seconds": 3600,
            }],
        }, ensure_ascii=False),
        error_kind="",
    )

    state = service.update_from_exchange(
        "yoyo",
        user_text="吃完了，要不睡个回笼觉，我还是没睡够",
        assistant_text="好呀，那就陪你歇一会儿",
    )

    assert state["topic"]["text"] == "吃完饭后想补觉"
    assert state["user_states"][0]["key"] == "energy"
    assert state["temporary_facts"][0]["text"] == "YoYo刚吃完饭"
    rendered = service.build_context("yoyo")
    assert "当前话题：吃完饭后想补觉" in rendered
    assert "YoYo当前临时状态：还没有睡够" in rendered


def test_recent_state_rejects_invented_evidence_and_secrets():
    module = load_service()
    service = module["RecentStateService"]("unused.json")
    module["chat_completion"] = lambda **kwargs: types.SimpleNamespace(
        ok=True,
        content=json.dumps({
            "topic": {
                "key": "topic",
                "text": "YoYo已经到公司",
                "evidence": "已经到公司",
                "source": "user",
                "ttl_seconds": 3600,
            },
            "user_states": [],
            "xiaoyou_stance": None,
            "open_loops": [],
            "referents": [],
            "temporary_facts": [{
                "key": "secret",
                "text": "密码是123456",
                "evidence": "今晚早点睡",
                "source": "user",
                "ttl_seconds": 3600,
            }],
        }, ensure_ascii=False),
        error_kind="",
    )

    state = service.update_from_exchange(
        "yoyo",
        user_text="今晚早点睡",
        assistant_text="好，我陪你早点休息",
    )

    assert not state["topic"]
    assert not state["temporary_facts"]
    assert service.build_context("yoyo") == ""


def test_recent_state_prunes_expired_items():
    module = load_service()
    service = module["RecentStateService"]("unused.json")
    service.store.data["sessions"]["yoyo"] = {
        "topic": {"key": "topic", "text": "旧话题", "expires_at": 1},
        "user_states": [],
        "xiaoyou_stance": {},
        "open_loops": [],
        "referents": [],
        "temporary_facts": [],
        "updated_at": 1,
    }

    assert service.get("yoyo")["topic"] == {}
    assert service.build_context("yoyo") == ""


def test_recent_assistant_guess_cannot_become_user_temporary_fact():
    module = load_service()
    service = module["RecentStateService"]("unused.json")
    update = service._normalize_update(
        {
            "topic": None,
            "user_states": [],
            "xiaoyou_stance": None,
            "open_loops": [],
            "referents": [],
            "temporary_facts": [{
                "key": "pet_preference",
                "text": "YoYo最怕猫",
                "evidence": "最怕猫",
                "source": "recent",
                "ttl_seconds": 3600,
            }],
        },
        user_text="这只猫挺可爱的",
        assistant_text="是呀",
        recent_corpus="[今天 12:00] 小悠：我猜你肯定最怕猫。\n[今天 12:01] YoYo：我只是怕它扑镜头。",
        now=int(time.time()),
    )

    assert update["temporary_facts"] == []


def test_clear_removes_recent_state_session():
    module = load_service()
    service = module["RecentStateService"]("unused.json")
    service.store.data["sessions"]["yoyo"] = {
        "topic": {"key": "topic", "text": "当前话题", "expires_at": int(time.time()) + 600},
        "user_states": [],
        "xiaoyou_stance": {},
        "open_loops": [],
        "referents": [],
        "temporary_facts": [],
        "updated_at": int(time.time()),
    }

    assert service.clear("yoyo") is True
    assert service.build_context("yoyo") == ""


def test_content_inspection_suspends_stale_derived_state_until_next_success():
    module = load_service()
    service = module["RecentStateService"]("unused.json")
    now = int(time.time())
    service.store.data["sessions"]["yoyo"] = {
        "topic": {"key": "topic", "text": "旧场景", "expires_at": now + 3600},
        "user_states": [],
        "xiaoyou_stance": {},
        "open_loops": [],
        "referents": [],
        "temporary_facts": [],
        "updated_at": now - 60,
    }
    module["chat_completion"] = lambda **kwargs: types.SimpleNamespace(
        ok=False,
        content="",
        error_kind="content_inspection",
    )

    service.update_from_exchange(
        "yoyo",
        user_text="鲍鱼还没洗呢",
        assistant_text="我去洗",
        last_user_ts=now,
    )

    assert service.build_context("yoyo") == ""
    stored = service.store.data["sessions"]["yoyo"]
    assert stored["suspended_reason"] == "provider_content_inspection"


def test_referent_target_must_exist_in_recent_evidence_corpus():
    module = load_service()
    service = module["RecentStateService"]("unused.json")
    update = service._normalize_update(
        {
            "topic": None,
            "user_states": [],
            "xiaoyou_stance": None,
            "open_loops": [],
            "temporary_facts": [],
            "referents": [
                {
                    "key": "它",
                    "mention": "它",
                    "target": "蓝色马克杯",
                    "evidence": "它到了吗",
                    "source": "user",
                    "ttl_seconds": 3600,
                },
                {
                    "key": "那个",
                    "mention": "那个",
                    "target": "红色雨伞",
                    "evidence": "它到了吗",
                    "source": "user",
                    "ttl_seconds": 3600,
                },
            ],
        },
        user_text="它到了吗",
        assistant_text="还不知道呀",
        recent_corpus="[今天 14:10] YoYo：我买了蓝色马克杯",
        now=int(time.time()),
    )

    assert [item["target"] for item in update["referents"]] == ["蓝色马克杯"]
