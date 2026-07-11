import plugins
import os
import re
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
from plugins.xiaoyou_common.trace_service import trace_event


@plugins.register(
    name="AliyunMemory",
    desire_priority=900,
    hidden=False,
    desc="Use Alibaba Bailian Memory Library for long-term memory",
    version="0.4-trace-runtime",
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

    def _search_memory(self, query, retrieval_mode="normal", result_limit=None):
        if not self.api_key:
            logger.warning("[AliyunMemory] missing api key")
            return []

        retrieval_mode = str(retrieval_mode or "normal").strip().lower()
        try:
            result_limit = int(result_limit or self.max_results)
        except Exception:
            result_limit = self.max_results
        result_limit = max(1, result_limit)

        if retrieval_mode == "recovery":
            fetch_k = max(
                result_limit,
                int(os.getenv("ALIYUN_MEMORY_RECOVERY_FETCH_K", "20")),
            )
        else:
            fetch_k = max(result_limit, self.max_results)

        payload = {
            "user_id": self.user_id,
            "messages": [
                {"role": "user", "content": query}
            ],
            "top_k": fetch_k
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
            for api_rank, node in enumerate(nodes):
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
                    "memory_id": str(
                        node.get("memory_node_id")
                        or node.get("node_id")
                        or node.get("id")
                        or ""
                    ),
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
                    "similarity_score": self._memory_similarity_score(node, meta),
                    "api_rank": api_rank,
                })

            ranked = self._rank_memories(
                memories,
                query=query,
                retrieval_mode=retrieval_mode,
            )
            selected = ranked[:result_limit]
            logger.info(
                "[AliyunMemory] search got %s memories mode=%s selected=%s",
                len(memories),
                retrieval_mode,
                self._selection_log(selected),
            )
            return selected

        except Exception as e:
            logger.warning(f"[AliyunMemory] search exception: {e}")
            return []

    def _memory_similarity_score(self, node, meta):
        for source in (node, meta):
            for key in ("score", "similarity", "similarity_score", "relevance_score"):
                value = source.get(key)
                if value in (None, ""):
                    continue
                try:
                    score = float(value)
                    if 0 <= score <= 1:
                        return score
                except Exception:
                    continue
        return None

    def _rank_memories(self, memories, query, retrieval_mode="normal"):
        if not memories:
            return []

        mode = str(retrieval_mode or "normal").strip().lower()
        temporal_intent = self._temporal_intent(query)
        total = max(1, len(memories))

        if temporal_intent:
            time_weight = self._env_weight("ALIYUN_MEMORY_EXPLICIT_TIME_WEIGHT", 0.75)
        elif mode == "recovery":
            time_weight = self._env_weight("ALIYUN_MEMORY_RECOVERY_TIME_WEIGHT", 0.65)
        else:
            time_weight = self._env_weight("ALIYUN_MEMORY_NORMAL_TIME_WEIGHT", 0.25)
        semantic_weight = 1.0 - time_weight

        ranked = []
        for memory in memories:
            api_rank = int(memory.get("api_rank") or 0)
            api_score = memory.get("similarity_score")
            if api_score is None:
                semantic_score = max(0.0, 1.0 - (api_rank / float(total)))
            else:
                semantic_score = float(api_score)
                if 0 < self.similarity_threshold and semantic_score < self.similarity_threshold:
                    semantic_score *= max(
                        0.15,
                        semantic_score / self.similarity_threshold,
                    )

            memory_dt = self._effective_memory_time(memory)
            time_score = self._memory_time_score(
                memory_dt,
                temporal_intent=temporal_intent,
                recovery_mode=(mode == "recovery"),
            )
            combined_score = (
                semantic_score * semantic_weight
                + time_score * time_weight
            )

            memory = dict(memory)
            memory["retrieval_score"] = round(combined_score, 6)
            memory["retrieval_time_score"] = round(time_score, 6)
            ranked.append(memory)

        return sorted(
            ranked,
            key=lambda memory: (
                float(memory.get("retrieval_score") or 0),
                self._memory_timestamp(memory),
                -int(memory.get("api_rank") or 0),
            ),
            reverse=True,
        )

    def _env_weight(self, key, default):
        try:
            value = float(os.getenv(key, str(default)))
        except Exception:
            value = float(default)
        return max(0.0, min(value, 1.0))

    def _temporal_intent(self, query):
        text = str(query or "")
        if re.search(r"刚才|刚刚|方才|这会儿|刚刚那|刚才那", text):
            return "recent"
        if re.search(r"今天|今早|今晨|上午|中午|下午|今晚|今夜", text):
            return "today"
        if re.search(r"昨晚|昨天|昨日", text):
            return "yesterday"
        if "前天" in text:
            return "day_before_yesterday"
        return ""

    def _effective_memory_time(self, memory):
        updated = self._parse_memory_time(memory.get("updated_at"))
        created = self._parse_memory_time(memory.get("created_at"))
        if updated and created:
            return max(updated, created)
        return updated or created

    def _memory_timestamp(self, memory):
        memory_dt = self._effective_memory_time(memory)
        return memory_dt.timestamp() if memory_dt else 0

    def _memory_time_score(self, memory_dt, temporal_intent="", recovery_mode=False):
        if not memory_dt:
            return 0.08

        now = datetime.now(self._tz())
        age_seconds = max(0, (now - memory_dt).total_seconds())
        day_age = (now.date() - memory_dt.date()).days

        if temporal_intent == "recent":
            if age_seconds <= 2 * 3600:
                return 1.0
            if age_seconds <= 6 * 3600:
                return 0.72
            if age_seconds <= 24 * 3600:
                return 0.28
            return 0.02
        if temporal_intent == "today":
            return 1.0 if day_age == 0 else (0.12 if day_age == 1 else 0.02)
        if temporal_intent == "yesterday":
            return 1.0 if day_age == 1 else (0.15 if day_age == 0 else 0.03)
        if temporal_intent == "day_before_yesterday":
            return 1.0 if day_age == 2 else 0.03

        # 恢复模式即使没有明确时间词，也默认把“当前断联”理解为近期事件。
        if age_seconds <= 2 * 3600:
            return 1.0
        if age_seconds <= 24 * 3600:
            return 0.45 if recovery_mode else 0.68
        if age_seconds <= 7 * 86400:
            return 0.22 if recovery_mode else 0.36
        if age_seconds <= 30 * 86400:
            return 0.14
        return 0.04

    def _selection_log(self, memories):
        selected = []
        for memory in memories:
            memory_dt = self._effective_memory_time(memory)
            memory_id = str(memory.get("memory_id") or "")
            selected.append({
                "id": memory_id[-10:] if memory_id else "rank-%s" % int(memory.get("api_rank") or 0),
                "time": memory_dt.strftime("%Y-%m-%d %H:%M") if memory_dt else "unknown",
                "score": memory.get("retrieval_score"),
                "time_score": memory.get("retrieval_time_score"),
            })
        return selected

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

    def build_memory_context(self, query, max_results=None, retrieval_mode="normal"):
        """供主动消息等插件复用的只读长期记忆上下文。"""
        if not self.enabled:
            return ""

        query = str(query or "").strip()
        if not query:
            return ""

        try:
            result_limit = int(max_results) if max_results is not None else self.max_results
        except Exception:
            result_limit = self.max_results
        result_limit = max(1, result_limit)

        memories = self._search_memory(
            query,
            retrieval_mode=retrieval_mode,
            result_limit=result_limit,
        )
        return "\n".join(
            self._format_memory_line(memory)
            for memory in memories
            if memory
        )

    def _add_memory(
        self,
        user_text,
        assistant_text,
        trace_id="",
        input_id="",
        session_id="",
    ):
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
                if trace_id:
                    trace_event(
                        "long_memory_recorded",
                        status="failed",
                        trace_id=trace_id,
                        input_id=input_id,
                        session_id=session_id,
                        attrs={
                            "component": "AliyunMemory",
                            "record_source": "conversation",
                            "status_code": resp.status_code,
                            "error_kind": "provider_error",
                        },
                    )
            else:
                logger.info("[AliyunMemory] memory added")
                if trace_id:
                    trace_event(
                        "long_memory_recorded",
                        status="saved",
                        trace_id=trace_id,
                        input_id=input_id,
                        session_id=session_id,
                        memory_record_id=self._memory_write_id(resp),
                        attrs={
                            "component": "AliyunMemory",
                            "record_source": "conversation",
                            "status_code": resp.status_code,
                        },
                    )

        except Exception as e:
            logger.warning(f"[AliyunMemory] add exception: {e}")
            if trace_id:
                trace_event(
                    "long_memory_recorded",
                    status="failed",
                    trace_id=trace_id,
                    input_id=input_id,
                    session_id=session_id,
                    attrs={
                        "component": "AliyunMemory",
                        "record_source": "conversation",
                        "error_kind": "exception",
                    },
                )

    def _memory_write_id(self, response):
        try:
            data = response.json()
        except Exception:
            return ""
        if not isinstance(data, dict):
            return ""
        containers = [data]
        for key in ("data", "output", "result"):
            value = data.get(key)
            if isinstance(value, dict):
                containers.append(value)
        for container in containers:
            for key in ("memory_id", "memory_node_id", "id", "request_id"):
                value = container.get(key)
                if value:
                    return str(value)[:80]
        return ""

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
        kwargs = getattr(context, "kwargs", {}) or {}

        if not user_text or not assistant_text:
            return
        if assistant_text.startswith("[ERROR]"):
            return

        threading.Thread(
            target=self._add_memory,
            args=(
                user_text,
                assistant_text,
                kwargs.get("xiaoyou_trace_id", ""),
                kwargs.get("xiaoyou_input_id", ""),
                session_id,
            ),
            daemon=True
        ).start()
