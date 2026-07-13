# -*- coding: utf-8 -*-
"""Private relationship/profile facts with real calendar calculations.

The actual JSON and face reference live under APPDATA_DIR and are intentionally
ignored by Git.  This module only exposes read-only prompt facts, visual
reference metadata and a once-per-special-day attention key.
"""

import copy
import json
import os
import threading
from datetime import date, datetime, timedelta

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

from common.log import logger

try:
    from plugins.xiaoyou_common.vendor.lunar_python import Solar
except Exception:
    Solar = None


LUNAR_FESTIVALS = {
    (1, 1): ("spring_festival", "春节", 1.0, True),
    (1, 15): ("lantern_festival", "元宵节", 0.82, True),
    (2, 2): ("dragon_heads_raising", "龙抬头", 0.55, True),
    (5, 5): ("dragon_boat_festival", "端午节", 0.78, True),
    (7, 7): ("qixi_festival", "七夕", 0.92, True),
    (7, 15): ("ghost_festival", "中元节", 0.48, False),
    (8, 15): ("mid_autumn_festival", "中秋节", 0.9, True),
    (9, 9): ("double_ninth_festival", "重阳节", 0.62, True),
    (12, 8): ("laba_festival", "腊八节", 0.62, True),
    (12, 24): ("southern_little_new_year", "南方小年", 0.68, True),
}

SOLAR_TERM_FESTIVALS = {
    "清明": ("qingming_festival", "清明节", 0.78, True),
    "冬至": ("winter_solstice", "冬至", 0.66, True),
}


def _default_appdata_dir():
    configured = os.getenv("APPDATA_DIR", "").strip()
    if configured:
        return configured
    repository_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    return os.path.join(repository_root, "data")


def _parse_date(value):
    try:
        return date.fromisoformat(str(value or "").strip())
    except Exception:
        return None


def _age_on(birth_date, today):
    if not birth_date or today < birth_date:
        return None
    return today.year - birth_date.year - int(
        (today.month, today.day) < (birth_date.month, birth_date.day)
    )


def _anniversary_years(start_date, today):
    return _age_on(start_date, today)


def _occurrence(year, month, day):
    try:
        return date(int(year), int(month), int(day))
    except Exception:
        return None


