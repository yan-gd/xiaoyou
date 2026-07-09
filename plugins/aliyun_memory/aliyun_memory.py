import plugins
import os
import json
import time
import threading
import requests
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

from plugins import *
from bridge.context import ContextType
from bridge.reply import ReplyType
from common.log import logger


@plugins.register(
    name="AliyunMemory",
    desire_priority=900,
    hidden=False,
    desc="Use Alibaba Bailian Memory Library for long-term memory",
    version="0.2-time-aware",
    author="YOYO"
)
class AliyunMemory(Plugin):
    def __init__(self):
        super().__init__()
        self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
        self.handlers[Event.ON_DECORATE_REPLY] = self.on_decorate_reply

        self.api_key = (
            os.getenv("ALIYUN_MEMORY_API_KEY")
            or os.getenv("DASHSCOPE_API_KEY")
            or os.getenv("OPEN_AI_API_KEY")
        )
        self.user_id = os.getenv("ALIYUN_MEMORY_USER_ID", "yoyo")
        self.memory_library_id = os.getenv("ALIYUN_MEMORY_LIBRARY_ID", "").strip()
        self.max_results = int(os.getenv("ALIYUN_MEMORY_MAX_RESULTS", "5"))
        self.similarity_threshold = float(os.getenv("ALIYUN_MEMORY_THRESHOLD", "0.55"))
        self.enabled = os.getenv("ALIYUN_MEMORY_ENABLED", "true").lower() == "true"

        self.base_url = "https://dashscope.aliyuncs.com/api/v2/apps/memory"
        self.last_user_msg = {}

        logger.info("[AliyunMemory] inited")

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

    def _safe_text(self, text):
        if not text:
            return False
        text = text.strip()
        if text.startswith("#") or text.startswith("$"):
            return False
        if "sk-" in text or "api key" in text.lower() or "apikey" in text.lower():
            return False
        return True

    def _search_memory(self, query):
        if not self.api_key:
            logger.warning("[AliyunMemory] missing api key")
            return []

        payload = {
            "user_id": self.user_id,
            "messages": [
                {"role": "user", "content": query}
            ],
            "top_k": self.max_results
        }

        if self.memory_library_id:
            payload["memory_library_id"] = self.memory_library_id

        try:
            resp = requests.post(
                f"{self.base_url}/memory_nodes/search",
                headers=self._headers(),
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                timeout=8
            )

            if resp.status_code != 200:
                logger.warning(f"[AliyunMemory] search failed: {resp.status_code} {resp.text[:500]}")
                return []

            data = resp.json()

            nodes = (
                data.get("memory_nodes")
                or data.get("data", {}).get("memory_nodes")
                or data.get("output", {}).get("memory_nodes")
                or data.get("result", {}).get("memory_nodes")
                or []
            )

            memories = []
            for node in nodes:
                if not isinstance(node, dict):
                    continue

                content = (
                    node.get("content")
                    or node.get("custom_content")
                    or node.get("text")
                    or node.get("summary")
                    or ""
                )

                if not content:
                    continue

                meta = node.get("meta_data") or node.get("metadata") or {}
                if not isinstance(meta, dict):
                    meta = {}

                memories.append({
                    "content": str(content).strip(),
                    "created_at": (
                        node.get("created_at")
                        or node.get("gmt_create")
                        or node.get("create_time")
                        or meta.get("created_at")
                        or meta.get("record_time")
                    ),
                    "updated_at": (
                        node.get("updated_at")
                        or node.get("gmt_modified")
                        or node.get("gmt_update")
                        or node.get("update_time")
                        or meta.get("updated_at")
                    ),
                })

            logger.info(f"[AliyunMemory] search got {len(memories)} memories")
            return memories[: self.max_results]

        except Exception as e:
            logger.warning(f"[AliyunMemory] search exception: {e}")
            return []

    def _tz(self):
        tz_name = os.getenv("ALIYUN_MEMORY_TIMEZONE", os.getenv("TZ", "Asia/Shanghai")).strip() or "Asia/Shanghai"
        if ZoneInfo:
            try:
                return ZoneInfo(tz_name)
            except Exception:
                pass
        return timezone.utc

    def _parse_memory_time(self, value):
        if value in (None, ""):
            return None

        try:
            if isinstance(value, str):
                raw = value.strip()
                if not raw:
                    return None

                if raw.isdigit():
                    value = int(raw)
                else:
                    raw = raw.replace("Z", "+00:00")
                    try:
                        dt = datetime.fromisoformat(raw)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        return dt.astimezone(self._tz())
                    except Exception:
                        return None

            if isinstance(value, (int, float)):
                ts = float(value)
                # Bailian examples use seconds. Be tolerant of millisecond timestamps.
                if ts > 100000000000:
                    ts = ts / 1000.0
                return datetime.fromtimestamp(ts, timezone.utc).astimezone(self._tz())
        except Exception:
            return None

        return None

    def _relative_time_label(self, dt):
        if not dt:
            return ""

        now = datetime.now(self._tz())
        delta = now - dt
        seconds = int(delta.total_seconds())

        if seconds < 0:
            return "刚刚"
        if seconds < 60:
            return "刚刚"
        if seconds < 3600:
            return f"{seconds // 60}分钟前"
        if seconds < 86400:
            return f"{seconds // 3600}小时前"
        if seconds < 86400 * 2:
            return "昨天"
        if seconds < 86400 * 7:
            return f"{seconds // 86400}天前"
        if seconds < 86400 * 30:
            return f"{seconds // 604800}周前"
        if seconds < 86400 * 365:
            return f"{seconds // 2592000}个月前"
        return f"{seconds // 31536000}年前"

    def _format_memory_time(self, created_at, updated_at):
        created_dt = self._parse_memory_time(created_at)
        updated_dt = self._parse_memory_time(updated_at)

        if not created_dt and not updated_dt:
            return "时间未知"

        parts = []
        if created_dt:
            parts.append("记录于：%s（%s）" % (
                created_dt.strftime("%Y-%m-%d %H:%M"),
                self._relative_time_label(created_dt),
            ))

        if updated_dt:
            should_show_update = True
            if created_dt:
                should_show_update = abs((updated_dt - created_dt).total_seconds()) >= 60
            if should_show_update:
                parts.append("更新于：%s（%s）" % (
                    updated_dt.strftime("%Y-%m-%d %H:%M"),
                    self._relative_time_label(updated_dt),
                ))

        return "；".join(parts) if parts else "时间未知"

    def _format_memory_line(self, memory):
        if isinstance(memory, dict):
            content = str(memory.get("content") or "").strip()
            time_label = self._format_memory_time(memory.get("created_at"), memory.get("updated_at"))
            return f"- [{time_label}] {content}"

        return f"- [时间未知] {str(memory).strip()}"

    def _add_memory(self, user_text, assistant_text):
        if not self.api_key:
            return
        if not self._safe_text(user_text) or not self._safe_text(assistant_text):
            return

        payload = {
            "user_id": self.user_id,
            "messages": [
                {"role": "user", "content": user_text[:2000]},
                {"role": "assistant", "content": assistant_text[:2000]}
            ],
            "meta_data": {
                "source": "chatgpt-on-wechat",
                "role": "xiaoyou",
                "created_at": int(time.time())
            }
        }

        if self.memory_library_id:
            payload["memory_library_id"] = self.memory_library_id

        try:
            resp = requests.post(
                f"{self.base_url}/add",
                headers=self._headers(),
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                timeout=10
            )

            if resp.status_code != 200:
                logger.warning(f"[AliyunMemory] add failed: {resp.status_code} {resp.text[:300]}")
            else:
                logger.info("[AliyunMemory] memory added")

        except Exception as e:
            logger.warning(f"[AliyunMemory] add exception: {e}")

    def on_handle_context(self, e_context: EventContext):
        if not self.enabled:
            return

        context = e_context["context"]
        if context.type != ContextType.TEXT:
            return

        user_text = context.content
        if not self._safe_text(user_text):
            return

        session_id = context.get("session_id", self.user_id)
        self.last_user_msg[session_id] = user_text

        memories = self._search_memory(user_text)
        if not memories:
            return

        memory_block = "\n".join([self._format_memory_line(m) for m in memories])
        context.content = (
            "以下是关于 YOYO 的长期记忆，带记录时间，只用于理解他的偏好、关系背景和近期状态。"
            "不要逐条复述，不要说“我记得数据库里写着”。\n"
            "越新的记忆通常越可信；旧记忆可能已经变化，不要把旧状态当成永久事实。\n"
            f"{memory_block}\n\n"
            f"YOYO 当前发来的微信消息：{user_text}"
        )

        logger.info(f"[AliyunMemory] injected {len(memories)} memories")

    def on_decorate_reply(self, e_context: EventContext):
        if not self.enabled:
            return

        context = e_context["context"]
        reply = e_context["reply"]

        if context.type != ContextType.TEXT:
            return
        if reply.type != ReplyType.TEXT:
            return

        session_id = context.get("session_id", self.user_id)
        user_text = self.last_user_msg.get(session_id)
        assistant_text = reply.content

        if not user_text or not assistant_text:
            return
        if assistant_text.startswith("[ERROR]"):
            return

        threading.Thread(
            target=self._add_memory,
            args=(user_text, assistant_text),
            daemon=True
        ).start()
