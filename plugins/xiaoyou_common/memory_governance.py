# -*- coding: utf-8 -*-
"""Auditable write governance for Xiaoyou long-term memory.

Long-term memory is not a transcript archive. This module separately governs
facts asserted by YoYo, facts Xiaoyou actually delivered, and memories jointly
formed by the relationship. Evidence remains role-bound so Xiaoyou's guesses
can never become user facts.
"""

import hashlib
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime

try:
    from plugins.xiaoyou_common.memory_schema import normalize_memory_type
except ModuleNotFoundError:  # Standalone unit-test loading via importlib.
    _CATEGORY_TO_TYPE = {
        "user_profile": "semantic",
        "durable_preference": "semantic",
        "response_preference": "semantic",
        "relationship": "relationship",
        "project_direction": "project",
        "correction": "correction",
        "episodic_event": "episodic",
        "pending_thread": "pending",
    }

    def normalize_memory_type(value="", category=""):
        return str(value or "").strip().lower() or _CATEGORY_TO_TYPE.get(
            str(category or "").strip().lower(),
            "legacy",
        )


ALLOWED_CATEGORIES = {
    "user_profile",
    "durable_preference",
    "response_preference",
    "relationship",
    "project_direction",
    "correction",
    "episodic_event",
    "pending_thread",
}
ALLOWED_SUBJECTS = {"user", "xiaoyou", "relationship"}
ALLOWED_SOURCE_MODES = {"user", "assistant_delivered"}
ALLOWED_TEMPORAL_PRECISIONS = {
    "",
    "exact",
    "day",
    "period",
    "approximate",
    "unknown",
}

_ACTIVE_STATUSES = {"approved", "failed", "written"}
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b", re.I),
    re.compile(
        r"(?:\b(?:api[_ -]?key|access[_ -]?token|password|passwd)\b|密码|密钥|令牌)\s*[:=：]",
        re.I,
    ),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", re.I),
)


def _default_state():
    return {
        "schema_version": 3,
        "entries": [],
        "audit": [],
        "turn_sequences": {},
        "processed_input_ids": {},
    }


