# -*- coding:utf-8 -*-
import os
import time
import base64
import mimetypes
import threading
import re
from plugins.xiaoyou_common.thinking_config import build_thinking_payload
from plugins.xiaoyou_common.model_gateway import chat_completion
from plugins.xiaoyou_common.outbound_dispatcher import send_text

import plugins
from plugins import *
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger
from plugins.xiaoyou_common.context_service import (
    build_time_context,
    extract_current_user_text,
)
from plugins.xiaoyou_common.trace_service import activate_context_trace


# session_id -> pending image context
PENDING_IMAGES = {}
LOCK = threading.Lock()
THREAD_STARTED = False


@plugins.register(
    name="QwenVision",
    desc="Use Qwen VLM to understand WeChat images with following user question",
    version="1.0-trace-runtime",
    author="yoyo",
    desire_priority=980,
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
            session_keys = self._get_session_keys(context)
            receiver = self._get_receiver(context)
            img_path = self._get_image_path(context)
            channel = e_context["channel"]

            if not session_id or not receiver:
                logger.warning("[QwenVision] missing session or receiver, skip image cache")
                return

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
                    "revision": 1,
                    "dirty": False,
                    "channel": channel,
                    "turn_context": context,
                    "keys": session_keys,
                }

            logger.info(
                "[QwenVision] cached image for session=%s keys=%s receiver=%s path=%s ttl=%ss",
                session_id,
                session_keys,
                receiver,
                img_path,
                ttl,
            )

            self._record_user_short_memory(
                session_id,
                "[YoYo 发来了一张图片]",
            )

            e_context.action = EventAction.BREAK

        except Exception:
            logger.exception("[QwenVision] image cache failed")
            # 图片链路失败时保持静默，不使用固定兜底回复。
            e_context["reply"] = Reply(ReplyType.TEXT, "")
            e_context.action = EventAction.BREAK_PASS

    def _handle_text(self, e_context, context):
        session_id = self._get_session_id(context)
        receiver = self._get_receiver(context)
        user_text = self._clean_followup_text(context.content)

        if not user_text:
            return

        # 明确是在让小悠拍自己/分享她的日常时，不把这句话误吞成上一张用户图片的补充说明。
        if self._looks_like_xiaoyou_photo_request(user_text):
            with LOCK:
                PENDING_IMAGES.pop(session_id, None)
                for key, pending in list(PENDING_IMAGES.items()):
                    if receiver and pending.get("receiver") == receiver:
                        PENDING_IMAGES.pop(key, None)
            logger.info(
                "[QwenVision] pending user image cancelled by Xiaoyou photo request session=%s",
                session_id,
            )
            return

        now = time.time()
        matched_key = None
        matched_item = None
        matched_by = ""

        with LOCK:
            # 1. 优先精确匹配 session_id
            item = PENDING_IMAGES.get(session_id)
            if item and item.get("status") in ("waiting", "sending"):
                matched_key = session_id
                matched_item = item
                matched_by = "session_id"

            # 2. 其次匹配 receiver
            if not matched_item:
                for key, item in list(PENDING_IMAGES.items()):
                    if item.get("status") not in ("waiting", "sending"):
                        continue
                    if receiver and item.get("receiver") == receiver:
                        matched_key = key
                        matched_item = item
                        matched_by = "receiver"
                        break

            # 3. 单聊兜底：如果当前只有一张等待中的图，就认为这句是在补充图片
            if not matched_item:
                candidates = []
                for key, item in list(PENDING_IMAGES.items()):
                    if item.get("status") not in ("waiting", "sending"):
                        continue

                    img_path = item.get("path")
                    ts = float(item.get("ts") or 0)
                    ttl = float(item.get("ttl") or 180)

                    if now - ts > ttl or not img_path or not os.path.exists(img_path):
                        PENDING_IMAGES.pop(key, None)
                        continue

                    candidates.append((key, item))

                if len(candidates) == 1:
                    matched_key, matched_item = candidates[0]
                    matched_by = "single_pending"

            if not matched_item:
                return

            img_path = matched_item.get("path")
            ts = float(matched_item.get("ts") or 0)
            ttl = float(matched_item.get("ttl") or 180)

            if now - ts > ttl or not img_path or not os.path.exists(img_path):
                logger.info("[QwenVision] pending image expired or missing for text session=%s", session_id)
                if matched_key:
                    PENDING_IMAGES.pop(matched_key, None)
                return

            max_messages = int(os.getenv("VISION_MAX_FOLLOWUP_MESSAGES", "6"))
            max_chars = int(os.getenv("VISION_MAX_FOLLOWUP_CHARS", "500"))

            matched_item.setdefault("texts", [])
            matched_item["texts"].append(user_text[:max_chars])
            matched_item["texts"] = matched_item["texts"][-max_messages:]
            matched_item["last_update"] = now
            matched_item["revision"] = int(matched_item.get("revision") or 0) + 1
            matched_item["channel"] = e_context["channel"]
            matched_item["turn_context"] = context
            if matched_item.get("status") == "sending":
                matched_item["dirty"] = True
            PENDING_IMAGES[matched_key] = matched_item

        logger.info(
            "[QwenVision] appended follow-up text matched_by=%s image_session=%s text_session=%s receiver=%s text=%r",
            matched_by,
            matched_key,
            session_id,
            receiver,
            user_text[:100],
        )

        self._record_user_short_memory(matched_key or session_id, user_text)

        # 关键：吃掉这条文字，不让普通 ChatGPT 再单独回复
        # 单纯 BREAK 在当前链路里仍可能继续进入 bot，这里用空 Reply + BREAK_PASS 强制截断。
        e_context["reply"] = Reply(ReplyType.TEXT, "")
        e_context.action = EventAction.BREAK_PASS

    def _clean_followup_text(self, content):
        return extract_current_user_text(content)

    def _looks_like_xiaoyou_photo_request(self, text):
        text = str(text or "").strip()
        patterns = (
            r"(?:给我|让我|想看|要看).{0,8}(?:你|小悠).{0,10}(?:自拍|照片|穿搭|现在)",
            r"(?:你|小悠).{0,8}(?:拍|发).{0,8}(?:自拍|照片|穿搭|日常)",
            r"(?:拍|发).{0,8}(?:你|你的|小悠).{0,8}(?:自拍|照片|穿搭)",
            r"(?:给我|来|发|拍).{0,6}(?:一张|张|个)?.{0,4}(?:照片|自拍|穿搭照)",
            r"(?:看看你|看你现在|看你今天|给我看看你)",
        )
        return any(re.search(pattern, text, re.I) for pattern in patterns)

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
                item["dirty"] = False
                PENDING_IMAGES[session_id] = item
                snapshot = dict(item)
                snapshot["texts"] = list(item.get("texts") or [])
                due_items.append((session_id, snapshot))

        for session_id, item in due_items:
            self._send_pending_response(session_id, item)

    def _send_pending_response(self, session_id, item):
        turn_context = item.get("turn_context")
        if turn_context is not None:
            activate_context_trace(turn_context)
        receiver = item.get("receiver") or session_id
        img_path = item.get("path")
        pending_id = item.get("id")
        revision = int(item.get("revision") or 0)
        texts = item.get("texts") or []
        keep_pending = False

        try:
            prompt = self._build_prompt(texts)
            answer = self._ask_vision(img_path, prompt)
            answer = self._clean_answer(answer)

            if not answer:
                logger.warning("[QwenVision] model returned empty vision reply; keep silent session=%s", session_id)
                return

            if not self._vision_snapshot_current(session_id, pending_id, revision, item):
                keep_pending = True
                logger.info(
                    "[QwenVision] stale vision result discarded session=%s revision=%s",
                    session_id,
                    revision,
                )
                return

            logger.info("[QwenVision] sending vision reply session=%s receiver=%s text=%r", session_id, receiver, answer[:100])
            send_result = self._send_text_with_split_delay(
                answer,
                receiver,
                session_id=session_id,
                tag="vision",
                channel=item.get("channel"),
                turn_context=item.get("turn_context"),
            )
            sent_text = send_result.get("text", "")
            keep_pending = bool(send_result.get("stale"))

        except Exception:
            logger.exception("[QwenVision] async vision answer failed session=%s", session_id)

        finally:
            with LOCK:
                current = PENDING_IMAGES.get(session_id)
                if current and current.get("id") == pending_id:
                    current_revision = int(current.get("revision") or 0)
                    if keep_pending or current_revision != revision or current.get("dirty"):
                        current["status"] = "waiting"
                        current["dirty"] = False
                        if keep_pending and current_revision == revision:
                            current["last_update"] = time.time()
                        PENDING_IMAGES[session_id] = current
                    else:
                        PENDING_IMAGES.pop(session_id, None)


    def _vision_snapshot_current(self, session_id, pending_id, revision, item):
        with LOCK:
            current = PENDING_IMAGES.get(session_id)
            if not current or current.get("id") != pending_id:
                return False
            if int(current.get("revision") or 0) != int(revision):
                return False

        return self._context_is_current(
            item.get("channel"),
            item.get("turn_context"),
        )

    def _context_is_current(self, channel, context):
        checker = getattr(channel, "is_context_current", None)
        if not callable(checker):
            return True
        try:
            return bool(checker(context))
        except Exception:
            logger.exception("[QwenVision] failed to check input turn version")
            return False

    def _send_text_with_split_delay(self, text, receiver, session_id, tag="vision", channel=None, turn_context=None):
        text = str(text or "").strip()
        if not text:
            return {"text": "", "stale": False}

        enabled = os.getenv("VISION_SPLIT_REPLY_ENABLED", os.getenv("SPLIT_REPLY_ENABLED", "true")).strip().lower() in ("1", "true", "yes", "on")
        delay_per_char = float(os.getenv("VISION_SPLIT_REPLY_DELAY_PER_CHAR", os.getenv("SPLIT_REPLY_DELAY_PER_CHAR", "0.4")))
        max_parts = int(os.getenv("VISION_SPLIT_REPLY_MAX_PARTS", os.getenv("SPLIT_REPLY_MAX_PARTS", "4")))
        max_chars = int(os.getenv("VISION_SPLIT_REPLY_MAX_CHARS", os.getenv("SPLIT_REPLY_MAX_CHARS", "80")))

        if not enabled:
            receipt = send_text(
                session_id=session_id,
                source="qwen_vision",
                text=text,
                receiver=receiver,
                channel=channel,
                context=turn_context,
                record_memory=True,
            )
            return {"text": receipt.sent_text, "stale": receipt.stale}

        # 先尊重大模型自己的换行：一行就是一条微信消息
        normalized = re.sub(r"\n\s*\n+", "\n", text)
        lines = [x.strip() for x in re.split(r"\n+", normalized) if x.strip()]

        if len(lines) >= 2:
            parts = lines[:max_parts]
        else:
            # 没换行时，按完整句子拆；最后才做长度兜底
            pieces = re.split(r"(?<=[。！？!?~～…])\s*", text)
            pieces = [p.strip() for p in pieces if p.strip()]

            parts = []
            buf = ""
            for p in pieces:
                if not buf:
                    buf = p
                elif len(buf) + len(p) <= max_chars:
                    buf += p
                else:
                    parts.append(buf)
                    buf = p

            if buf:
                parts.append(buf)

            if not parts:
                parts = [text]

            # 极端长句兜底，避免一条太长
            fixed = []
            for p in parts:
                if len(p) <= max_chars:
                    fixed.append(p)
                else:
                    for i in range(0, len(p), max_chars):
                        fixed.append(p[i:i + max_chars])
            parts = fixed[:max_parts]

        logger.info(
            "[QwenVision] split_send tag=%s receiver=%s parts=%s delay_per_char=%s",
            tag,
            receiver,
            len(parts),
            delay_per_char,
        )

        receipt = send_text(
            session_id=session_id,
            source="qwen_vision",
            parts=parts,
            receiver=receiver,
            channel=channel,
            context=turn_context,
            delay_before_part=lambda _index, part: max(0, len(part) * delay_per_char),
            record_memory=True,
        )
        return {"text": receipt.sent_text, "stale": receipt.stale}

    def _record_user_short_memory(self, session_id, text):
        try:
            manager = getattr(plugins, "instance", None)
            instances = getattr(manager, "instances", {}) if manager else {}
            short_memory = instances.get("SHORTMEMORY")
            record = getattr(short_memory, "append_external_user_message", None)

            if callable(record):
                record(session_id, text, source="qwen_vision")
            else:
                logger.warning("[QwenVision] ShortMemory external message API unavailable")
        except Exception:
            logger.exception("[QwenVision] failed to record vision reply in ShortMemory")

    def _split_text(self, text, max_chars=28, max_parts=6, tiny_merge=6):
        text = str(text or "").strip()
        max_chars = max(1, int(max_chars or 28))
        max_parts = max(1, int(max_parts or 6))
        tiny_merge = max(0, int(tiny_merge or 0))

        if len(text) <= max_chars:
            return [text]

        seeds = []
        for line in re.split(r"\n+", text):
            line = line.strip()
            if not line:
                continue
            chunks = re.findall(r".+?[。！？!?；;，,、~～]|.+$", line)
            for chunk in chunks:
                chunk = chunk.strip()
                if not chunk:
                    continue
                if len(chunk) <= max_chars:
                    seeds.append(chunk)
                else:
                    seeds.extend(chunk[i:i + max_chars] for i in range(0, len(chunk), max_chars))

        if not seeds:
            seeds = [text]

        merged = []
        for chunk in seeds:
            if not merged:
                merged.append(chunk)
                continue

            prev = merged[-1]
            joiner = "\n" if ("\n" in text or len(prev) + len(chunk) > max_chars // 2) else ""
            can_merge = len(prev) + len(joiner) + len(chunk) <= max_chars
            if can_merge and (len(chunk) <= tiny_merge or len(prev) <= tiny_merge):
                merged[-1] = prev + joiner + chunk
            else:
                merged.append(chunk)

        if len(merged) > max_parts:
            head = merged[:max_parts - 1]
            tail = "\n".join(merged[max_parts - 1:]).strip()
            merged = head + ([tail] if tail else [])

        return [p for p in merged if p.strip()]

    def _env_bool(self, key, default=False):
        value = os.getenv(key)
        if value is None:
            return bool(default)
        return str(value).strip().lower() in ("1", "true", "yes", "on", "y")

    def _env_int(self, key, default=0):
        value = os.getenv(key)
        if value is None or str(value).strip() == "":
            return int(default)
        try:
            return int(float(str(value).strip()))
        except Exception:
            logger.warning("[QwenVision] invalid int env %s=%r, use %r", key, value, default)
            return int(default)

    def _env_float(self, key, default=0.0):
        value = os.getenv(key)
        if value is None or str(value).strip() == "":
            return float(default)
        try:
            return float(str(value).strip())
        except Exception:
            logger.warning("[QwenVision] invalid float env %s=%r, use %r", key, value, default)
            return float(default)

    def _get_session_id(self, context):
        keys = self._get_session_keys(context)
        return keys[0] if keys else ""

    def _get_session_keys(self, context):
        kwargs = getattr(context, "kwargs", {}) or {}
        keys = [
            kwargs.get("session_id"),
            kwargs.get("receiver"),
            kwargs.get("actual_user_id"),
            kwargs.get("from_user_id"),
            kwargs.get("to_user_id"),
            kwargs.get("other_user_id"),
        ]

        msg = kwargs.get("msg")
        if msg is not None:
            for attr in (
                "session_id",
                "receiver",
                "actual_user_id",
                "from_user_id",
                "to_user_id",
                "other_user_id",
                "user_id",
            ):
                keys.append(getattr(msg, attr, None))

        keys = self._merge_unique([], keys)
        return keys or ["default"]

    def _merge_unique(self, base, extra):
        result = []
        for value in list(base or []) + list(extra or []):
            if value is None:
                continue
            value = str(value).strip()
            if not value or value in result:
                continue
            result.append(value)
        return result

    def _find_pending_item_locked(self, session_keys):
        keys = self._merge_unique([], session_keys)
        for key in keys:
            item = PENDING_IMAGES.get(key)
            if item and item.get("status") == "waiting":
                return key, item

        key_set = set(keys)
        for saved_session_id, item in list(PENDING_IMAGES.items()):
            if item.get("status") != "waiting":
                continue
            saved_keys = set(self._merge_unique([saved_session_id], item.get("keys") or []))
            if key_set & saved_keys:
                return saved_session_id, item

        return None, None

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

        _xiaoyou_time_context = build_time_context()
        if _xiaoyou_time_context and _xiaoyou_time_context not in str(base_prompt or ""):
            base_prompt = (str(base_prompt or "").strip() + "\n\n" + _xiaoyou_time_context).strip()


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
            **build_thinking_payload("VISION"),
        }

        logger.info("[QwenVision] ask vision model=%s image=%s", model, image_path)
        result = chat_completion(
            component="QwenVision",
            purpose="vision_understanding",
            payload=payload,
            timeout=90,
            api_key=api_key,
        )
        if not result.ok:
            raise RuntimeError(
                "vision api failed category=%s status=%s code=%s"
                % (result.error_kind, result.status_code, result.error_code)
            )
        return result.content.strip()
