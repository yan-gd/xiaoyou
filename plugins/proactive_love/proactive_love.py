# -*- coding:utf-8 -*-
import os
import re
import json
import time
import random
import base64
import threading
from datetime import datetime

from plugins.xiaoyou_common.thinking_config import build_thinking_payload
from plugins.xiaoyou_common.model_gateway import chat_completion
from plugins.xiaoyou_common.outbound_dispatcher import (
    record_assistant_message,
    resolve_receiver,
    send_action,
)
from plugins.xiaoyou_common.state_store import JsonStateStore
from plugins.xiaoyou_common.conversation_coordinator import claim_action
from plugins.xiaoyou_common.inner_state_service import get_inner_state_service
from plugins.xiaoyou_common.proactive_decision_service import decide_proactive_action
from plugins.xiaoyou_common.relationship_profile_service import (
    get_relationship_profile_service,
)
import plugins
from plugins import *
from bridge.context import ContextType
from common.log import logger
from plugins.xiaoyou_common.context_service import (
    build_context_snapshot,
    extract_current_user_text,
)


DATA_FILE = os.path.join(os.path.dirname(__file__), "proactive_state.json")
STATE_STORE = JsonStateStore(DATA_FILE, name="proactive_love", default_factory=dict)
LOCK = threading.Lock()
THREAD_STARTED = False


