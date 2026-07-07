# -*- coding:utf-8 -*-
import os
import time
import base64
import mimetypes
import requests

import plugins
from plugins import *
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger


# session_id -> {"path": image_path, "ts": timestamp}
PENDING_IMAGES = {}


@plugins.register(
    name="QwenVision",
    desc="Use Qwen VLM to understand WeChat images with following user question",
    version="0.2",
    author="yoyo",
    desire_priority=80,
)
class QwenVision(Plugin):
    def __init__(self):
        super().__init__()
        self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
        logger.info("[QwenVision] inited")

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
            img_path = self._get_image_path(context)

            ttl = int(os.getenv("VISION_IMAGE_TTL", "180"))
            PENDING_IMAGES[session_id] = {
                "path": img_path,
                "ts": time.time(),
                "ttl": ttl,
            }

            logger.info(
                "[QwenVision] cached image for session=%s path=%s ttl=%ss",
                session_id,
                img_path,
                ttl,
            )

            # 默认不立即描述图片，等待用户下一条文字问题。
            # 这样“图片 + 下面的问题”可以一起传给视觉模型。
            auto_reply = os.getenv("VISION_AUTO_DESCRIBE", "false").strip().lower()
            if auto_reply in ("1", "true", "yes", "on"):
                prompt = self._build_prompt("你先看看这张图，简单说说你看到了什么。")
                answer = self._ask_vision(img_path, prompt)
                e_context["reply"] = Reply(ReplyType.TEXT, answer)
                e_context.action = EventAction.BREAK_PASS
            else:
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
        item = PENDING_IMAGES.get(session_id)

        if not item:
            return

        img_path = item.get("path")
        ts = item.get("ts", 0)
        ttl = item.get("ttl", 180)

        if time.time() - ts > ttl or not img_path or not os.path.exists(img_path):
            logger.info("[QwenVision] pending image expired or missing for session=%s", session_id)
            PENDING_IMAGES.pop(session_id, None)
            return

        user_question = str(context.content or "").strip()

        # 用户发了图片后，如果下一条文字只是表情或很短，也照样让视觉模型结合图片回答。
        try:
            prompt = self._build_prompt(user_question)
            answer = self._ask_vision(img_path, prompt)

            PENDING_IMAGES.pop(session_id, None)

            e_context["reply"] = Reply(ReplyType.TEXT, answer)
            e_context.action = EventAction.BREAK_PASS

        except Exception:
            logger.exception("[QwenVision] vision answer failed")
            PENDING_IMAGES.pop(session_id, None)
            e_context["reply"] = Reply(
                ReplyType.TEXT,
                "我刚刚看到图了，但回答的时候卡了一下……你再问我一遍嘛。"
            )
            e_context.action = EventAction.BREAK_PASS

    def _get_session_id(self, context):
        kwargs = getattr(context, "kwargs", {}) or {}
        return (
            kwargs.get("session_id")
            or kwargs.get("receiver")
            or "default"
        )

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

    def _build_prompt(self, user_question):
        base_prompt = os.getenv(
            "VISION_PROMPT",
            "你是小悠的眼睛，正在认真看用户刚刚发来的图片。请结合用户的问题，用小悠的微信聊天语气自然回答。"
        )

        if not user_question:
            user_question = "你看看这张图，告诉我你的感觉。"

        return f"""{base_prompt}

用户接着图片问你：
{user_question}

请你必须结合图片内容回答用户这个具体问题，不要只机械描述图片。
如果用户问“好不好看”“可不可爱”“像不像头像”，就直接评价并给理由。
如果用户问图片里的文字、截图、细节，就先读懂图片再回答。
回复要像微信聊天一样自然，别写报告。"""

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
