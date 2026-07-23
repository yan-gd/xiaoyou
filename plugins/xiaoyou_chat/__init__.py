# -*- coding:utf-8 -*-
import os
import re
import time
import uuid
import threading
from plugins.xiaoyou_common.thinking_config import build_thinking_payload
from plugins.xiaoyou_common.model_gateway import chat_completion
from plugins.xiaoyou_common.outbound_dispatcher import resolve_receiver, send_text
from plugins.xiaoyou_common.state_store import JsonStateStore
from plugins.xiaoyou_common.runtime_paths import appdata_root, runtime_path
from plugins.xiaoyou_common.conversation_coordinator import claim_action

import plugins
from plugins import *
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger
from plugins.xiaoyou_common.context_service import (
    build_character_context,
    extract_current_user_text,
    load_long_memory_context,
)
from plugins.xiaoyou_common.context_compiler import (
    PACK_MARKER,
    compile_context_pack,
)
from plugins.xiaoyou_common.context_planner import plan_context
from plugins.xiaoyou_common.conversation_messages import (
    build_chat_messages,
    prepare_native_history,
)
from plugins.xiaoyou_common.continuity_recovery import build_recovery_candidates
from plugins.xiaoyou_common.intent_fastpath import should_use_chat_thinking
from plugins.xiaoyou_common.recent_state_service import get_recent_state_service
from plugins.xiaoyou_common.selective_critic import SelectiveCritic
from plugins.xiaoyou_common.conversation_archive_service import (
    get_conversation_archive_service,
)


RECOVERY_FILE = runtime_path(
    "xiaoyou_chat",
    "recovery_state.json",
    env_var="XIAOYOU_RECOVERY_STATE_PATH",
    legacy_paths=(
        os.path.join(appdata_root(), "xiaoyou_recovery_state.json"),
        os.path.join(os.path.dirname(__file__), "recovery_state.json"),
        os.path.join(os.path.dirname(__file__), "xiaoyou_recovery_state.json"),
    ),
)
RECOVERY_BACKUP_FILE = RECOVERY_FILE + ".backup"
RECOVERY_STORE = JsonStateStore(
    RECOVERY_FILE,
    backup_path=RECOVERY_BACKUP_FILE,
    name="xiaoyou_recovery",
    default_factory=lambda: {"schema_version": 1, "sessions": {}},
)
RECOVERY_LOCK = threading.RLock()
RECOVERY_THREAD_STARTED = False


