# -*- coding: utf-8 -*-
"""Short-lived, evidence-grounded conversation state for Xiaoyou.

RecentState is not a second long-term memory.  It keeps only the active topic,
temporary user state, unresolved threads and local references, and every item
expires automatically.  Updates run after a completed exchange in a background
worker so normal chat latency is unchanged.
"""

import json
import os
import re
import threading
import time
from datetime import datetime

from common.log import logger
from plugins.xiaoyou_common.context_service import build_context_snapshot
from plugins.xiaoyou_common.model_gateway import chat_completion
from plugins.xiaoyou_common.runtime_paths import runtime_path
from plugins.xiaoyou_common.state_store import JsonStateStore
from plugins.xiaoyou_common.thinking_config import build_thinking_payload


STATE_FILE = runtime_path(
    "xiaoyou_recent_state",
    "state.json",
    env_var="XIAOYOU_RECENT_STATE_PATH",
    legacy_paths=(
        os.path.join(os.path.dirname(__file__), "xiaoyou_recent_state", "state.json"),
    ),
)
STATE_BACKUP_FILE = STATE_FILE + ".backup"

DEFAULT_TTLS = {
    "topic": 21600,
    "user_state": 10800,
    "xiaoyou_stance": 10800,
    "open_loop": 86400,
    "referent": 21600,
    "temporary_fact": 43200,
}
LIST_CAPS = {
    "user_states": 5,
    "open_loops": 5,
    "referents": 6,
    "temporary_facts": 6,
}
SENSITIVE_RE = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{12,}|密码|验证码|银行卡|身份证|api[_ -]?key|access[_ -]?token)",
    re.I,
)


