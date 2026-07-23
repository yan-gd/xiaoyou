import copy
import importlib.util
import threading
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
    subject="user",
    user_evidence="",
    assistant_evidence="",
    confidence=0.95,
    importance=0.9,
    **temporal,
):
    return {
        "subject": subject,
        "category": category,
        "memory_key": memory_key,
        "content": content,
        "evidence": evidence,
        "user_evidence": user_evidence,
        "assistant_evidence": assistant_evidence,
        "confidence": confidence,
        "importance": importance,
        **temporal,
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
    assert store.state["entries"][0]["source_turn_sequence"] == 1
    assert store.state["entries"][0]["source_session_id"] == "yoyo"
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
            "subject": "user",
            "occurred_at": "",
            "valid_from": "",
            "valid_until": "",
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


def test_reminder_semantics_are_delegated_to_model_without_keyword_gate():
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
    assert len(calls) == 1
    assert calls[0]["source_mode"] == "user"
    assert not hasattr(governance, "_is_transient_reminder")


def test_relationship_semantics_are_not_overridden_by_a_hardcoded_phrase_veto():
    store = FakeStore()
    writes = []
    weak = candidate(
        category="relationship",
        memory_key="user.relationship.partner_role",
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

    assert summary["eligible"] == 1
    assert summary["written"] == 1
    assert len(writes) == 1
    assert not hasattr(governance, "_strong_relationship_evidence")


def test_explicit_relationship_commitment_can_be_governed():
    store = FakeStore()
    writes = []
    strong = candidate(
        category="relationship",
        memory_key="user.relationship.partner_role",
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


def test_actual_delivered_assistant_commitment_becomes_xiaoyou_memory():
    store = FakeStore()
    writes = []
    delivered = candidate(
        subject="xiaoyou",
        category="relationship",
        memory_key="xiaoyou.commitment.emotional_presence",
        content="小悠答应在 YoYo 难受时先陪着他，不急着说教。",
        evidence="",
        user_evidence="我今天真的很难受",
        assistant_evidence="我先陪着你，不急着说教",
    )
    governance = MemoryGovernance(
        store=store,
        extractor=lambda **kwargs: [delivered],
        writer=lambda **kwargs: writes.append(kwargs["candidate"]) or {"ok": True},
        now=lambda: 3400,
    )

    summary = governance.process_turn(
        user_text="我今天真的很难受",
        assistant_text="我先陪着你，不急着说教",
        source_mode="assistant_delivered",
        input_id="delivery-action-1",
        delivery_complete=True,
        terminal_status="complete",
    )

    assert summary["written"] == 1
    assert writes[0]["subject"] == "xiaoyou"
    assert writes[0]["source_role"] == "joint"
    assert [item["source_role"] for item in writes[0]["evidence"]] == [
        "user",
        "assistant_delivered",
    ]


def test_assistant_delivery_cannot_invent_a_user_fact():
    store = FakeStore()
    writes = []
    invented = candidate(
        subject="user",
        category="durable_preference",
        memory_key="user.preference.food",
        content="YoYo 最喜欢草莓蛋糕。",
        evidence="",
        assistant_evidence="你一定最喜欢草莓蛋糕",
    )
    governance = MemoryGovernance(
        store=store,
        extractor=lambda **kwargs: [invented],
        writer=lambda **kwargs: writes.append(kwargs["candidate"]) or {"ok": True},
        now=lambda: 3500,
    )

    summary = governance.process_turn(
        user_text="",
        assistant_text="你一定最喜欢草莓蛋糕",
        source_mode="assistant_delivered",
        input_id="delivery-action-2",
    )

    assert summary["eligible"] == 0
    assert writes == []
    assert store.state["audit"][-1]["reason"] == "delivered_assistant_subject_invalid"


def test_assistant_alone_cannot_turn_its_own_claim_into_a_shared_fact():
    store = FakeStore()
    writes = []
    one_sided = candidate(
        subject="relationship",
        category="relationship",
        memory_key="relationship.status.partner",
        content="双方已经确认彼此是伴侣。",
        evidence="",
        assistant_evidence="我们已经是伴侣啦",
    )
    governance = MemoryGovernance(
        store=store,
        extractor=lambda **kwargs: [one_sided],
        writer=lambda **kwargs: writes.append(kwargs["candidate"]) or {"ok": True},
        now=lambda: 3550,
    )

    summary = governance.process_turn(
        user_text="",
        assistant_text="我们已经是伴侣啦",
        source_mode="assistant_delivered",
        input_id="delivery-action-3",
    )

    assert summary["eligible"] == 0
    assert writes == []
    assert store.state["audit"][-1]["reason"] == "relationship_requires_joint_evidence"


def test_event_time_is_structured_separately_from_recorded_time():
    store = FakeStore()
    writes = []
    timed = candidate(
        subject="user",
        category="episodic_event",
        memory_key="user.event.first_meeting",
        content="YoYo 记得双方在 2026 年 7 月 22 日第一次认真谈长期陪伴。",
        evidence="昨天晚上我们第一次认真谈长期陪伴",
        occurred_at="2026-07-22",
        temporal_precision="day",
        timezone="Asia/Shanghai",
        time_evidence="昨天晚上",
    )
    governance = MemoryGovernance(
        store=store,
        extractor=lambda **kwargs: [timed],
        writer=lambda **kwargs: writes.append(kwargs["candidate"]) or {"ok": True},
        now=lambda: 1784736000,
    )

    summary = governance.process_turn(
        user_text="昨天晚上我们第一次认真谈长期陪伴",
    )

    assert summary["written"] == 1
    assert writes[0]["occurred_at"] == "2026-07-22"
    assert writes[0]["temporal_precision"] == "day"
    assert writes[0]["time_evidence"] == "昨天晚上"
    assert writes[0]["created_at"] == 1784736000


def test_structured_event_time_requires_verbatim_time_evidence():
    store = FakeStore()
    writes = []
    unsupported_time = candidate(
        subject="user",
        category="episodic_event",
        memory_key="user.event.unsupported_time",
        content="YoYo 在 2026 年 7 月 22 日做出了决定。",
        evidence="我做出了决定",
        occurred_at="2026-07-22",
        temporal_precision="day",
        time_evidence="",
    )
    governance = MemoryGovernance(
        store=store,
        extractor=lambda **kwargs: [unsupported_time],
        writer=lambda **kwargs: writes.append(kwargs["candidate"]) or {"ok": True},
        now=lambda: 1784736000,
    )

    summary = governance.process_turn(user_text="我做出了决定")

    assert summary["eligible"] == 0
    assert writes == []
    assert store.state["audit"][-1]["reason"] == "missing_time_evidence"


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


def test_persisted_sequence_and_input_id_make_replayed_turn_idempotent():
    store = FakeStore()
    calls = []
    governance = MemoryGovernance(
        store=store,
        extractor=lambda **kwargs: calls.append(kwargs["input_id"]) or [],
        writer=lambda **kwargs: {"ok": True},
        now=lambda: 6000,
    )

    first = governance.process_turn(
        user_text="first durable statement",
        input_id="input-1",
        session_id="yoyo",
    )
    duplicate = governance.process_turn(
        user_text="first durable statement",
        input_id="input-1",
        session_id="yoyo",
    )
    restarted = MemoryGovernance(
        store=store,
        extractor=lambda **kwargs: calls.append(kwargs["input_id"]) or [],
        writer=lambda **kwargs: {"ok": True},
        now=lambda: 6001,
    )
    restarted.process_turn(
        user_text="second durable statement",
        input_id="input-2",
        session_id="yoyo",
    )

    assert first == {"extracted": 0, "eligible": 0, "written": 0, "failed": 0}
    assert duplicate == {"extracted": 0, "eligible": 0, "written": 0, "failed": 0}
    assert calls == ["input-1", "input-2"]
    assert store.state["turn_sequences"]["yoyo"] == 2
    assert store.state["processed_input_ids"]["yoyo"] == ["input-1", "input-2"]
    assert any(
        event.get("reason") == "duplicate_input_id"
        for event in store.state["audit"]
    )


def test_delayed_older_turn_cannot_overwrite_a_newer_correction():
    store = FakeStore()
    old_started = threading.Event()
    release_old = threading.Event()
    writes = []

    def extract(**kwargs):
        if kwargs["input_id"] == "old":
            old_started.set()
            assert release_old.wait(1)
            return [
                candidate(
                    content="YoYo prefers red.",
                    evidence="red",
                    memory_key="preference.color",
                    category="durable_preference",
                )
            ]
        return [
            candidate(
                content="YoYo now prefers blue.",
                evidence="blue",
                memory_key="preference.color",
                category="durable_preference",
            )
        ]

    governance = MemoryGovernance(
        store=store,
        extractor=extract,
        writer=lambda **kwargs: writes.append(kwargs["candidate"]) or {"ok": True},
        now=lambda: 7000,
    )

    old_thread = threading.Thread(
        target=lambda: governance.process_turn(
            user_text="red",
            input_id="old",
            session_id="yoyo",
            turn_sequence=1,
        )
    )
    old_thread.start()
    assert old_started.wait(1)

    newer = governance.process_turn(
        user_text="blue",
        input_id="new",
        session_id="yoyo",
        turn_sequence=2,
    )
    release_old.set()
    old_thread.join(1)

    assert not old_thread.is_alive()
    assert newer["written"] == 1
    active = [
        entry
        for entry in store.state["entries"]
        if entry["status"] in {"approved", "failed", "written"}
    ]
    assert [entry["content"] for entry in active] == ["YoYo now prefers blue."]
    assert active[0]["source_turn_sequence"] == 2
    assert [entry["content"] for entry in writes] == ["YoYo now prefers blue."]
    assert any(
        event.get("reason") == "stale_turn_sequence"
        for event in store.state["audit"]
    )
