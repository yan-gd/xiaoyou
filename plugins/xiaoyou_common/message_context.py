# -*- coding:utf-8 -*-
import re


CURRENT_USER_MARKERS = (
    "YOYO 当前发来的微信消息：",
    "YOYO 当前发来的微信消息:",
    "YoYo 当前发来的微信消息：",
    "YoYo 当前发来的微信消息:",
    "YoYo 当前消息：",
    "YoYo 当前消息:",
    "[YoYo 当前原话]",
    "[用户当前消息]",
    "[已有上下文与当前消息]",
    "现在 YoYo 回复：",
    "现在 YoYo 回复:",
)


def extract_current_user_text(content):
    """从多层插件注入内容中只取 YoYo 本轮真实原话。"""
    text = str(content or "").strip()
    if not text:
        return ""

    # 多个插件可能嵌套注入。每轮取最靠后的标记，直到没有标记为止。
    while True:
        matches = [
            (text.rfind(marker), marker)
            for marker in CURRENT_USER_MARKERS
            if marker in text
        ]
        if not matches:
            break

        position, marker = max(matches, key=lambda item: item[0])
        text = text[position + len(marker):].strip()

    text = re.sub(r"^(YoYo|YOYO|用户|我)[:：]\s*", "", text).strip()
    return re.sub(r"\s+", " ", text)