class RecentStateService:
    def __init__(self, path=None):
        path = path or STATE_FILE
        self.store = JsonStateStore(
            path,
            backup_path=path + ".backup",
            name="xiaoyou_recent_state",
            default_factory=lambda: {"schema_version": 1, "sessions": {}},
        )
        self.lock = threading.RLock()
        self.queues = {}
        self.workers = set()
        self.generations = {}

    def enabled(self):
        return os.getenv("XIAOYOU_RECENT_STATE_ENABLED", "true").strip().lower() in (
            "1", "true", "yes", "on"
        )

    def schedule_update(
        self,
        session_id,
        *,
        user_text,
        assistant_text,
        last_user_ts=0,
        trace_id="",
        input_id="",
    ):
        if not self.enabled():
            return False
        session_id = str(session_id or "").strip()
        user_text = str(user_text or "").strip()
        assistant_text = str(assistant_text or "").strip()
        if not session_id or not user_text or not assistant_text:
            return False

        job = {
            "user_text": user_text[:1600],
            "assistant_text": assistant_text[:1600],
            "last_user_ts": int(last_user_ts or time.time()),
            "trace_id": str(trace_id or "")[:80],
            "input_id": str(input_id or "")[:80],
        }
        with self.lock:
            job["_generation"] = int(self.generations.get(session_id, 0))
            queue = self.queues.setdefault(session_id, [])
            if job["input_id"] and any(
                queued.get("input_id") == job["input_id"] for queued in queue
            ):
                return True
            queue.append(job)
            if session_id in self.workers:
                return True
            self.workers.add(session_id)
            threading.Thread(
                target=self._run_queue,
                args=(session_id,),
                daemon=True,
                name="XiaoyouRecentState",
            ).start()
        return True

    def _run_queue(self, session_id):
        try:
            while True:
                with self.lock:
                    queue = self.queues.get(session_id, [])
                    if not queue:
                        self.queues.pop(session_id, None)
                        return
                    job = queue.pop(0)
                try:
                    self.update_from_exchange(session_id, **job)
                except Exception:
                    logger.exception(
                        "[RecentState] background update failed session=%s",
                        session_id,
                    )
        finally:
            with self.lock:
                self.workers.discard(session_id)
                if self.queues.get(session_id):
                    self.workers.add(session_id)
                    threading.Thread(
                        target=self._run_queue,
                        args=(session_id,),
                        daemon=True,
                        name="XiaoyouRecentState",
                    ).start()

    def update_from_exchange(
        self,
        session_id,
        *,
        user_text,
        assistant_text,
        last_user_ts=0,
        trace_id="",
        input_id="",
        _generation=None,
    ):
        if not self.enabled():
            return self.get(session_id)
        session_id = str(session_id or "").strip()
        user_text = str(user_text or "").strip()
        assistant_text = str(assistant_text or "").strip()
        if not session_id or not user_text or not assistant_text:
            return self.get(session_id)

        prior = self.get(session_id)
        snapshot = build_context_snapshot(
            content=user_text,
            session_id=session_id,
            include_character=False,
            include_time=True,
            include_short_memory=True,
            short_memory_max_chars=max(
                1200,
                int(os.getenv("XIAOYOU_RECENT_STATE_CONTEXT_MAX_CHARS", "4500")),
            ),
            component="XiaoyouRecentState",
        )
        prompt = self._build_update_prompt(
            prior,
            snapshot.short_memory,
            snapshot.time_context,
            user_text,
            assistant_text,
        )
        payload = {
            "model": os.getenv("XIAOYOU_RECENT_STATE_MODEL", "qwen3.7-plus"),
            "messages": [
                {
                    "role": "system",
                    "content": "你只提取短时对话状态，只输出合法JSON，不生成聊天回复。",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.15,
            "max_tokens": 900,
            **build_thinking_payload("XIAOYOU_RECENT_STATE"),
        }
        result = chat_completion(
            component="XiaoyouRecentState",
            purpose="update_after_exchange",
            payload=payload,
            timeout=int(os.getenv("XIAOYOU_RECENT_STATE_TIMEOUT", "30")),
            session_id=session_id,
            trace_id=trace_id,
            input_id=input_id,
        )
        if not result.ok:
            error_kind = getattr(result, "error_kind", "model_failed")
            if error_kind == "content_inspection":
                self._suspend_stale_state(
                    session_id,
                    last_user_ts=last_user_ts,
                    input_id=input_id,
                    generation=_generation,
                )
            logger.warning(
                "[RecentState] update skipped session=%s error=%s",
                session_id,
                error_kind,
            )
            return prior

        data = _parse_json(result.content)
        if not isinstance(data, dict):
            logger.warning("[RecentState] invalid model JSON session=%s", session_id)
            return prior

        now = int(time.time())
        recent_corpus = str(snapshot.short_memory or "")
        update = self._normalize_update(
            data,
            user_text=user_text,
            assistant_text=assistant_text,
            recent_corpus=recent_corpus,
            now=now,
        )
        if not _has_state_content(update):
            logger.info("[RecentState] no grounded update session=%s", session_id)
            return prior

        with self.lock:
            if (
                _generation is not None
                and int(self.generations.get(session_id, 0)) != int(_generation)
            ):
                logger.info(
                    "[RecentState] stale update discarded session=%s",
                    session_id,
                )
                return self.get(session_id)
            state = self._load_all()
            sessions = state.setdefault("sessions", {})
            current = self._prune_session(sessions.get(session_id, {}), now)
            merged = self._merge(current, update, now)
            merged["updated_at"] = now
            merged["last_user_at"] = int(last_user_ts or now)
            merged["last_input_id"] = str(input_id or "")[:80]
            merged["suspended_at"] = 0
            merged["suspended_reason"] = ""
            sessions[session_id] = merged
            self.store.save(state)

        logger.info(
            "[RecentState] updated session=%s topic=%s states=%s loops=%s refs=%s facts=%s",
            session_id,
            bool(merged.get("topic")),
            len(merged.get("user_states", [])),
            len(merged.get("open_loops", [])),
            len(merged.get("referents", [])),
            len(merged.get("temporary_facts", [])),
        )
        return self._public(merged)

    def get(self, session_id):
        session_id = str(session_id or "").strip()
        if not session_id:
            return self._empty_session()
        now = int(time.time())
        with self.lock:
            state = self._load_all()
            sessions = state.setdefault("sessions", {})
            original = sessions.get(session_id, {})
            pruned = self._prune_session(original, now)
            if pruned != original:
                sessions[session_id] = pruned
                self.store.save(state)
            return self._public(pruned)

    def build_context(self, session_id):
        state = self.get(session_id)
        if int(state.get("suspended_at") or 0) >= int(state.get("updated_at") or 0):
            return ""
        parts = []
        if state.get("topic"):
            parts.append("当前话题：%s" % state["topic"].get("text", ""))
        if state.get("user_states"):
            parts.append("YoYo当前临时状态：" + "；".join(
                item.get("text", "") for item in state["user_states"] if item.get("text")
            ))
        if state.get("xiaoyou_stance"):
            parts.append("小悠当前承接立场：%s" % state["xiaoyou_stance"].get("text", ""))
        if state.get("open_loops"):
            parts.append("尚未结束的话题或事项：" + "；".join(
                item.get("text", "") for item in state["open_loops"] if item.get("text")
            ))
        if state.get("referents"):
            parts.append("近期指代：" + "；".join(
                "%s→%s" % (item.get("mention", ""), item.get("target", ""))
                for item in state["referents"]
                if item.get("mention") and item.get("target")
            ))
        if state.get("temporary_facts"):
            parts.append("仅在当前时段有效的事实：" + "；".join(
                item.get("text", "") for item in state["temporary_facts"] if item.get("text")
            ))
        if not parts:
            return ""
        updated = state.get("updated_at")
        label = datetime.fromtimestamp(updated).strftime("%m-%d %H:%M") if updated else "未知"
        return "更新时间：%s\n%s" % (label, "\n".join(parts))

    def _suspend_stale_state(
        self,
        session_id,
        *,
        last_user_ts=0,
        input_id="",
        generation=None,
    ):
        """Hide stale derived state after inspection; exact history remains authoritative."""
        now = int(time.time())
        with self.lock:
            if (
                generation is not None
                and int(self.generations.get(session_id, 0)) != int(generation)
            ):
                return False
            state = self._load_all()
            sessions = state.setdefault("sessions", {})
            current = self._prune_session(sessions.get(session_id, {}), now)
            current["suspended_at"] = now
            current["suspended_reason"] = "provider_content_inspection"
            current["last_user_at"] = int(last_user_ts or now)
            current["last_input_id"] = str(input_id or "")[:80]
            sessions[session_id] = current
            self.store.save(state)
        logger.info(
            "[RecentState] stale derived context suspended session=%s",
            session_id,
        )
        return True

    def clear(self, session_id):
        session_id = str(session_id or "").strip()
        if not session_id:
            return False
        with self.lock:
            state = self._load_all()
            sessions = state.setdefault("sessions", {})
            existed = sessions.pop(session_id, None) is not None
            self.queues.pop(session_id, None)
            self.generations[session_id] = int(self.generations.get(session_id, 0)) + 1
            if existed:
                self.store.save(state)
            return existed

    def _build_update_prompt(self, prior, recent, time_context, user_text, assistant_text):
        return """你负责维护小悠的RecentState。它只描述几小时到一天内仍然有效的当前对话状态，不是长期记忆，也不是对小悠的永久行为指令。

只提取有原文证据、下一轮确实有助于接话的内容：当前话题、YoYo临时状态、小悠刚刚明确表达的承接立场、未结束事项、指代关系和临时事实。普通调情台词、修辞、口头禅、惩罚梗和没有证据的现实细节不要记录。密码、令牌、验证码等秘密绝不记录。

每个项目必须给出逐字出现在本轮用户原话、本轮小悠回复或近期聊天里的evidence，并设置ttl_seconds。无法确定就省略，不要猜测。旧状态和本轮冲突时，以本轮用户原话为准。key用于同类状态覆盖，使用简短稳定的英文或中文键。

当前时间：
%s

旧RecentState：
%s

近期聊天：
%s

YoYo本轮原话：
%s

小悠本轮实际回复：
%s

只输出合法JSON，缺少内容的字段使用null或空数组：
{
  "topic": {"key":"topic","text":"当前话题","evidence":"逐字证据","source":"user|assistant|recent","ttl_seconds":21600},
  "user_states": [{"key":"energy|emotion|activity|need|plan|health|location|availability|other","text":"临时状态","evidence":"逐字证据","source":"user|recent","ttl_seconds":10800}],
  "xiaoyou_stance": {"key":"stance","text":"刚刚明确表达的承接立场","evidence":"逐字证据","source":"assistant|recent","ttl_seconds":10800},
  "open_loops": [{"key":"事项键","text":"尚未结束的事项","evidence":"逐字证据","source":"user|assistant|recent","ttl_seconds":86400}],
  "referents": [{"key":"指代词","mention":"它","target":"实际对象","evidence":"逐字证据","source":"user|recent","ttl_seconds":21600}],
  "temporary_facts": [{"key":"事实键","text":"当前时段事实","evidence":"逐字证据","source":"user|recent","ttl_seconds":43200}]
}""" % (
            str(time_context or "暂无"),
            json.dumps(prior or {}, ensure_ascii=False),
            str(recent or "暂无")[:5000],
            user_text[:1600],
            assistant_text[:1600],
        )

    def _normalize_update(self, data, *, user_text, assistant_text, recent_corpus, now):
        corpora = {
            "user": user_text,
            "assistant": assistant_text,
            "recent": recent_corpus,
        }
        update = self._empty_session()
        update["topic"] = self._normalize_item(
            data.get("topic"), "topic", corpora, now,
            allowed_sources=("user", "assistant", "recent"),
        )
        update["xiaoyou_stance"] = self._normalize_item(
            data.get("xiaoyou_stance"), "xiaoyou_stance", corpora, now,
            allowed_sources=("assistant", "recent"),
            recent_role="assistant",
        )
        update["user_states"] = self._normalize_list(
            data.get("user_states"), "user_state", corpora, now,
            allowed_sources=("user", "recent"),
            recent_role="user",
        )
        update["open_loops"] = self._normalize_list(
            data.get("open_loops"), "open_loop", corpora, now,
            allowed_sources=("user", "assistant", "recent"),
        )
        update["temporary_facts"] = self._normalize_list(
            data.get("temporary_facts"), "temporary_fact", corpora, now,
            allowed_sources=("user", "recent"),
            recent_role="user",
        )
        update["referents"] = self._normalize_referents(
            data.get("referents"), corpora, now
        )
        return update

    def _normalize_list(
        self,
        values,
        kind,
        corpora,
        now,
        allowed_sources,
        recent_role="",
    ):
        if not isinstance(values, list):
            return []
        result = []
        for value in values[: LIST_CAPS.get(_bucket_for_kind(kind), 6) * 2]:
            item = self._normalize_item(
                value,
                kind,
                corpora,
                now,
                allowed_sources=allowed_sources,
                recent_role=recent_role,
            )
            if item:
                result.append(item)
        return result

    def _normalize_item(
        self,
        value,
        kind,
        corpora,
        now,
        allowed_sources,
        recent_role="",
    ):
        if not isinstance(value, dict):
            return {}
        text = _clean(value.get("text"), 180)
        evidence = _clean(value.get("evidence"), 180)
        source = str(value.get("source") or "").strip().lower()
        key = _clean(value.get("key"), 60) or kind
        if not text or not evidence or source not in allowed_sources:
            return {}
        if SENSITIVE_RE.search(text) or SENSITIVE_RE.search(evidence):
            return {}
        if not _evidence_supported(
            source,
            evidence,
            corpora,
            recent_role=recent_role,
        ):
            return {}
        ttl = _ttl(value.get("ttl_seconds"), DEFAULT_TTLS[kind])
        return {
            "key": key,
            "text": text,
            "evidence": evidence,
            "source": source,
            "expires_at": now + ttl,
        }

    def _normalize_referents(self, values, corpora, now):
        if not isinstance(values, list):
            return []
        result = []
        for value in values[:12]:
            if not isinstance(value, dict):
                continue
            mention = _clean(value.get("mention"), 40)
            target = _clean(value.get("target"), 140)
            evidence = _clean(value.get("evidence"), 180)
            source = str(value.get("source") or "").strip().lower()
            if source not in ("user", "recent"):
                continue
            if not mention or not target or not evidence:
                continue
            target_corpus = "\n".join(
                str(corpora.get(name) or "") for name in ("user", "recent", "assistant")
            )
            if target not in target_corpus:
                continue
            if SENSITIVE_RE.search(target) or not _evidence_supported(
                source,
                evidence,
                corpora,
                recent_role="user",
            ):
                continue
            result.append({
                "key": _clean(value.get("key"), 60) or mention,
                "mention": mention,
                "target": target,
                "evidence": evidence,
                "source": source,
                "expires_at": now + _ttl(
                    value.get("ttl_seconds"), DEFAULT_TTLS["referent"]
                ),
            })
        return result

    def _merge(self, current, update, now):
        merged = self._prune_session(current, now)
        if update.get("topic"):
            merged["topic"] = update["topic"]
        if update.get("xiaoyou_stance"):
            merged["xiaoyou_stance"] = update["xiaoyou_stance"]
        for bucket in ("user_states", "open_loops", "referents", "temporary_facts"):
            incoming = update.get(bucket, [])
            existing = merged.get(bucket, [])
            merged[bucket] = _merge_keyed(incoming, existing)[: LIST_CAPS[bucket]]
        return merged

    def _prune_session(self, value, now):
        item = self._empty_session()
        if not isinstance(value, dict):
            return item
        item.update({
            "updated_at": int(value.get("updated_at") or 0),
            "last_user_at": int(value.get("last_user_at") or 0),
            "last_input_id": str(value.get("last_input_id") or "")[:80],
            "suspended_at": int(value.get("suspended_at") or 0),
            "suspended_reason": str(value.get("suspended_reason") or "")[:80],
        })
        for field in ("topic", "xiaoyou_stance"):
            record = value.get(field)
            if isinstance(record, dict) and int(record.get("expires_at") or 0) > now:
                item[field] = dict(record)
        for bucket in ("user_states", "open_loops", "referents", "temporary_facts"):
            item[bucket] = [
                dict(record)
                for record in value.get(bucket, [])
                if isinstance(record, dict) and int(record.get("expires_at") or 0) > now
            ][: LIST_CAPS[bucket]]
        return item

    def _empty_session(self):
        return {
            "topic": {},
            "user_states": [],
            "xiaoyou_stance": {},
            "open_loops": [],
            "referents": [],
            "temporary_facts": [],
            "updated_at": 0,
            "last_user_at": 0,
            "last_input_id": "",
            "suspended_at": 0,
            "suspended_reason": "",
        }

    def _public(self, value):
        return json.loads(json.dumps(value or self._empty_session(), ensure_ascii=False))

    def _load_all(self):
        value = self.store.load()
        if not isinstance(value, dict):
            value = {"schema_version": 1, "sessions": {}}
        value.setdefault("schema_version", 1)
        if not isinstance(value.get("sessions"), dict):
            value["sessions"] = {}
        return value


def _merge_keyed(incoming, existing):
    result = []
    seen = set()
    for record in list(incoming or []) + list(existing or []):
        if not isinstance(record, dict):
            continue
        key = str(record.get("key") or record.get("mention") or record.get("text") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(dict(record))
    return result


def _evidence_supported(source, evidence, corpora, *, recent_role=""):
    corpus = str(corpora.get(source) or "")
    if not evidence or evidence not in corpus:
        return False
    if source != "recent" or not recent_role:
        return True

    role_markers = (
        ("YoYo", "用户") if recent_role == "user" else ("小悠", "assistant")
    )
    for line in corpus.splitlines():
        if evidence not in line:
            continue
        if any(marker in line for marker in role_markers):
            return True
    return False


def _has_state_content(value):
    return bool(
        value.get("topic")
        or value.get("xiaoyou_stance")
        or value.get("user_states")
        or value.get("open_loops")
        or value.get("referents")
        or value.get("temporary_facts")
    )


def _bucket_for_kind(kind):
    return {
        "user_state": "user_states",
        "open_loop": "open_loops",
        "temporary_fact": "temporary_facts",
    }.get(kind, "temporary_facts")


def _clean(value, limit):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _ttl(value, default):
    try:
        seconds = int(value)
    except Exception:
        seconds = int(default)
    return max(300, min(172800, seconds))


def _parse_json(raw):
    text = str(raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except Exception:
            return None


_SERVICE = None
_SERVICE_LOCK = threading.Lock()


def get_recent_state_service():
    global _SERVICE
    if _SERVICE is None:
        with _SERVICE_LOCK:
            if _SERVICE is None:
                _SERVICE = RecentStateService()
    return _SERVICE
