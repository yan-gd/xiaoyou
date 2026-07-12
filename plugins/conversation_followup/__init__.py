# -*- coding: utf-8 -*-
"""
ConversationFollowup_v0.8-soft-presence

小悠 v1.3 聊天中断追问插件。

处理：
- 正在聊天
- 小悠刚回复或刚问了问题
- YoYo 突然没回
- 等一小段时间后，由模型自主判断要不要追问、怎么追问

设计原则：
- 不替代 ProactiveLove
- 不做长周期主动消息
- 不写死固定回复
- 不强行限制模型语气
- 工程层只做：目标会话、频率、连续追问、防系统泄露
"""

import os
import re
import json
import time
import threading
import traceback
from pathlib import Path
from datetime import datetime

from plugins.xiaoyou_common.thinking_config import build_thinking_payload
from plugins.xiaoyou_common.model_gateway import chat_completion
from plugins.xiaoyou_common.outbound_dispatcher import resolve_receiver, send_text
from plugins.xiaoyou_common.state_store import JsonStateStore
from plugins.xiaoyou_common.conversation_coordinator import claim_action
from plugins.xiaoyou_common.trace_service import ensure_trace

import plugins
from plugins import *
from bridge.context import ContextType
from bridge.reply import ReplyType

try:
    from plugins.xiaoyou_common.context_service import build_time_context
except Exception:
    def build_time_context():
        return ""