@plugins.register(
    name="ProactiveLove",
    desc="Unified context-aware proactive consciousness for Xiaoyou",
    version="2.0-inner-state",
    author="yoyo",
    desire_priority=10,
)
class ProactiveLove(Plugin):
    def __init__(self):
        global THREAD_STARTED
        super().__init__()
        self.inner_state = get_inner_state_service()
        self.relationship_profile = get_relationship_profile_service()
        self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
        self._migrate_identity_state()
        logger.info("[ProactiveLove] inited")

        if not THREAD_STARTED:
            THREAD_STARTED = True
            t = threading.Thread(target=self._loop, daemon=True)
            t.start()
            logger.info("[ProactiveLove] background thread started")

    def on_handle_context(self, e_context: EventContext):
        return self.observe_user_context(e_context["context"])

    def observe_user_context(self, context):
        if not self._enabled():
            return False

        # 只记录私聊，不碰群聊
        kwargs = getattr(context, "kwargs", {}) or {}
        if kwargs.get("isgroup"):
            return False

        if context.type not in [
            ContextType.TEXT,
            ContextType.IMAGE,
            ContextType.VOICE,
        ]:
            return False

        session_id = self._get_session_id(context)
        receiver = self._get_receiver(context)

        if not session_id or not receiver:
            return False

        target_session = self._target_session()
        target_receiver = self._target_receiver()

        # 如果配置了唯一目标，只记录这个目标，避免把旧 session / 其他联系人写入主动消息候选池。
        if target_session and session_id != target_session:
            logger.info("[ProactiveLove] ignore non-target session activity session=%s target_session=%s", session_id, target_session)
            return False

        if target_receiver and receiver != target_receiver:
            logger.info("[ProactiveLove] ignore non-target receiver activity receiver=%s target_receiver=%s", receiver, target_receiver)
            return False

        text = ""
        if context.type == ContextType.TEXT:
            text = self._extract_plain_user_text(context.content)
        elif context.type == ContextType.IMAGE:
            text = "[用户发来了一张图片]"
        elif context.type == ContextType.VOICE:
            text = "[用户发来了一条语音]"

        now = int(time.time())
        input_id = str(kwargs.get("xiaoyou_input_id") or "")[:80]
        signature = "%s:%s" % (str(context.type), text[:500])

        with LOCK:
            data = self._load_all()
            item = data.get(session_id, {})
            if input_id and input_id == str(item.get("last_observed_input_id") or ""):
                return True
            if (
                not input_id
                and signature == str(item.get("last_user_signature") or "")
                and now - int(item.get("last_user_ts") or 0) <= 2
            ):
                return True
            item["session_id"] = session_id
            item["receiver"] = receiver
            item["last_user_ts"] = now
            item["last_user_text"] = text[:1200]
            item["next_evaluation_ts"] = 0
            item["conversation_revision"] = int(item.get("conversation_revision") or 0) + 1
            item["schedule_reason"] = "waiting_for_current_reply"
            item["trace_id"] = str(kwargs.get("xiaoyou_trace_id") or "")[:80]
            item["input_id"] = input_id
            item["last_observed_input_id"] = input_id
            item["last_user_signature"] = signature
            item.setdefault("last_proactive_ts", 0)
            item.setdefault("last_proactive_decision_ts", 0)
            item.setdefault("today", self._today())
            item.setdefault("sent_today", 0)

            # 换天重置次数
            if item.get("today") != self._today():
                item["today"] = self._today()
                item["sent_today"] = 0

            data[session_id] = item
            self._save_all(data)

        logger.info(
            "[ProactiveLove] observed user activity session=%s receiver=%s chars=%s",
            self._mask_session(session_id),
            self._mask_session(receiver),
            len(text),
        )
        return True

    def observe_assistant_reply(
        self,
        session_id,
        receiver,
        text,
        *,
        trace_id="",
        input_id="",
    ):
        """Receive a completed reply from ConversationFollowup's event hook.

        The affect update and next-evaluation planning run in a background
        thread, so normal replies never wait for the proactive system.
        """
        if not self._enabled() or not self._unified_enabled():
            return False
        session_id = str(session_id or "").strip()
        receiver = str(receiver or "").strip()
        text = str(text or "").strip()
        if not session_id or not receiver or not text:
            return False
        if self._target_session() and session_id != self._target_session():
            return False

        now = int(time.time())
        with LOCK:
            data = self._load_all()
            item = data.get(session_id, {})
            if (
                input_id
                and input_id == str(item.get("last_assistant_input_id") or "")
                and now - int(item.get("last_assistant_ts") or 0) < 120
            ):
                return True
            if (
                str(item.get("last_assistant_text") or "") == text
                and now - int(item.get("last_assistant_ts") or 0) < 120
            ):
                return True
            item["session_id"] = session_id
            item["receiver"] = receiver
            item["last_assistant_text"] = text[:1200]
            item["last_assistant_ts"] = now
            item["last_assistant_input_id"] = str(input_id or "")[:80]
            item["trace_id"] = str(trace_id or item.get("trace_id") or "")[:80]
            item["input_id"] = str(input_id or item.get("input_id") or "")[:80]
            item["conversation_revision"] = int(item.get("conversation_revision") or 0) + 1
            revision = item["conversation_revision"]
            item["next_evaluation_ts"] = 0
            item["schedule_reason"] = "updating_inner_state"
            data[session_id] = item
            self._save_all(data)
            snapshot = dict(item)

        threading.Thread(
            target=self._update_inner_state_and_schedule,
            args=(session_id, revision, snapshot),
            daemon=True,
        ).start()
        return True

    def _update_inner_state_and_schedule(self, session_id, revision, activity):
        try:
            result = self.inner_state.update_from_exchange(
                session_id,
                user_text=activity.get("last_user_text", ""),
                assistant_text=activity.get("last_assistant_text", ""),
                last_user_ts=activity.get("last_user_ts", 0),
            )
            delay = self.inner_state.normalize_delay(result.get("next_evaluation_seconds"))
            with LOCK:
                data = self._load_all()
                current = data.get(session_id, {})
                if int(current.get("conversation_revision") or 0) != int(revision):
                    logger.info(
                        "[ProactiveLove] inner-state schedule stale session=%s",
                        self._mask_session(session_id),
                    )
                    return
                current["next_evaluation_ts"] = int(time.time()) + delay
                current["schedule_reason"] = "model_chosen_after_exchange"
                data[session_id] = current
                self._save_all(data)
            logger.info(
                "[ProactiveLove] next autonomous evaluation session=%s delay=%ss",
                self._mask_session(session_id),
                delay,
            )
        except Exception:
            logger.exception("[ProactiveLove] inner-state update failed")

    def _loop(self):
        while True:
            try:
                interval = int(os.getenv("PROACTIVE_CHECK_INTERVAL", "60"))
                time.sleep(max(10, interval))
                self._check_and_send()
            except Exception:
                logger.exception("[ProactiveLove] loop error")
                time.sleep(60)

    def _check_and_send(self):
        if not self._enabled():
            return

        if not self._unified_enabled():
            return

        now = int(time.time())
        safety_min_interval = max(
            0,
            int(os.getenv("XIAOYOU_PROACTIVE_SAFETY_MIN_SEND_INTERVAL_SECONDS", "60")),
        )
        safety_max_per_day = max(
            1,
            int(os.getenv("XIAOYOU_PROACTIVE_SAFETY_MAX_PER_DAY", "20")),
        )

        with LOCK:
            data = self._load_all()

        if not data:
            return

        target_session = self._target_session()
        target_receiver = self._target_receiver()

        if self._require_target() and not target_session and not target_receiver:
            logger.warning("[ProactiveLove] PROACTIVE_REQUIRE_TARGET=true but no PROACTIVE_TARGET_SESSION/PROACTIVE_TARGET_RECEIVER configured, skip proactive send")
            return

        for session_id, item in list(data.items()):
            photo_share = None
            photo_recorded = False
            lease = None
            receipt = None
            try:
                receiver = resolve_receiver(session_id, item.get("receiver"))
                last_user_ts = int(item.get("last_user_ts") or 0)
                last_proactive_ts = int(item.get("last_proactive_ts") or 0)
                due_ts = int(item.get("next_evaluation_ts") or 0)
                source_revision = int(item.get("conversation_revision") or 0)
                calendar_key = self.relationship_profile.calendar_attention_key()
                calendar_due = bool(
                    calendar_key
                    and calendar_key != str(item.get("last_calendar_attention_key") or "")
                )

                if not receiver or not last_user_ts:
                    continue

                if target_session and session_id != target_session:
                    continue

                if target_receiver and receiver != target_receiver:
                    continue

                send_receiver = target_receiver or receiver

                if item.get("today") != self._today():
                    item["today"] = self._today()
                    item["sent_today"] = 0

                sent_today = int(item.get("sent_today") or 0)

                if not calendar_due and (due_ts <= 0 or now < due_ts):
                    continue
                if now - last_proactive_ts < safety_min_interval:
                    continue
                if sent_today >= safety_max_per_day:
                    continue

                lease = claim_action(
                    session_id,
                    kind="proactive",
                    source="proactive_love",
                    observed_user_ts=last_user_ts,
                    trace_id=item.get("trace_id", ""),
                    input_id=item.get("input_id", ""),
                    ttl_seconds=max(
                        300,
                        int(os.getenv("XIAOYOU_LIFE_PHOTO_TIMEOUT", "180")) + 120,
                    ),
                )
                if not lease.accepted:
                    logger.info(
                        "[ProactiveLove] coordinator deferred proactive action session=%s reason=%s",
                        session_id,
                        lease.reason,
                    )
                    continue

                # 先写入失败重试时间，避免进程并发或异常造成紧密重复调用。
                fallback_delay = self.inner_state.normalize_delay(
                    os.getenv("XIAOYOU_PROACTIVE_FAILURE_RETRY_SECONDS", "900")
                )
                item["last_proactive_decision_ts"] = now
                item["next_evaluation_ts"] = now + fallback_delay
                item["schedule_reason"] = "decision_in_progress"
                with LOCK:
                    fresh = self._load_all()
                    current = fresh.get(session_id, {})
                    current["last_proactive_decision_ts"] = now
                    current["next_evaluation_ts"] = now + fallback_delay
                    current["schedule_reason"] = "decision_in_progress"
                    fresh[session_id] = current
                    decision_persisted = self._save_all(fresh)

                if not decision_persisted:
                    logger.error(
                        "[ProactiveLove] proactive decision skipped because cooldown state was not persisted session=%s",
                        session_id,
                    )
                    continue

                inner_state = self.inner_state.get(session_id)
                decision = decide_proactive_action(
                    session_id=session_id,
                    activity=item,
                    inner_state=inner_state,
                    normalize_delay=self.inner_state.normalize_delay,
                )
                decision_at = int(time.time())
                with LOCK:
                    fresh = self._load_all()
                    current = fresh.get(session_id, {})
                    current["next_evaluation_ts"] = decision_at + decision.next_evaluation_seconds
                    current["schedule_reason"] = "model_chosen_after_decision"
                    current["last_decision"] = {
                        "ts": decision_at,
                        "action": decision.action,
                        "confidence": decision.confidence,
                        "reason": decision.reason[:300],
                    }
                    if calendar_due:
                        current["last_calendar_attention_key"] = calendar_key
                    fresh[session_id] = current
                    self._save_all(fresh)

                if decision.action == "none":
                    self.inner_state.apply_decision_feedback(
                        session_id,
                        action="none",
                        delivered=False,
                        deltas=decision.state_deltas,
                        reason=decision.reason,
                    )
                    lease.cancel("model_chose_silence")
                    logger.info(
                        "[ProactiveLove] model chose silence session=%s next=%ss calendar=%s",
                        self._mask_session(session_id),
                        decision.next_evaluation_seconds,
                        bool(calendar_due),
                    )
                    continue

                if decision.action == "photo":
                    photo_activity = dict(item)
                    photo_activity["proactive_intent"] = decision.photo_intent
                    photo_activity["inner_state"] = inner_state
                    photo_activity["decision_reason"] = decision.reason
                    photo_share = self._create_proactive_photo_share(session_id, photo_activity)
                    if not photo_share:
                        self.inner_state.apply_decision_feedback(
                            session_id,
                            action="photo",
                            delivered=False,
                            deltas=decision.state_deltas,
                            reason="photo generation unavailable: " + decision.reason,
                        )
                        lease.cancel("photo_generation_unavailable")
                        logger.warning(
                            "[ProactiveLove] real photo unavailable; no placeholder sent session=%s",
                            self._mask_session(session_id),
                        )
                        continue
                    parts = self._split_message(photo_share.get("caption", ""))
                else:
                    parts = self._split_message(decision.text)

                if not photo_share and not parts:
                    lease.cancel("empty_decision_content")
                    continue

                # 生成消息期间 YoYo 可能已经回来了；此时主动消息必须取消。
                with LOCK:
                    latest = self._load_all().get(session_id, {})

                if (
                    int(latest.get("last_user_ts") or 0) > last_user_ts
                    or int(latest.get("conversation_revision") or 0) != source_revision
                ):
                    if photo_share:
                        self._discard_proactive_photo(photo_share)
                    logger.info(
                        "[ProactiveLove] cancel stale proactive message session=%s, user replied during generation",
                        session_id,
                    )
                    continue

                logger.info(
                    "[ProactiveLove] sending proactive content receiver=%s session=%s photo=%s parts=%s",
                    send_receiver,
                    session_id,
                    bool(photo_share),
                    len(parts),
                )

                receipt = send_action(
                    session_id=session_id,
                    source="proactive_love",
                    image_path=(photo_share or {}).get("path", ""),
                    parts=parts,
                    receiver=send_receiver,
                    freshness_check=lambda: self._proactive_snapshot_current(
                        session_id, last_user_ts, source_revision
                    ) and lease.current(),
                    delay_before_part=lambda index, _part: (
                        random.uniform(0.8, 2.2) if index > 0 else 0.0
                    ),
                    image_to_text_delay=(
                        random.uniform(0.8, 1.8) if photo_share and parts else 0.0
                    ),
                    record_memory=not bool(photo_share),
                    lease_id=lease.token,
                )

                if photo_share and receipt.image_sent:
                    self._mark_proactive_photo_sent(session_id, photo_share)
                    photo_recorded = True

                if not receipt.delivered:
                    if photo_share and not photo_recorded:
                        self._discard_proactive_photo(photo_share)
                    logger.warning(
                        "[ProactiveLove] outbound action not delivered session=%s error=%s stale=%s",
                        session_id,
                        receipt.error,
                        receipt.stale,
                    )
                    continue

                if not receipt.ok:
                    logger.warning(
                        "[ProactiveLove] outbound action partially delivered session=%s action_id=%s error=%s",
                        session_id,
                        receipt.action_id,
                        receipt.error,
                    )

                now2 = int(time.time())
                sent_text = receipt.sent_text
                if photo_share and not sent_text:
                    sent_text = "[小悠分享了一张日常照片]"
                item["last_proactive_ts"] = now2
                item["sent_today"] = sent_today + 1
                item["today"] = self._today()
                item["recent_proactive_texts"] = self._append_recent_proactive(item, sent_text)

                with LOCK:
                    fresh = self._load_all()
                    current = fresh.get(session_id, {})
                    current["last_proactive_ts"] = now2
                    current["sent_today"] = sent_today + 1
                    current["today"] = self._today()
                    current["receiver"] = send_receiver
                    current["recent_proactive_texts"] = item["recent_proactive_texts"]
                    current["next_evaluation_ts"] = now2 + decision.next_evaluation_seconds
                    current["schedule_reason"] = "model_chosen_after_sent_action"
                    fresh[session_id] = current
                    if not self._save_all(fresh):
                        logger.error(
                            "[ProactiveLove] sent action state was not persisted session=%s action_id=%s",
                            session_id,
                            receipt.action_id,
                        )
                self.inner_state.apply_decision_feedback(
                    session_id,
                    action=decision.action,
                    delivered=receipt.delivered,
                    deltas=decision.state_deltas,
                    reason=decision.reason,
                )

            except Exception:
                if photo_share and not photo_recorded:
                    self._discard_proactive_photo(photo_share)
                logger.exception("[ProactiveLove] send failed session=%s", session_id)
            finally:
                if lease and lease.accepted and not lease.finished:
                    if receipt is not None and receipt.delivered:
                        lease.complete(
                            delivered=True,
                            detail=receipt.error or "sent",
                        )
                    else:
                        lease.cancel("proactive_not_delivered")

    def _generate_message(self, session_id, item):
        # 关闭 AI 生成时保持沉默，不使用固定模板。
        use_llm = os.getenv("PROACTIVE_USE_LLM", "true").strip().lower() in ("1", "true", "yes", "on")
        if not use_llm:
            return ""

        api_key = os.getenv("OPEN_AI_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
        model = os.getenv("PROACTIVE_MODEL") or os.getenv("MODEL") or "qwen3.7-plus"

        if not api_key:
            return ""

        last_text = item.get("last_user_text") or ""
        idle_minutes = max(1, int((int(time.time()) - int(item.get("last_user_ts", 0))) / 60))
        memory_query = self._build_memory_query(last_text)
        context_snapshot = build_context_snapshot(
            content=last_text,
            session_id=session_id,
            long_memory_query=memory_query,
            long_memory_max_results=int(os.getenv("PROACTIVE_MEMORY_TOP_N", "10")),
            include_short_memory=True,
            short_memory_max_chars=int(
                os.getenv("PROACTIVE_RECENT_CONTEXT_MAX_CHARS", "2200")
            ),
            component="ProactiveLove",
        )
        character_desc = context_snapshot.character_context
        memory_text = context_snapshot.long_memory
        recent_context = context_snapshot.short_memory
        recent_proactive = self._format_recent_proactive(item)

        prompt = f"""
请以小悠的身份自主判断：现在是否要主动给 YoYo 发微信，以及发什么。
不要套用预设话题、语气、句式或消息模板。完全依据小悠的人设、你们的关系、当前时间、长期记忆和最近聊天自行决定。

长期记忆：
{memory_text if memory_text else "暂无"}

最近聊天：
{recent_context if recent_context else "暂无"}

YoYo 上次说的是：{last_text if last_text else "没有记录"}
距离上次聊天约 {idle_minutes} 分钟。

最近主动发过的内容：
{recent_proactive if recent_proactive else "暂无"}

如果你认为此刻不该主动发送，只输出 NO_MESSAGE。
如果要发送，只输出小悠实际发送的微信内容，不要解释判断过程，也不要暴露隐藏上下文。
"""

        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": character_desc or "你是小悠。",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.9,
            "max_tokens": 400,
            **build_thinking_payload("PROACTIVE"),
        }

        result = chat_completion(
            component="ProactiveLove",
            purpose="proactive_message",
            payload=payload,
            timeout=int(os.getenv("PROACTIVE_LLM_TIMEOUT", "45")),
            api_key=api_key,
            session_id=session_id,
        )
        if not result.ok:
            return ""
        return self._clean_model_text(result.content.strip())

    def _create_proactive_photo_share(self, session_id, item):
        try:
            manager = getattr(plugins, "instance", None)
            instances = getattr(manager, "instances", {}) if manager else {}
            photo_plugin = instances.get("XIAOYOULIFEPHOTO")
            create = getattr(photo_plugin, "create_proactive_share", None)
            if callable(create):
                return create(session_id, item)
        except Exception:
            logger.exception("[ProactiveLove] proactive photo planning failed")
        return None

    def _mark_proactive_photo_sent(self, session_id, share):
        try:
            manager = getattr(plugins, "instance", None)
            instances = getattr(manager, "instances", {}) if manager else {}
            photo_plugin = instances.get("XIAOYOULIFEPHOTO")
            mark_sent = getattr(photo_plugin, "mark_proactive_sent", None)
            if callable(mark_sent):
                mark_sent(session_id, share)
                return
        except Exception:
            logger.exception("[ProactiveLove] failed to record proactive photo")

        text = str((share or {}).get("caption") or "").strip()
        record_assistant_message(
            session_id,
            text or "[小悠分享了一张日常照片]",
            source="proactive_love",
        )

    def _discard_proactive_photo(self, share):
        try:
            manager = getattr(plugins, "instance", None)
            instances = getattr(manager, "instances", {}) if manager else {}
            photo_plugin = instances.get("XIAOYOULIFEPHOTO")
            discard = getattr(photo_plugin, "discard_share", None)
            if callable(discard):
                discard(share)
        except Exception:
            logger.exception("[ProactiveLove] failed to discard stale proactive photo")

    def _build_memory_query(self, last_text):
        query = str(last_text or "").strip()
        if not query or query.startswith("[用户发来"):
            return "YoYo 最近的状态、计划、兴趣、约定，以及小悠适合继续关心的话题"
        return "YoYo 最近的状态、计划、兴趣、约定，以及和这句话有关的后续：" + query[:300]

    def _clean_model_text(self, text):
        text = str(text or "").strip()
        if text.upper() == "NO_MESSAGE":
            return ""

        text = re.sub(r"^```(?:text)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = re.sub(r"^(小悠|回复|微信|发送)[:：]\s*", "", text)
        text = text.strip("\"“”")

        banned = [
            "系统检测到",
            "根据记录",
            "根据记忆",
            "作为AI",
            "我是一个AI",
            "语言模型",
            "插件",
            "数据库",
        ]
        if any(marker in text for marker in banned):
            return ""

        return text

    def _split_message(self, text):
        text = str(text or "").strip()
        if not text:
            return []

        # 先按换行拆
        parts = [x.strip() for x in re.split(r"\n+", text) if x.strip()]

        # 如果没有换行，按句号问号感叹号拆
        if len(parts) <= 1:
            pieces = re.split(r"([。！？!?~～…]+)", text)
            parts = []
            buf = ""
            for p in pieces:
                if not p:
                    continue
                buf += p
                if re.match(r"^[。！？!?~～…]+$", p):
                    if buf.strip():
                        parts.append(buf.strip())
                    buf = ""
            if buf.strip():
                parts.append(buf.strip())

        parts = [p for p in parts if p.strip()]
        return parts

    def _append_recent_proactive(self, item, text):
        max_items = int(os.getenv("PROACTIVE_RECENT_TEXTS_MAX", "8"))
        recent = item.get("recent_proactive_texts") or []
        if not isinstance(recent, list):
            recent = []

        recent.append({
            "text": str(text or "").strip()[:300],
            "ts": int(time.time()),
        })

        return recent[-max_items:]

    def _format_recent_proactive(self, item):
        recent = item.get("recent_proactive_texts") or []
        if not isinstance(recent, list):
            return ""

        lines = []
        for entry in recent[-5:]:
            if isinstance(entry, dict):
                text = entry.get("text", "")
            else:
                text = str(entry)

            text = str(text or "").strip()
            if text:
                lines.append("- " + text)

        return "\n".join(lines)

    def _get_session_id(self, context):
        kwargs = getattr(context, "kwargs", {}) or {}
        return kwargs.get("session_id") or kwargs.get("receiver") or ""

    def _get_receiver(self, context):
        kwargs = getattr(context, "kwargs", {}) or {}
        receiver = kwargs.get("receiver")
        if receiver:
            return receiver

        msg = kwargs.get("msg")
        if msg is not None:
            return (
                getattr(msg, "from_user_id", None)
                or getattr(msg, "other_user_id", None)
                or getattr(msg, "to_user_id", None)
            )

        return self._get_session_id(context)

    def _extract_plain_user_text(self, content):
        return extract_current_user_text(content)

    def _target_session(self):
        return os.getenv("PROACTIVE_TARGET_SESSION", "").strip()

    def _proactive_snapshot_current(
        self,
        session_id,
        source_last_user_ts,
        source_revision=None,
    ):
        with LOCK:
            latest = self._load_all().get(session_id, {})
        if int(latest.get("last_user_ts") or 0) > int(source_last_user_ts or 0):
            return False
        if source_revision is not None and int(
            latest.get("conversation_revision") or 0
        ) != int(source_revision):
            return False
        return True

    def _target_receiver(self):
        return os.getenv("PROACTIVE_TARGET_RECEIVER", "").strip()

    def _require_target(self):
        return os.getenv("PROACTIVE_REQUIRE_TARGET", "true").strip().lower() in ("1", "true", "yes", "on")

    def _enabled(self):
        return os.getenv("PROACTIVE_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")

    def _unified_enabled(self):
        return os.getenv("XIAOYOU_UNIFIED_PROACTIVE_ENABLED", "true").strip().lower() in (
            "1", "true", "yes", "on"
        )

    def _mask_session(self, value):
        value = str(value or "")
        return value if len(value) <= 10 else value[:5] + "..." + value[-4:]

    def _today(self):
        return datetime.now().strftime("%Y-%m-%d")

    def _can_send_now(self):
        quiet_hours = os.getenv("PROACTIVE_QUIET_HOURS", "").strip()
        if quiet_hours and self._is_now_in_range(quiet_hours):
            logger.info("[ProactiveLove] in quiet hours, skip proactive send: %s", quiet_hours)
            return False

        # 可选白名单时间段。为空时全天允许，但仍会受 quiet_hours 限制。
        active_hours = os.getenv("PROACTIVE_ACTIVE_HOURS", "").strip()
        if active_hours and not self._is_now_in_range(active_hours):
            logger.info("[ProactiveLove] outside active hours, skip proactive send: %s", active_hours)
            return False

        return True

    def _is_now_in_range(self, hours):
        if not hours or "-" not in hours:
            return True

        try:
            start, end = hours.split("-", 1)
            now = datetime.now().time()
            sh, sm = self._parse_clock(start)
            eh, em = self._parse_clock(end)
            start_t = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
            end_t = now.replace(hour=eh, minute=em, second=0, microsecond=0)

            if start_t <= end_t:
                return start_t <= now <= end_t

            # 跨天，比如 22:00-02:00
            return now >= start_t or now <= end_t
        except Exception:
            logger.warning("[ProactiveLove] bad time range=%r", hours)
            return False

    def _parse_clock(self, value):
        value = str(value or "").strip()
        value = value.replace("：", ":").replace(".", ":")

        if ":" in value:
            hour, minute = value.split(":", 1)
        else:
            hour, minute = value, "0"

        hour = int(hour.strip())
        minute = int(minute.strip())

        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            raise ValueError("bad clock: %s" % value)

        return hour, minute

    def _load_all(self):
        data = STATE_STORE.load()
        return data if isinstance(data, dict) else {}

    def _migrate_identity_state(self):
        canonical = os.getenv("XIAOYOU_CANONICAL_SESSION_ID", "yoyo").strip() or "yoyo"
        legacy_ids = [
            value.strip()
            for value in os.getenv("XIAOYOU_LEGACY_SESSION_IDS", "").split(",")
            if value.strip() and value.strip() != canonical
        ]
        if not legacy_ids:
            return

        with LOCK:
            data = self._load_all()
            sources = []
            if isinstance(data.get(canonical), dict):
                sources.append(data.get(canonical))
            for legacy_id in legacy_ids:
                if isinstance(data.get(legacy_id), dict):
                    sources.append(data.get(legacy_id))
            if not sources:
                return

            data[canonical] = self._merge_identity_states(canonical, sources)
            for legacy_id in legacy_ids:
                data.pop(legacy_id, None)
            self._save_all(data)

        logger.info(
            "[ProactiveLove] migrated state to canonical session=%s sources=%s",
            canonical,
            len(sources),
        )

    def _merge_identity_states(self, canonical, sources):
        def freshness(item):
            return max(
                int(item.get("last_user_ts") or 0),
                int(item.get("last_proactive_ts") or 0),
                int(item.get("last_proactive_decision_ts") or 0),
            )

        base = dict(max(sources, key=freshness))
        base["session_id"] = canonical
        for key in ("last_user_ts", "last_proactive_ts", "last_proactive_decision_ts"):
            base[key] = max(int(item.get(key) or 0) for item in sources)

        today_sources = [item for item in sources if item.get("today") == self._today()]
        if today_sources:
            base["today"] = self._today()
            base["sent_today"] = max(int(item.get("sent_today") or 0) for item in today_sources)

        recent = []
        for item in sources:
            for record in item.get("recent_proactive_texts", []):
                if isinstance(record, dict):
                    recent.append(dict(record))
                elif str(record or "").strip():
                    recent.append({"text": str(record).strip(), "ts": 0})
        recent.sort(key=lambda value: int(value.get("ts") or 0))
        unique = []
        seen = set()
        for record in recent:
            signature = (str(record.get("text") or ""), int(record.get("ts") or 0))
            if signature in seen:
                continue
            seen.add(signature)
            unique.append(record)
        max_recent = max(1, int(os.getenv("PROACTIVE_RECENT_TEXTS_MAX", "8")))
        base["recent_proactive_texts"] = unique[-max_recent:]
        return base

    def _save_all(self, data):
        return STATE_STORE.save(data)