@plugins.register(
    name="XiaoyouChat",
    desc="Xiaoyou's own normal text chat handler",
    version="1.7-continuity-recovery",
    author="yoyo",
    desire_priority=-10000,
)
class XiaoyouChat(Plugin):
    def __init__(self):
        global RECOVERY_THREAD_STARTED
        super().__init__()
        self.recent_state = get_recent_state_service()
        self.selective_critic = SelectiveCritic()
        try:
            self.conversation_archive = get_conversation_archive_service()
        except Exception:
            self.conversation_archive = None
            logger.exception(
                "[XiaoyouChat] ConversationArchive unavailable; episodic recall disabled"
            )
        self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
        self.recovery_state = self._load_recovery_state()
        self._migrate_recovery_identity_state()
        self.recovery_runtime = {}

        if self._reconnect_enabled() and not RECOVERY_THREAD_STARTED:
            RECOVERY_THREAD_STARTED = True
            threading.Thread(
                target=self._recovery_loop,
                daemon=True,
                name="XiaoyouReconnect",
            ).start()

        logger.info(
            "[XiaoyouChat] inited reconnect_enabled=%s pending=%s",
            self._reconnect_enabled(),
            len(self.recovery_state.get("sessions", {})),
        )

    def on_handle_context(self, e_context: EventContext):
        if not self._enabled():
            return

        context = e_context["context"]

        if context.type != ContextType.TEXT:
            return

        kwargs = getattr(context, "kwargs", {}) or {}
        if kwargs.get("isgroup"):
            return

        raw_context = str(context.content or "").strip()
        if not raw_context:
            return

        current_text = self._extract_plain_user_text(raw_context)
        if not current_text:
            return

        if current_text.startswith("$"):
            logger.info("[XiaoyouChat] ignore legacy dollar command text=%r", current_text[:100])
            return

        session_id = str(kwargs.get("session_id") or kwargs.get("receiver") or "").strip()
        receiver = str(kwargs.get("receiver") or session_id or "").strip()

        # 任何新用户输入都会接管会话，尚未发送的旧重连任务立即作废。
        if session_id:
            self._cancel_recovery(session_id, reason="new_user_input")

        logger.info("[XiaoyouChat] handling normal text chat current_text=%r", self._log_safe_text(current_text))

        input_messages = kwargs.get("xiaoyou_input_messages") or []
        if not isinstance(input_messages, list):
            input_messages = []
        input_messages = [
            str(message or "").strip()
            for message in input_messages
            if str(message or "").strip()
        ]
        recent_state_context = ""
        if session_id:
            try:
                recent_state_context = self.recent_state.build_context(session_id)
            except Exception:
                logger.exception(
                    "[XiaoyouChat] failed to load RecentState session=%s",
                    session_id,
                )
        kwargs["xiaoyou_recent_state_context"] = recent_state_context

        context_plan = plan_context(
            current_text,
            input_messages,
            thinking_enabled=should_use_chat_thinking(current_text, input_messages),
        )
        kwargs["xiaoyou_context_plan"] = context_plan.as_dict()
        episodic_context = ""
        episodic_manifest = {
            "schema_version": 1,
            "episode_ids": [],
            "scores": [],
            "message_ids": [],
        }
        if (
            session_id
            and self.conversation_archive is not None
            and context_plan.use_episodic_memory
        ):
            try:
                episodic_context, episodic_manifest = (
                    self.conversation_archive.build_episodic_context(
                        session_id,
                        "\n".join(input_messages) or current_text,
                        mode=context_plan.mode,
                        max_results=context_plan.episodic_max_results,
                    )
                )
            except Exception:
                logger.exception(
                    "[XiaoyouChat] episodic retrieval failed session=%s",
                    session_id,
                )
        kwargs["xiaoyou_episodic_context_ready"] = True
        kwargs["xiaoyou_episodic_context"] = episodic_context
        kwargs["xiaoyou_episodic_manifest"] = episodic_manifest

        context_pack = self._compile_context_pack(
            raw_context=raw_context,
            current_text=current_text,
            input_messages=input_messages,
            kwargs=kwargs,
            plan=context_plan,
        )
        kwargs["xiaoyou_context_pack_manifest"] = context_pack.manifest
        visible_inputs = input_messages or [current_text]
        native_history = prepare_native_history(
            kwargs.get("short_memory_native_history") or [],
            current_inputs=visible_inputs,
        )
        kwargs["xiaoyou_native_history_manifest"] = {
            "schema_version": 1,
            "messages": len(native_history),
            "roles": [message.get("role") for message in native_history],
        }
        logger.info(
            "[XiaoyouChat] native history prepared messages=%s",
            len(native_history),
        )
        context.kwargs = kwargs

        reply, error = self._ask_llm_result(
            context_pack.rendered,
            current_text,
            input_messages,
            native_history=native_history,
        )

        if error == "content_inspection":
            retry_reply, retry_error, recovery_manifest = (
                self._recover_with_continuity(
                    current_text=current_text,
                    input_messages=input_messages,
                    native_history=native_history,
                    context_plan=context_plan,
                )
            )
            kwargs["xiaoyou_content_recovery_manifest"] = recovery_manifest
            context.kwargs = kwargs

            if retry_reply:
                reply = retry_reply
                error = ""
                logger.info(
                    "[XiaoyouChat] recovered from provider rejection with continuity session=%s mode=%s history=%s",
                    session_id,
                    recovery_manifest.get("mode", "unknown"),
                    recovery_manifest.get("history_messages", 0),
                )
            else:
                if retry_error == "content_inspection":
                    self._block_current_short_message(session_id, current_text)
                    self._block_archive_messages(
                        [kwargs.get("conversation_archive_current_message_id")],
                        reason="current_turn_data_inspection_failed",
                    )

                if session_id and receiver:
                    self._schedule_recovery(
                        session_id,
                        receiver,
                        channel=e_context["channel"],
                        context=context,
                    )

                logger.warning(
                    "[XiaoyouChat] current turn still blocked after continuity recovery; reconnect scheduled session=%s error=%s",
                    session_id,
                    retry_error or "empty",
                )

        if reply:
            reply, critic_manifest = self.selective_critic.review_if_needed(
                current_text=current_text,
                draft=reply,
                native_history=native_history,
                context_plan=kwargs.get("xiaoyou_context_plan"),
                context_pack=context_pack.rendered,
                recent_state=recent_state_context,
                session_id=session_id,
                trace_id=kwargs.get("xiaoyou_trace_id", ""),
                input_id=kwargs.get("xiaoyou_input_id", ""),
            )
            kwargs["xiaoyou_selective_critic_manifest"] = critic_manifest
            context.kwargs = kwargs
            logger.info(
                "[XiaoyouChat] selective critic status=%s risks=%s",
                critic_manifest.get("status", "unknown"),
                len(critic_manifest.get("risk_reasons", [])),
            )
            e_context["reply"] = Reply(ReplyType.TEXT, reply)
            e_context.action = EventAction.BREAK_PASS
            return

        logger.warning("[XiaoyouChat] no reply sent because llm failed and preset fallback is disabled")
        # Xiaoyou already owns this turn and may have scheduled a controlled
        # reconnect.  BREAK would make ChatChannel fall through to the legacy
        # ChatGPTBot, which emits a visible ``[ERROR]`` preset message.  A
        # pass-with-break keeps the current turn silent while the reconnect
        # worker continues normally.
        e_context.action = EventAction.BREAK_PASS

    def _enabled(self):
        return os.getenv("XIAOYOU_CHAT_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")

    def _ask_llm(self, raw_context, current_text, input_messages=None, native_history=None):
        reply, _ = self._ask_llm_result(
            raw_context,
            current_text,
            input_messages,
            native_history=native_history,
        )
        return reply

    def _ask_llm_result(
        self,
        raw_context,
        current_text,
        input_messages=None,
        purpose="XIAOYOU_CHAT",
        native_history=None,
    ):
        api_key = os.getenv("OPEN_AI_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            logger.warning("[XiaoyouChat] OPEN_AI_API_KEY missing")
            return "", "configuration"

        model = os.getenv("XIAOYOU_CHAT_MODEL") or os.getenv("MODEL") or "qwen3.7-plus"
        system_prompt = self._build_system_prompt()

        if purpose == "XIAOYOU_CHAT_RECOVERY":
            user_prompt = self._build_recovery_user_prompt(
                raw_context,
                current_text,
                input_messages,
            )
        else:
            user_prompt = self._build_user_prompt(raw_context, current_text, input_messages)

        thinking_payload = build_thinking_payload("XIAOYOU_CHAT")
        if not should_use_chat_thinking(current_text, input_messages):
            thinking_payload = {"enable_thinking": False}
            logger.info("[XiaoyouChat] adaptive thinking disabled for casual turn")

        payload = {
            "model": model,
            "messages": build_chat_messages(
                system_prompt,
                user_prompt,
                native_history=native_history,
            ),
            "temperature": float(os.getenv("XIAOYOU_CHAT_TEMPERATURE", os.getenv("TEMPERATURE", "0.75"))),
            "max_tokens": int(os.getenv("XIAOYOU_CHAT_MAX_TOKENS", "700")),
            **thinking_payload,
        }

        return self._request_payload(
            payload,
            purpose=purpose,
            timeout=int(os.getenv("XIAOYOU_CHAT_TIMEOUT", "45")),
        )

    def _build_system_prompt(self):
        character_context = build_character_context()
        return """%s

额外运行规则：
- 你正在微信里和 YoYo 日常聊天。
- 直接输出小悠要发给 YoYo 的微信内容。
- 不要 Markdown，不要标题，不要解释你的思考。
- 不要说自己是模型、系统、插件、接口或 AI。
- 不要把当前现实时间当成固定回复模板。
- 不要每次主动报时，除非 YoYo 明确问时间。
- API中的本轮用户原话和近期user/assistant消息是当前对话事实；它们与派生状态或长期记忆冲突时，以原话为准。
- 派生状态和长期记忆只用于补充理解，不能覆盖当前原话，也不能作为模仿小悠旧句式的范本。
- 换行只表示连续发送的微信消息；按完整语义自然换行，不为了形式强行拆句。
""" % (
            character_context,
        )

    def _compile_context_pack(
        self,
        *,
        raw_context,
        current_text,
        input_messages,
        kwargs,
        plan=None,
    ):
        kwargs = kwargs if isinstance(kwargs, dict) else {}
        plan = plan or plan_context(
            current_text,
            input_messages,
            thinking_enabled=should_use_chat_thinking(current_text, input_messages),
        )
        kwargs["xiaoyou_context_plan"] = plan.as_dict()
        structured_ready = bool(
            kwargs.get("short_memory_context_ready")
            or kwargs.get("long_memory_context_ready")
            or kwargs.get("xiaoyou_episodic_context_ready")
        )
        upstream_context = ""
        if not structured_ready:
            raw_context = str(raw_context or "").strip()
            if raw_context and raw_context != str(current_text or "").strip():
                upstream_context = raw_context

        if kwargs.get("short_memory_native_history_ready"):
            short_memory_context = kwargs.get("short_memory_summary_context", "")
        else:
            short_memory_context = kwargs.get("short_memory_context", "")

        pack = compile_context_pack(
            current_user_text=current_text,
            input_messages=input_messages,
            recent_state=kwargs.get("xiaoyou_recent_state_context", ""),
            short_memory=short_memory_context,
            episodic_memory=kwargs.get("xiaoyou_episodic_context", ""),
            long_memory=kwargs.get("long_memory_context", ""),
            upstream_context=upstream_context,
            max_chars=os.getenv("XIAOYOU_CONTEXT_MAX_CHARS", "7000"),
            max_tokens=plan.context_max_tokens,
            section_token_caps=plan.section_token_caps,
        )
        used = {
            item.get("name"): item.get("used_chars")
            for item in pack.manifest.get("sections", [])
        }
        logger.info(
            "[XiaoyouChat] context pack compiled chars=%s/%s tokens=%s/%s plan=%s sections=%s structured=%s",
            pack.total_chars,
            pack.max_chars,
            pack.total_tokens,
            pack.max_tokens,
            plan.mode,
            used,
            structured_ready,
        )
        return pack

    def _recover_with_continuity(
        self,
        *,
        current_text,
        input_messages,
        native_history,
        context_plan,
    ):
        """Retry a provider-rejected turn without destroying working memory.

        Content inspection applies to the complete request, so a rejection is
        not evidence that every injected message is unsafe.  Try progressively
        smaller, evidence-grounded views and keep the durable archive intact.
        """
        long_memory = ""
        if context_plan and context_plan.use_long_memory:
            long_memory = self._load_long_memory_context(
                query=current_text,
                retrieval_mode="recovery",
            )
        recovery_context = self._build_recovery_context(
            current_text,
            long_memory,
        )
        minimal_context = self._build_recovery_context(current_text, "")
        candidates = build_recovery_candidates(
            native_history,
            current_text,
            recent_limit=max(
                2,
                int(os.getenv("XIAOYOU_CONTENT_RECOVERY_HISTORY_MESSAGES", "12")),
            ),
        )
        maximum = max(
            2,
            int(os.getenv("XIAOYOU_CONTENT_RECOVERY_MAX_ATTEMPTS", "4")),
        )
        if len(candidates) > maximum:
            candidates = candidates[:maximum - 1] + [candidates[-1]]
        last_error = "content_inspection"
        attempted = 0

        for mode, history in candidates:
            attempted += 1
            attempt_context = (
                recovery_context if mode == "recent_exact" else minimal_context
            )
            reply, error = self._ask_llm_result(
                attempt_context,
                current_text,
                input_messages,
                purpose="XIAOYOU_CHAT_RECOVERY",
                native_history=history,
            )
            logger.info(
                "[XiaoyouChat] continuity recovery attempt=%s mode=%s history=%s ok=%s error=%s",
                attempted,
                mode,
                len(history),
                bool(reply),
                error or "-",
            )
            if reply:
                return reply, "", {
                    "schema_version": 1,
                    "mode": mode,
                    "history_messages": len(history),
                    "attempts": attempted,
                    "durable_history_preserved": True,
                }
            last_error = error or "empty"
            if error == "configuration":
                break

        return "", last_error, {
            "schema_version": 1,
            "mode": "failed",
            "history_messages": 0,
            "attempts": attempted,
            "durable_history_preserved": True,
        }

    def _block_archive_messages(self, message_ids, reason):
        if self.conversation_archive is None:
            return 0
        ids = [
            str(value or "").strip()
            for value in (message_ids or [])
            if str(value or "").strip()
        ]
        if not ids:
            return 0
        try:
            return int(
                self.conversation_archive.block_injected_messages(ids, reason=reason)
                or 0
            )
        except Exception:
            logger.exception("[XiaoyouChat] failed to isolate archive messages")
            return 0

    def _request_payload(self, payload, purpose, timeout):
        api_key = os.getenv("OPEN_AI_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
        result = chat_completion(
            component="XiaoyouChat",
            purpose=purpose,
            payload=payload,
            timeout=timeout,
            api_key=api_key,
        )
        if not result.ok:
            return "", result.error_kind

        cleaned = self._clean_reply(result.content)
        return cleaned, "" if cleaned else "empty"

    def _block_current_short_message(self, session_id, current_text):
        if not session_id or not current_text:
            return False
        try:
            manager = getattr(plugins, "instance", None)
            instances = getattr(manager, "instances", {}) if manager else {}
            short_memory = instances.get("SHORTMEMORY")
            block = getattr(short_memory, "block_latest_user_message", None)
            if callable(block):
                return bool(block(
                    session_id,
                    current_text,
                    reason="current_turn_data_inspection_failed",
                ))
        except Exception:
            logger.exception("[XiaoyouChat] failed to isolate current ShortMemory message")
        return False

    def _reconnect_enabled(self):
        return os.getenv("XIAOYOU_RECONNECT_ENABLED", "true").strip().lower() in (
            "1", "true", "yes", "on"
        )

    def _load_recovery_state(self):
        state = RECOVERY_STORE.load()
        if not isinstance(state, dict):
            state = {"schema_version": 1, "sessions": {}}

        state.setdefault("schema_version", 1)
        sessions = state.setdefault("sessions", {})
        if not isinstance(sessions, dict):
            state["sessions"] = {}
            sessions = state["sessions"]

        now = int(time.time())
        for job in sessions.values():
            if not isinstance(job, dict):
                continue
            if job.get("status") == "generating":
                job["status"] = "pending"
                job["due_at"] = min(int(job.get("due_at") or now), now)

        return state

    def _save_recovery_state_locked(self):
        return RECOVERY_STORE.save(self.recovery_state)

    def _migrate_recovery_identity_state(self):
        canonical = os.getenv("XIAOYOU_CANONICAL_SESSION_ID", "yoyo").strip() or "yoyo"
        legacy_ids = [
            value.strip()
            for value in os.getenv("XIAOYOU_LEGACY_SESSION_IDS", "").split(",")
            if value.strip() and value.strip() != canonical
        ]
        if not legacy_ids:
            return

        with RECOVERY_LOCK:
            sessions = self.recovery_state.setdefault("sessions", {})
            jobs = []
            if isinstance(sessions.get(canonical), dict):
                jobs.append(dict(sessions.get(canonical)))
            for legacy_id in legacy_ids:
                if isinstance(sessions.get(legacy_id), dict):
                    jobs.append(dict(sessions.get(legacy_id)))
            if not jobs:
                return

            job = max(
                jobs,
                key=lambda value: max(
                    int(value.get("created_at") or 0),
                    int(value.get("due_at") or 0),
                ),
            )
            job["session_id"] = canonical
            sessions[canonical] = job
            for legacy_id in legacy_ids:
                sessions.pop(legacy_id, None)
            self._save_recovery_state_locked()

        logger.info(
            "[XiaoyouChat] migrated reconnect state to canonical session=%s",
            canonical,
        )

    def _schedule_recovery(self, session_id, receiver, channel=None, context=None):
        if not self._reconnect_enabled():
            return ""

        session_id = str(session_id or "").strip()
        receiver = str(receiver or session_id or "").strip()
        if not session_id or not receiver:
            return ""

        delay = max(1, int(os.getenv("XIAOYOU_RECONNECT_DELAY_SECONDS", "20")))
        token = uuid.uuid4().hex
        now = int(time.time())
        context_kwargs = getattr(context, "kwargs", {}) or {}

        with RECOVERY_LOCK:
            sessions = self.recovery_state.setdefault("sessions", {})
            previous = sessions.get(session_id) or {}
            previous_token = previous.get("token")
            if previous_token:
                self.recovery_runtime.pop(previous_token, None)

            sessions[session_id] = {
                "token": token,
                "session_id": session_id,
                "receiver": receiver,
                "created_at": now,
                "due_at": now + delay,
                "attempts": 0,
                "status": "pending",
                "reason": "data_inspection_failed",
                "trace_id": str(context_kwargs.get("xiaoyou_trace_id") or "")[:80],
                "input_id": str(context_kwargs.get("xiaoyou_input_id") or "")[:80],
            }
            if channel is not None and context is not None:
                self.recovery_runtime[token] = {
                    "channel": channel,
                    "context": context,
                }
            self._save_recovery_state_locked()

        logger.warning(
            "[XiaoyouChat] reconnect scheduled session=%s delay=%ss",
            session_id,
            delay,
        )
        return token

    def _cancel_recovery(self, session_id, reason="cancelled"):
        session_id = str(session_id or "").strip()
        if not session_id:
            return False

        with RECOVERY_LOCK:
            sessions = self.recovery_state.setdefault("sessions", {})
            job = sessions.pop(session_id, None)
            if not job:
                return False
            self.recovery_runtime.pop(job.get("token"), None)
            self._save_recovery_state_locked()

        logger.info(
            "[XiaoyouChat] reconnect cancelled session=%s reason=%s",
            session_id,
            reason,
        )
        return True

    def _recovery_loop(self):
        while True:
            try:
                interval = max(
                    1,
                    int(os.getenv("XIAOYOU_RECONNECT_CHECK_INTERVAL", "3")),
                )
                time.sleep(interval)
                self._claim_and_run_due_recoveries()
            except Exception:
                logger.exception("[XiaoyouChat] reconnect loop failed")
                time.sleep(5)

    def _claim_and_run_due_recoveries(self):
        if not self._reconnect_enabled():
            return

        now = int(time.time())
        due_jobs = []

        with RECOVERY_LOCK:
            sessions = self.recovery_state.setdefault("sessions", {})
            for session_id, job in list(sessions.items()):
                if not isinstance(job, dict):
                    sessions.pop(session_id, None)
                    continue
                if job.get("status") != "pending":
                    continue
                if int(job.get("due_at") or 0) > now:
                    continue

                job["status"] = "generating"
                job["claimed_at"] = now
                job["attempts"] = int(job.get("attempts") or 0) + 1
                sessions[session_id] = job
                due_jobs.append(dict(job))

            if due_jobs:
                self._save_recovery_state_locked()

        for job in due_jobs:
            self._run_recovery_job(job)

    def _run_recovery_job(self, job):
        session_id = str(job.get("session_id") or "")
        receiver = resolve_receiver(session_id, job.get("receiver"))
        token = str(job.get("token") or "")
        lease = None
        receipt = None

        if not self._is_recovery_current(session_id, token):
            return

        if not receiver or receiver == session_id:
            self._reschedule_or_finish_recovery(session_id, token, "receiver_unavailable")
            return

        lease = claim_action(
            session_id,
            kind="reconnect",
            source="xiaoyou_reconnect",
            trace_id=job.get("trace_id", ""),
            input_id=job.get("input_id", ""),
            ttl_seconds=max(
                120,
                int(os.getenv("XIAOYOU_RECONNECT_TIMEOUT", "45")) + 60,
            ),
        )
        if not lease.accepted:
            self._defer_recovery_for_coordinator(session_id, token)
            logger.info(
                "[XiaoyouChat] coordinator deferred reconnect session=%s reason=%s",
                session_id,
                lease.reason,
            )
            return

        try:
            message, error = self._generate_reconnect_message()

            if not self._is_recovery_current(session_id, token) or not lease.current():
                return

            if message:
                receipt = self._send_reconnect_message(
                    session_id,
                    receiver,
                    token,
                    message,
                    lease,
                )
                if receipt.sent_text:
                    self._finish_recovery(session_id, token)
                    logger.info(
                        "[XiaoyouChat] reconnect sent session=%s attempts=%s",
                        session_id,
                        job.get("attempts"),
                    )
                    return

            if self._is_recovery_current(session_id, token):
                self._reschedule_or_finish_recovery(session_id, token, error or "empty")
        finally:
            if lease and lease.accepted and not lease.finished:
                if receipt is not None and receipt.delivered:
                    lease.complete(
                        delivered=True,
                        detail=receipt.error or "sent",
                    )
                else:
                    lease.cancel("reconnect_not_delivered")

    def _generate_reconnect_message(self):
        api_key = os.getenv("OPEN_AI_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            return "", "configuration"

        long_memory = self._load_long_memory_context(
            query="当前会话刚刚发生的事件，以及 YoYo 和小悠最近的关系状态",
            retrieval_mode="recovery",
        )
        prompt = """刚才你和 YoYo 正在进行的这一轮微信没有顺利接上。当前默认时间节点就是刚刚，不要在没有明确记忆时间支持时理解成昨晚、隔天或更早。

不要猜测、引用或复述被移除消息的具体内容，也不要编造长期记忆中没有的消失原因、地点、动作、人物或事件。不要提及模型、接口、审核、系统错误或重连机制。

请结合小悠的人格、当前时间以及下面可用的长期记忆，自主思考此刻怎样重新接上你们的关系，并只输出小悠实际要发送的微信内容。不要使用固定道歉或故障说明。

[可用的长期记忆]
%s""" % (long_memory if long_memory else "暂无")

        model = os.getenv("XIAOYOU_RECONNECT_MODEL") or os.getenv("XIAOYOU_CHAT_MODEL") or os.getenv("MODEL") or "qwen3.7-plus"
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": self._build_system_prompt()},
                {"role": "user", "content": prompt},
            ],
            "temperature": float(os.getenv("XIAOYOU_RECONNECT_TEMPERATURE", "0.85")),
            "max_tokens": int(os.getenv("XIAOYOU_RECONNECT_MAX_TOKENS", "400")),
            **build_thinking_payload("XIAOYOU_RECONNECT"),
        }
        return self._request_payload(
            payload,
            purpose="XIAOYOU_RECONNECT",
            timeout=int(os.getenv("XIAOYOU_RECONNECT_TIMEOUT", "45")),
        )

    def _load_long_memory_context(self, query=None, retrieval_mode="recovery"):
        query = str(query or "当前会话刚刚发生的事件，以及 YoYo 和小悠最近的关系状态").strip()
        return load_long_memory_context(
            query,
            max_results=max(0, int(os.getenv("XIAOYOU_RECONNECT_MEMORY_TOP_N", "10"))),
            retrieval_mode=retrieval_mode,
            component="XiaoyouChat",
        )

    def _send_reconnect_message(self, session_id, receiver, token, message, lease):
        parts = [
            part.strip()
            for part in re.split(r"\n+", str(message or "").strip())
            if part.strip()
        ]
        max_parts = max(1, int(os.getenv("XIAOYOU_RECONNECT_MAX_PARTS", "4")))
        delay = max(0.0, float(os.getenv("XIAOYOU_RECONNECT_PART_DELAY_SECONDS", "1.0")))
        return send_text(
            session_id=session_id,
            source="xiaoyou_reconnect",
            parts=parts[:max_parts],
            receiver=receiver,
            freshness_check=lambda: (
                lease.current() and self._is_recovery_current(session_id, token)
            ),
            delay_before_part=lambda index, _part: delay if index > 0 else 0.0,
            record_memory=True,
            lease_id=lease.token,
        )

    def _defer_recovery_for_coordinator(self, session_id, token):
        delay = max(
            3,
            int(os.getenv("XIAOYOU_RECONNECT_CHECK_INTERVAL", "3")),
        )
        with RECOVERY_LOCK:
            sessions = self.recovery_state.setdefault("sessions", {})
            job = sessions.get(session_id)
            if not isinstance(job, dict) or job.get("token") != token:
                return False
            job["status"] = "pending"
            job["due_at"] = int(time.time()) + delay
            job["attempts"] = max(0, int(job.get("attempts") or 0) - 1)
            sessions[session_id] = job
            return self._save_recovery_state_locked()

    def _is_recovery_current(self, session_id, token):
        with RECOVERY_LOCK:
            job = self.recovery_state.setdefault("sessions", {}).get(session_id)
            if not job or job.get("token") != token:
                return False
            runtime = self.recovery_runtime.get(token)

        if runtime:
            checker = getattr(runtime.get("channel"), "is_context_current", None)
            if callable(checker):
                try:
                    if not checker(runtime.get("context")):
                        self._cancel_recovery(session_id, reason="new_input_version")
                        return False
                except Exception:
                    logger.exception("[XiaoyouChat] failed to check reconnect input version")
                    return False
        return True

    def _finish_recovery(self, session_id, token):
        with RECOVERY_LOCK:
            sessions = self.recovery_state.setdefault("sessions", {})
            current = sessions.get(session_id)
            if not current or current.get("token") != token:
                return False
            sessions.pop(session_id, None)
            self.recovery_runtime.pop(token, None)
            self._save_recovery_state_locked()
        return True

    def _reschedule_or_finish_recovery(self, session_id, token, error):
        max_attempts = max(1, int(os.getenv("XIAOYOU_RECONNECT_MAX_ATTEMPTS", "3")))
        backoff = max(5, int(os.getenv("XIAOYOU_RECONNECT_BACKOFF_SECONDS", "45")))

        with RECOVERY_LOCK:
            sessions = self.recovery_state.setdefault("sessions", {})
            job = sessions.get(session_id)
            if not job or job.get("token") != token:
                return

            attempts = int(job.get("attempts") or 0)
            if attempts >= max_attempts:
                sessions.pop(session_id, None)
                self.recovery_runtime.pop(token, None)
                logger.warning(
                    "[XiaoyouChat] reconnect exhausted session=%s attempts=%s error=%s",
                    session_id,
                    attempts,
                    error,
                )
            else:
                delay = backoff * (2 ** max(0, attempts - 1))
                job["status"] = "pending"
                job["due_at"] = int(time.time()) + delay
                job["last_error"] = str(error or "")[:80]
                sessions[session_id] = job
                logger.warning(
                    "[XiaoyouChat] reconnect retry deferred session=%s attempts=%s delay=%ss error=%s",
                    session_id,
                    attempts,
                    delay,
                    error,
                )

            self._save_recovery_state_locked()

    def _build_recovery_context(self, current_text, long_memory):
        return """[当前会话的安全时间线事实]
- 这是当前正在进行的连续微信会话，不应在没有时间证据时解释成昨晚、隔天或很久以前。
- API中在本轮可见输入之前提供的user/assistant原话属于刚刚发生的真实对话，优先级高于旧状态和长期记忆。
- 当前话语若省略了对象或动作，应优先承接最近一条明确说出该对象或动作的YoYo原话，不要擅自切换场景。
- 小悠旧回复只代表她当时说过的话，不能用旧玩笑覆盖YoYo已经明确说出的现实事项。

[按语义与时间重新排序的长期记忆]
%s""" % (long_memory if long_memory else "本轮无需长期记忆",)

    def _build_recovery_user_prompt(self, recovery_context, current_text, input_messages=None):
        input_messages = [
            str(message or "").strip()
            for message in (input_messages or [])
            if str(message or "").strip()
        ]
        if len(input_messages) > 1:
            visible_input = "\n".join(
                "消息 %s：%s" % (index + 1, message)
                for index, message in enumerate(input_messages)
            )
        else:
            visible_input = str(current_text or "").strip()

        return """请根据下面提供的事实重新理解当前这一轮微信，并生成小悠此刻真正要发送的回复。

长期记忆只能作为有时间标记的事实背景。不要编造其中没有出现的消失原因、地点、动作、人物、时间或已经发生的事情；没有证据时就不要给出具体原因。除非时间标记明确支持，否则不要把当前连续对话说成昨晚或另一天。

你可以自主决定情绪、态度和表达方式，但只能输出实际微信内容，不要提及上下文缺失、系统、模型、审核、恢复或重连。

%s

[本轮可见输入]
%s""" % (
            str(recovery_context or "").strip(),
            visible_input,
        )

    def _build_user_prompt(self, raw_context, current_text, input_messages=None):
        raw_context = str(raw_context or "").strip()
        current_text = str(current_text or "").strip()
        if raw_context.startswith(PACK_MARKER):
            return raw_context
        input_messages = [
            str(message or "").strip()
            for message in (input_messages or [])
            if str(message or "").strip()
        ]

        if len(input_messages) > 1:
            ordered_messages = "\n".join(
                "消息 %s：%s" % (index + 1, message)
                for index, message in enumerate(input_messages)
            )
            return """YoYo 在你回复之前连续发来了下面这些消息。它们属于同一轮输入，后面的内容可能是在补充、修正或引导前面的意思。
请按发送顺序整体理解，然后只生成一次此刻最合适的回复。不要逐条机械作答，也不要提到消息合流机制。

[上游提供的记忆与上下文]
%s

[YoYo 本轮连续消息]
%s""" % (
                raw_context[:5000] if raw_context else "暂无",
                ordered_messages,
            )

        if raw_context and raw_context != current_text:
            return """下面是上游插件整理给你的上下文，只能作为参考，不要逐条复述，也不要说“根据记忆/根据上下文”。

[参考上下文]
%s

[YoYo 当前原话]
%s""" % (
                raw_context[:5000],
                current_text,
            )

        return """[YoYo 当前原话]
%s""" % current_text

    def _extract_plain_user_text(self, content):
        return extract_current_user_text(content)

    def _clean_reply(self, text):
        text = str(text or "").strip()
        if text.upper() == "NO_MESSAGE":
            return ""
        text = re.sub(r"^```(?:text)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip().strip('"“”')
        text = re.sub(r"^(小悠|Xiaoyou|AI|助手)[:：]\s*", "", text).strip()
        return text[:1200] if text else ""

    def _log_safe_text(self, text):
        text = str(text or "").replace("\n", " ")
        return text[:120]