class RelationshipProfileService:
    def __init__(self, path=None):
        self.path = os.path.realpath(
            path
            or os.getenv("XIAOYOU_RELATIONSHIP_PROFILE_PATH", "").strip()
            or os.path.join(
                _default_appdata_dir(),
                "xiaoyou_profile",
                "relationship_profile.json",
            )
        )
        self.lock = threading.RLock()
        self._mtime = None
        self._profile = {}

    def load(self):
        with self.lock:
            try:
                mtime = os.path.getmtime(self.path)
            except Exception:
                mtime = None
            if mtime is not None and mtime == self._mtime and self._profile:
                return copy.deepcopy(self._profile)
            try:
                with open(self.path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                if not isinstance(data, dict):
                    raise ValueError("profile root must be object")
                self._profile = data
                self._mtime = mtime
                return copy.deepcopy(data)
            except FileNotFoundError:
                logger.warning(
                    "[RelationshipProfile] private profile missing path=%s",
                    self.path,
                )
            except Exception:
                logger.exception("[RelationshipProfile] failed to load private profile")
            return copy.deepcopy(self._profile)

    def now(self):
        profile = self.load()
        tz_name = str(
            profile.get("timezone")
            or os.getenv("XIAOYOU_TIME_AWARENESS_TZ", "Asia/Shanghai")
        ).strip()
        if ZoneInfo:
            try:
                return datetime.now(ZoneInfo(tz_name))
            except Exception:
                pass
        return datetime.now()

    def facts(self, now=None):
        profile = self.load()
        now = now or self.now()
        today = now.date()
        people = profile.get("people") if isinstance(profile.get("people"), dict) else {}
        yoyo = people.get("yoyo") if isinstance(people.get("yoyo"), dict) else {}
        xiaoyou = people.get("xiaoyou") if isinstance(people.get("xiaoyou"), dict) else {}
        relationship = profile.get("relationship") if isinstance(profile.get("relationship"), dict) else {}
        yoyo_birth = _parse_date(yoyo.get("birth_date"))
        xiaoyou_birth = _parse_date(xiaoyou.get("birth_date"))
        first_met = _parse_date(relationship.get("first_met_date"))
        days_since = (today - first_met).days if first_met and today >= first_met else None
        return {
            "timezone": str(profile.get("timezone") or "Asia/Shanghai"),
            "home_city": str(relationship.get("home_city") or profile.get("home_city") or ""),
            "cohabiting": bool(relationship.get("cohabiting")),
            "yoyo": {
                **copy.deepcopy(yoyo),
                "current_age": _age_on(yoyo_birth, today),
            },
            "xiaoyou": {
                **copy.deepcopy(xiaoyou),
                "current_age": _age_on(xiaoyou_birth, today),
            },
            "relationship": {
                **copy.deepcopy(relationship),
                "days_since_first_met": days_since,
                "known_day_number": days_since + 1 if days_since is not None else None,
                "completed_anniversary_years": _anniversary_years(first_met, today),
            },
            "lunar_date": self.lunar_date_label(today),
            "today_events": self.events_on(today, profile=profile),
            "upcoming_events": self.upcoming_events(today, days=14, profile=profile),
        }

    def build_context(self, now=None):
        raw_profile = self.load()
        if not isinstance(raw_profile.get("people"), dict) or not raw_profile.get("people"):
            return ""
        facts = self.facts(now=now)
        yoyo = facts.get("yoyo") or {}
        xiaoyou = facts.get("xiaoyou") or {}
        relationship = facts.get("relationship") or {}
        appearance = xiaoyou.get("appearance") if isinstance(xiaoyou.get("appearance"), dict) else {}
        self_identity = xiaoyou.get("self_identity") if isinstance(xiaoyou.get("self_identity"), dict) else {}
        today_events = facts.get("today_events") or []
        upcoming = facts.get("upcoming_events") or []
        event_text = "、".join(event.get("display", "") for event in today_events if event.get("display")) or "无"
        upcoming_text = "、".join(
            "%s（%s天后）" % (event.get("display"), event.get("days_until"))
            for event in upcoming[:5]
            if event.get("days_until", 0) > 0
        ) or "无"
        appearance_text = "；".join(
            "%s：%s" % (label, str(appearance.get(key)).strip())
            for key, label in (
                ("face", "脸型"),
                ("skin", "肤色"),
                ("eyes", "眼睛"),
                ("hair", "头发"),
                ("build", "体态"),
            )
            if str(appearance.get(key) or "").strip()
        ) or "未单独配置"
        self_identity_text = "；".join(
            item
            for item in (
                "明确是成年女性" if self_identity.get("adult") is True else "",
                (
                    "与YoYo的关系：%s" % str(self_identity.get("relationship_to_yoyo")).strip()
                    if str(self_identity.get("relationship_to_yoyo") or "").strip()
                    else ""
                ),
                (
                    "生日意义：%s" % str(self_identity.get("birthday_meaning")).strip()
                    if str(self_identity.get("birthday_meaning") or "").strip()
                    else ""
                ),
            )
            if item
        ) or "以当前人物与关系事实为准"
        return """[人物与关系动态事实]
YoYo：%s，男性，出生于%s，当前%s岁，身高%s厘米，体重属于其保密信息。
小悠：%s，女性，出生于%s，当前%s岁，身高%s厘米；生日为7月7日。
小悠的稳定外貌自我认知：%s。
小悠的稳定身份自我认知：%s。
共同生活地点：%s；当前共同居住：%s。
首次相见日期：%s；已经相识%s天，今天是相识第%s天；已度过%s个完整周年。
今天的重要日子：%s。
未来14天内的重要日子：%s。
当前农历日期：%s。

使用规则：
1. 年龄、相识天数和周年数是按当前真实日期计算的事实，禁止沿用过期年龄。
2. 这些是稳定私密档案，不要像资料卡一样主动逐条复述；只在语境需要时自然体现。
3. 体重明确保密，不猜测、不推断、不泄露。
4. 重要日期只提供感知机会，不代表必须使用固定祝福模板；是否主动表达以及表达方式仍由小悠结合当时语境和内在状态决定。
5. 小悠应把外貌和身份信息作为对自己的自然认知，不要主动背诵资料卡；生活照的人脸、体态和画风仍以专用视觉档案及参考图为最高标准。
6. 不把本档案重复写入阿里云长期记忆。""" % (
            str(yoyo.get("name") or "YoYo"),
            str(yoyo.get("birth_date") or "未知"),
            str(yoyo.get("current_age") if yoyo.get("current_age") is not None else "未知"),
            str(yoyo.get("height_cm") or "未知"),
            str(xiaoyou.get("name") or "小悠"),
            str(xiaoyou.get("birth_date") or "未知"),
            str(xiaoyou.get("current_age") if xiaoyou.get("current_age") is not None else "未知"),
            str(xiaoyou.get("height_cm") or "未知"),
            appearance_text,
            self_identity_text,
            facts.get("home_city") or "未知",
            "是" if facts.get("cohabiting") else "否",
            str(relationship.get("first_met_date") or "未知"),
            str(relationship.get("days_since_first_met") if relationship.get("days_since_first_met") is not None else "未知"),
            str(relationship.get("known_day_number") if relationship.get("known_day_number") is not None else "未知"),
            str(relationship.get("completed_anniversary_years") if relationship.get("completed_anniversary_years") is not None else "未知"),
            event_text,
            upcoming_text,
            facts.get("lunar_date") or "不可用",
        )

    def events_on(self, target_date, profile=None):
        profile = profile if isinstance(profile, dict) else self.load()
        events = []
        for event in self._recurring_events(profile):
            if int(event.get("month") or 0) != target_date.month or int(event.get("day") or 0) != target_date.day:
                continue
            item = copy.deepcopy(event)
            item["date"] = target_date.isoformat()
            item["display"] = self._event_display(item, target_date, profile)
            events.append(item)
        events.extend(self.traditional_events_on(target_date))
        return events

    def upcoming_events(self, today, days=14, profile=None):
        profile = profile if isinstance(profile, dict) else self.load()
        results = []
        for event in self._recurring_events(profile):
            occurrence = _occurrence(today.year, event.get("month"), event.get("day"))
            if occurrence is None or occurrence < today:
                occurrence = _occurrence(today.year + 1, event.get("month"), event.get("day"))
            if occurrence is None:
                continue
            delta = (occurrence - today).days
            if 0 <= delta <= max(0, int(days)):
                item = copy.deepcopy(event)
                item["date"] = occurrence.isoformat()
                item["days_until"] = delta
                item["display"] = self._event_display(item, occurrence, profile)
                results.append(item)
        for offset in range(0, max(0, int(days)) + 1):
            occurrence = today + timedelta(days=offset)
            for event in self.traditional_events_on(occurrence):
                item = copy.deepcopy(event)
                item["date"] = occurrence.isoformat()
                item["days_until"] = offset
                results.append(item)
        unique = []
        seen = set()
        for item in results:
            signature = (str(item.get("id") or ""), str(item.get("date") or ""))
            if signature in seen:
                continue
            seen.add(signature)
            unique.append(item)
        results = unique
        results.sort(key=lambda item: (item.get("days_until", 9999), -float(item.get("importance") or 0)))
        return results

    def traditional_events_on(self, target_date):
        lunar = self._lunar(target_date)
        if lunar is None:
            return []
        events = []
        month = int(lunar.getMonth())
        day = int(lunar.getDay())
        # 闰月不重复庆祝同名节日。
        if month > 0:
            definition = LUNAR_FESTIVALS.get((month, day))
            if definition:
                events.append(self._traditional_event(*definition, target_date=target_date, lunar=lunar))

        tomorrow_lunar = self._lunar(target_date + timedelta(days=1))
        if (
            month == 12
            and tomorrow_lunar is not None
            and int(tomorrow_lunar.getMonth()) == 1
            and int(tomorrow_lunar.getDay()) == 1
        ):
            events.append(self._traditional_event(
                "lunar_new_year_eve",
                "除夕夜",
                1.0,
                True,
                target_date=target_date,
                lunar=lunar,
            ))

        jie_qi = str(lunar.getJieQi() or "").strip()
        definition = SOLAR_TERM_FESTIVALS.get(jie_qi)
        if definition:
            events.append(self._traditional_event(*definition, target_date=target_date, lunar=lunar))
        return events

    def lunar_date_label(self, target_date):
        lunar = self._lunar(target_date)
        if lunar is None:
            return ""
        leap = "闰" if int(lunar.getMonth()) < 0 else ""
        return "农历%s年%s%s月%s" % (
            str(lunar.getYearInGanZhi()),
            leap,
            str(lunar.getMonthInChinese()),
            str(lunar.getDayInChinese()),
        )

    def _lunar(self, target_date):
        if Solar is None:
            return None
        try:
            return Solar.fromYmd(
                int(target_date.year),
                int(target_date.month),
                int(target_date.day),
            ).getLunar()
        except Exception:
            logger.exception("[RelationshipProfile] lunar conversion failed")
            return None

    def _traditional_event(
        self,
        event_id,
        name,
        importance,
        proactive,
        *,
        target_date,
        lunar,
    ):
        return {
            "id": event_id,
            "name": name,
            "display": name,
            "kind": "traditional_festival",
            "date": target_date.isoformat(),
            "lunar_month": int(lunar.getMonth()),
            "lunar_day": int(lunar.getDay()),
            "importance": float(importance),
            "proactive": bool(proactive),
        }

    def calendar_attention_key(self, now=None):
        now = now or self.now()
        hour = max(0, min(23, int(os.getenv("XIAOYOU_CALENDAR_ATTENTION_HOUR", "8"))))
        if now.hour < hour:
            return ""
        events = [event for event in self.events_on(now.date()) if event.get("proactive", True)]
        if not events:
            return ""
        event_ids = ",".join(sorted(str(event.get("id") or "") for event in events))
        return "%s:%s" % (now.date().isoformat(), event_ids)

    def yoyo_visual_profile(self):
        facts = self.facts()
        yoyo = facts.get("yoyo") if isinstance(facts.get("yoyo"), dict) else {}
        return {
            "name": yoyo.get("name") or "YoYo",
            "gender": yoyo.get("gender") or "male",
            "current_age": yoyo.get("current_age"),
            "height_cm": yoyo.get("height_cm"),
            "face": copy.deepcopy(yoyo.get("face") or {}),
            "identity_rule": str(yoyo.get("identity_rule") or ""),
        }

    def xiaoyou_current_age(self):
        return (self.facts().get("xiaoyou") or {}).get("current_age")

    def yoyo_reference_path(self):
        profile = self.load()
        people = profile.get("people") if isinstance(profile.get("people"), dict) else {}
        yoyo = people.get("yoyo") if isinstance(people.get("yoyo"), dict) else {}
        value = str(yoyo.get("face_reference") or "").strip()
        if not value:
            return ""
        path = value if os.path.isabs(value) else os.path.join(os.path.dirname(self.path), value)
        path = os.path.realpath(path)
        return path if os.path.isfile(path) else ""

    def _recurring_events(self, profile):
        results = []
        seen = set()
        raw = profile.get("special_dates") if isinstance(profile.get("special_dates"), list) else []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            event = copy.deepcopy(entry)
            event_id = str(event.get("id") or "").strip()
            signature = (event_id, int(event.get("month") or 0), int(event.get("day") or 0))
            if signature in seen or not signature[1] or not signature[2]:
                continue
            seen.add(signature)
            results.append(event)

        people = profile.get("people") if isinstance(profile.get("people"), dict) else {}
        for person_id, label in (("yoyo", "YoYo生日"), ("xiaoyou", "小悠生日")):
            person = people.get(person_id) if isinstance(people.get(person_id), dict) else {}
            birth = _parse_date(person.get("birth_date"))
            if birth:
                results.append({
                    "id": "%s_birthday" % person_id,
                    "name": label,
                    "kind": "birthday",
                    "person": person_id,
                    "month": birth.month,
                    "day": birth.day,
                    "importance": 1.0,
                    "proactive": True,
                })
        relationship = profile.get("relationship") if isinstance(profile.get("relationship"), dict) else {}
        first_met = _parse_date(relationship.get("first_met_date"))
        if first_met:
            results.append({
                "id": "first_met_anniversary",
                "name": "首次相见纪念日",
                "kind": "anniversary",
                "month": first_met.month,
                "day": first_met.day,
                "importance": 1.0,
                "proactive": True,
            })
        return results

    def _event_display(self, event, occurrence, profile):
        name = str(event.get("name") or "重要日子")
        kind = str(event.get("kind") or "")
        if kind == "birthday":
            people = profile.get("people") if isinstance(profile.get("people"), dict) else {}
            person = people.get(event.get("person")) if isinstance(people.get(event.get("person")), dict) else {}
            age = _age_on(_parse_date(person.get("birth_date")), occurrence)
            return "%s（%s岁）" % (name, age) if age is not None else name
        if kind == "anniversary":
            relationship = profile.get("relationship") if isinstance(profile.get("relationship"), dict) else {}
            years = _anniversary_years(_parse_date(relationship.get("first_met_date")), occurrence)
            return "%s（%s周年）" % (name, years) if years is not None else name
        return name


_INSTANCE = None
_INSTANCE_LOCK = threading.Lock()


def get_relationship_profile_service():
    global _INSTANCE
    with _INSTANCE_LOCK:
        if _INSTANCE is None:
            _INSTANCE = RelationshipProfileService()
        return _INSTANCE
