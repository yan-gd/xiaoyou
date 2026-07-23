# -*- coding: utf-8 -*-
"""Governed long-term memory backed by a local SQLite database."""

from __future__ import annotations

import hashlib
import math
import os
import threading
from datetime import datetime, timezone

import plugins
from bridge.context import ContextType
from common.log import logger
from plugins import Event, EventContext, Plugin
from plugins.xiaoyou_common.context_planner import plan_context
from plugins.xiaoyou_common.embedding_gateway import embed_texts
from plugins.xiaoyou_common.long_memory_store import LongMemoryStore
from plugins.xiaoyou_common.memory_governance import MemoryGovernance
from plugins.xiaoyou_common.memory_schema import (
    display_name,
    normalize_allowed,
    normalize_memory_type,
)
from plugins.xiaoyou_common.runtime_paths import runtime_path
from plugins.xiaoyou_common.session_fifo import PerSessionFIFO
from plugins.xiaoyou_common.trace_service import trace_event


@plugins.register(
    name="LongTermMemory",
    desire_priority=900,
    hidden=False,
    desc="长期记忆：本地 SQLite 持久化与语义检索",
    version="1.0-local-sqlite",
    author="YOYO",
)
class LongTermMemory(Plugin):
    def __init__(self):
        super().__init__()
        self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context

        self.enabled = self._env_bool("LONG_MEMORY_ENABLED", True)
        self.user_id = os.getenv("LONG_MEMORY_USER_ID", "yoyo").strip() or "yoyo"
        self.max_results = self._bounded_int(
            os.getenv("LONG_MEMORY_MAX_RESULTS"),
            5,
            minimum=1,
            maximum=50,
        )
        self.min_similarity = self._unit_float(
            os.getenv("LONG_MEMORY_MIN_SIMILARITY", "0.30")
        )
        self.max_scan = self._bounded_int(
            os.getenv("LONG_MEMORY_MAX_SCAN"),
            2000,
            minimum=10,
            maximum=10000,
        )
        self.backfill_batch_size = self._bounded_int(
            os.getenv("LONG_MEMORY_BACKFILL_BATCH_SIZE"),
            30,
            minimum=1,
            maximum=500,
        )
        self.embedding_model = (
            os.getenv("LONG_MEMORY_EMBEDDING_MODEL", "text-embedding-v4").strip()
            or "text-embedding-v4"
        )
        self.embedding_dimensions = self._bounded_int(
            os.getenv("LONG_MEMORY_EMBEDDING_DIMENSIONS"),
            512,
            minimum=64,
            maximum=2048,
        )
        self.embedding_signature = "%s:%s" % (
            self.embedding_model,
            self.embedding_dimensions,
        )

        self.database_path = runtime_path(
            "long_memory",
            "memories.db",
            env_var="LONG_MEMORY_DB_PATH",
        )
        self.store = LongMemoryStore(
            self.database_path,
            timeout=self._bounded_int(
                os.getenv("LONG_MEMORY_SQLITE_TIMEOUT"),
                8,
                minimum=1,
                maximum=60,
            ),
        )
        self.governance_enabled = self._env_bool(
            "LONG_MEMORY_GOVERNANCE_ENABLED",
            True,
        )
        self.memory_governance = None
        if self.governance_enabled:
            try:
                self.memory_governance = MemoryGovernance(
                    writer=self._write_governed_candidate,
                )
            except Exception:
                logger.exception(
                    "[LongTermMemory] memory governance initialization failed"
                )

        self.memory_write_queue = PerSessionFIFO(
            self._process_memory_job,
            on_error=self._on_memory_job_error,
            thread_name_prefix="long-memory",
        )
        self._backfill_lock = threading.Lock()
        self._backfill_running = False
        imported = self.store.import_governance_ledger(
            runtime_path(
                "xiaoyou_memory",
                "memory_governance.json",
                env_var="MEMORY_GOVERNANCE_STATE_PATH",
            ),
            user_id=self.user_id,
        )
        logger.info(
            "[LongTermMemory] inited database=%s memories=%s imported=%s",
            self.database_path,
            self.store.count(user_id=self.user_id),
            imported,
        )
        self._schedule_embedding_backfill()

    def build_memory_context(
        self,
        query,
        max_results=None,
        retrieval_mode="normal",
        allowed_memory_types=None,
    ):
        if not self.enabled:
            return ""
        query = str(query or "").strip()
        if not query:
            return ""
        result_limit = self._bounded_int(
            max_results,
            self.max_results,
            minimum=1,
            maximum=50,
        )
        memories = self._search_memory(
            query,
            retrieval_mode=retrieval_mode,
            result_limit=result_limit,
            allowed_memory_types=allowed_memory_types,
        )
        return "\n".join(
            self._format_memory_line(memory)
            for memory in memories
            if memory
        )

    def _search_memory(
        self,
        query,
        *,
        retrieval_mode="normal",
        result_limit=None,
        allowed_memory_types=None,
    ):
        result_limit = self._bounded_int(
            result_limit,
            self.max_results,
            minimum=1,
            maximum=50,
        )
        allowed_types = normalize_allowed(allowed_memory_types)
        memories = self.store.list_memories(
            user_id=self.user_id,
            allowed_types=allowed_types,
            limit=self.max_scan,
        )
        if not memories:
            return []
        self._schedule_embedding_backfill()

        query_result = self._embed(
            [str(query or "").strip()],
            purpose="semantic_query",
        )
        if not query_result:
            logger.warning(
                "[LongTermMemory] semantic search skipped because query embedding failed"
            )
            return []
        query_vector = query_result[0]

        ranked = []
        for memory in memories:
            vector = memory.get("embedding")
            if str(memory.get("embedding_model") or "") != self.embedding_signature:
                continue
            if not self._compatible_vectors(query_vector, vector):
                continue
            similarity = self._cosine_similarity(query_vector, vector)
            if similarity < self.min_similarity:
                continue
            item = dict(memory)
            item["similarity_score"] = round(similarity, 6)
            item["retrieval_score"] = round(similarity, 6)
            ranked.append(item)

        ranked.sort(
            key=lambda memory: (
                float(memory.get("retrieval_score") or 0),
                float(memory.get("importance") or 0),
                int(memory.get("updated_at") or 0),
            ),
            reverse=True,
        )
        selected = ranked[:result_limit]
        logger.info(
            "[LongTermMemory] search candidates=%s indexed=%s selected=%s mode=%s",
            len(memories),
            len(ranked),
            [
                {
                    "id": str(item.get("memory_id") or "")[-10:],
                    "score": item.get("retrieval_score"),
                }
                for item in selected
            ],
            str(retrieval_mode or "normal")[:40],
        )
        return selected

    def _schedule_embedding_backfill(self):
        with self._backfill_lock:
            if self._backfill_running:
                return False
            self._backfill_running = True
        worker = threading.Thread(
            target=self._run_embedding_backfill,
            name="long-memory-backfill",
            daemon=True,
        )
        worker.start()
        return True

    def _run_embedding_backfill(self):
        indexed = 0
        try:
            while True:
                memories = self.store.list_memories(
                    user_id=self.user_id,
                    limit=self.max_scan,
                )
                missing = [
                    memory
                    for memory in memories
                    if not memory.get("embedding")
                    or str(memory.get("embedding_model") or "")
                    != self.embedding_signature
                ][: self.backfill_batch_size]
                if not missing:
                    break

                for start in range(0, len(missing), 10):
                    batch = missing[start : start + 10]
                    vectors = self._embed(
                        [memory.get("content", "") for memory in batch],
                        purpose="semantic_backfill",
                    )
                    if not vectors or len(vectors) != len(batch):
                        logger.warning(
                            "[LongTermMemory] background embedding backfill paused "
                            "indexed=%s remaining_batch=%s",
                            indexed,
                            len(batch),
                        )
                        return
                    for memory, vector in zip(batch, vectors):
                        if self.store.update_embedding(
                            memory.get("memory_id"),
                            vector,
                            self.embedding_signature,
                        ):
                            indexed += 1
                if len(missing) < self.backfill_batch_size:
                    break
        except Exception:
            logger.exception(
                "[LongTermMemory] background embedding backfill failed"
            )
        finally:
            with self._backfill_lock:
                self._backfill_running = False
            if indexed:
                logger.info(
                    "[LongTermMemory] background embedding backfill completed "
                    "indexed=%s",
                    indexed,
                )

    def _write_governed_candidate(
        self,
        *,
        candidate,
        trace_id="",
        input_id="",
        session_id="",
    ):
        if not isinstance(candidate, dict):
            return {"ok": False, "error": "invalid_candidate"}
        content = str(candidate.get("content") or "").strip()
        if not content:
            return {"ok": False, "error": "empty_candidate_content"}

        governed = dict(candidate)
        governed["memory_type"] = normalize_memory_type(
            governed.get("memory_type"),
            governed.get("category"),
        )
        vectors = self._embed(
            [content],
            purpose="semantic_index",
            session_id=session_id,
            trace_id=trace_id,
            input_id=input_id,
        )
        embedding = vectors[0] if vectors else None
        result = self.store.upsert(
            user_id=self.user_id,
            candidate=governed,
            embedding=embedding,
            embedding_model=self.embedding_signature if embedding else "",
        )
        if result.get("ok") and not embedding:
            self._schedule_embedding_backfill()
        if not result.get("ok"):
            if trace_id:
                trace_event(
                    "long_memory_recorded",
                    status="failed",
                    trace_id=trace_id,
                    input_id=input_id,
                    session_id=session_id,
                    attrs={
                        "component": "LongTermMemory",
                        "record_source": "governed_candidate",
                        "storage": "local_sqlite",
                        "error_kind": str(result.get("error") or "storage_error")[:80],
                    },
                )
            return result

        memory_id = str(result.get("memory_id") or "")
        logger.info(
            "[LongTermMemory] governed memory saved operation=%s key=%s indexed=%s",
            result.get("operation"),
            str(governed.get("memory_key") or "")[:120],
            bool(embedding),
        )
        if trace_id:
            trace_event(
                "long_memory_recorded",
                status="saved",
                trace_id=trace_id,
                input_id=input_id,
                session_id=session_id,
                memory_record_id=memory_id,
                attrs={
                    "component": "LongTermMemory",
                    "record_source": "governed_candidate",
                    "write_operation": str(result.get("operation") or "")[:40],
                    "storage": "local_sqlite",
                    "embedding_indexed": bool(embedding),
                    "memory_key": str(governed.get("memory_key") or "")[:120],
                },
            )
        return {
            "ok": True,
            # MemoryGovernance retains this compatibility field as an opaque
            # storage record id. It now points to a local SQLite row.
            "provider_memory_id": memory_id,
        }

    def _govern_memory_turn(
        self,
        user_text,
        *,
        assistant_text="",
        source_mode="user",
        delivery_complete=True,
        terminal_status="complete",
        completed_at=0,
        trace_id="",
        input_id="",
        session_id="",
    ):
        if self.memory_governance is None:
            return
        summary = self.memory_governance.process_turn(
            user_text=user_text,
            assistant_text=assistant_text,
            source_mode=source_mode,
            delivery_complete=delivery_complete,
            terminal_status=terminal_status,
            completed_at=completed_at,
            trace_id=trace_id,
            input_id=input_id,
            session_id=session_id,
        )
        logger.info(
            "[LongTermMemory] governance completed session=%s source_mode=%s "
            "extracted=%s eligible=%s written=%s failed=%s",
            str(session_id or "-")[:40],
            str(source_mode or "user")[:40],
            summary.get("extracted", 0),
            summary.get("eligible", 0),
            summary.get("written", 0),
            summary.get("failed", 0),
        )

    def _enqueue_memory_turn(
        self,
        *,
        user_text,
        assistant_text="",
        source_mode="user",
        delivery_complete=True,
        terminal_status="complete",
        completed_at=0,
        action_id="",
        source="",
        trace_id="",
        input_id="",
        session_id="",
    ):
        sequence = self.memory_write_queue.submit(
            session_id,
            {
                "user_text": str(user_text or ""),
                "assistant_text": str(assistant_text or ""),
                "source_mode": str(source_mode or "user"),
                "delivery_complete": bool(delivery_complete),
                "terminal_status": str(terminal_status or "complete"),
                "completed_at": int(completed_at or 0),
                "action_id": str(action_id or ""),
                "source": str(source or ""),
                "trace_id": str(trace_id or ""),
                "input_id": str(input_id or ""),
            },
        )
        logger.info(
            "[LongTermMemory] queued session=%s sequence=%s source_mode=%s",
            str(session_id or "-")[:40],
            sequence,
            str(source_mode or "user")[:40],
        )
        return sequence

    def _process_memory_job(self, session_id, queue_sequence, payload):
        del queue_sequence
        self._govern_memory_turn(
            (payload or {}).get("user_text", ""),
            assistant_text=(payload or {}).get("assistant_text", ""),
            source_mode=(payload or {}).get("source_mode", "user"),
            delivery_complete=bool(
                (payload or {}).get("delivery_complete", True)
            ),
            terminal_status=(payload or {}).get(
                "terminal_status",
                "complete",
            ),
            completed_at=(payload or {}).get("completed_at", 0),
            trace_id=(payload or {}).get("trace_id", ""),
            input_id=(payload or {}).get("input_id", ""),
            session_id=session_id,
        )

    def append_delivered_assistant_message(
        self,
        session_id,
        assistant_text,
        *,
        user_text="",
        source="",
        action_id="",
        trace_id="",
        input_id="",
        delivery_complete=True,
        terminal_status="complete",
        completed_at=0,
    ):
        """Queue only assistant text that the WeChat send path actually delivered."""
        session_id = str(session_id or "").strip()
        assistant_text = str(assistant_text or "").strip()
        action_id = str(action_id or "").strip()
        if not self.enabled or not session_id or not assistant_text:
            return 0
        delivery_input_id = (
            "delivery:" + action_id
            if action_id
            else "delivery:%s:%s" % (
                str(input_id or "")[:80],
                hashlib.sha256(
                    assistant_text.encode("utf-8")
                ).hexdigest()[:24],
            )
        )
        return self._enqueue_memory_turn(
            user_text=str(user_text or ""),
            assistant_text=assistant_text,
            source_mode="assistant_delivered",
            delivery_complete=bool(delivery_complete),
            terminal_status=str(terminal_status or "complete"),
            completed_at=int(completed_at or 0),
            action_id=action_id,
            source=str(source or "outbound"),
            trace_id=str(trace_id or ""),
            input_id=delivery_input_id,
            session_id=session_id,
        )

    @staticmethod
    def _on_memory_job_error(session_id, sequence, payload, error):
        del payload
        logger.error(
            "[LongTermMemory] queued write failed session=%s sequence=%s error=%s",
            str(session_id or "-")[:40],
            sequence,
            type(error).__name__,
            exc_info=(type(error), error, error.__traceback__),
        )

    def on_handle_context(self, e_context: EventContext):
        if not self.enabled:
            return
        context = e_context["context"]
        if context.type != ContextType.TEXT:
            return
        user_text = str(context.content or "").strip()
        if not user_text:
            return

        session_id = context.get("session_id", self.user_id)
        kwargs = getattr(context, "kwargs", {}) or {}
        kwargs["long_memory_user_text"] = user_text
        if (
            self.governance_enabled
            and self.memory_governance is not None
            and not kwargs.get("xiaoyou_skip_long_memory_write")
            and not kwargs.get("long_memory_governance_enqueued")
        ):
            kwargs["long_memory_governance_enqueued"] = True
            kwargs["long_memory_queue_sequence"] = self._enqueue_memory_turn(
                user_text=user_text,
                trace_id=kwargs.get("xiaoyou_trace_id", ""),
                input_id=kwargs.get("xiaoyou_input_id", ""),
                session_id=session_id,
            )

        kwargs["long_memory_context_ready"] = True
        kwargs["long_memory_context"] = ""
        plan = plan_context(
            user_text,
            kwargs.get("xiaoyou_input_messages") or [],
            thinking_enabled=False,
        )
        kwargs["xiaoyou_context_plan"] = plan.as_dict()
        context.kwargs = kwargs

        if not plan.use_long_memory:
            logger.info(
                "[LongTermMemory] retrieval skipped plan=%s reason=%s",
                plan.mode,
                plan.reason,
            )
            return

        memories = self._search_memory(
            user_text,
            retrieval_mode=plan.retrieval_mode,
            result_limit=plan.long_memory_max_results,
            allowed_memory_types=plan.allowed_memory_types,
        )
        if not memories:
            return
        memory_block = "\n".join(
            self._format_memory_line(memory)
            for memory in memories
            if memory
        )
        kwargs["long_memory_context"] = memory_block
        context.kwargs = kwargs
        context.content = (
            "以下是小悠与 YoYo 的本地长期记忆，包含 YoYo 的事实、小悠实际说过且值得保持一致的自我记忆，以及双方共同形成的关系经历。"
            "不要逐条复述，也不要提及数据库。\n"
            "严格区分记忆主体；小悠说过的话不能反向证明 YoYo 的事实。"
            "优先尊重有效期和事件发生时间；旧状态可能已经变化，不要把旧状态当成永久事实。\n"
            f"{memory_block}\n\n"
            f"YOYO 当前发来的微信消息：{user_text}"
        )
        logger.info(
            "[LongTermMemory] injected %s memories plan=%s mode=%s",
            len(memories),
            plan.mode,
            plan.retrieval_mode,
        )

    def _embed(
        self,
        texts,
        *,
        purpose,
        session_id="",
        trace_id="",
        input_id="",
    ):
        result = embed_texts(
            texts,
            component="LongTermMemory",
            purpose=purpose,
            model=self.embedding_model,
            dimensions=self.embedding_dimensions,
            session_id=session_id,
            trace_id=trace_id,
            input_id=input_id,
        )
        return result.vectors if result.ok else []

    @staticmethod
    def _format_memory_line(memory):
        content = str((memory or {}).get("content") or "").strip()
        subject = str((memory or {}).get("subject") or "user").strip().lower()
        subject_name = {
            "user": "YoYo",
            "xiaoyou": "小悠",
            "relationship": "双方关系",
        }.get(subject, "YoYo")
        memory_type = normalize_memory_type(
            (memory or {}).get("memory_type"),
            (memory or {}).get("category"),
        )
        occurred_at = str((memory or {}).get("occurred_at") or "").strip()
        valid_from = str((memory or {}).get("valid_from") or "").strip()
        valid_until = str((memory or {}).get("valid_until") or "").strip()
        timestamp = int(
            (memory or {}).get("updated_at")
            or (memory or {}).get("created_at")
            or 0
        )
        if timestamp:
            recorded_at = datetime.fromtimestamp(
                timestamp,
                timezone.utc,
            ).astimezone().strftime("%Y-%m-%d %H:%M")
        else:
            recorded_at = "时间未知"
        time_label = (
            "发生于：%s" % occurred_at
            if occurred_at
            else "记录于：%s" % recorded_at
        )
        validity = ""
        if valid_from or valid_until:
            validity = "[有效期：%s 至 %s]" % (
                valid_from or "未注明",
                valid_until or "仍有效",
            )
        return "- [主体：%s][%s][%s]%s %s" % (
            subject_name,
            display_name(memory_type),
            time_label,
            validity,
            content,
        )

    @staticmethod
    def _compatible_vectors(left, right):
        return (
            isinstance(left, (list, tuple))
            and isinstance(right, (list, tuple))
            and bool(left)
            and len(left) == len(right)
        )

    @staticmethod
    def _cosine_similarity(left, right):
        numerator = sum(a * b for a, b in zip(left, right))
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        denominator = left_norm * right_norm
        if denominator <= 0:
            return 0.0
        return max(-1.0, min(1.0, numerator / denominator))

    @staticmethod
    def _env_bool(key, default):
        raw = os.getenv(key)
        if raw is None:
            return bool(default)
        return raw.strip().lower() in ("1", "true", "yes", "on")

    @staticmethod
    def _bounded_int(value, default, *, minimum, maximum):
        try:
            parsed = int(default if value is None else value)
        except Exception:
            parsed = int(default)
        return max(minimum, min(maximum, parsed))

    @staticmethod
    def _unit_float(value):
        try:
            return max(0.0, min(1.0, float(value)))
        except Exception:
            return 0.0
