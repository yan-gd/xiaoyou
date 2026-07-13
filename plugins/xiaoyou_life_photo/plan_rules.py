# -*- coding:utf-8 -*-
"""Deterministic validation for structured photo-planner decisions.

No function in this module infers intent from user words.  Semantic choices
come from the model as explicit fields; local code only validates enums and
prevents physically contradictory combinations.
"""


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

ALLOWED_PHYSICAL_CONSTRAINTS = (
    "princess_carry",
    "hands_free_pose",
    "distant_full_body",
)

ALLOWED_CAMERA_OPERATORS = (
    "yoyo",
    "friend",
    "passerby",
    "companion",
    "unspecified_third_person",
)


def as_bool(value):
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return bool(value)


def normalize_choice(value, allowed, default):
    normalized = str(value or "").strip().lower()
    return normalized if normalized in allowed else default


def normalize_constraints(values):
    if not isinstance(values, (list, tuple)):
        return []
    result = []
    for value in values:
        normalized = str(value or "").strip().lower()
        if normalized in ALLOWED_PHYSICAL_CONSTRAINTS and normalized not in result:
            result.append(normalized)
    return result


def action_requires_free_hands(user_text="", visual_prompt="", pose="", declared=False):
    """Use only the planner's structured declaration; text is compatibility-only."""
    return as_bool(declared)


def action_requires_distant_camera(user_text="", visual_prompt="", pose="", declared=False):
    """Use only the planner's structured declaration; never scan raw wording."""
    return as_bool(declared)


def normalize_capture_mode(
    requested_mode,
    user_text="",
    visual_prompt="",
    pose="",
    hands_free_required=False,
    distant_camera_required=False,
):
    """Validate the model's mode and correct only impossible combinations."""
    mode = normalize_choice(
        requested_mode,
        ALLOWED_CAPTURE_MODES,
        "front_camera_selfie",
    )
    if mode == "front_camera_selfie" and (
        as_bool(hands_free_required) or as_bool(distant_camera_required)
    ):
        return "timer_camera"
    return mode


def normalize_camera_operator(capture_mode, requested_operator="", user_text=""):
    """Derive operator from the structured camera mode, not from user keywords."""
    if capture_mode == "front_camera_selfie":
        return "xiaoyou_handheld"
    if capture_mode == "mirror_selfie":
        return "xiaoyou_mirror"
    if capture_mode == "timer_camera":
        return "timer_tripod"
    if capture_mode == "first_person_scene":
        return "xiaoyou_handheld"
    return normalize_choice(
        requested_operator,
        ALLOWED_CAMERA_OPERATORS,
        "unspecified_third_person",
    )
