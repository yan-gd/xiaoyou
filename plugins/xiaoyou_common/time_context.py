# -*- coding:utf-8 -*-
import os
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None


def _now():
    tz_name = os.getenv("XIAOYOU_TIME_AWARENESS_TZ", os.getenv("TZ", "Asia/Shanghai")).strip() or "Asia/Shanghai"

    if ZoneInfo:
        try:
            return datetime.now(ZoneInfo(tz_name)), tz_name
        except Exception:
            return datetime.now(), tz_name

    return datetime.now(), tz_name


def build_time_context():
    """Build neutral real-world time facts for Xiaoyou.

    This function must not contain preset replies, suggestions, or fixed care templates.
    It only returns factual time context and usage constraints.
    """
    now, tz_name = _now()

    weekday_map = {
        0: "周一",
        1: "周二",
        2: "周三",
        3: "周四",
        4: "周五",
        5: "周六",
        6: "周日",
    }

    hour = now.hour
    if 5 <= hour < 8:
        day_part = "清晨"
    elif 8 <= hour < 11:
        day_part = "上午"
    elif 11 <= hour < 13:
        day_part = "中午"
    elif 13 <= hour < 17:
        day_part = "下午"
    elif 17 <= hour < 19:
        day_part = "傍晚"
    elif 19 <= hour < 22:
        day_part = "晚上"
    elif 22 <= hour < 24:
        day_part = "深夜"
    else:
        day_part = "凌晨"

    weekday = weekday_map.get(now.weekday(), "")
    day_type = "周末" if now.weekday() >= 5 else "工作日"

    return """[当前现实时间]
当前时区：%s
当前日期：%s
当前时间：%s
当前星期：%s
当前类型：%s
当前时段：%s

使用规则：
1. 这些只是现实时间事实，只用于理解 YoYo 当前话语发生的时间背景。
2. 不要把这些内容当成回复模板。
3. 不要每次主动报时，不要说“现在是几点几点”，除非 YoYo 明确问时间。
4. 不要因为某个时段固定输出吃饭、睡觉、上班、休息等提醒。
5. 只有当 YoYo 的原话本身涉及时间、今天、明天、刚才、晚上、早上、吃饭、睡觉、回家、计划、提醒、身体状态时，才自然参考这些事实。
6. 回复仍然完全按照小悠的人设和当前聊天语境自由生成。""" % (
        tz_name,
        now.strftime("%Y-%m-%d"),
        now.strftime("%H:%M"),
        weekday,
        day_type,
        day_part,
    )