class MemoryGovernance:
    """Extract, validate, consolidate and persist long-memory candidates."""

    def __init__(
        self,
        *,
        writer,
        store=None,
        extractor=None,
        now=None,
        min_confidence=None,
        min_importance=None,
        max_candidates=None,
        audit_limit=None,
    ):
        if not callable(writer):
            raise ValueError("writer must be callable")
        self.writer = writer
        self.store = store or self._build_default_store()
        self.extractor = extractor or self._extract_with_model
        self.now = now or time.time
        self.min_confidence = self._float_setting(
            min_confidence,
            "MEMORY_GOVERNANCE_MIN_CONFIDENCE",
            0.82,
        )
        self.min_importance = self._float_setting(
            min_importance,
            "MEMORY_GOVERNANCE_MIN_IMPORTANCE",
            0.68,
        )
        self.safety_max_candidates = self._int_setting(
            max_candidates,
            "MEMORY_GOVERNANCE_SAFETY_MAX_CANDIDATES",
            12,
            minimum=1,
            maximum=50,
        )
        self.audit_limit = self._int_setting(
            audit_limit,
            "MEMORY_GOVERNANCE_AUDIT_LIMIT",
            500,
            minimum=50,
            maximum=5000,
        )
        self.existing_top_n = self._int_setting(
            None,
            "MEMORY_GOVERNANCE_EXISTING_TOP_N",
            30,
            minimum=0,
            maximum=100,
        )
        self._lock = threading.RLock()

    def process_turn(
        self,
        *,
        user_text,
        assistant_text="",
        source_mode="user",
        delivery_complete=True,
        terminal_status="complete",
        completed_at=0,
        trace_id="",
        input_id="",
        session_id="",
        turn_sequence=0,
    ):
        """Govern committed user facts or an actually delivered assistant turn."""
        source_mode = str(source_mode or "user").strip().lower()
        if source_mode not in ALLOWED_SOURCE_MODES:
            source_mode = "user"
        user_text = str(user_text or "").strip()
        assistant_text = str(assistant_text or "").strip()
        primary_text = (
            assistant_text if source_mode == "assistant_delivered" else user_text
        )
        if not primary_text or self._contains_secret(primary_text):
            self._record_audit(
                action="turn_skipped",
                reason="empty_or_sensitive_source_text",
                source_mode=source_mode,
                trace_id=trace_id,
                input_id=input_id,
                session_id=session_id,
            )
            return {"extracted": 0, "eligible": 0, "written": 0, "failed": 0}

        resolved_sequence = self._reserve_turn_sequence(
            session_id=session_id,
            input_id=input_id,
            requested_sequence=turn_sequence,
            trace_id=trace_id,
        )
        if resolved_sequence is None:
            return {"extracted": 0, "eligible": 0, "written": 0, "failed": 1}
        if resolved_sequence <= 0:
            return {"extracted": 0, "eligible": 0, "written": 0, "failed": 0}
        turn_links = {
            "trace_id": trace_id,
            "input_id": input_id,
            "session_id": session_id,
            "turn_sequence": resolved_sequence,
            "source_mode": source_mode,
            "delivery_complete": bool(delivery_complete),
            "terminal_status": str(terminal_status or "")[:40],
            "completed_at": self._positive_int(completed_at),
        }

        existing_memories = self._existing_memory_snapshot()
        if existing_memories is None:
            # An unavailable audit ledger must block new durable writes. This
            # keeps storage state and the local decision history consistent.
            return {"extracted": 0, "eligible": 0, "written": 0, "failed": 1}

        try:
            raw_candidates = self.extractor(
                user_text=user_text,
                assistant_text=assistant_text,
                source_mode=source_mode,
                delivery_complete=bool(delivery_complete),
                terminal_status=str(terminal_status or ""),
                completed_at=self._positive_int(completed_at),
                existing_memories=existing_memories,
                session_id=str(session_id or ""),
                trace_id=str(trace_id or ""),
                input_id=str(input_id or ""),
            )
        except Exception as exc:
            self._record_audit(
                action="extraction_failed",
                reason=type(exc).__name__,
                **turn_links,
            )
            return {"extracted": 0, "eligible": 0, "written": 0, "failed": 1}

        if not isinstance(raw_candidates, list):
            raw_candidates = []
        if len(raw_candidates) > self.safety_max_candidates:
            self._record_audit(
                action="candidate_output_truncated",
                reason="technical_safety_limit",
                candidate_count=len(raw_candidates),
                **turn_links,
            )
        summary = {
            "extracted": min(len(raw_candidates), self.safety_max_candidates),
            "eligible": 0,
            "written": 0,
            "failed": 0,
        }

        for raw in raw_candidates[: self.safety_max_candidates]:
            candidate, reason = self._validate_candidate(
                raw,
                user_text,
                assistant_text=assistant_text,
                source_mode=source_mode,
            )
            if candidate is None:
                self._record_audit(
                    action="candidate_rejected",
                    reason=reason,
                    **turn_links,
                )
                continue

            summary["eligible"] += 1
            entry, should_write = self._upsert_candidate(
                candidate,
                **turn_links,
            )
            if not entry or not should_write:
                continue

            try:
                result = self.writer(
                    candidate=dict(entry),
                    trace_id=str(trace_id or ""),
                    input_id=str(input_id or ""),
                    session_id=str(session_id or ""),
                )
            except Exception as exc:
                result = {"ok": False, "error": type(exc).__name__}

            if isinstance(result, bool):
                result = {"ok": result}
            if not isinstance(result, dict):
                result = {"ok": False, "error": "invalid_writer_result"}
            if self._record_write_result(entry["id"], result):
                summary["written"] += 1
            else:
                summary["failed"] += 1

        return summary

    def _reserve_turn_sequence(
        self,
        *,
        session_id,
        input_id="",
        requested_sequence=0,
        trace_id="",
    ):
        """Persist one monotonic sequence before slow extraction begins."""
        with self._lock:
            state = self._load_state()
            if state is None:
                return None

            session_key = str(session_id or "_default")[:160]
            clean_input_id = str(input_id or "")[:160]
            current = self._positive_int(
                state["turn_sequences"].get(session_key),
            )
            requested = self._positive_int(requested_sequence)
            processed = state["processed_input_ids"].setdefault(session_key, [])

            if clean_input_id and clean_input_id in processed:
                self._append_audit(
                    state,
                    "turn_skipped",
                    None,
                    "duplicate_input_id",
                    {
                        "trace_id": trace_id,
                        "input_id": clean_input_id,
                        "session_id": session_key,
                        "turn_sequence": current,
                    },
                )
                return 0 if self.store.save(state) else None

            if requested and requested <= current:
                self._append_audit(
                    state,
                    "turn_skipped",
                    None,
                    "stale_turn_sequence",
                    {
                        "trace_id": trace_id,
                        "input_id": clean_input_id,
                        "session_id": session_key,
                        "turn_sequence": requested,
                    },
                )
                return 0 if self.store.save(state) else None

            sequence = requested or (current + 1)
            state["turn_sequences"][session_key] = sequence
            if clean_input_id:
                processed.append(clean_input_id)
                state["processed_input_ids"][session_key] = processed[-256:]
            self._append_audit(
                state,
                "turn_sequence_reserved",
                None,
                "",
                {
                    "trace_id": trace_id,
                    "input_id": clean_input_id,
                    "session_id": session_key,
                    "turn_sequence": sequence,
                },
            )
            return sequence if self.store.save(state) else None

    def _validate_candidate(
        self,
        raw,
        user_text,
        *,
        assistant_text="",
        source_mode="user",
    ):
        if not isinstance(raw, dict):
            return None, "candidate_not_object"

        category = str(raw.get("category") or "").strip().lower()
        if category not in ALLOWED_CATEGORIES:
            return None, "unsupported_category"

        subject = str(raw.get("subject") or "").strip().lower()
        source_mode = str(source_mode or "user").strip().lower()
        if source_mode == "user":
            subject = subject or "user"
            if subject != "user":
                return None, "user_turn_cannot_assert_non_user_subject"
        elif subject not in ("xiaoyou", "relationship"):
            return None, "delivered_assistant_subject_invalid"

        memory_key = str(raw.get("memory_key") or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9._:-]{2,119}", memory_key):
            return None, "invalid_memory_key"
        if source_mode == "assistant_delivered":
            if not memory_key.startswith(subject + "."):
                return None, "memory_key_subject_mismatch"
        elif memory_key.startswith(("xiaoyou.", "relationship.")):
            return None, "user_memory_key_crosses_subject_boundary"

        content = self._clean_text(raw.get("content"), 800)
        user_evidence = self._clean_text(
            raw.get("user_evidence")
            or (raw.get("evidence") if source_mode == "user" else ""),
            500,
        )
        assistant_evidence = self._clean_text(
            raw.get("assistant_evidence")
            or (
                raw.get("evidence")
                if source_mode == "assistant_delivered"
                else ""
            ),
            500,
        )
        if not content:
            return None, "missing_content_or_evidence"
        if source_mode == "user":
            if not user_evidence:
                return None, "missing_user_evidence"
            if not self._evidence_supported(user_evidence, user_text):
                return None, "evidence_not_in_user_message"
        else:
            if not assistant_evidence:
                return None, "missing_delivered_assistant_evidence"
            if not self._evidence_supported(assistant_evidence, assistant_text):
                return None, "evidence_not_in_delivered_assistant_message"
            if subject == "relationship" and not user_evidence:
                return None, "relationship_requires_joint_evidence"
            if user_evidence and not self._evidence_supported(
                user_evidence,
                user_text,
            ):
                return None, "joint_evidence_not_in_user_message"

        evidence_values = [
            value for value in (user_evidence, assistant_evidence) if value
        ]
        if self._contains_secret(content) or any(
            self._contains_secret(value) for value in evidence_values
        ):
            return None, "sensitive_content"

        confidence = self._unit_float(raw.get("confidence"))
        importance = self._unit_float(raw.get("importance"))
        if confidence < self.min_confidence:
            return None, "confidence_below_threshold"
        if importance < self.min_importance:
            return None, "importance_below_threshold"

        time_evidence = self._clean_text(raw.get("time_evidence"), 240)
        if time_evidence and not (
            self._evidence_supported(time_evidence, user_text)
            or self._evidence_supported(time_evidence, assistant_text)
        ):
            return None, "time_evidence_not_in_delivered_turn"

        temporal_precision = str(
            raw.get("temporal_precision") or ""
        ).strip().lower()
        if temporal_precision not in ALLOWED_TEMPORAL_PRECISIONS:
            temporal_precision = ""
        occurred_at = self._normalize_iso_time(raw.get("occurred_at"))
        valid_from = self._normalize_iso_time(raw.get("valid_from"))
        valid_until = self._normalize_iso_time(raw.get("valid_until"))
        if (
            occurred_at or valid_from or valid_until
        ) and not time_evidence:
            return None, "missing_time_evidence"
        timezone_name = self._clean_text(
            raw.get("timezone") or os.getenv("TZ") or "Asia/Shanghai",
            80,
        )
        evidence_items = []
        if user_evidence:
            evidence_items.append(
                {"text": user_evidence, "source_role": "user"}
            )
        if assistant_evidence:
            evidence_items.append(
                {
                    "text": assistant_evidence,
                    "source_role": "assistant_delivered",
                }
            )
        source_role = (
            "joint"
            if user_evidence and assistant_evidence
            else evidence_items[0]["source_role"]
        )
        return {
            "memory_key": memory_key,
            "category": category,
            "memory_type": normalize_memory_type("", category),
            "subject": subject,
            "source_role": source_role,
            "content": content,
            "confidence": confidence,
            "importance": importance,
            "evidence": evidence_items[0]["text"],
            "evidence_items": evidence_items,
            "occurred_at": occurred_at,
            "temporal_precision": temporal_precision,
            "valid_from": valid_from,
            "valid_until": valid_until,
            "timezone": timezone_name,
            "time_evidence": time_evidence,
        }, ""

    def _upsert_candidate(self, candidate, **links):
        with self._lock:
            state = self._load_state()
            if state is None:
                return None, False

            now = int(self.now())
            active = None
            for item in reversed(state["entries"]):
                if (
                    item.get("memory_key") == candidate["memory_key"]
                    and item.get("status") in _ACTIVE_STATUSES
                ):
                    active = item
                    break

            incoming_sequence = self._positive_int(links.get("turn_sequence"))
            active_sequence = self._positive_int(
                (active or {}).get("source_turn_sequence"),
            )
            incoming_session = str(links.get("session_id") or "_default")[:160]
            active_session = str(
                (active or {}).get("source_session_id") or "_default",
            )[:160]
            if (
                active
                and incoming_sequence
                and active_sequence
                and incoming_session == active_session
                and incoming_sequence < active_sequence
            ):
                self._append_audit(
                    state,
                    "candidate_rejected",
                    active,
                    "stale_turn_sequence",
                    links,
                )
                if not self.store.save(state):
                    return None, False
                return None, False

            evidence_items = [
                self._evidence_item(
                    item.get("text"),
                    now,
                    source_role=item.get("source_role"),
                )
                for item in candidate.get("evidence_items", [])
                if isinstance(item, dict) and str(item.get("text") or "").strip()
            ]
            if active and self._normalized(active.get("content")) == self._normalized(candidate["content"]):
                active["last_confirmed_at"] = now
                active["updated_at"] = now
                if incoming_sequence >= active_sequence:
                    active["source_turn_sequence"] = incoming_sequence
                    active["source_input_id"] = str(
                        links.get("input_id") or ""
                    )[:160]
                    active["source_session_id"] = incoming_session
                active["confidence"] = max(
                    self._unit_float(active.get("confidence")),
                    candidate["confidence"],
                )
                active["importance"] = max(
                    self._unit_float(active.get("importance")),
                    candidate["importance"],
                )
                metadata_changed = False
                for field in (
                    "subject",
                    "source_role",
                    "occurred_at",
                    "temporal_precision",
                    "valid_from",
                    "valid_until",
                    "timezone",
                    "time_evidence",
                ):
                    value = candidate.get(field)
                    if value and value != active.get(field):
                        active[field] = value
                        metadata_changed = True
                for evidence_item in evidence_items:
                    self._append_evidence(active, evidence_item)
                should_write = (
                    active.get("status") == "failed" or metadata_changed
                )
                action = (
                    "candidate_retry"
                    if active.get("status") == "failed"
                    else (
                        "candidate_metadata_enriched"
                        if metadata_changed
                        else "candidate_confirmed"
                    )
                )
                self._append_audit(state, action, active, "same_key_same_content", links)
                if not self.store.save(state):
                    return None, False
                return dict(active), should_write

            entry = {
                "id": "mem_" + uuid.uuid4().hex,
                "memory_key": candidate["memory_key"],
                "category": candidate["category"],
                "memory_type": candidate["memory_type"],
                "subject": candidate["subject"],
                "content": candidate["content"],
                "status": "approved",
                "confidence": candidate["confidence"],
                "importance": candidate["importance"],
                "source_role": candidate["source_role"],
                "source_turn_sequence": incoming_sequence,
                "source_input_id": str(links.get("input_id") or "")[:160],
                "source_session_id": incoming_session,
                "evidence": evidence_items,
                "occurred_at": candidate.get("occurred_at", ""),
                "temporal_precision": candidate.get("temporal_precision", ""),
                "valid_from": candidate.get("valid_from", ""),
                "valid_until": candidate.get("valid_until", ""),
                "timezone": candidate.get("timezone", ""),
                "time_evidence": candidate.get("time_evidence", ""),
                "created_at": now,
                "updated_at": now,
                "last_confirmed_at": now,
                "write_attempts": 0,
                "provider_memory_id": "",
                "last_error": "",
            }
            if active:
                active["status"] = "superseded"
                active["superseded_by"] = entry["id"]
                active["updated_at"] = now
                entry["supersedes"] = active.get("id", "")
                old_provider_id = str(active.get("provider_memory_id") or "").strip()
                if old_provider_id:
                    # Keep the prior storage ID in the ledger for compatibility
                    # and correction lineage across storage backends.
                    entry["superseded_provider_memory_id"] = old_provider_id[:160]
                action = "candidate_superseded"
            else:
                action = "candidate_approved"
            state["entries"].append(entry)
            self._append_audit(
                state,
                action,
                entry,
                "validated_source_evidence",
                links,
            )
            if not self.store.save(state):
                return None, False
            return dict(entry), True

    def _record_write_result(self, entry_id, result):
        with self._lock:
            state = self._load_state()
            if state is None:
                return False
            target = next(
                (item for item in state["entries"] if item.get("id") == entry_id),
                None,
            )
            if target is None:
                return False
            target["write_attempts"] = int(target.get("write_attempts") or 0) + 1
            target["updated_at"] = int(self.now())
            ok = bool(result.get("ok"))
            if ok:
                target["status"] = "written"
                target["provider_memory_id"] = self._clean_text(
                    result.get("provider_memory_id"),
                    160,
                )
                target["last_error"] = ""
                action = "provider_write_succeeded"
                reason = ""
            else:
                target["status"] = "failed"
                target["last_error"] = self._clean_text(
                    result.get("error") or "provider_write_failed",
                    240,
                )
                action = "provider_write_failed"
                reason = target["last_error"]
            self._append_audit(state, action, target, reason, {})
            if not self.store.save(state):
                return False
            return ok

    def _record_audit(self, *, action, reason="", **links):
        with self._lock:
            state = self._load_state()
            if state is None:
                return False
            self._append_audit(state, action, None, reason, links)
            return self.store.save(state)

    def _existing_memory_snapshot(self):
        with self._lock:
            state = self._load_state()
            if state is None:
                return None
            active = [
                {
                    "memory_key": str(item.get("memory_key") or "")[:120],
                    "category": str(item.get("category") or "")[:80],
                    "memory_type": normalize_memory_type(
                        item.get("memory_type"),
                        item.get("category"),
                    ),
                    "subject": str(item.get("subject") or "user")[:40],
                    "content": self._clean_text(item.get("content"), 800),
                    "status": str(item.get("status") or "")[:40],
                    "occurred_at": str(item.get("occurred_at") or "")[:64],
                    "valid_from": str(item.get("valid_from") or "")[:64],
                    "valid_until": str(item.get("valid_until") or "")[:64],
                }
                for item in reversed(state["entries"])
                if item.get("status") in _ACTIVE_STATUSES
            ]
            return active[: self.existing_top_n]

    def _append_audit(self, state, action, entry, reason, links):
        event = {
            "timestamp": int(self.now()),
            "action": str(action or "unknown")[:80],
            "reason": self._clean_text(reason, 240),
        }
        if entry:
            event["candidate_id"] = str(entry.get("id") or "")[:80]
            event["memory_key"] = str(entry.get("memory_key") or "")[:120]
        for key in (
            "trace_id",
            "input_id",
            "session_id",
            "source_mode",
            "terminal_status",
        ):
            value = str((links or {}).get(key) or "")
            if value:
                event[key] = value[:160]
        if (links or {}).get("delivery_complete") is not None:
            event["delivery_complete"] = bool(
                (links or {}).get("delivery_complete")
            )
        candidate_count = self._positive_int(
            (links or {}).get("candidate_count")
        )
        if candidate_count:
            event["candidate_count"] = candidate_count
        turn_sequence = self._positive_int((links or {}).get("turn_sequence"))
        if turn_sequence:
            event["turn_sequence"] = turn_sequence
        state["audit"].append(event)
        if len(state["audit"]) > self.audit_limit:
            state["audit"] = state["audit"][-self.audit_limit:]

    def _load_state(self):
        state = self.store.load(transform=self._normalize_state)
        return state if isinstance(state, dict) else None

    def _normalize_state(self, value):
        state = value if isinstance(value, dict) else _default_state()
        entries = state.get("entries")
        audit = state.get("audit")
        turn_sequences = state.get("turn_sequences")
        processed_input_ids = state.get("processed_input_ids")
        normalized_entries = entries if isinstance(entries, list) else []
        for item in normalized_entries:
            if isinstance(item, dict):
                item["memory_type"] = normalize_memory_type(
                    item.get("memory_type"),
                    item.get("category"),
                )
                subject = str(item.get("subject") or "user").strip().lower()
                item["subject"] = (
                    subject if subject in ALLOWED_SUBJECTS else "user"
                )
                source_role = str(
                    item.get("source_role") or "user"
                ).strip().lower()
                item["source_role"] = (
                    source_role
                    if source_role in ("user", "assistant_delivered", "joint")
                    else "user"
                )
                for field in (
                    "occurred_at",
                    "temporal_precision",
                    "valid_from",
                    "valid_until",
                    "timezone",
                    "time_evidence",
                ):
                    item[field] = str(item.get(field) or "")
        normalized_sequences = {}
        if isinstance(turn_sequences, dict):
            for session_id, sequence in turn_sequences.items():
                session_key = str(session_id or "_default")[:160]
                normalized_sequences[session_key] = self._positive_int(sequence)
        normalized_inputs = {}
        if isinstance(processed_input_ids, dict):
            for session_id, input_ids in processed_input_ids.items():
                if not isinstance(input_ids, list):
                    continue
                session_key = str(session_id or "_default")[:160]
                normalized_inputs[session_key] = [
                    str(input_id)[:160]
                    for input_id in input_ids[-256:]
                    if str(input_id or "")
                ]
        return {
            "schema_version": 3,
            "entries": normalized_entries,
            "audit": audit if isinstance(audit, list) else [],
            "turn_sequences": normalized_sequences,
            "processed_input_ids": normalized_inputs,
        }

    def _extract_with_model(
        self,
        *,
        user_text,
        assistant_text="",
        source_mode="user",
        delivery_complete=True,
        terminal_status="complete",
        completed_at=0,
        existing_memories=None,
        session_id="",
        trace_id="",
        input_id="",
    ):
        from plugins.xiaoyou_common.model_gateway import chat_completion
        from plugins.xiaoyou_common.thinking_config import build_thinking_payload

        source_mode = str(source_mode or "user").strip().lower()
        now_timestamp = self._positive_int(completed_at) or int(self.now())
        current_time = datetime.fromtimestamp(now_timestamp).astimezone().isoformat()
        if source_mode == "assistant_delivered":
            source_rules = """本次审计的是“小悠已经实际发送成功的文字”。
- 只允许 subject=xiaoyou 或 subject=relationship。
- subject=xiaoyou 用于小悠真实说出的稳定偏好、立场、承诺和以后需要保持一致的自我经历。
- subject=relationship 用于双方共同形成的约定、重要共同经历、关系变化或持续中的共同计划。
- 不得把小悠对 YoYo 的猜测写成 subject=user。
- assistant_evidence 必须逐字来自“实际送达的小悠文字”。
- subject=relationship 必须同时填写逐字 user_evidence 与 assistant_evidence，证明双方都参与形成了该事实。
- subject=xiaoyou 如果依赖 YoYo 本轮的参与，也应同时填写逐字 user_evidence；不能证明的部分不要补写。
- 如果是部分送达，只能依据实际送达文字，绝不能使用未发送内容。"""
        else:
            source_rules = """本次审计的是“YoYo 已提交的用户原话”。
- 只允许 subject=user。
- 只提取 YoYo 亲自表达的稳定资料、偏好、纠正、重要经历和需要持续承接的事项。
- user_evidence 必须逐字来自“YoYo 用户原话”。
- 不得从用户原话推断小悠自己的感受、承诺或双方已经共同确认的关系事实。"""

        prompt = """你是小悠的长期记忆审计器。小悠是有自身连续性和判断的女友 AI；她既要记得 YoYo，也要记得自己实际说过的话，以及双方真实形成的共同经历。长期记忆不是聊天记录，不要为了数量保存普通对话。

允许类别：
- user_profile：稳定身份或背景
- durable_preference：长期兴趣、习惯、偏好
- response_preference：YoYo希望小悠怎样回复、称呼或互动
- relationship：双方关系、共同约定或重要共同经历
- project_direction：长期项目目标或已经确认的方向
- correction：对旧记忆的明确纠正
- episodic_event：有时间或场景、以后值得双方回忆的重要经历
- pending_thread：尚未完成且以后需要继续承接的共同事项

本次来源规则：
%s

共同规则：
1. 只依据提供的实际原话判断，不猜测，不把计划生成但没有送达的内容当事实。
2. 是否值得长期记忆由你结合女友 AI 的身份和未来关系连续性判断。普通寒暄、无持续意义的瞬时措辞和纯工具执行过程通常不保存；宁可输出空数组，也不要制造低价值事实。
3. 不记录密码、密钥、令牌、验证码、身份证件、银行卡等敏感秘密。
4. content 必须简洁、独立、无歧义，并明确主体是“YoYo”“小悠”或“双方”。
5. memory_key 使用稳定的英文小写点号键，并按主体命名，例如 user.preference.reply_style、xiaoyou.commitment.emotional_presence、relationship.shared_plan.xiaoyou。
6. 参考现有记忆；同一事实或对旧事实的修正必须复用已有 memory_key，让新事实覆盖旧值。
7. 完整输出本轮所有真正具有长期价值的不同事实，不设置业务数量目标，也不要把同一事实拆成多条。
8. confidence 表示证据确定性，importance 表示对未来理解双方关系和保持连续性的价值。
9. 时间由模型结合“当前现实时间”理解，不用关键词规则。只有来源原话表达了发生时间、开始时间或结束时间时才填写：
   - occurred_at：事件实际发生时间，ISO 8601
   - temporal_precision：exact、day、period、approximate 或 unknown
   - valid_from / valid_until：状态或约定的有效区间，ISO 8601
   - timezone：IANA 时区
   - time_evidence：逐字时间证据
   没有时间证据时这些字段留空，不要把系统记录时间冒充事件发生时间。
10. 当 YoYo 明确要求小悠长期记住某项信息时，这是显式记忆授权；应提高 importance 并优先提取，但仍不得补写来源中没有证据的细节。

当前现实时间：%s
送达状态：complete=%s，terminal_status=%s

现有受治理记忆：
%s

YoYo 用户原话：
%s

实际送达的小悠文字：
%s

只输出合法 JSON，不要 Markdown：
{"candidates":[{"subject":"user","category":"response_preference","memory_key":"user.preference.reply_style","content":"YoYo希望小悠回复自然且有自己的判断。","user_evidence":"对应的逐字用户证据","assistant_evidence":"","confidence":0.95,"importance":0.9,"occurred_at":"","temporal_precision":"","valid_from":"","valid_until":"","timezone":"","time_evidence":""}]}""" % (
            source_rules,
            current_time,
            str(bool(delivery_complete)).lower(),
            str(terminal_status or "complete")[:40],
            json.dumps(existing_memories or [], ensure_ascii=False)[:12000],
            user_text[:6000],
            assistant_text[:6000],
        )
        payload = {
            "model": os.getenv("MEMORY_GOVERNANCE_MODEL", "qwen3.7-plus"),
            "messages": [
                {
                    "role": "system",
                    "content": "你只做长期记忆候选审计，只输出一个 JSON 对象。",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.05,
            "max_tokens": 2200,
            **build_thinking_payload("MEMORY_GOVERNANCE", default=False),
        }
        result = chat_completion(
            component="MemoryGovernance",
            purpose="extract_long_memory_candidates",
            payload=payload,
            timeout=int(os.getenv("MEMORY_GOVERNANCE_TIMEOUT", "45")),
            session_id=session_id,
            trace_id=trace_id,
            input_id=input_id,
        )
        if not result.ok:
            raise RuntimeError("extractor_unavailable:" + str(result.error_kind or "unknown"))
        data = self._parse_json(result.content)
        if not isinstance(data, dict) or not isinstance(data.get("candidates"), list):
            raise ValueError("extractor_invalid_json")
        return data["candidates"]

    def _build_default_store(self):
        from plugins.xiaoyou_common.state_store import JsonStateStore

        path = os.getenv(
            "MEMORY_GOVERNANCE_STATE_PATH",
            "/app/data/xiaoyou_memory/memory_governance.json",
        )
        return JsonStateStore(
            path,
            name="memory-governance",
            default_factory=_default_state,
            expected_type=dict,
            strict_unavailable=True,
        )

    @staticmethod
    def _parse_json(value):
        text = str(value or "").strip()
        if text.startswith("```"):
            lines = text.splitlines()[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            return json.loads(text)
        except Exception:
            start = text.find("{")
            end = text.rfind("}")
            if start < 0 or end <= start:
                return None
            try:
                return json.loads(text[start:end + 1])
            except Exception:
                return None

    @staticmethod
    def _evidence_supported(evidence, user_text):
        evidence_norm = re.sub(r"\s+", " ", str(evidence or "")).strip()
        user_norm = re.sub(r"\s+", " ", str(user_text or "")).strip()
        return bool(evidence_norm) and evidence_norm in user_norm

    @staticmethod
    def _evidence_item(evidence, timestamp, source_role="user"):
        source_role = (
            str(source_role or "user")
            if str(source_role or "user")
            in ("user", "assistant_delivered")
            else "user"
        )
        return {
            "hash": hashlib.sha256(
                ("%s:%s" % (source_role, evidence)).encode("utf-8")
            ).hexdigest(),
            "excerpt": str(evidence),
            "recorded_at": int(timestamp),
            "source_role": source_role,
        }

    @staticmethod
    def _append_evidence(entry, evidence_item):
        evidence = entry.get("evidence")
        if not isinstance(evidence, list):
            evidence = []
            entry["evidence"] = evidence
        if not any(item.get("hash") == evidence_item["hash"] for item in evidence if isinstance(item, dict)):
            evidence.append(evidence_item)
        entry["evidence"] = evidence[-5:]

    @staticmethod
    def _normalized(value):
        return re.sub(r"\s+", "", str(value or "")).strip().lower()

    @staticmethod
    def _contains_secret(value):
        text = str(value or "")
        return any(pattern.search(text) for pattern in _SECRET_PATTERNS)

    @staticmethod
    def _clean_text(value, limit):
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        return text[: int(limit)]

    @staticmethod
    def _normalize_iso_time(value):
        text = str(value or "").strip()
        if not text:
            return ""
        try:
            normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
            if "T" in normalized or " " in normalized:
                datetime.fromisoformat(normalized)
            else:
                datetime.strptime(normalized, "%Y-%m-%d")
            return text[:64]
        except (TypeError, ValueError):
            return ""

    @staticmethod
    def _unit_float(value):
        try:
            return max(0.0, min(1.0, float(value)))
        except Exception:
            return 0.0

    @staticmethod
    def _positive_int(value):
        try:
            return max(0, int(value))
        except Exception:
            return 0

    @staticmethod
    def _float_setting(explicit, key, default):
        try:
            value = float(os.getenv(key, str(default)) if explicit is None else explicit)
        except Exception:
            value = float(default)
        return max(0.0, min(1.0, value))

    @staticmethod
    def _int_setting(explicit, key, default, *, minimum, maximum):
        try:
            value = int(os.getenv(key, str(default)) if explicit is None else explicit)
        except Exception:
            value = int(default)
        return max(minimum, min(maximum, value))
