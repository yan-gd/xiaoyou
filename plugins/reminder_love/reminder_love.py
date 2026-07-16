# -*- coding:utf-8 -*-
import os
import re
import json
import time
import uuid
import random
import threading
from datetime import datetime, timedelta

from plugins.xiaoyou_common.thinking_config import build_thinking_payload
from plugins.xiaoyou_common.model_gateway import chat_completion
from plugins.xiaoyou_common.outbound_dispatcher import resolve_receiver, send_text
from plugins.xiaoyou_common.state_store import JsonStateStore
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


DATA_FILE = os.path.join(os.path.dirname(__file__), "reminders.json")
STATE_STORE = JsonStateStore(DATA_FILE, name="reminder_love", default_factory=dict)
LOCK = threading.Lock()
THREAD_STARTED = False


@plugins.register(
    name="ReminderLove",
    desc="Schedule reminders from natural Chinese text",
    version="0.7-trace-runtime",
    author="yoyo",
    desire_priority=35,
)
class ReminderLove(Plugin):
    def __init__(self):
        global THREAD_STARTED
        super().__init__()
        self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
        self._migrate_identity_state()
        logger.info("[ReminderLove] inited")

        if not THREAD_STARTED:
            THREAD_STARTED = True
            t = threading.Thread(target=self._loop, daemon=True)
            t.start()
            logger.info("[ReminderLove] background thread started")

    def on_handle_context(self, e_context: EventContext):
        if not self._enabled():
            return

        context = e_context["context"]
        if context.type != ContextType.TEXT:
            return

        kwargs = getattr(context, "kwargs", {}) or {}
        if kwargs.get("isgroup"):
            return

        text = str(context.content or "").strip()
        text = self._extract_actual_user_text(text)
        if not text:
            return

        session_id = self._get_session_id(context)
        receiver = self._get_receiver(context)

        if not session_id or not receiver:
            return

        # 查看提醒
        if self._is_list_cmd(text):
            reply = self._list_reminders(session_id)
            e_context["reply"] = Reply(ReplyType.TEXT, reply)
            e_context.action = EventAction.BREAK_PASS
            return

        # 取消提醒
        if self._is_cancel_cmd(text):
            reply = self._cancel_reminder(session_id, text)
            e_context["reply"] = Reply(ReplyType.TEXT, reply)
            e_context.action = EventAction.BREAK_PASS
            return

        # 普通聊天：如果刚刚触发过提醒，把提醒上下文塞给聊天模型
        # 提醒由统一发送器直接送达，主 CoW 回复上下文本身不知道这件事。
        if not self._has_reminder_intent(text):
            self._inject_recent_reminder_context(context, session_id, text)
            return

        parsed = self._parse_due_time(text)
        if not parsed:
            context.content = """[隐藏上下文]
YoYo 这句话看起来像是在让小悠设置提醒，但没有说清楚具体时间。
请你作为小悠自然追问他需要什么时候提醒，不要像客服，不要说系统或插件。

YoYo 当前消息：
%s
""" % text
            return

        due_dt, span = parsed
        now = datetime.now()

        if due_dt <= now:
            context.content = """[隐藏上下文]
YoYo 想设置一个提醒，但解析出来的时间已经过去。
请你作为小悠自然告诉他换一个未来时间，不要像客服，不要说系统或插件。

YoYo 当前消息：
%s
""" % text
            return

        task = self._extract_task(text, span)
        if not task:
            task = "这件事"

        reminder = {
            "id": uuid.uuid4().hex[:8],
            "session_id": session_id,
            "receiver": receiver,
            "task": task,
            "original": text,
            "due_ts": int(due_dt.timestamp()),
            "due_text": due_dt.strftime("%Y-%m-%d %H:%M"),
            "status": "pending",
            "created_at": int(time.time()),
            "sent_at": 0,
            "trace_id": str(kwargs.get("xiaoyou_trace_id") or "")[:80],
            "input_id": str(kwargs.get("xiaoyou_input_id") or "")[:80],
        }

        if not self._add_reminder(session_id, reminder):
            logger.error(
                "[ReminderLove] reminder creation aborted because state was not persisted session=%s",
                session_id,
            )
            e_context.action = EventAction.BREAK
            return

        reply = self._generate_ack_message(reminder)

        if reply:
            e_context["reply"] = Reply(ReplyType.TEXT, reply)
            e_context.action = EventAction.BREAK_PASS
        else:
            context.content = """[隐藏上下文]
小悠已经成功帮 YoYo 创建了一个提醒。
提醒时间：%s
提醒事项：%s

请你作为小悠自然确认已经记好了。不要说系统、插件、定时任务。
YoYo 当前消息：
%s
""" % (reminder.get("due_text", ""), reminder.get("task", ""), text)
            return

    def _loop(self):
        while True:
            try:
                interval = int(os.getenv("REMINDER_CHECK_INTERVAL", "15"))
                time.sleep(max(5, interval))
                self._check_due()
            except Exception:
                logger.exception("[ReminderLove] loop error")
                time.sleep(30)

    def _check_due(self):
        if not self._enabled():
            return

        now_ts = int(time.time())
        due_items = []

        with LOCK:
            data = self._load_all()
            changed = False

            for session_id, items in data.items():
                for item in items:
                    if item.get("status") != "pending":
                        continue

                    due_ts = int(item.get("due_ts") or 0)
                    if due_ts and due_ts <= now_ts:
                        item["status"] = "sending"
                        due_items.append(item)
                        changed = True

            if changed:
                if not self._save_all(data):
                    logger.error(
                        "[ReminderLove] due reminders not sent because sending state was not persisted"
                    )
                    due_items = []

        for item in due_items:
            lease = None
            receipt = None
            try:
                session_id = item.get("session_id")
                receiver = resolve_receiver(session_id, item.get("receiver"))
                if not receiver:
                    self._mark_pending(session_id, item.get("id"))
                    logger.warning(
                        "[ReminderLove] no temporary WeChat receiver; reminder kept pending session=%s",
                        session_id,
                    )
                    continue
                if receiver != item.get("receiver"):
                    self._update_receiver(session_id, item.get("id"), receiver)
                    item["receiver"] = receiver

                lease = claim_action(
                    session_id,
                    kind="reminder",
                    source="reminder_love",
                    ttl_seconds=150,
                    trace_id=item.get("trace_id", ""),
                    input_id=item.get("input_id", ""),
                )
                if not lease.accepted:
                    self._mark_pending(session_id, item.get("id"))
                    logger.info(
                        "[ReminderLove] coordinator deferred reminder session=%s reason=%s",
                        session_id,
                        lease.reason,
                    )
                    continue

                msg = self._generate_reminder_message(item)
                parts = self._split_message(msg)

                if not parts:
                    logger.warning("[ReminderLove] no model-generated reminder text, keep pending: %r", item)
                    self._mark_pending(item.get("session_id"), item.get("id"))
                    continue

                logger.info("[ReminderLove] send reminder to %s: %r", receiver, parts)

                receipt = send_text(
                    session_id=session_id,
                    source="reminder_love",
                    parts=parts,
                    receiver=receiver,
                    delay_before_part=lambda index, _part: (
                        random.uniform(0.8, 1.8) if index > 0 else 0.0
                    ),
                    freshness_check=lease.current,
                    record_memory=True,
                    lease_id=lease.token,
                )
                if receipt.delivered:
                    self._mark_sent(session_id, item.get("id"), receipt.sent_text)
                    if not receipt.ok:
                        logger.warning(
                            "[ReminderLove] reminder partially delivered action_id=%s error=%s",
                            receipt.action_id,
                            receipt.error,
                        )
                else:
                    self._mark_pending(session_id, item.get("id"))

            except Exception:
                logger.exception("[ReminderLove] send reminder failed: %r", item)
                self._mark_pending(item.get("session_id"), item.get("id"))
            finally:
                if lease and lease.accepted and not lease.finished:
                    if receipt is not None and receipt.delivered:
                        lease.complete(
                            delivered=True,
                            detail=receipt.error or "sent",
                        )
                    else:
                        lease.cancel("reminder_not_delivered")

    def _generate_reminder_message(self, item):
        use_llm = os.getenv("REMINDER_USE_LLM", "true").strip().lower() in ("1", "true", "yes", "on")
        task = item.get("task") or "这件事"
        due_text = item.get("due_text") or ""

        if not use_llm:
            return ""

        api_key = os.getenv("OPEN_AI_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
        model = os.getenv("REMINDER_MODEL") or os.getenv("MODEL") or "qwen3.7-plus"

        if not api_key:
            return ""

        character_desc = build_character_context()
        memory_text = self._load_memory_text(item.get("session_id"), task)

        prompt = f"""
你是小悠，正在微信里提醒 YoYo 一件他之前让你记住的事。

这是你的人设：
{character_desc}

这是你记住的关于 YoYo 的信息：
{memory_text if memory_text else "暂无"}

提醒时间：{due_text}
提醒事项：{task}

现在时间到了，你要主动发微信提醒他。

要求：
1. 只能输出你要发给他的微信内容。
2. 像女朋友提醒，不要像闹钟或客服。
3. 1 到 3 句，短一点。
4. 可以温柔、撒娇、轻微吐槽。
5. 如果是起床提醒，可以催他起来，不要太客气。
6. 不要说“根据记录”“系统提醒”“定时任务触发”。
7. 不要冒充真人线下行为。
"""

        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            "temperature": 0.85,
            "max_tokens": 200,
            **build_thinking_payload("REMINDER"),
        }

        result = chat_completion(
            component="ReminderLove",
            purpose="due_message",
            payload=payload,
            timeout=60,
            api_key=api_key,
            session_id=item.get("session_id"),
        )
        if not result.ok:
            return ""
        return self._clean_model_text(result.content.strip()) or ""

    def _generate_ack_message(self, reminder):
        task = reminder.get("task") or "这件事"
        original = reminder.get("original") or ""
        friendly_time = self._friendly_due_text(int(reminder.get("due_ts") or 0))

        use_llm = os.getenv("REMINDER_ACK_USE_LLM", "true").strip().lower() in ("1", "true", "yes", "on")
        if not use_llm:
            return ""

        api_key = os.getenv("OPEN_AI_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
        model = os.getenv("REMINDER_MODEL") or os.getenv("MODEL") or "qwen3.7-plus"

        if not api_key:
            return ""

        character_desc = build_character_context()

        prompt = f"""
你是小悠，正在微信里回复 YoYo 的提醒请求。

这是你的人设：
{character_desc}

YoYo 刚刚说：
{original}

你已经成功创建提醒：
时间：{friendly_time}
事项：{task}

现在你要回复他“已经帮他记好了”。

要求：
1. 只能输出你要发给 YoYo 的微信内容。
2. 不要机械复述完整日期，比如不要说“07月07日15:05我会提醒你”。
3. 像女朋友一样自然确认，1 到 2 句。
4. 可以撒娇、轻微吐槽、催他别忘。
5. 如果是起床，就可以说“到时候我叫你起床”；如果是关水/关火/关灯，就说“到时候我凶你去关”。
6. 不要说“系统”“定时任务”“已创建提醒”。
"""

        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            "temperature": 0.85,
            "max_tokens": 120,
            **build_thinking_payload("REMINDER_ACK"),
        }

        result = chat_completion(
            component="ReminderLove",
            purpose="acknowledgement",
            payload=payload,
            timeout=60,
            api_key=api_key,
            session_id=reminder.get("session_id"),
        )
        if not result.ok:
            return ""
        return self._clean_model_text(result.content.strip()) or ""

    def _fallback_ack(self, task, friendly_time):
        task = task or "这件事"

        if "起床" in task or "叫醒" in task:
            return "好，%s我叫你起床。\n敢赖床你就完蛋了🙄" % friendly_time

        if "关水" in task or "关灯" in task or "关火" in task:
            return "好，%s我来凶你%s。\n别又迷迷糊糊忘了，听到没。" % (friendly_time, task)

        return "好，%s我提醒你%s。\n我记住啦，别到时候又装傻。" % (friendly_time, task)

    def _friendly_due_text(self, due_ts):
        if not due_ts:
            return "到时候"

        now = datetime.now()
        due = datetime.fromtimestamp(due_ts)
        delta = int(due.timestamp() - now.timestamp())

        if delta <= 90:
            return "一会儿"
        if delta < 3600:
            mins = max(1, round(delta / 60))
            return "%s分钟后" % mins
        if due.date() == now.date():
            return "今天%s" % due.strftime("%H:%M")
        if due.date() == (now + timedelta(days=1)).date():
            return "明天%s" % due.strftime("%H:%M")
        if due.date() == (now + timedelta(days=2)).date():
            return "后天%s" % due.strftime("%H:%M")
        return due.strftime("%m月%d日 %H:%M")

    def _inject_recent_reminder_context(self, context, session_id, user_text):
        window = int(os.getenv("REMINDER_FOLLOWUP_CONTEXT_SECONDS", "900"))
        recent = self._get_recent_sent_reminder(session_id, window)

        if not recent:
            return False

        task = recent.get("task") or "这件事"
        sent_text = recent.get("sent_text") or ""
        due_text = recent.get("due_text") or ""

        context.content = """[隐藏上下文]
小悠刚刚主动提醒过 YoYo 一件事。
提醒事项：%s
提醒时间：%s
小悠刚刚发出的提醒内容：%s

现在 YoYo 回复：
%s

请你自然接话。你知道 YoYo 说的“关啦/好了/知道了/起了/弄完了”等，是在回应刚刚这个提醒。
不要问“你关了什么”“你做了什么”。
不要提“隐藏上下文”“系统记录”“提醒事项”这些词。
继续保持小悠的微信女友语气。
""" % (task, due_text, sent_text, user_text)

        logger.info("[ReminderLove] injected recent reminder context task=%s user_text=%r", task, user_text[:50])
        return True

    def _get_recent_sent_reminder(self, session_id, window):
        now_ts = int(time.time())

        with LOCK:
            data = self._load_all()
            items = data.get(session_id, [])

        candidates = []
        for item in items:
            if item.get("status") != "sent":
                continue

            sent_at = int(item.get("sent_at") or 0)
            if not sent_at:
                continue

            if now_ts - sent_at <= window:
                candidates.append(item)

        if not candidates:
            return None

        candidates.sort(key=lambda x: int(x.get("sent_at") or 0), reverse=True)
        return candidates[0]

    def _parse_due_time(self, text):
        now = datetime.now()
        raw = text

        # 1. 相对时间：10秒后 / 5分钟后 / 2小时后 / 1天后
        m = re.search(r"(?P<num>\d+|[一二两三四五六七八九十两百]+)\s*(?P<unit>秒|分钟|分|小时|钟头|天)\s*后", raw)
        if m:
            num = self._to_int(m.group("num"))
            unit = m.group("unit")
            if num <= 0:
                return None

            if unit == "秒":
                due = now + timedelta(seconds=num)
            elif unit in ("分钟", "分"):
                due = now + timedelta(minutes=num)
            elif unit in ("小时", "钟头"):
                due = now + timedelta(hours=num)
            elif unit == "天":
                due = now + timedelta(days=num)
            else:
                return None

            return due.replace(second=0 if unit != "秒" else due.second, microsecond=0), m.span()

        # 2. 具体日期：7月8日9点 / 7月8号 09:30
        date_base = now.date()
        span_start = None

        dm = re.search(r"(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*[日号]?", raw)
        if dm:
            month = int(dm.group("month"))
            day = int(dm.group("day"))
            year = now.year
            try:
                date_base = datetime(year, month, day).date()
                if date_base < now.date():
                    date_base = datetime(year + 1, month, day).date()
                span_start = dm.start()
            except Exception:
                return None
        else:
            if "大后天" in raw:
                date_base = (now + timedelta(days=3)).date()
                span_start = raw.find("大后天")
            elif "后天" in raw:
                date_base = (now + timedelta(days=2)).date()
                span_start = raw.find("后天")
            elif "明天" in raw:
                date_base = (now + timedelta(days=1)).date()
                span_start = raw.find("明天")
            elif "明早" in raw:
                date_base = (now + timedelta(days=1)).date()
                span_start = raw.find("明早")
            elif "明晚" in raw:
                date_base = (now + timedelta(days=1)).date()
                span_start = raw.find("明晚")
            elif "今晚" in raw:
                date_base = now.date()
                span_start = raw.find("今晚")
            elif "今天" in raw:
                date_base = now.date()
                span_start = raw.find("今天")

        # 3. 具体时间：9点 / 9.0 / 9:30 / 下午3点 / 晚上8点半
        time_pattern = (
            r"(?P<period>凌晨|早上|上午|中午|下午|晚上|今晚|明早|明晚)?\s*"
            r"(?P<hour>\d{1,2}|[一二两三四五六七八九十]{1,3})"
            r"\s*(?:点|:|：|\.)?\s*"
            r"(?P<minute>\d{1,2}|[一二三四五六七八九十]{1,3})?"
            r"\s*(?P<half>半)?"
        )

        candidates = []
        for tm in re.finditer(time_pattern, raw):
            h_raw = tm.group("hour")
            hour = self._to_int(h_raw)
            minute_raw = tm.group("minute")
            minute = self._to_int(minute_raw) if minute_raw else 0

            # 避免把“我1天后”这种误判，这里相对时间前面已经处理过
            if hour < 0 or hour > 23 or minute < 0 or minute > 59:
                continue

            # 必须有明显时间标志：点、冒号、句点、半、早上下午晚上，或者前面有明天/今天
            seg = raw[tm.start():tm.end()]
            has_time_mark = any(x in seg for x in ["点", ":", "：", "."]) or tm.group("half") or tm.group("period") or span_start is not None
            if not has_time_mark:
                continue

            candidates.append(tm)

        if not candidates:
            return None

        # 优先选择出现在日期词后面的时间
        chosen = None
        if span_start is not None:
            for tm in candidates:
                if tm.start() >= span_start:
                    chosen = tm
                    break

        if chosen is None:
            chosen = candidates[0]

        period = chosen.group("period") or ""
        hour = self._to_int(chosen.group("hour"))
        minute_raw = chosen.group("minute")
        minute = self._to_int(minute_raw) if minute_raw else 0

        if chosen.group("half"):
            minute = 30

        if period in ("下午", "晚上", "今晚", "明晚") and 1 <= hour <= 11:
            hour += 12

        if period in ("凌晨",) and hour == 12:
            hour = 0

        if period in ("早上", "上午", "明早") and hour == 12:
            hour = 0

        if period == "中午" and 1 <= hour <= 10:
            hour += 12

        due = datetime.combine(date_base, datetime.min.time()).replace(
            hour=hour,
            minute=minute,
            second=0,
            microsecond=0,
        )

        # 没有指定今天/明天这种日期，只说“9点提醒我”，如果已经过了就默认明天
        if span_start is None and dm is None and due <= now:
            due = due + timedelta(days=1)

        if span_start is not None:
            span = (span_start, chosen.end())
        elif dm is not None:
            span = (dm.start(), chosen.end())
        else:
            span = chosen.span()

        return due, span

    def _extract_task(self, text, span):
        s = text

        try:
            a, b = span
            s = s[:a] + s[b:]
        except Exception:
            pass

        replacements = [
            "小悠",
            "悠悠",
            "你记得",
            "记得",
            "帮我",
            "给我",
            "到时候",
            "的时候",
            "提醒我",
            "提醒一下我",
            "提醒一下",
            "提醒",
            "叫我",
            "喊我",
            "叫醒我",
            "闹钟",
            "定个",
            "设个",
            "哦",
            "呀",
            "嘛",
            "哈",
        ]

        for r in replacements:
            s = s.replace(r, "")

        s = re.sub(r"[，。,.！!？?：:\s]+", "", s)
        s = re.sub(r"^我", "", s)

        if len(s) > 80:
            s = s[:80]

        return s.strip()

    def _extract_actual_user_text(self, text):
        return extract_current_user_text(text)

    def _has_reminder_intent(self, text):
        # 让模型判断“用户是否明确要求创建未来提醒/闹钟”
        # 失败时默认 False，避免误触发。
        use_llm = os.getenv("REMINDER_INTENT_USE_LLM", "true").strip().lower() in ("1", "true", "yes", "on")
        if not use_llm:
            return False

        user_text = self._extract_actual_user_text(text)
        if not user_text:
            return False

        api_key = os.getenv("OPEN_AI_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
        model = os.getenv("REMINDER_INTENT_MODEL") or os.getenv("MODEL") or "qwen3.7-plus"

        if not api_key:
            logger.warning("[ReminderLove] no api key for reminder intent judge, skip")
            return False

        prompt = f"""
你是一个意图分类器，只判断用户是否在明确要求创建一个未来提醒/闹钟。

只在以下情况回答 YES：
- 用户明确要求“提醒我/帮我提醒/到时候提醒/叫醒我/设个闹钟/定个提醒”等
- 且语义上是在让小悠未来某个时间点或一段时间后主动提醒他

以下情况必须回答 NO：
- 用户只是让小悠“记住”偏好、习惯、称呼、关系设定
- 用户只是询问提醒功能怎么实现、触发逻辑是什么
- 用户只是聊天里提到“提醒、闹钟、叫我”等词，但不是要创建提醒
- 用户说“你要记得我喜欢什么”“记住哦”这种属于长期记忆，不是提醒
- 用户没有明确让小悠在未来提醒他

只输出 YES 或 NO，不要解释。

用户消息：
{user_text}
"""

        payload = {
            "model": model,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "temperature": 0,
            "max_tokens": 8,
            **build_thinking_payload("REMINDER_INTENT"),
        }

        result = chat_completion(
            component="ReminderLove",
            purpose="intent_classification",
            payload=payload,
            timeout=20,
            api_key=api_key,
        )
        if not result.ok:
            return False
        ans = result.content.strip().upper()
        logger.info("[ReminderLove] intent judge text=%r ans=%r", user_text[:80], ans)
        return ans.startswith("YES")

    def _is_list_cmd(self, text):
        patterns = [
            r"^提醒列表$",
            r"^我的提醒$",
            r"^查看提醒$",
            r"^你要提醒我什么",
            r"^我有什么提醒",
        ]
        return any(re.search(p, text) for p in patterns)

    def _is_cancel_cmd(self, text):
        patterns = [
            r"^取消提醒",
            r"^删除提醒",
            r"^取消闹钟",
            r"^删除闹钟",
        ]
        return any(re.search(p, text) for p in patterns)

    def _list_reminders(self, session_id):
        items = self._get_items(session_id)
        pending = [x for x in items if x.get("status") == "pending"]

        if not pending:
            return "现在没有待提醒的事。"

        pending.sort(key=lambda x: x.get("due_ts", 0))

        lines = ["你现在有这些提醒："]
        for idx, item in enumerate(pending, 1):
            due = item.get("due_text", "")
            task = item.get("task", "")
            lines.append("%d. %s：%s" % (idx, due, task))

        return "\n".join(lines)

    def _cancel_reminder(self, session_id, text):
        items = self._get_items(session_id)
        pending = [x for x in items if x.get("status") == "pending"]

        if not pending:
            return "你现在没有待取消的提醒。"

        pending.sort(key=lambda x: x.get("due_ts", 0))

        m = re.search(r"(\d+)", text)
        if m:
            idx = int(m.group(1))
            if idx < 1 or idx > len(pending):
                return "没有第 %s 个提醒啦。" % idx
            target = pending[idx - 1]
        else:
            target = pending[-1]

        target_id = target.get("id")

        with LOCK:
            data = self._load_all()
            changed = False

            for item in data.get(session_id, []):
                if item.get("id") == target_id:
                    item["status"] = "cancelled"
                    changed = True

            if changed:
                self._save_all(data)

        return "好，我取消这个提醒了：%s" % target.get("task", "这件事")

    def _add_reminder(self, session_id, reminder):
        with LOCK:
            data = self._load_all()
            items = data.get(session_id, [])
            items.append(reminder)
            data[session_id] = items
            saved = self._save_all(data)

        if saved:
            logger.info("[ReminderLove] added reminder session=%s due=%s task=%s", session_id, reminder.get("due_text"), reminder.get("task"))
        return saved

    def _mark_sent(self, session_id, reminder_id, sent_text=""):
        with LOCK:
            data = self._load_all()
            for item in data.get(session_id, []):
                if item.get("id") == reminder_id:
                    item["status"] = "sent"
                    item["sent_at"] = int(time.time())
                    if sent_text:
                        item["sent_text"] = sent_text
            self._save_all(data)

    def _mark_pending(self, session_id, reminder_id):
        with LOCK:
            data = self._load_all()
            for item in data.get(session_id, []):
                if item.get("id") == reminder_id and item.get("status") == "sending":
                    item["status"] = "pending"
            self._save_all(data)

    def _update_receiver(self, session_id, reminder_id, receiver):
        with LOCK:
            data = self._load_all()
            for item in data.get(session_id, []):
                if item.get("id") == reminder_id:
                    item["receiver"] = receiver
            self._save_all(data)

    def _split_message(self, text):
        text = str(text or "").strip()
        if not text:
            return []

        parts = [x.strip() for x in re.split(r"\n+", text) if x.strip()]

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
        return parts[:3] if parts else [text]

    def _clean_model_text(self, text):
        text = str(text or "").strip()
        text = re.sub(r"^小悠[:：]\s*", "", text)
        text = text.strip("\"“”")
        return text[:300]

    def _load_memory_text(self, session_id, task=""):
        task = str(task or "").strip()
        query = "YoYo 与当前提醒事项有关的偏好、计划和约定"
        if task:
            query += "：" + task[:300]
        return load_long_memory_context(
            query,
            max_results=max(0, int(os.getenv("REMINDER_MEMORY_TOP_N", "20"))),
            component="ReminderLove",
        )

    def _to_int(self, value):
        if value is None:
            return 0

        value = str(value).strip()
        if not value:
            return 0

        if value.isdigit():
            return int(value)

        mp = {
            "零": 0,
            "一": 1,
            "二": 2,
            "两": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
            "十": 10,
        }

        if value in mp:
            return mp[value]

        if "十" in value:
            left, _, right = value.partition("十")
            tens = mp.get(left, 1) if left else 1
            ones = mp.get(right, 0) if right else 0
            return tens * 10 + ones

        return mp.get(value, 0)

    def _get_items(self, session_id):
        with LOCK:
            data = self._load_all()
            return data.get(session_id, [])

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

    def _enabled(self):
        return os.getenv("REMINDER_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")

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
            source_ids = [canonical] + legacy_ids
            reminders = []
            found = False
            for source_id in source_ids:
                items = data.get(source_id)
                if not isinstance(items, list):
                    continue
                found = True
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    migrated = dict(item)
                    migrated["session_id"] = canonical
                    reminders.append(migrated)

            if not found:
                return

            by_id = {}
            without_id = []
            for item in reminders:
                reminder_id = str(item.get("id") or "").strip()
                if not reminder_id:
                    without_id.append(item)
                    continue
                existing = by_id.get(reminder_id)
                if existing is None or self._reminder_freshness(item) >= self._reminder_freshness(existing):
                    by_id[reminder_id] = item

            merged = list(by_id.values()) + without_id
            merged.sort(key=lambda value: (int(value.get("due_ts") or 0), int(value.get("created_at") or 0)))
            data[canonical] = merged
            for legacy_id in legacy_ids:
                data.pop(legacy_id, None)
            self._save_all(data)

        logger.info(
            "[ReminderLove] migrated reminders to canonical session=%s count=%s",
            canonical,
            len(merged),
        )

    def _reminder_freshness(self, item):
        return max(
            int(item.get("sent_at") or 0),
            int(item.get("created_at") or 0),
            int(item.get("due_ts") or 0),
        )

    def _save_all(self, data):
        return STATE_STORE.save(data)
