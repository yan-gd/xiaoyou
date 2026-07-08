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


TOOLS_CACHE = {}
SESSION_CACHE = {}


@plugins.register(
    name="XiaoyouMCP",
    desc="Give Xiaoyou MCP tools for search, weather, map and time",
    version="0.2",
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
            e_context["reply"] = Reply(ReplyType.TEXT, self._missing_config_reply(kind))
            e_context.action = EventAction.BREAK_PASS
            return

        try:
            tools = self._list_tools(endpoint)
            tool_name = self._select_tool(kind, tools)
            if not tool_name:
                e_context["reply"] = Reply(
                    ReplyType.TEXT,
                    "这个 MCP 服务连上了，但我没在里面找到合适的工具名。你把工具测试页里的工具名发我，我给它对上。"
                )
                e_context.action = EventAction.BREAK_PASS
                return

            tool_schema = self._tool_schema(tools, tool_name)
            args = self._build_tool_args(kind, text, tool_name, tool_schema)
            result = self._call_mcp_tool(endpoint, tool_name, args)
            reply = self._format_reply(kind, text, result)

            e_context["reply"] = Reply(ReplyType.TEXT, reply)
            e_context.action = EventAction.BREAK_PASS

        except Exception:
            logger.exception("[XiaoyouMCP] call failed kind=%s text=%r", kind, text[:100])
            e_context["reply"] = Reply(
                ReplyType.TEXT,
                "我刚刚去查了，但 MCP 工具那边卡住了。你等一下再问我嘛。"
            )
            e_context.action = EventAction.BREAK_PASS

    def _enabled(self):
        return os.getenv("XIAOYOU_MCP_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")

    def _route_intent(self, text):
        text = str(text or "").strip()

        if re.search(r"提醒我|记得提醒|叫我|喊我|闹钟|定个提醒|设个提醒", text):
            return None

        if re.search(r"天气|气温|温度|下雨|下雪|空气质量|湿度|风力|冷不冷|热不热|穿什么", text):
            return "weather"

        if re.search(r"导航|路线|怎么去|怎么走|到.+多久|距离|开车|打车|公交|地铁|步行|骑行", text):
            return "map"

        if re.search(r"附近|周边|哪里有|哪有|地址|营业时间|电话|门店|餐厅|酒店|医院|地铁站|景点", text):
            return "map_search"

        if re.search(r"几点|几点啦|几点了|现在几点|今天几号|今天星期几|当前时间|北京时间|时间同步|校准时间|现在时间|时区", text):
            return "time"

        if self._extract_url(text) or re.search(r"查一下|查下|搜一下|搜搜|搜索|联网查|帮我查|资料|新闻|最新|最近|价格|汇率|百科|是什么|是谁|怎么回事|链接|网页|", text, re.I):
            return "search"

        return None

    def _endpoint_for_kind(self, kind):
        if kind in ("weather", "map", "map_search"):
            return os.getenv("XIAOYOU_MCP_AMAP_ENDPOINT", "").strip() or os.getenv("XIAOYOU_MCP_ENDPOINT", "").strip()
        if kind == "time":
            return os.getenv("XIAOYOU_MCP_TIME_ENDPOINT", "").strip() or os.getenv("XIAOYOU_MCP_ENDPOINT", "").strip()
        if kind == "search":
            return os.getenv("XIAOYOU_MCP_SEARCH_ENDPOINT", "").strip()
        return ""

    def _missing_config_reply(self, kind):
        name = {
            "search": "必应中文搜索",
            "weather": "高德天气",
            "map": "高德路线",
            "map_search": "高德地点搜索",
            "time": "时间服务",
        }.get(kind, "工具")

        env_name = {
            "search": "XIAOYOU_MCP_SEARCH_ENDPOINT",
            "weather": "XIAOYOU_MCP_AMAP_ENDPOINT",
            "map": "XIAOYOU_MCP_AMAP_ENDPOINT",
            "map_search": "XIAOYOU_MCP_AMAP_ENDPOINT",
            "time": "XIAOYOU_MCP_TIME_ENDPOINT",
        }.get(kind, "XIAOYOU_MCP_ENDPOINT")

        return "我知道你想用%s，但还没填它的 Hosted MCP URL。\n把魔搭右侧复制出来的地址填到 %s 就能用啦。" % (name, env_name)

    def _select_tool(self, kind, tools):
        configured = self._configured_tool_name(kind)
        tool_names = [t.get("name", "") for t in tools if isinstance(t, dict)]

        if configured and configured in tool_names:
            return configured

        aliases = {
            "weather": ["maps_weather", "weather"],
            "map": ["maps_direction_driving", "direction_driving", "driving", "route", "direction"],
            "map_search": ["maps_text_search", "text_search", "around_search", "search"],
            "time": ["get_current_time", "current_time", "time"],
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
            "time": "XIAOYOU_MCP_TIME_CURRENT_TOOL",
            "search": "XIAOYOU_MCP_SEARCH_TOOL",
        }

        defaults = {
            "weather": "maps_weather",
            "map": "maps_direction_driving",
            "map_search": "maps_text_search",
            "time": "get_current_time",
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
        timezone = os.getenv("XIAOYOU_TIMEZONE", os.getenv("TZ", "Asia/Shanghai")).strip()

        prompt = """你要为 MCP 工具生成调用参数。
只能输出 JSON 对象，不要 Markdown，不要解释。

用户原话：
%s

工具用途：%s
工具名：%s

默认信息：
- 默认城市：%s
- 默认出发地：%s
- 默认时区：%s

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
            timezone,
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
        timezone = os.getenv("XIAOYOU_TIMEZONE", os.getenv("TZ", "Asia/Shanghai")).strip()
        location = os.getenv("XIAOYOU_DEFAULT_LOCATION", "").strip()

        if kind == "time":
            return {"timezone": timezone}

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

        data = self._jsonrpc(endpoint, "tools/list", {})
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
                "params": params or {},
            }

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

    def _format_reply(self, kind, user_text, tool_result):
        tool_result = str(tool_result or "").strip()
        if not tool_result:
            return "我查了一下，但工具没给我有效结果。你换个说法再问我嘛。"

        if os.getenv("XIAOYOU_MCP_POLISH_REPLY", "true").strip().lower() not in ("1", "true", "yes", "on"):
            return tool_result[:1200]

        api_key = os.getenv("OPEN_AI_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
        base = (os.getenv("OPEN_AI_API_BASE") or "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
        model = os.getenv("XIAOYOU_MCP_POLISH_MODEL") or os.getenv("MODEL") or "qwen3.7-plus"

        if not api_key:
            return tool_result[:1200]

        prompt = """你是小悠，正在微信里把 MCP 工具查询结果自然告诉 YoYo。

YoYo 问：
%s

工具类型：%s

工具结果：
%s

要求：
1. 只基于工具结果回答，不要编造。
2. 像女朋友聊天一样自然，别像报告。
3. 默认 1 到 4 句；天气/路线可以稍微具体一点。
4. 如果结果里有链接或关键来源，可以保留。
5. 如果工具结果不确定，要直接说不确定。
""" % (user_text, kind, tool_result[:3000])

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.35,
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
                logger.warning("[XiaoyouMCP] polish error %s: %s", r.status_code, r.text[:500])
                return tool_result[:1200]

            text = r.json()["choices"][0]["message"]["content"].strip()
            return text[:1200] if text else tool_result[:1200]

        except Exception:
            logger.exception("[XiaoyouMCP] polish failed")
            return tool_result[:1200]

    def _extract_plain_user_text(self, content):
        text = str(content or "").strip()

        markers = [
            "现在 YoYo 回复：",
            "[用户当前消息]",
            "[已有上下文与当前消息]",
        ]

        for marker in markers:
            if marker in text:
                text = text.rsplit(marker, 1)[1].strip()

        return re.sub(r"\s+", " ", text)
