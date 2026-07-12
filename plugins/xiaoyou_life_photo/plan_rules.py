# -*- coding:utf-8 -*-
import re


ALLOWED_CAPTURE_MODES = (
    "front_camera_selfie",
    "mirror_selfie",
    "timer_camera",
    "third_person_camera",
    "first_person_scene",
)

ALLOWED_SHARE_INTENTS = (
    "check_in",
    "requested_pose",
    "outfit_showcase",
    "scene_share",
    "couple_moment",
    "proactive_share",
)

_THIRD_PERSON_RE = re.compile(
    r"第三人称|第三视角|第三方视角|他拍|别人拍|朋友拍|路人拍|抓拍|"
    r"(?:YoYo|yoyo|你)(?:来|帮|给)?我?拍|帮(?:她|小悠)拍",
    re.I,
)
_MIRROR_RE = re.compile(r"镜子自拍|镜前自拍|对镜自拍|对镜拍|镜子前", re.I)
_TIMER_RE = re.compile(r"定时拍|延时拍|三脚架|支架拍|架好(?:手机|相机)", re.I)
_FIRST_PERSON_RE = re.compile(r"第一人称|第一视角|眼前|拍(?:食物|风景|街景|礼物|物件)", re.I)
_SELFIE_RE = re.compile(r"前置摄像头|前置自拍|自拍", re.I)
_FREE_HANDS_RE = re.compile(
    r"(?:双手|两只手|双臂).{0,10}(?:托|捧|抱|举|张开|伸开|叉|比心|放在|搭在)|"
    r"(?:双手托脸|双手捧脸|两手托脸|两手捧脸)",
    re.I,
)
_NEGATED_SUFFIX_RE = re.compile(r"(?:不要用|不要|别用|别|不是|不用|不想用|拒绝用|取消)$", re.I)
_DISTANT_CAMERA_RE = re.compile(
    r"全身照|全身照片|拍全身|从头到脚|完整全身|背影|走路抓拍|跑步|转圈",
    re.I,
)


def as_bool(value):
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return bool(value)


def normalize_choice(value, allowed, default):
    normalized = str(value or "").strip().lower()
    return normalized if normalized in allowed else default


def action_requires_free_hands(user_text="", visual_prompt="", pose="", declared=False):
    if as_bool(declared):
        return True
    text = " ".join((str(user_text or ""), str(visual_prompt or ""), str(pose or "")))
    text = re.sub(r"(?:YoYo|yoyo|男朋友)(?:的|用|伸出)?双手", "", text, flags=re.I)
    return bool(_FREE_HANDS_RE.search(text))


def action_requires_distant_camera(user_text="", visual_prompt="", pose=""):
    text = " ".join((str(user_text or ""), str(visual_prompt or ""), str(pose or "")))
    return bool(_DISTANT_CAMERA_RE.search(text))


def _has_positive_match(pattern, text):
    for match in pattern.finditer(str(text or "")):
        prefix = str(text or "")[max(0, match.start() - 4):match.start()]
        if not _NEGATED_SUFFIX_RE.search(prefix):
            return True
    return False


def normalize_capture_mode(
    requested_mode,
    user_text="",
    visual_prompt="",
    pose="",
    hands_free_required=False,
):
    """Resolve explicit camera intent first, then enforce zero-cost physical feasibility."""
    user_text = str(user_text or "")
    if _has_positive_match(_THIRD_PERSON_RE, user_text):
        mode = "third_person_camera"
    elif _has_positive_match(_MIRROR_RE, user_text):
        mode = "mirror_selfie"
    elif _has_positive_match(_TIMER_RE, user_text):
        mode = "timer_camera"
    elif _has_positive_match(_FIRST_PERSON_RE, user_text):
        mode = "first_person_scene"
    elif _has_positive_match(_SELFIE_RE, user_text):
        mode = "front_camera_selfie"
    else:
        mode = normalize_choice(
            requested_mode,
            ALLOWED_CAPTURE_MODES,
            "front_camera_selfie",
        )

    needs_free_hands = action_requires_free_hands(
        user_text=user_text,
        visual_prompt=visual_prompt,
        pose=pose,
        declared=hands_free_required,
    )
    needs_distance = action_requires_distant_camera(
        user_text=user_text,
        visual_prompt=visual_prompt,
        pose=pose,
    )
    if mode == "front_camera_selfie" and (needs_free_hands or needs_distance):
        mode = "timer_camera"
    return mode


def normalize_camera_operator(capture_mode, requested_operator="", user_text=""):
    if capture_mode == "front_camera_selfie":
        return "xiaoyou_handheld"
    if capture_mode == "mirror_selfie":
        return "xiaoyou_mirror"
    if capture_mode == "timer_camera":
        return "timer_tripod"
    if capture_mode == "first_person_scene":
        return "xiaoyou_handheld"

    text = str(user_text or "")
    if re.search(r"(?:YoYo|yoyo|你)(?:来|帮|给)?我?拍", text, re.I):
        return "yoyo"
    if re.search(r"朋友拍|闺蜜拍", text, re.I):
        return "friend"
    if re.search(r"路人拍", text, re.I):
        return "passerby"
    return normalize_choice(
        requested_operator,
        ("yoyo", "friend", "passerby", "companion", "unspecified_third_person"),
        "unspecified_third_person",
    )
