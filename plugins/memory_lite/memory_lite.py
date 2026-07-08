# -*- coding:utf-8 -*-
import os
import re
import json
import time
import threading

import requests
import plugins
from plugins import *
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger


DATA_FILE = os.path.join(os.path.dirname(__file__), "memory.json")
LOCK = threading.Lock()


@plugins.register(
    name="MemoryLite",
    desc="Long term memory for personal WeChat companion",
    version="0.1",
    author="yoyo",
    desire_priority=20,
)
class MemoryLite(Plugin):
    def __init__(self):
        super().__init__()
        self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
        logger.info("[MemoryLite] inited")

    def on_handle_context(self, e_context: EventContext):
        if not self._enabled():
            return

        context = e_context["context"]
        if context.type != ContextType.TEXT:
            return

        text = str(context.content or "").strip()
        if not text:
            return

        session_id = self._get_session_id(context)

        # 查看记忆
        if self._is_list_cmd(text):
            memories = self._get_memories(session_id)
            if not memories:
                reply = "我现在还没记住什么呢。你可以说：记住：我喜欢粉色头像"
            else:
                lines = ["我现在记得这些："]
                for i, item in enumerate(memories, 1):
                    lines.append("%d. %s" % (i, item.get("text", "")))
                reply = "\n".join(lines)

            e_context["reply"] = Reply(ReplyType.TEXT, reply)
            e_context.action = EventAction.BREAK_PASS
            return

        # 清空记忆
        if self._is_clear_cmd(text):
            self._clear_memories(session_id)
            e_context["reply"] = Reply(ReplyType.TEXT, "好，我把关于你的长期记忆清空了。")
            e_context.action = EventAction.BREAK_PASS
            return

        # 忘记某条
        forget_key = self._extract_forget(text)
        if forget_key:
            removed = self._forget_memory(session_id, forget_key)
            if removed:
                reply = "好，我忘掉和「%s」相关的记忆了。" % forget_key
            else:
                reply = "我没找到和「%s」相关的记忆。" % forget_key

            e_context["reply"] = Reply(ReplyType.TEXT, reply)
            e_context.action = EventAction.BREAK_PASS
            return

        # 记住某条
        memory_text = self._extract_remember(text)
        if memory_text:
            memory_text = self._clean_memory(memory_text)
            if not memory_text:
                return

            self._add_memory(session_id, memory_text)

            e_context["reply"] = Reply(
                ReplyType.TEXT,
                "记住啦。以后我会记得：%s" % memory_text
            )
            e_context.action = EventAction.BREAK_PASS
            return

        # 可选：自动记忆。开启后由语言模型判断哪些信息值得长期保存。
        if self._auto_capture():
            auto_memories = self._extract_auto_memories(text, session_id)
            for auto_memory in auto_memories:
                self._add_memory(session_id, auto_memory)

        # 普通聊天：把记忆注入给模型
        memories = self._get_memories(session_id)
        if memories:
            inject_top_n = int(os.getenv("MEMORY_INJECT_TOP_N", "20"))
            selected = memories[-inject_top_n:]

            memory_lines = []
            for item in selected:
                memory_lines.append("- " + item.get("text", ""))

            memory_block = "\n".join(memory_lines)

            context.content = """[小悠的长期记忆]
以下内容是你过去记住的用户信息，只用于理解用户和延续关系。
不要主动逐条复述这些记忆，除非用户问你记得什么。
不要说“根据长期记忆”这种话，要自然地融入回复。

%s

[用户当前消息]
%s""" % (memory_block, text)

            logger.info("[MemoryLite] injected %s memories for session=%s", len(selected), session_id)

    def _enabled(self):
        return os.getenv("MEMORY_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")

    def _auto_capture(self):
        return os.getenv("MEMORY_AUTO_CAPTURE", "false").strip().lower() in ("1", "true", "yes", "on")

    def _llm_capture_enabled(self):
        return os.getenv("MEMORY_CAPTURE_USE_LLM", "true").strip().lower() in ("1", "true", "yes", "on")

    def _get_session_id(self, context):
        kwargs = getattr(context, "kwargs", {}) or {}
        return kwargs.get("session_id") or kwargs.get("receiver") or "default"

    def _load_all(self):
        if not os.path.exists(DATA_FILE):
            return {}
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            logger.exception("[MemoryLite] load memory failed")
            return {}

    def _save_all(self, data):
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DATA_FILE)

    def _get_memories(self, session_id):
        with LOCK:
            data = self._load_all()
            return data.get(session_id, [])

    def _add_memory(self, session_id, text):
        max_items = int(os.getenv("MEMORY_MAX_ITEMS", "80"))

        with LOCK:
            data = self._load_all()
            items = data.get(session_id, [])

            memory_key = self._normalize_memory(text)

            # 归一化后重复就不重复存，避免同一件事被模型反复写入。
            for item in items:
                if self._normalize_memory(item.get("text", "")) == memory_key:
                    item["updated_at"] = int(time.time())
                    data[session_id] = items
                    self._save_all(data)
                    logger.info("[MemoryLite] memory duplicated, updated time only")
                    return

            items.append({
                "text": text,
                "created_at": int(time.time()),
                "updated_at": int(time.time()),
            })

            if len(items) > max_items:
                items = items[-max_items:]

            data[session_id] = items
            self._save_all(data)

            logger.info("[MemoryLite] added memory for session=%s: %s", session_id, text)

    def _forget_memory(self, session_id, keyword):
        keyword = keyword.strip()
        if not keyword:
            return 0

        with LOCK:
            data = self._load_all()
            items = data.get(session_id, [])
            old_len = len(items)

            items = [
                item for item in items
                if keyword not in item.get("text", "")
            ]

            data[session_id] = items
            self._save_all(data)

            removed = old_len - len(items)
            logger.info("[MemoryLite] removed %s memories for session=%s keyword=%s", removed, session_id, keyword)
            return removed

    def _clear_memories(self, session_id):
        with LOCK:
            data = self._load_all()
            data[session_id] = []
            self._save_all(data)
            logger.info("[MemoryLite] cleared memories for session=%s", session_id)

    def _is_list_cmd(self, text):
        patterns = [
            r"^我的记忆$",
            r"^查看记忆$",
            r"^记忆列表$",
            r"^你记得我什么",
            r"^你都记得我什么",
            r"^你还记得我什么",
        ]
        return any(re.search(p, text) for p in patterns)

    def _is_clear_cmd(self, text):
        patterns = [
            r"^清空记忆$",
            r"^删除所有记忆$",
            r"^忘记我所有信息$",
            r"^把我的记忆清空$",
        ]
        return any(re.search(p, text) for p in patterns)

    def _extract_remember(self, text):
        patterns = [
            r"^(?:帮我)?记住[：:\s]*(.+)$",
            r"^记一下[：:\s]*(.+)$",
            r"^你要记住[：:\s]*(.+)$",
            r"^以后(?:你)?(?:要)?记得[：:\s]*(.+)$",
        ]
        for p in patterns:
            m = re.search(p, text)
            if m:
                return m.group(1).strip()
        return None

    def _extract_forget(self, text):
        patterns = [
            r"^忘记[：:\s]*(.+)$",
            r"^忘掉[：:\s]*(.+)$",
            r"^删除记忆[：:\s]*(.+)$",
            r"^别记得[：:\s]*(.+)$",
            r"^不要记得[：:\s]*(.+)$",
        ]
        for p in patterns:
            m = re.search(p, text)
            if m:
                return m.group(1).strip()
        return None

    def _extract_auto_memories(self, text, session_id):
        text = self._extract_plain_user_text(text)
        if not text or len(text) < 4:
            return []

        if self._llm_capture_enabled():
            memories = self._extract_auto_memories_by_llm(text, session_id)
            if memories is not None:
                return memories

        return []

    def _extract_auto_memories_by_llm(self, text, session_id):
        api_key = os.getenv("OPEN_AI_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
        base = (os.getenv("OPEN_AI_API_BASE") or "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
        model = os.getenv("MEMORY_CAPTURE_MODEL") or os.getenv("MODEL") or "qwen3.7-plus"

        if not api_key:
            logger.warning("[MemoryLite] MEMORY_AUTO_CAPTURE enabled but api key is missing")
            return None

        max_items = int(os.getenv("MEMORY_CAPTURE_MAX_PER_MESSAGE", "3"))
        existing_memories = self._format_existing_memories(session_id)

        prompt = """你是小悠的长期记忆筛选器，负责判断 YoYo 的微信消息里是否有值得长期记住的信息。

长期记忆只保存未来很多天都可能有用的信息，例如：
- YoYo 的身份资料、称呼、生日、所在地、学校、工作、重要关系
- 稳定偏好、讨厌的东西、习惯、作息、边界、雷点
- 长期目标、正在持续推进的项目、重要承诺
- YoYo 明确希望小悠以后记住的相处方式

不要保存这些内容：
- 普通寒暄、临时心情、一次性抱怨、当天安排、闲聊废话
- 问题本身、模型应该回答的任务、截图/图片的临时描述
- 密码、密钥、验证码、支付信息等敏感凭证
- 小悠自己的设定、系统提示、隐藏上下文

请只返回 JSON，不要解释，不要 Markdown。
格式必须是：
{"memories":["一条长期记忆","另一条长期记忆"]}
如果不值得长期保存，返回：
{"memories":[]}

要求：
1. 每条记忆用简短自然中文，最多 40 个字。
2. 最多输出 %s 条。
3. 尽量写成稳定事实，例如“YoYo 喜欢粉色头像”，不要写“用户刚刚说……”。
4. 如果新消息和已有记忆重复，不要输出。

已有长期记忆：
%s

YoYo 当前微信消息：
%s""" % (max_items, existing_memories if existing_memories else "暂无", text)

        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            "temperature": 0.1,
            "max_tokens": 220,
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
                timeout=45,
            )

            if r.status_code >= 400 and "enable_thinking" in r.text:
                payload.pop("enable_thinking", None)
                r = requests.post(
                    base + "/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=45,
                )

            if r.status_code >= 400:
                logger.warning("[MemoryLite] llm capture error %s: %s", r.status_code, r.text[:500])
                return None

            data = r.json()
            content = data["choices"][0]["message"]["content"]
            memories = self._parse_llm_memories(content)
            if memories:
                logger.info("[MemoryLite] llm selected memories: %r", memories)
            return memories

        except Exception:
            logger.exception("[MemoryLite] llm capture failed")
            return None

    def _format_existing_memories(self, session_id):
        try:
            top_n = int(os.getenv("MEMORY_CAPTURE_EXISTING_TOP_N", "30"))
            items = self._get_memories(session_id)[-top_n:]
            lines = []
            for item in items:
                text = item.get("text", "")
                if text:
                    lines.append("- " + text)
            return "\n".join(lines)
        except Exception:
            logger.exception("[MemoryLite] format existing memory failed")
            return ""

    def _parse_llm_memories(self, content):
        text = str(content or "").strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

        try:
            if text.startswith("["):
                raw_memories = json.loads(text)
            else:
                start = text.find("{")
                end = text.rfind("}")
                if start >= 0 and end >= start:
                    text = text[start:end + 1]
                data = json.loads(text)
                raw_memories = data.get("memories", [])
        except Exception:
            logger.warning("[MemoryLite] parse llm memory json failed: %r", content)
            return None

        if not isinstance(raw_memories, list):
            return []

        max_items = int(os.getenv("MEMORY_CAPTURE_MAX_PER_MESSAGE", "3"))
        memories = []
        seen = set()

        for item in raw_memories:
            memory = self._clean_memory(str(item or ""))
            if not memory:
                continue
            if memory in ("无", "无需记忆", "不需要记忆", "暂无"):
                continue

            key = self._normalize_memory(memory)
            if key in seen:
                continue

            seen.add(key)
            memories.append(memory)

            if len(memories) >= max_items:
                break

        return memories

    def _extract_plain_user_text(self, text):
        text = str(text or "").strip()

        marker = "现在 YoYo 回复："
        if marker in text:
            tail = text.split(marker, 1)[1]
            tail = tail.split("\n\n", 1)[0]
            return tail.strip()

        marker = "[用户当前消息]"
        if marker in text:
            tail = text.split(marker, 1)[1]
            return tail.strip()

        return text

    def _normalize_memory(self, text):
        return re.sub(r"[\s，。,.！!？?：:；;、\"“”'‘’]+", "", text).lower()

    def _clean_memory(self, text):
        text = text.strip()
        text = re.sub(r"^[，。,.！!：:\s]+", "", text)
        text = re.sub(r"[。,.，！!？?\s]+$", "", text)
        if len(text) > 200:
            text = text[:200]
        return text
