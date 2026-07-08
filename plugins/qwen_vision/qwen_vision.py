# -*- coding:utf-8 -*-
import os
import time
import base64
import mimetypes
import threading
import requests

import plugins
from plugins import *
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger
from lib import itchat


# session_id -> pending image context
PENDING_IMAGES = {}
LOCK = threading.Lock()
THREAD_STARTED = False


@plugins.register(
    name="QwenVision",
    desc="Use Qwen VLM to understand WeChat images with following user question",
    version="0.2",
    author="yoyo",
    desire_priority=80,
)
class QwenVision(Plugin):
    def __init__(self):
        global THREAD_STARTED
        super().__init__()
        self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
        logger.info("[QwenVision] inited")

        if not THREAD_STARTED:
            THREAD_STARTED = True
            t = threading.Thread(target=self._loop, daemon=True)
            t.start()
            logger.info("[QwenVision] background thread started")

    def on_handle_context(self, e_context: EventContext):
        context = e_context["context"]

        if context.type == ContextType.IMAGE:
            self._handle_image(e_context, context)
            return

        if context.type == ContextType.TEXT:
            self._handle_text(e_context, context)
            return

    def _handle_image(self, e_context, context):
        try:
            session_id = self._get_session_id(context)
            receiver = self._get_receiver(context)
            img_path = self._get_image_path(context)

            ttl = int(os.getenv("VISION_IMAGE_TTL", "180"))
            now = time.time()

            with LOCK:
                PENDING_IMAGES[session_id] = {
                    "id": "%s:%s" % (now, img_path),
                    "path": img_path,
                    "receiver": receiver,
                    "ts": now,
                    "last_update": now,
                    "ttl": ttl,
                    "texts": [],
                    "status": "waiting",
                }

            logger.info(
                "[QwenVision] cached image for session=%s receiver=%s path=%s ttl=%ss",
                session_id,
                receiver,
                img_path,
                ttl,
            )

            e_context.action = EventAction.BREAK

        except Exception:
            logger.exception("[QwenVision] image cache failed")
            e_context["reply"] = Reply(
                ReplyType.TEXT,
                "这张图我刚刚没存好，可能是图片下载出了点小问题。"
            )
            e_context.action = EventAction.BREAK_PASS

    def _handle_text(self, e_context, context):
        session_id = self._get_session_id(context)
        user_text = str(context.content or "").strip()

        with LOCK:
            item = PENDING_IMAGES.get(session_id)

            if not item or item.get("status") != "waiting":
                return

            img_path = item.get("path")
            ts = item.get("ts", 0)
            ttl = item.get("ttl", 180)

            if time.time() - ts > ttl or not img_path or not os.path.exists(img_path):
                logger.info("[QwenVision] pending image expired or missing for session=%s", session_id)
                PENDING_IMAGES.pop(session_id, None)
                return

            max_messages = int(os.getenv("VISION_MAX_FOLLOWUP_MESSAGES", "6"))
            max_chars = int(os.getenv("VISION_MAX_FOLLOWUP_CHARS", "500"))
            item.setdefault("texts", [])
            item["texts"].append(user_text[:max_chars])
            item["texts"] = item["texts"][-max_messages:]
            item["last_update"] = time.time()
            PENDING_IMAGES[session_id] = item

        logger.info("[QwenVision] appended follow-up text session=%s text=%r", session_id, user_text[:80])
        e_context.action = EventAction.BREAK

    def _loop(self):
        while True:
            try:
                interval = float(os.getenv("VISION_CHECK_INTERVAL", "1.0"))
                time.sleep(max(0.5, interval))
                self._check_pending_images()
            except Exception:
                logger.exception("[QwenVision] loop error")
                time.sleep(3)

    def _check_pending_images(self):
        now = time.time()
        due_items = []

        image_wait = float(os.getenv("VISION_IMAGE_WAIT_SECONDS", "5.0"))
        text_settle = float(os.getenv("VISION_TEXT_SETTLE_SECONDS", "3.0"))

        with LOCK:
            for session_id, item in list(PENDING_IMAGES.items()):
                if item.get("status") != "waiting":
                    continue

                img_path = item.get("path")
                ts = float(item.get("ts") or 0)
                ttl = float(item.get("ttl") or 180)
                last_update = float(item.get("last_update") or ts)
                texts = item.get("texts") or []

                if now - ts > ttl or not img_path or not os.path.exists(img_path):
                    logger.info("[QwenVision] drop expired pending image session=%s", session_id)
                    PENDING_IMAGES.pop(session_id, None)
                    continue

                wait_seconds = text_settle if texts else image_wait
                if now - last_update < wait_seconds:
                    continue

                item["status"] = "sending"
                PENDING_IMAGES[session_id] = item
                due_items.append((session_id, dict(item)))

        for session_id, item in due_items:
            self._send_pending_response(session_id, item)

    def _send_pending_response(self, session_id, item):
        receiver = item.get("receiver") or session_id
        img_path = item.get("path")
        pending_id = item.get("id")
        texts = item.get("texts") or []

        try:
            prompt = self._build_prompt(texts)
            answer = self._ask_vision(img_path, prompt)
            answer = self._clean_answer(answer)

            if not answer:
                answer = "我看到了呀，但这张图有点让我不知道先从哪句说起。你想让我看哪里嘛？"

            logger.info("[QwenVision] sending vision reply session=%s receiver=%s text=%r", session_id, receiver, answer[:100])
            itchat.send(answer, toUserName=receiver)

        except Exception:
            logger.exception("[QwenVision] async vision answer failed session=%s", session_id)
            try:
                itchat.send("我刚刚看到图了，但回答的时候卡了一下……你再发我一下嘛。", toUserName=receiver)
            except Exception:
                logger.exception("[QwenVision] send fallback failed session=%s", session_id)

        finally:
            with LOCK:
                current = PENDING_IMAGES.get(session_id)
                if current and current.get("id") == pending_id:
                    PENDING_IMAGES.pop(session_id, None)

    def _get_session_id(self, context):
        kwargs = getattr(context, "kwargs", {}) or {}
        return (
            kwargs.get("session_id")
            or kwargs.get("receiver")
            or "default"
        )

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

    def _get_image_path(self, context):
        content = context.content
        logger.info("[QwenVision] get image content=%r", content)

        def exists(path):
            return path and os.path.exists(path) and os.path.getsize(path) > 0

        def candidate_paths(value):
            paths = []
            if isinstance(value, str):
                paths.extend([
                    value,
                    os.path.abspath(value),
                    os.path.join("/app", value),
                    os.path.join(os.getcwd(), value),
                    os.path.join("/app/tmp", os.path.basename(value)),
                    os.path.join("/tmp", os.path.basename(value)),
                ])
            return list(dict.fromkeys(paths))

        for path in candidate_paths(content):
            if exists(path):
                logger.info("[QwenVision] image exists before prepare: %s size=%s", path, os.path.getsize(path))
                return path

        kwargs = getattr(context, "kwargs", {}) or {}
        msg = kwargs.get("msg")
        logger.info("[QwenVision] wrapped msg=%r", msg)
        logger.info("[QwenVision] wrapped msg dict=%r", getattr(msg, "__dict__", {}))

        if msg is not None:
            msg_content = getattr(msg, "content", None)
            logger.info("[QwenVision] msg.content=%r", msg_content)

            os.makedirs("/app/tmp", exist_ok=True)
            os.makedirs("tmp", exist_ok=True)

            if hasattr(msg, "_prepare_fn"):
                logger.info("[QwenVision] calling msg._prepare_fn()")
                msg._prepare_fn()
                logger.info("[QwenVision] msg._prepare_fn() finished")

            if hasattr(msg, "prepare"):
                try:
                    logger.info("[QwenVision] calling msg.prepare()")
                    msg.prepare()
                    logger.info("[QwenVision] msg.prepare() finished")
                except Exception as ex:
                    logger.warning("[QwenVision] msg.prepare failed: %s", ex)

            for value in [content, msg_content]:
                for path in candidate_paths(value):
                    if exists(path):
                        logger.info("[QwenVision] image prepared: %s size=%s", path, os.path.getsize(path))
                        return path

        logger.error(
            "[QwenVision] cannot locate image after prepare. cwd=%s content=%r kwargs=%r",
            os.getcwd(), content, kwargs
        )
        raise RuntimeError("cannot locate image file, content=%r" % (content,))

    def _build_prompt(self, followup_texts):
        base_prompt = os.getenv(
            "VISION_PROMPT",
            "你是小悠，正在微信里看 YoYo 刚刚发来的图片。请像女朋友一样自然回应。"
        )

        if isinstance(followup_texts, str):
            followup_texts = [followup_texts] if followup_texts.strip() else []

        followup_texts = [str(x or "").strip() for x in (followup_texts or []) if str(x or "").strip()]

        if followup_texts:
            joined = "\n".join("%d. %s" % (idx + 1, text) for idx, text in enumerate(followup_texts))
            situation = """YoYo 发完图片后又补充了这些话：
%s

你要把图片和这些补充消息一起理解，然后回复最后真实想问/想表达的点。
如果这些话像是在连续补充同一个问题，就合并理解；不要每句都单独回答。""" % joined
        else:
            situation = """YoYo 只发了这张图片，没有继续补充文字。
你要像女朋友收到男朋友随手发来的图片一样自然回应。
根据图片内容决定该夸、调侃、关心、吐槽、问一句，还是提醒他补充说明。"""

        return f"""{base_prompt}

{situation}

回复要求：
1. 不要先描述画面，不要以“这张图里/画面中/我看到”开头。
2. 第一反应要像真人聊天，可以先给情绪、判断、调侃、关心或一句很自然的反问。
3. 如果需要提细节，只挑 1 到 2 个最关键的细节作为理由，别铺陈画面。
4. 如果 YoYo 问好不好看、像不像头像、可不可爱，直接给结论，再简单说理由。
5. 如果图片是截图/文字，先读懂重点，再像小悠一样解释或总结，不要说“有很多文字”。
6. 如果图片看不清，只说哪里影响判断，并自然让他再发清楚一点。
7. 默认 1 到 3 句，短一点，像微信消息。
8. 不要写标题，不要列清单，不要像分析报告。
9. 不要说自己是 AI、模型、图片识别工具。"""

    def _clean_answer(self, text):
        text = str(text or "").strip()
        text = text.strip("\"“”")
        return text[:500]

    def _ask_vision(self, image_path, prompt):
        api_key = os.getenv("OPEN_AI_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
        base = (
            os.getenv("OPEN_AI_API_BASE")
            or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ).rstrip("/")
        model = os.getenv("VISION_MODEL") or os.getenv("MODEL") or "qwen3.7-plus"

        if not api_key:
            raise RuntimeError("OPEN_AI_API_KEY missing")

        mime = mimetypes.guess_type(image_path)[0] or "image/jpeg"
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")

        data_url = "data:%s;base64,%s" % (mime, b64)

        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            "max_tokens": 800,
            "temperature": 0.7,
            "enable_thinking": False,
        }

        headers = {
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
        }

        logger.info("[QwenVision] ask vision model=%s image=%s", model, image_path)

        r = requests.post(
            base + "/chat/completions",
            headers=headers,
            json=payload,
            timeout=90,
        )

        if r.status_code >= 400 and "enable_thinking" in r.text:
            payload.pop("enable_thinking", None)
            r = requests.post(
                base + "/chat/completions",
                headers=headers,
                json=payload,
                timeout=90,
            )

        if r.status_code >= 400:
            raise RuntimeError("vision api error %s: %s" % (r.status_code, r.text[:1000]))

        data = r.json()
        return data["choices"][0]["message"]["content"].strip()