@plugins.register(
    name="ConversationFollowup",
    desire_priority=9998,
    hidden=False,
    desc="小悠聊天中断后的自然追问",
    version="0.8-soft-presence",
    author="YoYo"
)
class ConversationFollowup(Plugin):
    def __init__(self):
        super().__init__()

        self.enabled = self._env_bool("CONVERSATION_FOLLOWUP_ENABLED", True)

        self.check_interval = self._env_int("CONVERSATION_FOLLOWUP_CHECK_INTERVAL", 30)
        self.min_delay = self._env_int("CONVERSATION_FOLLOWUP_MIN_DELAY_SECONDS", 120)
        self.max_delay = self._env_int("CONVERSATION_FOLLOWUP_MAX_DELAY_SECONDS", 900)
        self.max_delay = max(self.min_delay, self.max_delay)

        self.max_per_day = self._env_int("CONVERSATION_FOLLOWUP_MAX_PER_DAY", 12)
        self.max_chain = self._env_int("CONVERSATION_FOLLOWUP_MAX_CHAIN", 1)
        self.soft_fallback_enabled = self._env_bool(
            "CONVERSATION_FOLLOWUP_SOFT_FALLBACK_ENABLED", True
        )
        self.soft_fallback_delay = self._env_int(
            "CONVERSATION_FOLLOWUP_SOFT_FALLBACK_DELAY_SECONDS", 240
        )

        # 这里只作为模型参考，不做硬拦截
        self.quiet_hours = os.getenv("CONVERSATION_FOLLOWUP_QUIET_HOURS", "02:30-08:00").strip()

        self.target_session = os.getenv("CONVERSATION_FOLLOWUP_TARGET_SESSION", "").strip()
        self.require_target = self._env_bool("CONVERSATION_FOLLOWUP_REQUIRE_TARGET", True)

        self.model = os.getenv("CONVERSATION_FOLLOWUP_MODEL", os.getenv("MODEL", "qwen3.7-plus"))
        self.classify_model = os.getenv("CONVERSATION_FOLLOWUP_CLASSIFY_MODEL", self.model)
        self.classify_timeout = self._env_int("CONVERSATION_FOLLOWUP_CLASSIFY_TIMEOUT", 20)
        self.generate_timeout = self._env_int("CONVERSATION_FOLLOWUP_GENERATE_TIMEOUT", 30)

        self.api_base = os.getenv("OPEN_AI_API_BASE", "").rstrip("/")
        self.api_key = os.getenv("OPEN_AI_API_KEY", "")

        self.state_path = Path(__file__).with_name("followup_state.json")
        self.state_store = JsonStateStore(
            self.state_path,
            name="conversation_followup",
            default_factory=lambda: {
                "version": "ConversationFollowup_v0.2-stable-identity",
                "sessions": {},
            },
        )
        self.lock = threading.RLock()
        self.state = self._load_state()
        self._migrate_identity_state()

        self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context

        if hasattr(Event, "ON_DECORATE_REPLY"):
            self.handlers[Event.ON_DECORATE_REPLY] = self.on_decorate_reply

        if hasattr(Event, "ON_SEND_REPLY"):
            self.handlers[Event.ON_SEND_REPLY] = self.on_send_reply

        if self.enabled:
            t = threading.Thread(target=self._loop, daemon=True)
            t.start()

        logger.info(
            "[ConversationFollowup] ConversationFollowup_v0.8-soft-presence loaded "
            f"enabled={self.enabled} "
            f"target_required={self.require_target} "
            f"target_set={bool(self.target_session)} "
            f"classify_model={self.classify_model} "
            f"classify_timeout={self.classify_timeout}s"
        )

    # ============================================================
    # 事件入口
    # ============================================================

    def on_handle_context(self, e_context):
        """
        记录 YoYo 的消息。
        只要 YoYo 回来了，就取消上一轮 pending followup。
        """
        if not self.enabled:
            return

        context = self._event_get(e_context, "context")
        if not context:
            return

        session_id = self._get_session_id(context)
        receiver = self._get_receiver(context)

        if not self._session_allowed(session_id):
            return

        ctype = getattr(context, "type", None)

        if ctype == ContextType.TEXT:
            text = self._safe_text(getattr(context, "content", ""))

            if not text:
                return

            if self._looks_like_system_text(text):
                return

            cancelled_pending = False

            with self.lock:
                s = self._session_state(session_id)
                self._roll_day(s)
                if receiver:
                    s["receiver"] = receiver

                cancelled_pending = bool(s.get("pending"))
                if cancelled_pending and isinstance(s.get("last_classification"), dict):
                    s["last_classification"]["status"] = "cancelled_user_replied"

                s["last_user_ts"] = time.time()
                s["chain_count"] = 0
                s["pending"] = None

                self._append_history(s, "user", text)
                self._save_state_locked()

            if cancelled_pending:
                logger.info(
                    "[ConversationFollowup] pending cancelled by user reply "
                    f"session={self._mask_session(session_id)}"
                )

            logger.debug(
                "[ConversationFollowup] user message "
                f"session={self._mask_session(session_id)} text={text[:80]}"
            )

        elif self._is_image_context_type(ctype):
            with self.lock:
                s = self._session_state(session_id)
                self._roll_day(s)
                if receiver:
                    s["receiver"] = receiver

                s["last_image_ts"] = time.time()
                self._save_state_locked()

            logger.debug(
                "[ConversationFollowup] image context "
                f"session={self._mask_session(session_id)}"
            )

    def on_decorate_reply(self, e_context):
        self._observe_reply(e_context, source="decorate")

    def on_send_reply(self, e_context):
        self._observe_reply(e_context, source="send")

    # ============================================================
    # 观察小悠回复
    # ============================================================

    def _observe_reply(self, e_context, source="unknown"):
        """
        记录小悠发出的文字回复。
        注意：这里只记录和开后台线程，不在回复链路里同步等模型。
        否则 DashScope 一慢，小悠正常回复也会被卡住。
        """
        if not self.enabled:
            return

        context = self._event_get(e_context, "context")
        reply = self._event_get(e_context, "reply")

        if not context or not reply:
            return

        session_id = self._get_session_id(context)
        receiver = self._get_receiver(context)

        if not self._session_allowed(session_id):
            return

        rtype = getattr(reply, "type", None)

        if rtype != ReplyType.TEXT:
            return

        text = self._safe_text(getattr(reply, "content", ""))

        if not text:
            return

        if self._is_empty_or_hidden_reply(text):
            return

        now = time.time()
        context_kwargs = getattr(context, "kwargs", {}) or {}
        trace_id = str(context_kwargs.get("xiaoyou_trace_id") or "")
        input_id = str(context_kwargs.get("xiaoyou_input_id") or "")

        with self.lock:
            s = self._session_state(session_id)
            self._roll_day(s)
            if receiver:
                s["receiver"] = receiver

            # 防止 decorate/send 或 SplitReply 链路里重复观察同一条回复。
            # 原来 8 秒太短，模型一超时就会重复进来，所以放宽到 120 秒。
            last = s.get("history", [])[-1] if s.get("history") else None

            if (
                last
                and last.get("role") == "assistant"
                and last.get("content") == text
                and now - float(last.get("ts", 0)) < 120
            ):
                logger.debug(
                    "[ConversationFollowup] duplicate assistant reply ignored "
                    f"session={self._mask_session(session_id)} source={source}"
                )
                return

            s["last_bot_ts"] = now
            self._append_history(s, "assistant", text)

            if int(s.get("followups_today", 0)) >= self.max_per_day:
                s["pending"] = None
                self._save_state_locked()
                logger.info(
                    "[ConversationFollowup] skip max_per_day "
                    f"session={self._mask_session(session_id)}"
                )
                return

            if int(s.get("chain_count", 0)) >= self.max_chain:
                s["pending"] = None
                self._save_state_locked()
                logger.info(
                    "[ConversationFollowup] skip max_chain "
                    f"session={self._mask_session(session_id)}"
                )
                return

            history = list(s.get("history", []))
            last_user_ts = float(s.get("last_user_ts", 0))
            source_bot_ts = float(s.get("last_bot_ts", now))
            recent_image = now - float(s.get("last_image_ts", 0)) < 240

            self._save_state_locked()

        # 分类判断放到后台，绝不阻塞小悠当前回复
        t = threading.Thread(
            target=self._classify_and_set_pending,
            args=(
                session_id,
                history,
                text,
                source_bot_ts,
                last_user_ts,
                recent_image,
                source,
                trace_id,
                input_id,
            ),
            daemon=True,
        )
        t.start()

    def _classify_and_set_pending(
        self,
        session_id,
        history,
        text,
        source_bot_ts,
        last_user_ts,
        recent_image,
        source,
        trace_id="",
        input_id="",
    ):
        """
        后台判断是否需要挂追问任务。
        模型慢、超时、失败，都不能影响正常聊天回复。
        """
        ensure_trace(
            session_id=session_id,
            source="conversation_followup_classify",
            trace_id=trace_id,
            input_id=input_id,
        )
        try:
            decision = self._classify_followup(
                history=history,
                last_bot_text=text,
                idle_after_user=time.time() - last_user_ts if last_user_ts else 0,
                recent_image=recent_image,
            )
            decision, fallback_applied = self._apply_soft_fallback(decision)

            with self.lock:
                s = self._session_state(session_id)
                self._roll_day(s)
                s["last_classification"] = self._classification_snapshot(
                    decision,
                    status="evaluated",
                )

                # YoYo 已经回了，不追
                if float(s.get("last_user_ts", 0)) > source_bot_ts:
                    s["last_classification"]["status"] = "stale_user_replied"
                    self._save_state_locked()
                    logger.info(
                        "[ConversationFollowup] classify stale, user already replied "
                        f"session={self._mask_session(session_id)}"
                    )
                    return

                # 已经有更新的小悠回复了，不用拿旧回复挂追问
                if abs(float(s.get("last_bot_ts", 0)) - source_bot_ts) > 3:
                    s["last_classification"]["status"] = "stale_newer_bot_reply"
                    self._save_state_locked()
                    logger.info(
                        "[ConversationFollowup] classify stale, newer bot reply exists "
                        f"session={self._mask_session(session_id)}"
                    )
                    return

                if int(s.get("followups_today", 0)) >= self.max_per_day:
                    s["pending"] = None
                    s["last_classification"]["status"] = "blocked_max_per_day"
                    self._save_state_locked()
                    return

                if int(s.get("chain_count", 0)) >= self.max_chain:
                    s["pending"] = None
                    s["last_classification"]["status"] = "blocked_max_chain"
                    self._save_state_locked()
                    return

                if not decision.get("need_followup"):
                    s["pending"] = None
                    s["last_classification"]["status"] = "no_followup"
                    self._save_state_locked()

                    logger.info(
                        "[ConversationFollowup] decision says no followup "
                        f"session={self._mask_session(session_id)} "
                        f"reason={decision.get('reason', '')[:80]}"
                    )
                    return

                delay = decision.get("followup_delay_seconds")

                try:
                    delay = int(delay)
                except Exception:
                    s["pending"] = None
                    s["last_classification"]["status"] = "invalid_model_delay"
                    self._save_state_locked()
                    logger.warning(
                        "[ConversationFollowup] model returned invalid delay, skip followup "
                        f"session={self._mask_session(session_id)}"
                    )
                    return

                delay = max(self.min_delay, min(self.max_delay, delay))

                s["pending"] = {
                    "due_ts": time.time() + delay,
                    "intent": decision.get("followup_intent", ""),
                    "reason": decision.get("reason", ""),
                    "source_bot_ts": source_bot_ts,
                    "created_ts": time.time(),
                    "sent": False,
                    "trace_id": str(trace_id or "")[:80],
                    "input_id": str(input_id or "")[:80],
                }
                s["last_classification"]["status"] = (
                    "pending_soft_fallback" if fallback_applied else "pending"
                )
                s["last_classification"]["delay_seconds"] = delay
                s["last_classification"]["due_ts"] = s["pending"]["due_ts"]

                self._save_state_locked()

                logger.info(
                    "[ConversationFollowup] pending "
                    f"session={self._mask_session(session_id)} "
                    f"delay={delay}s "
                    f"source={source} fallback={fallback_applied} "
                    f"intent={s['pending'].get('intent', '')[:80]} "
                    f"reason={s['pending'].get('reason', '')[:80]}"
                )

        except Exception as exc:
            try:
                with self.lock:
                    s = self._session_state(session_id)
                    s["last_classification"] = {
                        "ts": time.time(),
                        "status": "error",
                        "reason": str(exc)[:160],
                    }
                    self._save_state_locked()
            except Exception:
                pass
            logger.error(
                "[ConversationFollowup] classify background error: "
                + traceback.format_exc()
            )

    # ============================================================
    # 定时检查
    # ============================================================

    def _loop(self):
        while True:
            try:
                time.sleep(max(5, self.check_interval))
                self._check_due_followups()
            except Exception:
                logger.error("[ConversationFollowup] loop error: " + traceback.format_exc())

    def _check_due_followups(self):
        if not self.enabled:
            return

        now = time.time()
        due_jobs = []

        with self.lock:
            for session_id, s in self.state.get("sessions", {}).items():
                if not self._session_allowed(session_id):
                    continue

                self._roll_day(s)

                pending = s.get("pending")

                if not pending:
                    continue

                if pending.get("sent"):
                    continue

                if int(s.get("followups_today", 0)) >= self.max_per_day:
                    s["pending"] = None
                    continue

                if int(s.get("chain_count", 0)) >= self.max_chain:
                    s["pending"] = None
                    continue

                # YoYo 已经回了，不追
                if float(s.get("last_user_ts", 0)) > float(pending.get("source_bot_ts", 0)):
                    s["pending"] = None
                    continue

                if now < float(pending.get("due_ts", 0)):
                    continue

                due_jobs.append((session_id, dict(s), dict(pending)))

            self._save_state_locked()

        for session_id, s, pending in due_jobs:
            self._send_followup(session_id, s, pending)

    def _send_followup(self, session_id, s, pending):
        history = list(s.get("history", []))
        last_user_ts = float(s.get("last_user_ts", time.time()))
        idle_seconds = max(0, int(time.time() - last_user_ts))

        lease = claim_action(
            session_id,
            kind="followup",
            source="conversation_followup",
            observed_user_ts=last_user_ts,
            ttl_seconds=max(60, self.generate_timeout + 60),
            trace_id=pending.get("trace_id", ""),
            input_id=pending.get("input_id", ""),
        )
        if not lease.accepted:
            logger.info(
                "[ConversationFollowup] coordinator deferred followup session=%s reason=%s",
                self._mask_session(session_id),
                lease.reason,
            )
            return

        try:
            text = self._generate_followup(
                history=history,
                pending=pending,
                idle_seconds=idle_seconds
            )
        except Exception:
            lease.cancel("generation_exception")
            raise

        text = self._clean_generated_text(text)

        if not text:
            lease.cancel("model_silent")
            logger.info(
                "[ConversationFollowup] empty followup "
                f"session={self._mask_session(session_id)}"
            )

            with self.lock:
                ss = self._session_state(session_id)
                current = ss.get("pending") or {}
                if float(current.get("source_bot_ts", 0)) == float(pending.get("source_bot_ts", 0)):
                    ss["pending"] = None
                self._save_state_locked()

            return

        # 生成追问期间 YoYo 可能已经回来，发送前必须再检查一次。
        # 否则模型刚生成完就补发，会显得像完全没看到他的新消息。
        if not self._pending_is_current(session_id, pending):
            lease.cancel("pending_stale")
            logger.info(
                "[ConversationFollowup] send cancelled, pending became stale "
                f"session={self._mask_session(session_id)}"
            )
            return

        receiver = self._resolve_receiver(session_id)
        if not receiver or receiver == session_id:
            lease.cancel("receiver_unavailable")
            logger.warning(
                "[ConversationFollowup] no temporary WeChat receiver; keep silent "
                f"session={self._mask_session(session_id)}"
            )
            ok = False
        else:
            receipt = send_text(
                session_id=session_id,
                source="conversation_followup",
                text=text,
                receiver=receiver,
                freshness_check=lambda: (
                    lease.current() and self._pending_is_current(session_id, pending)
                ),
                record_memory=True,
                lease_id=lease.token,
            )
            ok = receipt.ok
            lease.complete(
                delivered=receipt.delivered,
                detail=receipt.error or "sent",
            )

        with self.lock:
            ss = self._session_state(session_id)
            self._roll_day(ss)

            current = ss.get("pending") or {}
            if float(current.get("source_bot_ts", 0)) == float(pending.get("source_bot_ts", 0)):
                ss["pending"] = None

            if ok:
                ss["receiver"] = receiver
                ss["last_bot_ts"] = time.time()
                ss["last_followup_ts"] = time.time()
                ss["followups_today"] = int(ss.get("followups_today", 0)) + 1
                ss["chain_count"] = int(ss.get("chain_count", 0)) + 1

                self._append_history(ss, "assistant", text)

                logger.info(
                    "[ConversationFollowup] sent "
                    f"session={self._mask_session(session_id)} "
                    f"intent={pending.get('intent', '')[:80]} "
                    f"text={text}"
                )
            else:
                logger.warning(
                    "[ConversationFollowup] send failed "
                    f"session={self._mask_session(session_id)}"
                )

            self._save_state_locked()

    def _pending_is_current(self, session_id, pending):
        with self.lock:
            s = self._session_state(session_id)
            current = s.get("pending")

            if not current:
                return False

            source_bot_ts = float(pending.get("source_bot_ts", 0))

            if float(current.get("source_bot_ts", 0)) != source_bot_ts:
                return False

            if float(s.get("last_user_ts", 0)) > source_bot_ts:
                s["pending"] = None
                self._save_state_locked()
                return False

            return True

    # ============================================================
    # 模型：判断是否需要追问
    # ============================================================

    def _classify_followup(self, history, last_bot_text, idle_after_user, recent_image):
        if not self.api_base or not self.api_key:
            return self._no_followup_decision("classifier unavailable")

        compact = self._compact_history(history)

        prompt = f"""
请判断小悠是否应该在 YoYo 暂时没有继续回复后，再主动发一条消息。

不要套用预设场景或语气分类。请根据完整对话关系、上下文、气氛和当前时间自主判断。
默认把亲密关系中的短暂断聊理解为“稍后可以自然再关心一句”，不要因为小悠上一句已经完整、
问了问题、让 YoYo 去做某件事，或暂时“轮到对方回复”，就直接判定永远不追问。
只有出现明确的安静依据时才将 hard_silence 设为 true，例如：YoYo 明确要求别发消息、正在睡觉、
开会、考试、驾驶，或继续发消息会明显打扰或不安全。普通忙碌、吃饭、洗澡、暂时没回、对话自然
停顿都不是 hard_silence，仍可在几分钟后轻轻续一句。
如果适合，请自行决定等待多久，以及届时最自然的表达意图。表达意图使用自由文本，不需要匹配任何标签。

可选延迟范围：{self.min_delay} 到 {self.max_delay} 秒。

最近是否有图片上下文：{recent_image}
YoYo 在小悠回复前已经停顿约：{int(idle_after_user)} 秒
安静时段参考：{self.quiet_hours}

最近对话：
{compact}

小悠最后一句：
{last_bot_text}

只输出合法 JSON：
{{
  "need_followup": true,
  "followup_delay_seconds": 300,
  "followup_intent": "由你根据对话自由决定的表达意图",
  "hard_silence": false,
  "reason": "..."
}}
""".strip()

        raw = self._chat_completion(
            model=self.classify_model,
            messages=[
                {
                    "role": "system",
                    "content": "你只输出合法 JSON，不要输出 Markdown，不要解释。"
                },
                {
                    "role": "user",
                    "content": prompt
                },
            ],
            temperature=0.35,
            timeout=self.classify_timeout,
            purpose="classify",
        )

        data = self._parse_json(raw)

        if not isinstance(data, dict):
            logger.warning(
                "[ConversationFollowup] classify parse failed raw="
                + str(raw)[:300]
            )
            return self._no_followup_decision(
                "classifier timeout, http error, or invalid JSON"
            )

        intent = str(data.get("followup_intent", "")).strip()
        reason = str(data.get("reason", "")).strip()
        hard_silence = data.get("hard_silence", False)
        if isinstance(hard_silence, str):
            hard_silence = hard_silence.strip().lower() in ["true", "yes", "1", "y"]
        else:
            hard_silence = bool(hard_silence)

        need = data.get("need_followup", False)

        if isinstance(need, str):
            need = need.strip().lower() in ["true", "yes", "1", "y"]
        else:
            need = bool(need)

        delay = data.get("followup_delay_seconds")

        if need:
            try:
                delay = int(delay)
            except Exception:
                return self._no_followup_decision("classifier returned invalid delay")

            delay = max(self.min_delay, min(self.max_delay, delay))
        else:
            delay = None

        return {
            "need_followup": need,
            "followup_delay_seconds": delay,
            "followup_intent": intent[:300],
            "hard_silence": hard_silence,
            "reason": reason[:300],
        }

    def _no_followup_decision(self, reason):
        return {
            "need_followup": False,
            "followup_delay_seconds": None,
            "followup_intent": "",
            "hard_silence": False,
            # 分类请求失败或结果无效时无法可靠辨认睡眠、会议、驾驶等
            # 明确静默语境，因此不能套用主动跟进兜底。
            "allow_soft_fallback": False,
            "reason": str(reason or "")[:300],
        }

    def _apply_soft_fallback(self, decision):
        """模型没有发现明确打扰依据时，保留一次有上限的自然续聊机会。"""
        decision = dict(decision or {})
        if decision.get("need_followup"):
            return decision, False
        if (
            not self.soft_fallback_enabled
            or decision.get("hard_silence")
            or decision.get("allow_soft_fallback") is False
        ):
            return decision, False

        delay = max(
            self.min_delay,
            min(self.max_delay, int(self.soft_fallback_delay or 240)),
        )
        original_reason = str(decision.get("reason") or "模型未给出明确理由")[:180]
        decision.update({
            "need_followup": True,
            "followup_delay_seconds": delay,
            "followup_intent": (
                "结合最近对话自然续上一句关心、惦记或轻松互动；不要复述上一条，"
                "不要责怪 YoYo 没有回复"
            ),
            "hard_silence": False,
            "reason": "未发现明确安静依据，启用一次轻量续聊；原判断：" + original_reason,
        })
        return decision, True

    def _classification_snapshot(self, decision, status):
        return {
            "ts": time.time(),
            "status": status,
            "need_followup": bool(decision.get("need_followup")),
            "intent": str(decision.get("followup_intent", ""))[:300],
            "reason": str(decision.get("reason", ""))[:300],
        }

    # ============================================================
    # 模型：生成追问内容
    # ============================================================

    def _generate_followup(self, history, pending, idle_seconds):
        if not self.api_base or not self.api_key:
            return ""

        compact = self._compact_history(history)
        time_context = build_time_context()
        character_desc = os.getenv("CHARACTER_DESC", "").strip()

        prompt = f"""
你正在以小悠的身份和 YoYo 微信聊天。你刚刚已经发过一条消息，YoYo 暂时没有继续回复。

请完全依据小悠的人设、最近对话、当前时间和你刚才做出的追问判断，自主决定现在最自然的表达。
不要套用固定句式或预设语气。如果你最终认为此刻确实不该发送，只输出 NO_MESSAGE。

当前时间事实：
{time_context}

安静时段参考：
{self.quiet_hours}

追问判断：
intent={pending.get("intent", "")}
reason={pending.get("reason", "")}

YoYo 已经大约 {idle_seconds} 秒没回。

最近对话：
{compact}

只输出小悠实际要发送的微信内容，不要解释判断过程。
""".strip()

        system_content = character_desc or "你是小悠。"

        raw = self._chat_completion(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": system_content
                },
                {
                    "role": "user",
                    "content": prompt
                },
            ],
            temperature=0.85,
            timeout=self.generate_timeout,
            purpose="generate",
        )

        return raw or ""

    def _chat_completion(self, model, messages, temperature=0.7, timeout=30, purpose="unknown"):
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
            **build_thinking_payload(
                "CONVERSATION_FOLLOWUP_GENERATE"
                if purpose == "generate"
                else "CONVERSATION_FOLLOWUP_CLASSIFY"
            ),
        }

        result = chat_completion(
            component="ConversationFollowup",
            purpose=purpose,
            payload=payload,
            timeout=timeout,
            api_key=self.api_key,
            base_url=self.api_base,
        )
        if not result.ok:
            return ""
        return result.content.strip()

    # ============================================================
    # 状态文件
    # ============================================================

    def _load_state(self):
        data = self.state_store.load()
        if not isinstance(data, dict):
            data = {
                "version": "ConversationFollowup_v0.2-stable-identity",
                "sessions": {},
            }
        data.setdefault("version", "ConversationFollowup_v0.2-stable-identity")
        if not isinstance(data.get("sessions"), dict):
            data["sessions"] = {}
        return data

    def _migrate_identity_state(self):
        canonical = os.getenv("XIAOYOU_CANONICAL_SESSION_ID", "yoyo").strip() or "yoyo"
        legacy_ids = [
            value.strip()
            for value in os.getenv("XIAOYOU_LEGACY_SESSION_IDS", "").split(",")
            if value.strip() and value.strip() != canonical
        ]
        if not legacy_ids:
            return

        with self.lock:
            sessions = self.state.setdefault("sessions", {})
            sources = []
            if isinstance(sessions.get(canonical), dict):
                sources.append(sessions.get(canonical))
            for legacy_id in legacy_ids:
                if isinstance(sessions.get(legacy_id), dict):
                    sources.append(sessions.get(legacy_id))

            if not sources:
                return

            sessions[canonical] = self._merge_identity_session_states(sources)
            for legacy_id in legacy_ids:
                sessions.pop(legacy_id, None)
            self.state["version"] = "ConversationFollowup_v0.2-stable-identity"
            self._save_state_locked()

        logger.info(
            "[ConversationFollowup] migrated state to canonical session=%s sources=%s",
            self._mask_session(canonical),
            len(sources),
        )

    def _merge_identity_session_states(self, sources):
        def latest_value(item):
            return max(
                float(item.get("last_user_ts") or 0),
                float(item.get("last_bot_ts") or 0),
                float(item.get("last_followup_ts") or 0),
            )

        base = dict(max(sources, key=latest_value))
        history = []
        for item in sources:
            for record in item.get("history", []):
                if isinstance(record, dict):
                    history.append(dict(record))
        history.sort(key=lambda value: float(value.get("ts") or 0))

        unique_history = []
        seen = set()
        for record in history:
            signature = (
                str(record.get("role") or ""),
                str(record.get("content") or ""),
                float(record.get("ts") or 0),
            )
            if signature in seen:
                continue
            seen.add(signature)
            unique_history.append(record)
        base["history"] = unique_history[-16:]

        for key in ("last_user_ts", "last_bot_ts", "last_image_ts", "last_followup_ts"):
            base[key] = max(float(item.get(key) or 0) for item in sources)

        pendings = [item.get("pending") for item in sources if isinstance(item.get("pending"), dict)]
        base["pending"] = max(
            pendings,
            key=lambda value: float(value.get("due_ts") or value.get("source_bot_ts") or 0),
        ) if pendings else None

        classifications = [
            item.get("last_classification")
            for item in sources
            if isinstance(item.get("last_classification"), dict)
        ]
        base["last_classification"] = max(
            classifications,
            key=lambda value: float(value.get("ts") or 0),
        ) if classifications else None

        current_day = datetime.now().strftime("%Y-%m-%d")
        current_sources = [item for item in sources if item.get("day") == current_day]
        if current_sources:
            base["day"] = current_day
            base["followups_today"] = max(int(item.get("followups_today") or 0) for item in current_sources)
            base["chain_count"] = max(int(item.get("chain_count") or 0) for item in current_sources)

        receivers = [str(item.get("receiver") or "").strip() for item in sorted(sources, key=latest_value, reverse=True)]
        base["receiver"] = next((value for value in receivers if value), "")
        return base

    def _save_state_locked(self):
        return self.state_store.save(self.state)

    def _session_state(self, session_id):
        sessions = self.state.setdefault("sessions", {})
        s = sessions.setdefault(session_id, {})

        today = datetime.now().strftime("%Y-%m-%d")

        s.setdefault("day", today)
        s.setdefault("last_user_ts", 0)
        s.setdefault("last_bot_ts", 0)
        s.setdefault("last_image_ts", 0)
        s.setdefault("last_followup_ts", 0)
        s.setdefault("followups_today", 0)
        s.setdefault("chain_count", 0)
        s.setdefault("pending", None)
        s.setdefault("last_classification", None)
        s.setdefault("history", [])
        s.setdefault("receiver", "")

        return s

    def _roll_day(self, s):
        today = datetime.now().strftime("%Y-%m-%d")

        if s.get("day") != today:
            s["day"] = today
            s["followups_today"] = 0
            s["chain_count"] = 0

    def _append_history(self, s, role, content):
        content = self._safe_text(content)

        if not content:
            return

        s.setdefault("history", []).append({
            "role": role,
            "content": content[:700],
            "ts": time.time(),
        })

        # 保留最近 16 条，给模型判断气氛用
        s["history"] = s["history"][-16:]

    # ============================================================
    # 工具函数
    # ============================================================

    def _event_get(self, e_context, key, default=None):
        try:
            return e_context[key]
        except Exception:
            pass

        try:
            return e_context.get(key, default)
        except Exception:
            return default

    def _get_session_id(self, context):
        for key in ["session_id", "receiver", "from_user_id"]:
            try:
                v = context[key]

                if v:
                    return str(v)

            except Exception:
                pass

        kwargs = getattr(context, "kwargs", {}) or {}

        for key in ["session_id", "receiver", "from_user_id"]:
            v = kwargs.get(key)

            if v:
                return str(v)

        return ""

    def _get_receiver(self, context):
        kwargs = getattr(context, "kwargs", {}) or {}
        receiver = str(kwargs.get("receiver") or "").strip()
        if receiver:
            return receiver

        msg = kwargs.get("msg")
        if msg is not None:
            receiver = str(
                getattr(msg, "from_user_id", None)
                or getattr(msg, "other_user_id", None)
                or getattr(msg, "to_user_id", None)
                or ""
            ).strip()
            if receiver:
                return receiver

        try:
            receiver = str(context["receiver"] or "").strip()
        except Exception:
            receiver = ""
        return receiver

    def _resolve_receiver(self, session_id):
        with self.lock:
            fallback = str(self._session_state(session_id).get("receiver") or "").strip()
        if not fallback and str(session_id or "").startswith("@"):
            fallback = str(session_id)
        return resolve_receiver(session_id, fallback)

    def _session_allowed(self, session_id):
        if not session_id:
            return False

        if self.target_session:
            return session_id == self.target_session

        if self.require_target:
            return False

        return True

    def _safe_text(self, text):
        if text is None:
            return ""

        text = str(text).strip()

        # 保留换行，让小悠可以发短两句
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)

        return text.strip()

    def _is_empty_or_hidden_reply(self, text):
        if not text.strip():
            return True

        bad_markers = [
            "[系统",
            "以下是关于 YOYO 的长期记忆",
            "以下是关于YOYO的长期记忆",
            "长期记忆，带记录时间",
            "短期记忆",
            "SearchMemory",
            "AddMemory",
            "工具调用",
            "MCP",
        ]

        return any(m in text for m in bad_markers)

    def _looks_like_system_text(self, text):
        if not text:
            return False

        markers = [
            "以下是关于 YOYO 的长期记忆",
            "长期记忆，带记录时间",
            "短期记忆",
            "系统提示",
            "工具调用",
        ]

        return any(m in text for m in markers)

    def _is_image_context_type(self, ctype):
        try:
            if ctype == ContextType.IMAGE:
                return True
        except Exception:
            pass

        return "image" in str(ctype).lower()

    def _compact_history(self, history):
        lines = []

        for item in history[-12:]:
            role = item.get("role")
            content = self._safe_text(item.get("content", ""))[:260]

            if not content:
                continue

            name = "YoYo" if role == "user" else "小悠"
            lines.append(f"{name}: {content}")

        return "\n".join(lines)

    def _parse_json(self, raw):
        if not raw:
            return None

        raw = raw.strip()

        raw = re.sub(r"^```json\s*", "", raw)
        raw = re.sub(r"^```\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        try:
            return json.loads(raw)
        except Exception:
            pass

        m = re.search(r"\{.*\}", raw, re.S)

        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None

        return None

    def _clean_generated_text(self, text):
        text = self._safe_text(text)

        if not text:
            return ""

        text = text.strip("「」“”\"' ")

        normalized = re.sub(r"[\s_.-]+", "", text).upper()
        if normalized in ("NOMESSAGE", "NOREPLY", "SILENT", "保持沉默"):
            return ""

        text = re.sub(r"^(小悠[:：]\s*)", "", text)
        text = re.sub(r"^(回复[:：]\s*)", "", text)
        text = re.sub(r"^(发送[:：]\s*)", "", text)
        text = re.sub(r"^(微信[:：]\s*)", "", text)

        # 只拦明显系统泄露，不限制小悠表达风格
        banned = [
            "检测到你未回复",
            "检测到",
            "系统提示",
            "插件",
            "数据库",
            "规则要求",
            "作为AI",
            "我是一个AI",
            "语言模型",
            "根据上下文",
            "根据规则",
        ]

        if any(x in text for x in banned):
            return ""

        return text

    def _env_bool(self, key, default=False):
        v = os.getenv(key)

        if v is None:
            return default

        return str(v).strip().lower() in [
            "1",
            "true",
            "yes",
            "y",
            "on",
        ]

    def _env_int(self, key, default):
        try:
            return int(os.getenv(key, default))
        except Exception:
            return default

    def _mask_session(self, session_id):
        if not session_id:
            return ""

        if len(session_id) <= 12:
            return session_id

        return session_id[:6] + "***" + session_id[-6:]
