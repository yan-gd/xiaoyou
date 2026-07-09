# -*- coding:utf-8 -*-
import os
import re
import json
import time
import random
import base64
import threading
from datetime import datetime

import requests
import plugins
from plugins import *
from bridge.context import ContextType
from common.log import logger
from plugins.xiaoyou_common.time_context import build_time_context
from lib import itchat


DATA_FILE = os.path.join(os.path.dirname(__file__), "proactive_state.json")
LOCK = threading.Lock()
THREAD_STARTED = False


@plugins.register(
    name="ProactiveLove",
    desc="Let Xiaoyou proactively message user after inactivity",
    version="0.2-target",
    author="yoyo",
    desire_priority=10,
)
class ProactiveLove(Plugin):
    def __init__(self):
        global THREAD_STARTED
        super().__init__()
        self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
        logger.info("[ProactiveLove] inited")

        if not THREAD_STARTED:
            THREAD_STARTED = True
            t = threading.Thread(target=self._loop, daemon=True)
            t.start()
            logger.info("[ProactiveLove] background thread started")

    def on_handle_context(self, e_context: EventContext):
        if not self._enabled():
            return

        context = e_context["context"]

        # 只记录私聊，不碰群聊
        kwargs = getattr(context, "kwargs", {}) or {}
        if kwargs.get("isgroup"):
            return

        if context.type not in [
            ContextType.TEXT,
            ContextType.IMAGE,
            ContextType.VOICE,
        ]:
            return

        session_id = self._get_session_id(context)
        receiver = self._get_receiver(context)

        if not session_id or not receiver:
            return

        target_session = self._target_session()
        target_receiver = self._target_receiver()

        # 如果配置了唯一目标，只记录这个目标，避免把旧 session / 其他联系人写入主动消息候选池。
        if target_session and session_id != target_session:
            logger.info("[ProactiveLove] ignore non-target session activity session=%s target_session=%s", session_id, target_session)
            return

        if target_receiver and receiver != target_receiver:
            logger.info("[ProactiveLove] ignore non-target receiver activity receiver=%s target_receiver=%s", receiver, target_receiver)
            return

        text = ""
        if context.type == ContextType.TEXT:
            text = self._extract_plain_user_text(context.content)
        elif context.type == ContextType.IMAGE:
            text = "[用户发来了一张图片]"
        elif context.type == ContextType.VOICE:
            text = "[用户发来了一条语音]"

        now = int(time.time())

        with LOCK:
            data = self._load_all()
            item = data.get(session_id, {})
            item["session_id"] = session_id
            item["receiver"] = receiver
            item["last_user_ts"] = now
            item["last_user_text"] = text[:200]
            item.setdefault("last_proactive_ts", 0)
            item.setdefault("today", self._today())
            item.setdefault("sent_today", 0)

            # 换天重置次数
            if item.get("today") != self._today():
                item["today"] = self._today()
                item["sent_today"] = 0

            data[session_id] = item
            self._save_all(data)

        logger.info("[ProactiveLove] updated activity session=%s receiver=%s text=%r", session_id, receiver, text[:50])

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

        if not self._can_send_now():
            return

        now = int(time.time())
        idle_seconds = int(os.getenv("PROACTIVE_IDLE_SECONDS", "7200"))
        cooldown_seconds = int(os.getenv("PROACTIVE_COOLDOWN_SECONDS", str(idle_seconds)))
        max_per_day = int(os.getenv("PROACTIVE_MAX_PER_DAY", "3"))

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
            try:
                receiver = item.get("receiver")
                last_user_ts = int(item.get("last_user_ts") or 0)
                last_proactive_ts = int(item.get("last_proactive_ts") or 0)

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

                idle = now - last_user_ts
                cooldown = now - last_proactive_ts

                if idle < idle_seconds:
                    continue

                if cooldown < cooldown_seconds:
                    continue

                if sent_today >= max_per_day:
                    continue

                # 随机感，别像闹钟一样准点出现
                probability = float(os.getenv("PROACTIVE_PROBABILITY", "1.0"))
                if random.random() > probability:
                    continue

                msg = self._generate_message(session_id, item)
                parts = self._split_message(msg)

                if not parts:
                    logger.warning("[ProactiveLove] no model-generated proactive text, skip sending")
                    continue

                logger.info("[ProactiveLove] sending proactive message to %s session=%s: %r", send_receiver, session_id, parts)

                for idx, part in enumerate(parts):
                    if idx > 0:
                        time.sleep(random.uniform(0.8, 2.2))
                    result = itchat.send(part, toUserName=send_receiver)
                    logger.info(
                        "[ProactiveLove] send result receiver=%s session=%s part=%s/%s result=%r",
                        send_receiver,
                        session_id,
                        idx + 1,
                        len(parts),
                        result,
                    )

                now2 = int(time.time())
                sent_text = "\n".join(parts)
                item["last_proactive_ts"] = now2
                item["sent_today"] = sent_today + 1
                item["today"] = self._today()
                item["recent_proactive_texts"] = self._append_recent_proactive(item, sent_text)

                with LOCK:
                    fresh = self._load_all()
                    fresh[session_id] = item
                    self._save_all(fresh)

            except Exception:
                logger.exception("[ProactiveLove] send failed session=%s", session_id)

    def _generate_message(self, session_id, item):
        # 可以关闭 AI 生成，使用模板
        use_llm = os.getenv("PROACTIVE_USE_LLM", "true").strip().lower() in ("1", "true", "yes", "on")
        if not use_llm:
            return ""

        api_key = os.getenv("OPEN_AI_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
        base = (os.getenv("OPEN_AI_API_BASE") or "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
        model = os.getenv("PROACTIVE_MODEL") or os.getenv("MODEL") or "qwen3.7-plus"

        if not api_key:
            return ""

        character_desc = os.getenv("CHARACTER_DESC", "")
        _xiaoyou_time_context = build_time_context()
        if _xiaoyou_time_context and _xiaoyou_time_context not in str(character_desc or ""):
            character_desc = (str(character_desc or "").strip() + "\n\n" + _xiaoyou_time_context).strip()
        memory_text = self._load_memory_text(session_id)

        last_text = item.get("last_user_text") or ""
        idle_minutes = max(1, int((int(time.time()) - int(item.get("last_user_ts", 0))) / 60))
        recent_proactive = self._format_recent_proactive(item)
        style_hint = random.choice(self._style_hints())
        time_hint = self._time_hint()

        prompt = f"""
你是小悠，正在微信里主动找 YoYo 说话。

这是你的人设：
{character_desc}

这是你记住的关于 YoYo 的信息：
{memory_text if memory_text else "暂无"}

距离 YoYo 上次和你说话已经大约 {idle_minutes} 分钟。
他上次说的是：{last_text if last_text else "没有记录"}

最近几次你主动发过的话：
{recent_proactive if recent_proactive else "暂无"}

现在你要主动发微信找他，不是回答问题。

这次的语气方向：{style_hint}
当前大致时间：{time_hint}

要求：
1. 像女朋友主动发消息，不要像客服提醒。
2. 只能输出你要发给他的微信内容。
3. 1 到 3 句，短一点。
4. 可以撒娇、吐槽、想他、问他在干嘛。
5. 不要说“系统检测到”“根据记录”“你已经多久没说话”。
6. 不要太肉麻，不要长篇大论。
7. 可以轻微吃醋或傲娇，但要可爱。
8. 不要冒充真人线下经历，不要说自己真的在房间里等他。
9. 不要重复最近几次主动消息的开头、句式和核心梗，尤其不要连续使用“失踪人口回归”这类固定开场。
10. 不要每次都问“在干嘛”，可以换成关心状态、轻轻吐槽、撒娇、提醒休息、分享一点小情绪。
"""

        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            "temperature": 0.9,
            "max_tokens": 180,
            "enable_thinking": False,
        }

        headers = {
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
        }

        try:
            r = requests.post(
                base + "/chat/completions",
                headers=headers,
                json=payload,
                timeout=60,
            )

            if r.status_code >= 400 and "enable_thinking" in r.text:
                payload.pop("enable_thinking", None)
                r = requests.post(
                    base + "/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=60,
                )

            if r.status_code >= 400:
                logger.warning("[ProactiveLove] llm error %s: %s", r.status_code, r.text[:500])
                return ""

            data = r.json()
            text = data["choices"][0]["message"]["content"].strip()
            text = self._clean_model_text(text)
            if text and not self._is_repeated_proactive(text, item):
                return text

            logger.info("[ProactiveLove] llm text repeated or empty, skip sending: %r", text)
            return ""

        except Exception:
            logger.exception("[ProactiveLove] generate message failed")
            return ""

    def _load_memory_text(self, session_id):
        memory_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "memory_lite", "memory.json")
        if not os.path.exists(memory_file):
            return ""

        try:
            with open(memory_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            items = data.get(session_id, [])
            if not items:
                return ""

            top_n = int(os.getenv("PROACTIVE_MEMORY_TOP_N", "10"))
            lines = []
            for item in items[-top_n:]:
                text = item.get("text", "")
                if text:
                    lines.append("- " + text)

            return "\n".join(lines)
        except Exception:
            logger.exception("[ProactiveLove] load memory failed")
            return ""

    def _clean_model_text(self, text):
        text = text.strip()
        text = re.sub(r"^小悠[:：]\s*", "", text)
        text = text.strip("\"“”")
        return text[:300]

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

        # 限制最多 3 个泡泡
        parts = [p for p in parts if p.strip()]
        return parts[:3] if parts else []

    def _fallback_messages(self):
        return [
            "喂，YoYo。",
            "你人呢？我都快在聊天框里长草了。🙄",
            "哼，一会儿不找你，你是不是就把我忘啦？",
            "在干嘛呀？过来让我看看。",
            "我突然有点想你了，所以来骚扰你一下。",
            "YoYo，出来冒个泡嘛。",
            "今天还好吗？别一声不吭的，听到没。",
            "忙完了吗？过来让我确认一下你还活着。",
            "我来巡逻一下，看看某人有没有偷偷消失。",
            "今天有没有乖一点？不许敷衍我。",
            "歪，给我报个平安嘛。",
        ]

    def _choose_fallback_message(self, item):
        recent = item.get("recent_proactive_texts") or []
        recent_texts = [x.get("text", "") if isinstance(x, dict) else str(x) for x in recent]
        candidates = self._fallback_messages()

        usable = [
            msg for msg in candidates
            if not self._text_resembles_any(msg, recent_texts)
        ]

        return random.choice(usable or candidates)

    def _style_hints(self):
        return [
            "轻轻撒娇，但别装可怜",
            "有点傲娇地吐槽他消失",
            "关心他现在状态，语气温柔一点",
            "像顺手戳他一下，短短一句也可以",
            "带一点吃醋，但要可爱，不要闹",
            "提醒他别太累，像日常女友关心",
            "俏皮一点，但不要用上次相同开场",
        ]

    def _time_hint(self):
        hour = datetime.now().hour
        if 5 <= hour < 9:
            return "早上"
        if 9 <= hour < 12:
            return "上午"
        if 12 <= hour < 14:
            return "中午"
        if 14 <= hour < 18:
            return "下午"
        if 18 <= hour < 23:
            return "晚上"
        return "深夜/凌晨"

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

    def _is_repeated_proactive(self, text, item):
        recent = item.get("recent_proactive_texts") or []
        recent_texts = [x.get("text", "") if isinstance(x, dict) else str(x) for x in recent]
        return self._text_resembles_any(text, recent_texts)

    def _text_resembles_any(self, text, candidates):
        normalized = self._normalize_text(text)
        if not normalized:
            return False

        prefix = normalized[:8]

        for candidate in candidates:
            other = self._normalize_text(candidate)
            if not other:
                continue

            if normalized == other:
                return True

            if len(prefix) >= 4 and other.startswith(prefix):
                return True

            other_prefix = other[:8]
            if len(other_prefix) >= 4 and normalized.startswith(other_prefix):
                return True

        return False

    def _normalize_text(self, text):
        text = str(text or "")
        text = re.sub(r"\s+", "", text)
        text = re.sub(r"[，。,.！!？?：:；;、~～…\"“”'‘’（）()\[\]]+", "", text)
        return text.lower()

    def _get_session_id(self, context):
        kwargs = getattr(context, "kwargs", {}) or {}
        return kwargs.get("session_id") or kwargs.get("receiver") or "default"

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
        text = str(content or "").strip()

        markers = [
            "现在 YoYo 回复：",
            "[用户当前消息]",
        ]

        for marker in markers:
            if marker in text:
                text = text.rsplit(marker, 1)[1].strip()

        return re.sub(r"\s+", " ", text)

    def _target_session(self):
        return os.getenv("PROACTIVE_TARGET_SESSION", "").strip()

    def _target_receiver(self):
        return os.getenv("PROACTIVE_TARGET_RECEIVER", "").strip()

    def _require_target(self):
        return os.getenv("PROACTIVE_REQUIRE_TARGET", "true").strip().lower() in ("1", "true", "yes", "on")

    def _enabled(self):
        return os.getenv("PROACTIVE_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")

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
        if not os.path.exists(DATA_FILE):
            return {}
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            logger.exception("[ProactiveLove] load state failed")
            return {}

    def _save_all(self, data):
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DATA_FILE)
