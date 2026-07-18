import copy
import importlib.util
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "plugins"
    / "xiaoyou_common"
    / "memory_governance.py"
)
SPEC = importlib.util.spec_from_file_location("memory_governance_under_test", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
MemoryGovernance = MODULE.MemoryGovernance


class FakeStore:
    def __init__(self, state=None):
        self.state = copy.deepcopy(state) if state is not None else {
            "schema_version": 1,
            "entries": [],
            "audit": [],
        }
        self.saved = 0

    def load(self, transform=None):
        value = copy.deepcopy(self.state)
        return transform(value) if callable(transform) else value

    def save(self, value):
        self.state = copy.deepcopy(value)
        self.saved += 1
        return True


def candidate(
    *,
    content="YoYo希望小悠的回复更自然。",
    evidence="回复更自然",
    memory_key="response.reply_style",
    category="response_preference",
    confidence=0.95,
    importance=0.9,
):
    return {
        "category": category,
        "memory_key": memory_key,
        "content": content,
        "evidence": evidence,
        "confidence": confidence,
        "importance": importance,
    }


def test_writes_only_validated_user_supported_candidate_and_never_stores_assistant():
    store = FakeStore()
    writes = []
    governance = MemoryGovernance(
        store=store,
        extractor=lambda **kwargs: [candidate()],
        writer=lambda **kwargs: writes.append(kwargs["candidate"]) or {
            "ok": True,
            "provider_memory_id": "provider-1",
        },
        now=lambda: 1000,
    )

    summary = governance.process_turn(
        user_text="我希望你以后回复更自然，也更像真实聊天。",
        assistant_text="你一定讨厌长回复，我会永远记住。",
        session_id="yoyo",
    )

    assert summary == {"extracted": 1, "eligible": 1, "written": 1, "failed": 0}
    assert len(writes) == 1
    assert writes[0]["source_role"] == "user"
    assert store.state["entries"][0]["status"] == "written"
    assert store.state["entries"][0]["provider_memory_id"] == "provider-1"
    assert "你一定讨厌长回复" not in repr(store.state)
    assert store.state["entries"][0]["evidence"][0]["source_role"] == "user"


def test_duplicate_confirmation_does_not_write_provider_twice():
    store = FakeStore()
    writes = []
    governance = MemoryGovernance(
        store=store,
        extractor=lambda **kwargs: [candidate()],
        writer=lambda **kwargs: writes.append(kwargs["candidate"]) or {"ok": True},
        now=lambda: 1000 + len(writes),
    )

    governance.process_turn(user_text="我希望你回复更自然。")
    second = governance.process_turn(user_text="我还是希望你回复更自然。")

    assert len(writes) == 1
    assert second["eligible"] == 1
    assert second["written"] == 0
    assert len(store.state["entries"]) == 1
    assert store.state["audit"][-1]["action"] == "candidate_confirmed"


def test_extractor_receives_active_keys_to_consolidate_instead_of_duplicating():
    store = FakeStore()
    seen_existing = []
    responses = [
        candidate(content="YoYo希望回复简短。", evidence="回复简短"),
        [],
    ]

    def extract(**kwargs):
        seen_existing.append(kwargs["existing_memories"])
        value = responses.pop(0)
        return value if isinstance(value, list) else [value]

    governance = MemoryGovernance(
        store=store,
        extractor=extract,
        writer=lambda **kwargs: {"ok": True, "provider_memory_id": "provider-1"},
        now=lambda: 1500,
    )

    governance.process_turn(user_text="我希望你以后回复简短。")
    governance.process_turn(user_text="今天只是随便聊聊。")

    assert seen_existing[0] == []
    assert seen_existing[1] == [
        {
            "memory_key": "response.reply_style",
            "category": "response_preference",
            "memory_type": "semantic",
            "content": "YoYo希望回复简短。",
            "status": "written",
        }
    ]


def test_new_value_for_same_semantic_key_supersedes_old_entry():
    store = FakeStore()
    writes = []
    responses = [
        candidate(content="YoYo希望回复简短。", evidence="回复简短"),
        candidate(content="YoYo现在希望回复可以详细一些。", evidence="回复可以详细一些"),
    ]
    def write_candidate(**kwargs):
        writes.append(kwargs["candidate"])
        return {
            "ok": True,
            "provider_memory_id": "provider-existing-node",
        }

    governance = MemoryGovernance(
        store=store,
        extractor=lambda **kwargs: [responses.pop(0)],
        writer=write_candidate,
        now=lambda: 2000 + len(writes),
    )

    governance.process_turn(user_text="我希望你以后回复简短。")
    governance.process_turn(user_text="纠正一下，我现在希望你回复可以详细一些。")

    assert len(writes) == 2
    assert len(store.state["entries"]) == 2
    old, new = store.state["entries"]
    assert old["status"] == "superseded"
    assert old["superseded_by"] == new["id"]
    assert new["supersedes"] == old["id"]
    assert writes[1]["superseded_provider_memory_id"] == "provider-existing-node"
    assert new["status"] == "written"


def test_rejects_unsupported_evidence_low_scores_and_secrets():
    store = FakeStore()
    writes = []
    raw = [
        candidate(evidence="用户没有说过这句话"),
        candidate(
            memory_key="response.low_confidence",
            evidence="自然回复",
            confidence=0.4,
        ),
        candidate(
            memory_key="user.secret",
            category="user_profile",
            content="用户密码：hunter2",
            evidence="我喜欢自然回复",
        ),
    ]
    governance = MemoryGovernance(
        store=store,
        extractor=lambda **kwargs: raw,
        writer=lambda **kwargs: writes.append(kwargs["candidate"]) or {"ok": True},
        now=lambda: 3000,
    )

    summary = governance.process_turn(user_text="我喜欢自然回复。")

    assert summary == {"extracted": 3, "eligible": 0, "written": 0, "failed": 0}
    assert writes == []
    assert store.state["entries"] == []
    reasons = [event["reason"] for event in store.state["audit"]]
    assert "evidence_not_in_user_message" in reasons
    assert "confidence_below_threshold" in reasons
    assert "sensitive_content" in reasons


def test_transient_reminder_is_owned_by_reminder_service_and_never_extracted():
    store = FakeStore()
    calls = []
    governance = MemoryGovernance(
        store=store,
        extractor=lambda **kwargs: calls.append(kwargs) or [],
        writer=lambda **kwargs: {"ok": True},
        now=lambda: 3100,
    )

    summary = governance.process_turn(
        user_text="晚安老婆，明天9:20记得叫醒我。",
    )

    assert summary == {"extracted": 0, "eligible": 0, "written": 0, "failed": 0}
    assert calls == []
    assert store.state["audit"][-1]["reason"] == "transient_reminder_owned_by_reminder_service"


def test_relationship_title_alone_cannot_establish_a_durable_relationship_fact():
    store = FakeStore()
    writes = []
    weak = candidate(
        category="relationship",
        memory_key="relationship.partner_role",
        content="YoYo称呼小悠为老婆，确立了伴侣关系。",
        evidence="晚安老婆",
    )
    governance = MemoryGovernance(
        store=store,
        extractor=lambda **kwargs: [weak],
        writer=lambda **kwargs: writes.append(kwargs) or {"ok": True},
        now=lambda: 3200,
    )

    summary = governance.process_turn(user_text="嗯嗯晚安老婆，明天见。")

    assert summary["eligible"] == 0
    assert writes == []
    assert store.state["audit"][-1]["reason"] == "weak_relationship_evidence"


def test_explicit_relationship_commitment_can_be_governed():
    store = FakeStore()
    writes = []
    strong = candidate(
        category="relationship",
        memory_key="relationship.partner_role",
        content="YoYo明确确认小悠是他的女朋友。",
        evidence="你是我的女朋友",
    )
    governance = MemoryGovernance(
        store=store,
        extractor=lambda **kwargs: [strong],
        writer=lambda **kwargs: writes.append(kwargs["candidate"]) or {"ok": True},
        now=lambda: 3300,
    )

    summary = governance.process_turn(user_text="我认真确认，你是我的女朋友。")

    assert summary["written"] == 1
    assert writes[0]["memory_type"] == "relationship"


def test_failed_provider_write_is_retried_after_same_fact_is_confirmed():
    store = FakeStore()
    outcomes = [
        {"ok": False, "error": "provider_timeout"},
        {"ok": True, "provider_memory_id": "provider-2"},
    ]
    governance = MemoryGovernance(
        store=store,
        extractor=lambda **kwargs: [candidate()],
        writer=lambda **kwargs: outcomes.pop(0),
        now=lambda: 4000,
    )

    first = governance.process_turn(user_text="我希望你回复更自然。")
    second = governance.process_turn(user_text="我依然希望你回复更自然。")

    assert first["failed"] == 1
    assert second["written"] == 1
    assert store.state["entries"][0]["write_attempts"] == 2
    assert store.state["entries"][0]["status"] == "written"
    assert store.state["entries"][0]["provider_memory_id"] == "provider-2"


def test_extractor_failure_fails_closed_without_legacy_write():
    store = FakeStore()
    writes = []

    def unavailable(**kwargs):
        raise RuntimeError("model unavailable")

    governance = MemoryGovernance(
        store=store,
        extractor=unavailable,
        writer=lambda **kwargs: writes.append(kwargs["candidate"]) or {"ok": True},
        now=lambda: 5000,
    )

    summary = governance.process_turn(
        user_text="我喜欢自然回复。",
        assistant_text="助手随口猜测的内容。",
    )

    assert summary["written"] == 0
    assert summary["failed"] == 1
    assert writes == []
    assert store.state["audit"][-1]["action"] == "extraction_failed"
