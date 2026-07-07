# -*- coding:utf-8 -*-
import os
import re
import json
import time
import threading

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

        # 可选：自动记忆，默认关闭
        if self._auto_capture():
            auto_memory = self._extract_auto_memory(text)
            if auto_memory:
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

            # 完全重复就不重复存
            for item in items:
                if item.get("text") == text:
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

    def _extract_auto_memory(self, text):
        # 默认关闭。开启后，只抓明显偏好，不抓普通废话。
        if len(text) > 80:
            return None

        patterns = [
            r"^(我喜欢.+)$",
            r"^(我不喜欢.+)$",
            r"^(我讨厌.+)$",
            r"^(我的生日是.+)$",
            r"^(我叫.+)$",
            r"^(我最喜欢.+)$",
        ]
        for p in patterns:
            m = re.search(p, text)
            if m:
                return self._clean_memory(m.group(1).strip())
        return None

    def _clean_memory(self, text):
        text = text.strip()
        text = re.sub(r"^[，。,.！!：:\s]+", "", text)
        text = re.sub(r"[。,.，\s]+$", "", text)
        if len(text) > 200:
            text = text[:200]
        return text
