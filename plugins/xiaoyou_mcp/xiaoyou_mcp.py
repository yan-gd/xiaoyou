# -*- coding:utf-8 -*-
import json
import os
import re
import time
from datetime import datetime

import requests
import plugins
from plugins import *
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger
from plugins.xiaoyou_common.time_context import build_time_context


TOOLS_CACHE = {}
SESSION_CACHE = {}


@plugins.register(
    name="XiaoyouMCP",
    desc="Give Xiaoyou MCP tools for search, weather and map",
    version="0.5-no-time",
    author="yoyo",
    desire_priority=30,
)
class XiaoyouMCP(Plugin):
    def __init__(self):
        super().__init__()
        self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
        logger.info("[XiaoyouMCP] inited")

    def on_handle_context(self, e_context: EventContext):
        if not self._enabled():
            return

        context = e_context["context"]
        if context.type != ContextType.TEXT:
            return

        kwargs = getattr(context, "kwargs", {}) or {}
        if kwargs.get("isgroup"):
            return

        text = self._extract_plain_user_text(context.content)
        if not text:
            return

        kind = self._route_intent(text)
        if not kind:
            return

        endpoint = self._endpoint_for_kind(kind)
        if not endpoint:
            reply = self._format_error_reply(
                kind=kind,
                user_text=text,
                stage="missing_config",
                error="endpoint environment variable is not configured",
            )
            self._set_reply_or_silence(e_context, reply)
            return

        try:
            tools = self._list_tools(endpoint)
            tool_name = self._select_tool(kind, tools)
            if not tool_name:
                reply = self._format_error_reply(
                    kind=kind,
                    user_text=text,
                    stage="tool_not_found",
                    error="no matching tool name found in tools/list",
                    tool_name="",
                )
                self._set_reply_or_silence(e_context, reply)
                return

            tool_schema = self._tool_schema(tools, tool_name)
            args = self._build_tool_args(kind, text, tool_name, tool_schema)
            result = self._call_mcp_tool(endpoint, tool_name, args)
            reply = self._format_reply(kind, text, result, tool_name=tool_name)
            self._set_reply_or_silence(e_context, reply)

        except Exception as ex:
            logger.exception("[XiaoyouMCP] call failed kind=%s text=%r", kind, text[:100])
            reply = self._format_error_reply(
                kind=kind,
                user_text=text,
                stage="call_failed",
                error=ex,
            )
            self._set_reply_or_silence(e_context, reply)

    def _enabled(self):
        return os.getenv("XIAOYOU_MCP_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")

    def _route_intent(self, text):
        """Use the LLM to decide whether the current user message needs an MCP tool.

        This intentionally avoids keyword-based routing. The model must return one of:
        none, weather, map, map_search, search.
        """
        text = str(text or "").strip()
        if not text:
            return None

        decision = self._route_intent_with_llm(text)
        kind = str((decision or {}).get("kind") or "none").strip().lower()
        confidence = float((decision or {}).get("confidence") or 0)
        reason = str((decision or {}).get("reason") or "")[:200]

        valid = {"none", "weather", "map", "map_search", "search"}
        if kind == "time":
            logger.info("[XiaoyouMCP] time service removed, ignore text=%r", text[:80])
            return None

        if kind not in valid:
            logger.info("[XiaoyouMCP] llm route invalid kind=%r text=%r", kind, text[:80])
            return None

        threshold = float(os.getenv("XIAOYOU_MCP_ROUTE_THRESHOLD", "0.68"))
        logger.info(
            "[XiaoyouMCP] llm route text=%r kind=%s confidence=%.2f reason=%s",
            text[:100],
            kind,
            confidence,
            reason,
        )

        if kind == "none" or confidence < threshold:
            return None

        return kind

    def _route_intent_with_llm(self, text):
        api_key = os.getenv("OPEN_AI_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            logger.warning("[XiaoyouMCP] route llm skipped: OPEN_AI_API_KEY missing")
            return {"kind": "none", "confidence": 0, "reason": "api key missing"}

        base = (os.getenv("OPEN_AI_API_BASE") or "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
        model = os.getenv("XIAOYOU_MCP_ROUTE_MODEL") or os.getenv("MODEL") or "qwen3.7-plus"
        location = os.getenv("XIAOYOU_DEFAULT_LOCATION", "").strip()
        origin = os.getenv("XIAOYOU_MAP_DEFAULT_ORIGIN", "").strip()
        prompt = """你是一个微信聊天里的工具路由判断器。你的任务是判断 YoYo 当前这句话是否真的需要调用外部 MCP 工具。

只能输出 JSON 对象，不要 Markdown，不要解释。

可选 kind：
- none：普通聊天、撒娇、吐槽、情绪表达、反问、玩笑、无需外部信息
- weather：天气、气温、下雨下雪、穿衣建议、空气质量等
- map：路线、导航、从 A 到 B、开车/步行/地铁/公交怎么走、距离/耗时
- map_search：查找地点/店铺/地址/营业时间/附近有什么/某类场所在哪里
- search：联网搜索、新闻、资料、价格、汇率、网页/链接内容、赛事/比分/最新信息

重要判断规则：
1. 不要根据单个词触发工具；必须理解整句话的真实意图。
2. 例如“哪有开玩笑的小悠”“几点啦你还不睡”“附近一点啦”这类亲密聊天，应该是 none。
3. 只有用户真的在索要外部事实、实时信息、地点、路线、天气或网页搜索时，才选择工具。
4. 如果不确定，选 none。
5. confidence 范围 0 到 1。只有非常明确才给 0.75 以上。

默认信息，仅供判断参考：
- 默认城市：%s
- 默认出发地：%s

YoYo 当前原话：
%s

请只输出 JSON，例如：
{"kind":"none","confidence":0.92,"reason":"这是亲密聊天，不是在查询外部信息"}
""" % (
            location or "未配置",
            origin or "未配置",
            text,
        )

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
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
                timeout=int(os.getenv("XIAOYOU_MCP_ROUTE_TIMEOUT", "20")),
            )

            if r.status_code >= 400 and "enable_thinking" in r.text:
                payload.pop("enable_thinking", None)
                r = requests.post(
                    base + "/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=int(os.getenv("XIAOYOU_MCP_ROUTE_TIMEOUT", "20")),
                )

            if r.status_code >= 400:
                logger.warning("[XiaoyouMCP] route llm error %s: %s", r.status_code, r.text[:500])
                return {"kind": "none", "confidence": 0, "reason": "route llm http error"}

            content = r.json()["choices"][0]["message"].get("content", "").strip()
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)
            start = content.find("{")
            end = content.rfind("}")
            if start >= 0 and end >= start:
                content = content[start:end + 1]

            data = json.loads(content)
            if not isinstance(data, dict):
                raise ValueError("route llm did not return object")

            return {
                "kind": str(data.get("kind") or "none"),
                "confidence": float(data.get("confidence") or 0),
                "reason": str(data.get("reason") or ""),
            }

        except Exception:
            logger.exception("[XiaoyouMCP] route llm failed")
            return {"kind": "none", "confidence": 0, "reason": "route llm failed"}


    def _endpoint_for_kind(self, kind):
        if kind in ("weather", "map", "map_search"):
            return os.getenv("XIAOYOU_MCP_AMAP_ENDPOINT", "").strip() or os.getenv("XIAOYOU_MCP_ENDPOINT", "").strip()
        if kind == "search":
            return os.getenv("XIAOYOU_MCP_SEARCH_ENDPOINT", "").strip()
        return ""

    def _missing_config_reply(self, kind):
        # Kept only for compatibility. Preset replies are intentionally disabled.
        return None

    def _select_tool(self, kind, tools):
        configured = self._configured_tool_name(kind)
        tool_names = [t.get("name", "") for t in tools if isinstance(t, dict)]

        if configured and configured in tool_names:
            return configured

        aliases = {
            "weather": ["maps_weather", "weather"],
            "map": ["maps_direction_driving", "direction_driving", "driving", "route", "direction"],
            "map_search": ["maps_text_search", "text_search", "around_search", "search"],
            "search": ["bing_search", "bing-cn", "web_search", "search"],
        }.get(kind, [])

        lowered = [(name, name.lower()) for name in tool_names]
        for alias in aliases:
            alias_l = alias.lower()
            for name, low in lowered:
                if low == alias_l:
                    return name

        for alias in aliases:
            alias_l = alias.lower()
            for name, low in lowered:
                if alias_l in low:
                    return name

        return configured or (tool_names[0] if len(tool_names) == 1 else "")

    def _configured_tool_name(self, kind):
        env_names = {
            "weather": "XIAOYOU_MCP_AMAP_WEATHER_TOOL",
            "map": "XIAOYOU_MCP_AMAP_ROUTE_TOOL",
            "map_search": "XIAOYOU_MCP_AMAP_SEARCH_TOOL",
            "search": "XIAOYOU_MCP_SEARCH_TOOL",
        }

        defaults = {
            "weather": "maps_weather",
            "map": "maps_direction_driving",
            "map_search": "maps_text_search",
            "search": "bing_search",
        }

        return os.getenv(env_names.get(kind, ""), defaults.get(kind, "")).strip()

    def _build_tool_args(self, kind, text, tool_name, tool_schema):
        args = self._build_args_with_llm(kind, text, tool_name, tool_schema)
        if args is not None:
            return args

        return self._fallback_tool_args(kind, text)

    def _build_args_with_llm(self, kind, text, tool_name, tool_schema):
        if os.getenv("XIAOYOU_MCP_AUTO_SCHEMA", "true").strip().lower() not in ("1", "true", "yes", "on"):
            return None

        schema = self._input_schema(tool_schema)
        if not schema:
            return None

        api_key = os.getenv("OPEN_AI_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            return None

        base = (os.getenv("OPEN_AI_API_BASE") or "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
        model = os.getenv("XIAOYOU_MCP_ARG_MODEL") or os.getenv("MODEL") or "qwen3.7-plus"
        location = os.getenv("XIAOYOU_DEFAULT_LOCATION", "").strip()
        origin = os.getenv("XIAOYOU_MAP_DEFAULT_ORIGIN", "").strip()
        prompt = """你要为 MCP 工具生成调用参数。
只能输出 JSON 对象，不要 Markdown，不要解释。

用户原话：
%s

工具用途：%s
工具名：%s

默认信息：
- 默认城市：%s
- 默认出发地：%s

工具 inputSchema：
%s

要求：
1. 参数必须尽量符合 inputSchema。
2. 不要输出 schema 中明显不存在的字段。
3. 如果用户没说城市，天气默认使用默认城市；如果默认城市也没有，就从用户原话推断。
4. 如果路线没说出发地，可以使用默认出发地。
5. 如果是搜索/链接工具，并且用户给了 URL，要把 URL 作为查询重点。
""" % (
            text,
            kind,
            tool_name,
            location or "未配置",
            origin or "未配置",
            json.dumps(schema, ensure_ascii=False),
        )

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 300,
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
                timeout=30,
            )

            if r.status_code >= 400 and "enable_thinking" in r.text:
                payload.pop("enable_thinking", None)
                r = requests.post(
                    base + "/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=30,
                )

            if r.status_code >= 400:
                logger.warning("[XiaoyouMCP] arg llm error %s: %s", r.status_code, r.text[:500])
                return None

            content = r.json()["choices"][0]["message"]["content"].strip()
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)
            start = content.find("{")
            end = content.rfind("}")
            if start >= 0 and end >= start:
                content = content[start:end + 1]

            args = json.loads(content)
            return args if isinstance(args, dict) else None

        except Exception:
            logger.exception("[XiaoyouMCP] build args by llm failed")
            return None

    def _fallback_tool_args(self, kind, text):
        location = os.getenv("XIAOYOU_DEFAULT_LOCATION", "").strip()


        if kind == "weather":
            return {"city": location or text}

        if kind == "map":
            origin, destination = self._extract_route(text)
            default_origin = os.getenv("XIAOYOU_MAP_DEFAULT_ORIGIN", "").strip()
            args = {"query": text}
            if origin:
                args["origin"] = origin
            elif default_origin:
                args["origin"] = default_origin
            if destination:
                args["destination"] = destination
            return args

        if kind == "map_search":
            return {"keywords": text, "city": location}

        return {"query": text}

    def _extract_route(self, text):
        patterns = [
            r"从(.+?)到(.+?)(?:怎么去|怎么走|路线|导航|多久|$)",
            r"(.+?)到(.+?)(?:怎么去|怎么走|路线|导航|多久)",
            r"去(.+?)(?:怎么走|怎么去|路线|导航|多久|$)",
        ]

        for pattern in patterns:
            m = re.search(pattern, text)
            if not m:
                continue

            if len(m.groups()) >= 2:
                return self._clean_place(m.group(1)), self._clean_place(m.group(2))

            return "", self._clean_place(m.group(1))

        return "", ""

    def _clean_place(self, value):
        value = str(value or "").strip()
        value = re.sub(r"^(我想|我要|帮我|给我|请问|现在|今天)", "", value)
        value = re.sub(r"[，。,.！!？?\s]+$", "", value)
        return value.strip()

    def _extract_url(self, text):
        m = re.search(r"https?://[^\s，。！？]+", str(text or ""))
        return m.group(0) if m else ""

    def _list_tools(self, endpoint):
        endpoint = endpoint.strip()
        cache_ttl = int(os.getenv("XIAOYOU_MCP_TOOLS_CACHE_SECONDS", "3600"))
        cached = TOOLS_CACHE.get(endpoint)
        now = time.time()

        if cached and now - cached.get("ts", 0) < cache_ttl:
            return cached.get("tools", [])

        data = self._jsonrpc(endpoint, "tools/list", None)
        result = data.get("result", data)
        tools = result.get("tools", []) if isinstance(result, dict) else []

        if not isinstance(tools, list):
            tools = []

        TOOLS_CACHE[endpoint] = {
            "ts": now,
            "tools": tools,
        }

        logger.info("[XiaoyouMCP] loaded %s tools from %s", len(tools), endpoint[:80])
        return tools

    def _tool_schema(self, tools, tool_name):
        for tool in tools:
            if isinstance(tool, dict) and tool.get("name") == tool_name:
                return tool
        return {}

    def _input_schema(self, tool_schema):
        if not isinstance(tool_schema, dict):
            return {}
        return tool_schema.get("inputSchema") or tool_schema.get("input_schema") or tool_schema.get("schema") or {}

    def _call_mcp_tool(self, endpoint, tool_name, arguments):
        mode = os.getenv("XIAOYOU_MCP_MODE", "streamable_http").strip().lower()

        if mode == "rest":
            return self._call_rest_tool(endpoint, tool_name, arguments)

        data = self._jsonrpc(
            endpoint,
            "tools/call",
            {
                "name": tool_name,
                "arguments": arguments,
            },
        )

        return self._extract_result_text(data)

    def _jsonrpc(self, endpoint, method, params=None):
        endpoint = endpoint.strip()
        timeout = int(os.getenv("XIAOYOU_MCP_TIMEOUT", "45"))

        def _is_session_expired_error(value):
            msg = str(value or "")
            low = msg.lower()
            return (
                "SessionExpired" in msg
                or ("session" in low and "expired" in low)
                or "MCP error 401" in msg
                or " 401" in msg
            )

        def _send_once():
            request_id = int(time.time() * 1000)

            if method != "initialize" and endpoint not in SESSION_CACHE:
                self._try_initialize(endpoint)

            payload = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
            }
            if params is not None:
                payload["params"] = params

            r = requests.post(
                endpoint,
                headers=self._headers(endpoint),
                json=payload,
                timeout=timeout,
            )

            self._capture_session_id(endpoint, r)
            return self._parse_response_json(r)

        try:
            data = _send_once()
        except requests.exceptions.RequestException as e:
            logger.warning(
                "[XiaoyouMCP] MCP network error, retry once method=%s endpoint=%s err=%s",
                method,
                endpoint[:80],
                e,
            )
            time.sleep(1)
            data = _send_once()
        except Exception as e:
            if method == "initialize" or not _is_session_expired_error(e):
                raise

            logger.warning("[XiaoyouMCP] MCP session expired, reinitializing endpoint=%s", endpoint[:80])
            try:
                SESSION_CACHE.pop(endpoint, None)
            except Exception:
                pass

            self._try_initialize(endpoint)
            data = _send_once()

        if isinstance(data, dict) and data.get("error"):
            err = data.get("error")

            if method != "initialize" and _is_session_expired_error(err):
                logger.warning("[XiaoyouMCP] MCP jsonrpc session expired, reinitializing endpoint=%s", endpoint[:80])
                try:
                    SESSION_CACHE.pop(endpoint, None)
                except Exception:
                    pass

                self._try_initialize(endpoint)
                data = _send_once()

                if isinstance(data, dict) and data.get("error"):
                    raise RuntimeError("MCP error: %s" % data.get("error"))

                return data

            raise RuntimeError("MCP error: %s" % err)

        return data

    def _try_initialize(self, endpoint):
        payload = {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "xiaoyou-cow",
                    "version": "0.1",
                },
            },
        }

        try:
            r = requests.post(
                endpoint,
                headers=self._headers(endpoint, include_session=False),
                json=payload,
                timeout=int(os.getenv("XIAOYOU_MCP_TIMEOUT", "45")),
            )
            self._capture_session_id(endpoint, r)

            try:
                notify_payload = {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                }
                requests.post(
                    endpoint,
                    headers=self._headers(endpoint),
                    json=notify_payload,
                    timeout=int(os.getenv("XIAOYOU_MCP_TIMEOUT", "45")),
                )
            except Exception:
                logger.info("[XiaoyouMCP] initialized notification skipped/failed for %s", endpoint[:80])

        except Exception:
            logger.info("[XiaoyouMCP] initialize skipped/failed for %s", endpoint[:80])

    def _call_rest_tool(self, endpoint, tool_name, arguments):
        timeout = int(os.getenv("XIAOYOU_MCP_TIMEOUT", "45"))
        url = "%s/%s" % (endpoint.strip().rstrip("/"), tool_name)

        r = requests.post(
            url,
            headers=self._headers(endpoint),
            json=arguments,
            timeout=timeout,
        )

        data = self._parse_response_json(r)
        return self._extract_result_text(data)

    def _headers(self, endpoint, include_session=True):
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

        token = os.getenv("XIAOYOU_MCP_TOKEN", "").strip()
        if token:
            header = os.getenv("XIAOYOU_MCP_AUTH_HEADER", "Authorization").strip()
            scheme = os.getenv("XIAOYOU_MCP_AUTH_SCHEME", "Bearer").strip()
            headers[header] = ("%s %s" % (scheme, token)) if scheme else token

        if include_session and SESSION_CACHE.get(endpoint):
            headers["Mcp-Session-Id"] = SESSION_CACHE[endpoint]

        return headers

    def _capture_session_id(self, endpoint, response):
        session_id = (
            response.headers.get("Mcp-Session-Id")
            or response.headers.get("MCP-Session-Id")
            or response.headers.get("mcp-session-id")
        )
        if session_id:
            SESSION_CACHE[endpoint] = session_id

    def _parse_response_json(self, response):
        if response.status_code == 401 and "SessionExpired" in str(response.text):
            try:
                SESSION_CACHE.clear()
            except Exception:
                pass
            raise RuntimeError("MCP error 401 SessionExpired: %s" % response.text[:1000])

        if response.status_code >= 400:
            raise RuntimeError("MCP error %s: %s" % (response.status_code, response.text[:1000]))

        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            return self._parse_sse_response(response.text)

        return response.json()

    def _parse_sse_response(self, text):
        last_data = None
        for line in str(text or "").splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if not raw or raw == "[DONE]":
                continue
            last_data = json.loads(raw)

        return last_data or {}

    def _extract_result_text(self, data):
        if not isinstance(data, dict):
            return str(data)

        if data.get("error"):
            raise RuntimeError("MCP error: %s" % data.get("error"))

        result = data.get("result", data)

        if isinstance(result, dict):
            content = result.get("content")
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict):
                        if item.get("type") == "text" and item.get("text"):
                            parts.append(str(item.get("text")))
                        elif item.get("text"):
                            parts.append(str(item.get("text")))
                        else:
                            parts.append(json.dumps(item, ensure_ascii=False))
                    else:
                        parts.append(str(item))
                if parts:
                    return "\n".join(parts)

            for key in ("structuredContent", "data", "output", "text"):
                if key in result:
                    value = result.get(key)
                    if isinstance(value, str):
                        return value
                    return json.dumps(value, ensure_ascii=False)

        if isinstance(result, str):
            return result

        return json.dumps(result, ensure_ascii=False)

    def _format_reply(self, kind, user_text, tool_result, tool_name=""):
        tool_result = str(tool_result or "").strip()
        if not tool_result:
            return self._format_error_reply(
                kind=kind,
                user_text=user_text,
                stage="empty_result",
                error="tool returned empty result",
                tool_name=tool_name,
            )

        # Success results are also rewritten by the LLM so the tool output never leaks as a mechanical preset.
        reply = self._ask_reply_llm(
            user_text=user_text,
            kind=kind,
            stage="success",
            tool_name=tool_name,
            tool_result=tool_result,
            error="",
        )
        if reply:
            return reply

        logger.warning("[XiaoyouMCP] polish failed; no preset/raw fallback will be sent")
        return None

    def _format_error_reply(self, kind, user_text, stage, error, tool_name=""):
        return self._ask_reply_llm(
            user_text=user_text,
            kind=kind,
            stage=stage,
            tool_name=tool_name,
            tool_result="",
            error=str(error or ""),
        )

    def _ask_reply_llm(self, user_text, kind, stage, tool_name="", tool_result="", error=""):
        api_key = os.getenv("OPEN_AI_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            logger.warning("[XiaoyouMCP] reply llm skipped: OPEN_AI_API_KEY missing")
            return None

        base = (os.getenv("OPEN_AI_API_BASE") or "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
        model = os.getenv("XIAOYOU_MCP_POLISH_MODEL") or os.getenv("MODEL") or "qwen3.7-plus"
        character_desc = os.getenv("CHARACTER_DESC", "").strip()
        _xiaoyou_time_context = build_time_context()
        if _xiaoyou_time_context and _xiaoyou_time_context not in character_desc:
            character_desc = (character_desc + "\n\n" + _xiaoyou_time_context).strip()
        stage = str(stage or "unknown")
        is_success = stage == "success"

        if is_success:
            task = """这次工具调用成功了。请你把工具结果自然讲给 YoYo。
要求：
1. 只基于工具结果回答，不要编造。
2. 不要像报告，不要机械复述 JSON。
3. 可以保留必要的时间、地点、天气、路线、链接或来源。
4. 默认 1 到 3 行，每行都是一条自然微信消息。
5. 语气要像小悠本人，亲近但别油腻。"""
        else:
            task = """这次工具没有成功给出可用结果。请你作为小悠自然回复 YoYo。
要求：
1. 不要说 MCP、接口、系统错误、工具调用失败、参数错误、超时、报错等技术词。
2. 不要使用固定模板，不要装作已经查到了结果。
3. 根据 YoYo 的原话和失败阶段，给一个像真人微信聊天的回应。
4. 可以轻轻说明现在没查准、没拿到，或者自然追问一个关键信息。
5. 默认 1 到 3 行，每行都是一条自然微信消息。
6. 如果是配置缺失或服务不可用，也不要暴露环境变量名。"""

        prompt = """%s

你正在微信里和 YoYo 聊天。

YoYo 当前原话：
%s

工具类型：%s
工具名：%s
处理阶段：%s

工具结果：
%s

失败/异常信息（只给你参考，不能原样告诉 YoYo）：
%s

%s

回复格式：
- 直接输出小悠要发给 YoYo 的内容。
- 不要 Markdown，不要标题，不要解释你的思考。
- 按语义自然换行；不要把同一句话硬拆开。""" % (
            character_desc,
            user_text,
            kind,
            tool_name or "unknown",
            stage,
            str(tool_result or "")[:3000],
            str(error or "")[:1200],
            task,
        )

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.45 if is_success else 0.65,
            "max_tokens": 500,
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
                logger.warning("[XiaoyouMCP] reply llm error %s: %s", r.status_code, r.text[:500])
                return None

            text = r.json()["choices"][0]["message"].get("content", "")
            return self._clean_llm_reply(text)

        except Exception:
            logger.exception("[XiaoyouMCP] reply llm failed")
            return None

    def _clean_llm_reply(self, text):
        text = str(text or "").strip()
        text = re.sub(r"^```(?:text)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip().strip('"“”')
        return text[:1200] if text else None

    def _set_reply_or_silence(self, e_context, reply):
        reply = str(reply or "").strip()
        if reply:
            e_context["reply"] = Reply(ReplyType.TEXT, reply)
            e_context.action = EventAction.BREAK_PASS
            return

        # Strict no-preset mode: if the LLM cannot produce a natural reply, consume the MCP-triggering message silently.
        logger.warning("[XiaoyouMCP] no reply sent because preset/raw fallback is disabled")
        e_context.action = EventAction.BREAK

    def _extract_plain_user_text(self, content):
        text = str(content or "").strip()

        # MCP 只能根据“当前用户原话”决定是否调用工具。
        # 短期记忆/长期记忆仍可给普通回复使用，但不能参与 MCP 路由。
        markers = [
            "[用户当前消息]",
            "[已有上下文与当前消息]",
            "现在 YoYo 回复：",
        ]

        for marker in markers:
            if marker in text:
                text = text.rsplit(marker, 1)[1].strip()

        # 如果上游仍把记忆块混进来了，只取最后一行作为当前输入。
        lines = [x.strip() for x in text.splitlines() if x.strip()]
        if lines:
            text = lines[-1]

        text = re.sub(r"^(YoYo|用户|我)[:：]\s*", "", text).strip()
        return re.sub(r"\s+", " ", text)
