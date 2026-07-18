# -*- coding: utf-8 -*-
"""Auditable write governance for Xiaoyou long-term memory.

Long-term memory is not a transcript archive.  This module extracts a small
set of durable, user-supported candidates, validates their evidence, merges
them by a stable semantic key and only then calls the provider writer.

The assistant reply is deliberately accepted by ``process_turn`` for API
compatibility but is never sent to the extractor, used as evidence or stored
in the ledger.  This prevents Xiaoyou's own guesses from becoming user facts.
"""

import hashlib
import json
import os
import re
import threading
import time
import uuid

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
        "schema_version": 1,
        "entries": [],
        "audit": [],
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
        self.max_candidates = self._int_setting(
            max_candidates,
            "MEMORY_GOVERNANCE_MAX_CANDIDATES",
            4,
            minimum=1,
            maximum=10,
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
        trace_id="",
        input_id="",
        session_id="",
    ):
        """Govern one completed turn and return a non-sensitive summary.

        ``assistant_text`` must remain unused.  It exists so callers can move
        from transcript writes to governed writes without changing lifecycle
        timing.
        """
        del assistant_text
        user_text = str(user_text or "").strip()
        if not user_text or self._contains_secret(user_text):
            self._record_audit(
                action="turn_skipped",
                reason="empty_or_sensitive_user_text",
                trace_id=trace_id,
                input_id=input_id,
                session_id=session_id,
            )
            return {"extracted": 0, "eligible": 0, "written": 0, "failed": 0}
        if self._is_transient_reminder(user_text):
            self._record_audit(
                action="turn_skipped",
                reason="transient_reminder_owned_by_reminder_service",
                trace_id=trace_id,
                input_id=input_id,
                session_id=session_id,
            )
            return {"extracted": 0, "eligible": 0, "written": 0, "failed": 0}

        existing_memories = self._existing_memory_snapshot()
        if existing_memories is None:
            # An unavailable audit ledger must block new cloud writes.  This
            # keeps provider state and the local decision history consistent.
            return {"extracted": 0, "eligible": 0, "written": 0, "failed": 1}

        try:
            raw_candidates = self.extractor(
                user_text=user_text,
                existing_memories=existing_memories,
                session_id=str(session_id or ""),
                trace_id=str(trace_id or ""),
                input_id=str(input_id or ""),
            )
        except Exception as exc:
            self._record_audit(
                action="extraction_failed",
                reason=type(exc).__name__,
                trace_id=trace_id,
                input_id=input_id,
                session_id=session_id,
            )
            return {"extracted": 0, "eligible": 0, "written": 0, "failed": 1}

        if not isinstance(raw_candidates, list):
            raw_candidates = []
        summary = {
            "extracted": min(len(raw_candidates), self.max_candidates),
            "eligible": 0,
            "written": 0,
            "failed": 0,
        }

        for raw in raw_candidates[:self.max_candidates]:
            candidate, reason = self._validate_candidate(raw, user_text)
            if candidate is None:
                self._record_audit(
                    action="candidate_rejected",
                    reason=reason,
                    trace_id=trace_id,
                    input_id=input_id,
                    session_id=session_id,
                )
                continue

            summary["eligible"] += 1
            entry, should_write = self._upsert_candidate(
                candidate,
                trace_id=trace_id,
                input_id=input_id,
                session_id=session_id,
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

    def _validate_candidate(self, raw, user_text):
        if not isinstance(raw, dict):
            return None, "candidate_not_object"

        category = str(raw.get("category") or "").strip().lower()
        if category not in ALLOWED_CATEGORIES:
            return None, "unsupported_category"

        memory_key = str(raw.get("memory_key") or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9._:-]{2,119}", memory_key):
            return None, "invalid_memory_key"

        content = self._clean_text(raw.get("content"), 800)
        evidence = self._clean_text(raw.get("evidence"), 500)
        if not content or not evidence:
            return None, "missing_content_or_evidence"
        if not self._evidence_supported(evidence, user_text):
            return None, "evidence_not_in_user_message"
        if category == "relationship" and not self._strong_relationship_evidence(
            user_text,
            evidence,
        ):
            return None, "weak_relationship_evidence"
        if self._contains_secret(content) or self._contains_secret(evidence):
            return None, "sensitive_content"

        confidence = self._unit_float(raw.get("confidence"))
        importance = self._unit_float(raw.get("importance"))
        if confidence < self.min_confidence:
            return None, "confidence_below_threshold"
        if importance < self.min_importance:
            return None, "importance_below_threshold"

        return {
            "memory_key": memory_key,
            "category": category,
            "memory_type": normalize_memory_type("", category),
            "content": content,
            "confidence": confidence,
            "importance": importance,
            "evidence": evidence,
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

            evidence_item = self._evidence_item(candidate["evidence"], now)
            if active and self._normalized(active.get("content")) == self._normalized(candidate["content"]):
                active["last_confirmed_at"] = now
                active["updated_at"] = now
                active["confidence"] = max(
                    self._unit_float(active.get("confidence")),
                    candidate["confidence"],
                )
                active["importance"] = max(
                    self._unit_float(active.get("importance")),
                    candidate["importance"],
                )
                self._append_evidence(active, evidence_item)
                should_write = active.get("status") == "failed"
                action = "candidate_retry" if should_write else "candidate_confirmed"
                self._append_audit(state, action, active, "same_key_same_content", links)
                if not self.store.save(state):
                    return None, False
                return dict(active), should_write

            entry = {
                "id": "mem_" + uuid.uuid4().hex,
                "memory_key": candidate["memory_key"],
                "category": candidate["category"],
                "memory_type": candidate["memory_type"],
                "content": candidate["content"],
                "status": "approved",
                "confidence": candidate["confidence"],
                "importance": candidate["importance"],
                "source_role": "user",
                "evidence": [evidence_item],
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
                    # The provider can PATCH an existing memory node.  Carry
                    # the old node ID forward so a correction replaces cloud
                    # content instead of leaving contradictory duplicates.
                    entry["superseded_provider_memory_id"] = old_provider_id[:160]
                action = "candidate_superseded"
            else:
                action = "candidate_approved"
            state["entries"].append(entry)
            self._append_audit(state, action, entry, "validated_user_evidence", links)
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
                    "content": self._clean_text(item.get("content"), 800),
                    "status": str(item.get("status") or "")[:40],
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
        for key in ("trace_id", "input_id", "session_id"):
            value = str((links or {}).get(key) or "")
            if value:
                event[key] = value[:160]
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
        normalized_entries = entries if isinstance(entries, list) else []
        for item in normalized_entries:
            if isinstance(item, dict):
                item["memory_type"] = normalize_memory_type(
                    item.get("memory_type"),
                    item.get("category"),
                )
        return {
            "schema_version": 1,
            "entries": normalized_entries,
            "audit": audit if isinstance(audit, list) else [],
        }

    def _extract_with_model(
        self,
        *,
        user_text,
        existing_memories=None,
        session_id="",
        trace_id="",
        input_id="",
    ):
        from plugins.xiaoyou_common.model_gateway import chat_completion
        from plugins.xiaoyou_common.thinking_config import build_thinking_payload

        prompt = """你是小悠的长期记忆写入审计器。长期记忆不是聊天记录，只提取用户亲自表达、未来仍有帮助的稳定信息。

允许类别：
- user_profile：稳定身份或背景
- durable_preference：长期兴趣、习惯、偏好
- response_preference：用户希望小悠怎样回复、称呼或互动
- relationship：用户明确确认的关系约定或重要共同经历
- project_direction：长期项目目标、已经确认的架构方向
- correction：用户对旧事实的明确纠正
- episodic_event：有明确时间或场景、以后可能会被回忆的重要经历
- pending_thread：用户明确提出、尚未完成且后续需要继续的事项

严格规则：
1. 证据只能来自下方“用户原话”，不得猜测，不得把助手说过的话当事实。
2. 忽略寒暄、普通情绪瞬间、一次性请求、助手建议和普通问句。只有确实重要且以后可能回忆的经历才用 episodic_event；只有明确需要后续继续的事项才用 pending_thread。
3. 不记录密码、密钥、令牌、验证码、身份证件、银行卡等敏感秘密。
4. content 必须是简洁、独立、无歧义的第三人称事实，不补写用户没说过的内容。
5. evidence 必须逐字复制用户原话中的一小段；找不到直接证据就不要输出。
6. memory_key 使用稳定的英文小写点号键，代表“可被新值覆盖的事实槽位”，例如 response.reply_style 或 project.xiaoyou.primary_goal。
7. 参考“现有受治理记忆”：同一事实必须复用已有 memory_key；用户明确改变旧事实时也复用该键，让系统覆盖旧值。不要仅仅重复已有内容。
8. 同一事实只输出一次，最多 %d 条。宁可输出空数组，也不要低质量记忆。
9. 当用户明确使用“记住、你要记得、以后都、从现在起”等表达要求保存某项稳定信息时，这是显式记忆授权；只要内容不含秘密、不是纯玩笑且未来仍有帮助，应提高 importance 并优先提取。显式授权不代表可以补写用户没说过的细节。
10. “老婆、老公、宝贝、女朋友”等单独称呼只是当轮亲密表达，不能据此输出 relationship 或声称“确立关系”。relationship 必须有“我们正式在一起、你是我的女朋友、我们的纪念日是……”等明确关系事实或约定。
11. 提醒、闹钟、叫醒、到点做事等一次性或定时任务由专门提醒系统管理，即使尚未完成也不得输出 pending_thread；pending_thread 只用于跨多轮项目、决定或明确需要以后继续讨论的事项。

现有受治理记忆：
%s

用户原话：
%s

只输出合法 JSON，不要 Markdown：
{"candidates":[{"category":"response_preference","memory_key":"response.reply_style","content":"YoYo希望小悠回复更自然、更聪明。","evidence":"永远是如何让小悠回复的更好","confidence":0.95,"importance":0.9}]}""" % (
            self.max_candidates,
            json.dumps(existing_memories or [], ensure_ascii=False)[:12000],
            user_text[:6000],
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
            "max_tokens": 1000,
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
    def _is_transient_reminder(value):
        text = re.sub(r"\s+", "", str(value or ""))
        action = re.search(r"提醒|叫醒|闹钟|到点.{0,8}(?:叫|喊|告诉|通知)|记得.{0,12}(?:叫|提醒|喊)", text)
        schedule = re.search(
            r"今天|明天|后天|今晚|明早|早上|上午|中午|下午|晚上|每天|每晚|每周|"
            r"\d{1,2}(?:[:：.]\d{1,2}|点(?:半|\d{1,2}分)?)|"
            r"[一二三四五六七八九十两]{1,3}点",
            text,
        )
        return bool(action and schedule)

    @staticmethod
    def _strong_relationship_evidence(user_text, evidence):
        text = re.sub(r"\s+", "", "%s%s" % (user_text or "", evidence or ""))
        patterns = (
            r"我们(?:现在|已经|正式)?(?:是|成为).{0,10}(?:情侣|恋人|伴侣|夫妻)",
            r"你是我(?:的)?.{0,6}(?:女朋友|男朋友|老婆|老公|伴侣|爱人)",
            r"我是你(?:的)?.{0,6}(?:女朋友|男朋友|老婆|老公|伴侣|爱人)",
            r"我们.{0,10}(?:在一起|结婚|订婚|分手|复合)",
            r"我们.{0,16}(?:纪念日|第一次见面|初次见面|约定)",
            r"记住.{0,16}(?:我们的关系|我们是|你是我的|我是你的)",
        )
        return any(re.search(pattern, text) for pattern in patterns)

    @staticmethod
    def _evidence_item(evidence, timestamp):
        return {
            "hash": hashlib.sha256(str(evidence).encode("utf-8")).hexdigest(),
            "excerpt": str(evidence),
            "recorded_at": int(timestamp),
            "source_role": "user",
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
    def _unit_float(value):
        try:
            return max(0.0, min(1.0, float(value)))
        except Exception:
            return 0.0

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
