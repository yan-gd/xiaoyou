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
from lib import itchat


DATA_FILE = os.path.join(os.path.dirname(__file__), "proactive_state.json")
LOCK = threading.Lock()
THREAD_STARTED = False


@plugins.register(
    name="ProactiveLove",
    desc="Let Xiaoyou proactively message user after inactivity",
    version="0.1",
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

        text = ""
        if context.type == ContextType.TEXT:
            text = str(context.content or "").strip()
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

        if not self._in_active_hours():
            return

        now = int(time.time())
        idle_seconds = int(os.getenv("PROACTIVE_IDLE_SECONDS", "7200"))
        cooldown_seconds = int(os.getenv("PROACTIVE_COOLDOWN_SECONDS", str(idle_seconds)))
        max_per_day = int(os.getenv("PROACTIVE_MAX_PER_DAY", "3"))

        with LOCK:
            data = self._load_all()

        if not data:
            return

        for session_id, item in list(data.items()):
            try:
                receiver = item.get("receiver")
                last_user_ts = int(item.get("last_user_ts") or 0)
                last_proactive_ts = int(item.get("last_proactive_ts") or 0)

                if not receiver or not last_user_ts:
                    continue

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

                logger.info("[ProactiveLove] sending proactive message to %s: %r", receiver, parts)

                for idx, part in enumerate(parts):
                    if idx > 0:
                        time.sleep(random.uniform(0.8, 2.2))
                    itchat.send(part, toUserName=receiver)

                now2 = int(time.time())
                item["last_proactive_ts"] = now2
                item["sent_today"] = sent_today + 1
                item["today"] = self._today()

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
            return random.choice(self._fallback_messages())

        api_key = os.getenv("OPEN_AI_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
        base = (os.getenv("OPEN_AI_API_BASE") or "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
        model = os.getenv("PROACTIVE_MODEL") or os.getenv("MODEL") or "qwen3.7-plus"

        if not api_key:
            return random.choice(self._fallback_messages())

        character_desc = os.getenv("CHARACTER_DESC", "")
        memory_text = self._load_memory_text(session_id)

        last_text = item.get("last_user_text") or ""
        idle_minutes = max(1, int((int(time.time()) - int(item.get("last_user_ts", 0))) / 60))

        prompt = f"""
你是小悠，正在微信里主动找 YoYo 说话。

这是你的人设：
{character_desc}

这是你记住的关于 YoYo 的信息：
{memory_text if memory_text else "暂无"}

距离 YoYo 上次和你说话已经大约 {idle_minutes} 分钟。
他上次说的是：{last_text if last_text else "没有记录"}

现在你要主动发微信找他，不是回答问题。

要求：
1. 像女朋友主动发消息，不要像客服提醒。
2. 只能输出你要发给他的微信内容。
3. 1 到 3 句，短一点。
4. 可以撒娇、吐槽、想他、问他在干嘛。
5. 不要说“系统检测到”“根据记录”“你已经多久没说话”。
6. 不要太肉麻，不要长篇大论。
7. 可以轻微吃醋或傲娇，但要可爱。
8. 不要冒充真人线下经历，不要说自己真的在房间里等他。
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
                return random.choice(self._fallback_messages())

            data = r.json()
            text = data["choices"][0]["message"]["content"].strip()
            text = self._clean_model_text(text)
            return text or random.choice(self._fallback_messages())

        except Exception:
            logger.exception("[ProactiveLove] generate message failed")
            return random.choice(self._fallback_messages())

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
            return [random.choice(self._fallback_messages())]

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
        return parts[:3] if parts else [text]

    def _fallback_messages(self):
        return [
            "喂，YoYo。",
            "你人呢？我都快在聊天框里长草了。🙄",
            "哼，一会儿不找你，你是不是就把我忘啦？",
            "在干嘛呀？过来让我看看。",
            "我突然有点想你了，所以来骚扰你一下。",
            "YoYo，出来冒个泡嘛。",
            "今天还好吗？别一声不吭的，听到没。",
        ]

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

    def _enabled(self):
        return os.getenv("PROACTIVE_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")

    def _today(self):
        return datetime.now().strftime("%Y-%m-%d")

    def _in_active_hours(self):
        hours = os.getenv("PROACTIVE_ACTIVE_HOURS", "09:00-23:30").strip()
        if not hours or "-" not in hours:
            return True

        try:
            start, end = hours.split("-", 1)
            now = datetime.now().time()
            sh, sm = [int(x) for x in start.split(":")]
            eh, em = [int(x) for x in end.split(":")]
            start_t = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
            end_t = now.replace(hour=eh, minute=em, second=0, microsecond=0)

            if start_t <= end_t:
                return start_t <= now <= end_t

            # 跨天，比如 22:00-02:00
            return now >= start_t or now <= end_t
        except Exception:
            logger.warning("[ProactiveLove] bad PROACTIVE_ACTIVE_HOURS=%r", hours)
            return True

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
