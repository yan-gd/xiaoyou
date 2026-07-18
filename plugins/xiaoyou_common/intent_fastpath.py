# -*- coding: utf-8 -*-
"""Conservative local gates for Xiaoyou's semantic capability routers.

The gates never execute a capability.  They only identify ordinary chat that
does not contain even a domain anchor, allowing it to bypass three sequential
LLM classifiers.  Ambiguous or capability-shaped text still goes through the
existing semantic model and keeps its full context-aware decision process.
"""

import os
import re


_ANCHORS = {
    "reminder": (
        "提醒", "闹钟", "叫醒", "到时候叫我", "记得叫我", "别忘了叫我",
    ),
    "photo": (
        "照片", "自拍", "拍一张", "拍张", "发张", "发一张", "看看你",
        "看下你", "看一下你", "给我看看", "长什么样", "穿什么",
    ),
    "external": (
        "天气", "气温", "下雨", "下雪", "空气质量", "路线", "导航",
        "怎么走", "附近", "地址", "营业时间", "搜索", "搜一下", "查一下",
        "帮我查", "帮我找", "新闻", "价格", "汇率", "比分", "网页", "链接",
        "实时", "最新消息",
    ),
}


def fast_route_enabled():
    return os.getenv("XIAOYOU_FAST_CHAT_ROUTE_ENABLED", "true").strip().lower() in (
        "1", "true", "yes", "on",
    )


def might_need_capability(text, capability, *, pending_user_image=False):
    """Return True when the semantic classifier must still inspect the text."""
    if not fast_route_enabled():
        return True
    if capability == "photo" and pending_user_image:
        return True

    normalized = re.sub(r"\s+", "", str(text or "")).lower()
    if not normalized:
        return False
    if re.search(r"https?://|www\.", normalized, re.I) and capability == "external":
        return True

    anchors = _ANCHORS.get(str(capability or "").strip().lower())
    if not anchors:
        return True
    if any(anchor.lower() in normalized for anchor in anchors):
        return True
    # A generic continuation such as "按刚才说的来吧" may rely on recent
    # photo/tool context.  Only bypass when the utterance itself is clearly
    # relationship chat; ambiguous continuations keep semantic routing.
    return not is_obvious_casual_chat(normalized)


def is_obvious_casual_chat(normalized):
    if len(normalized) > 60:
        return False
    casual_anchors = (
        "想你", "爱你", "喜欢你", "抱抱", "亲亲", "晚安", "早安",
        "我到家", "还没回家", "等我", "等等", "休息会", "休息一会", "歇一会",
        "加班", "吃饭", "外卖", "饿了", "困了", "累了", "羞羞",
        "好呢", "好呀", "好哒", "哈哈", "嘿嘿", "偷笑",
    )
    if any(anchor in normalized for anchor in casual_anchors):
        return True
    return len(normalized) <= 30 and normalized.startswith(
        ("好", "嗯", "行", "知道啦", "收到", "可以呀")
    )


def should_use_chat_thinking(text, input_messages=None):
    """Reserve slow reasoning for genuinely complex turns, not casual chat."""
    enabled = os.getenv(
        "XIAOYOU_CHAT_ADAPTIVE_THINKING_ENABLED", "true"
    ).strip().lower() in ("1", "true", "yes", "on")
    if not enabled:
        return True

    messages = [str(item or "").strip() for item in (input_messages or []) if str(item or "").strip()]
    visible = "\n".join(messages) if messages else str(text or "").strip()
    if len(visible) >= int(os.getenv("XIAOYOU_CHAT_THINKING_MIN_CHARS", "90")):
        return True
    if len(messages) >= 3:
        return True
    complex_anchors = (
        "分析", "解释", "为什么", "怎么实现", "怎么设计", "代码", "报错",
        "架构", "方案", "比较", "推理", "计算", "证明", "总结", "规划",
    )
    return any(anchor in visible for anchor in complex_anchors)
