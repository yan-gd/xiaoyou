# -*- coding:utf-8 -*-
import base64
import copy
import hashlib
import json
import mimetypes
import os
import re
import threading
import time
import uuid
from datetime import datetime

import requests

import plugins
from plugins import *
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger
from plugins.xiaoyou_common.context_service import (
    build_context_snapshot,
    extract_current_user_text,
)
from plugins.xiaoyou_common.thinking_config import build_thinking_payload
from plugins.xiaoyou_common.model_gateway import chat_completion
from plugins.xiaoyou_common.photo_intent_service import classify_photo_semantics
from plugins.xiaoyou_common.outbound_dispatcher import (
    record_assistant_message,
    send_image,
)
from plugins.xiaoyou_common.state_store import JsonStateStore
from plugins.xiaoyou_common.runtime_paths import appdata_root, runtime_path
from plugins.xiaoyou_common.relationship_profile_service import (
    get_relationship_profile_service,
)
from plugins.xiaoyou_life_photo.plan_rules import (
    ALLOWED_SHARE_INTENTS,
    action_requires_free_hands,
    as_bool,
    normalize_camera_operator,
    normalize_capture_mode,
    normalize_choice,
    normalize_constraints,
)


PLUGIN_DIR = os.path.dirname(__file__)
PROFILE_FILE = os.path.join(PLUGIN_DIR, "assets", "xiaoyou_body_profile.json")
APPDATA_DIR = appdata_root()
DATA_DIR = os.path.join(APPDATA_DIR, "xiaoyou_life_photo")
STATE_FILE = runtime_path(
    "xiaoyou_life_photo",
    "state.json",
    env_var="XIAOYOU_LIFE_PHOTO_STATE_PATH",
    legacy_paths=(
        os.path.join(PLUGIN_DIR, "xiaoyou_life_photo", "state.json"),
        os.path.join(PLUGIN_DIR, "state.json"),
    ),
)
BACKUP_FILE = STATE_FILE + ".backup"
STATE_STORE = JsonStateStore(
    STATE_FILE,
    backup_path=BACKUP_FILE,
    name="xiaoyou_life_photo",
    default_factory=lambda: {"schema_version": 2, "sessions": {}},
)
GENERATED_DIR = os.path.join(DATA_DIR, "generated")
LOCK = threading.RLock()


@plugins.register(
    name="XiaoyouLifePhoto",
    desc="Memory-aware daily-life photos shared by Xiaoyou",
    version="0.9-couple-visual-identity",
    author="yoyo",
    desire_priority=31,
)
class XiaoyouLifePhoto(Plugin):
    def __init__(self):
        super().__init__()
        self.relationship_profile = get_relationship_profile_service()
        self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
        self.profile = self._load_profile()
        self.state = self._load_state()
        logger.info(
            "[XiaoyouLifePhoto] inited enabled=%s model=%s references=%s",
            self._enabled(),
            self._seedream_model(),
            len(self._reference_paths()),
        )

    def on_handle_context(self, e_context: EventContext):
        if not self._enabled():
            return

        context = e_context["context"]
        if context.type != ContextType.TEXT:
            return

        kwargs = getattr(context, "kwargs", {}) or {}
        if kwargs.get("isgroup"):
            return

        session_id = str(kwargs.get("session_id") or kwargs.get("receiver") or "").strip()
        receiver = str(kwargs.get("receiver") or "").strip()
        if not self._session_allowed(session_id) or not receiver:
            return

        current_text = extract_current_user_text(context.content)
        if not current_text:
            return

        semantic_route = classify_photo_semantics(
            text=current_text,
            session_id=session_id,
            pending_user_image=False,
            context=context,
        )
        if not semantic_route.should_generate:
            return

        plan = self._plan_photo(
            mode="user_request",
            session_id=session_id,
            user_text=current_text,
            context_text=str(context.content or ""),
            semantic_route=semantic_route,
        )
        if not plan or not plan.get("should_generate"):
            return

        share = self._generate_share(session_id, plan, source="user_request")
        if not share:
            self._attach_generation_failure_fact(context)
            return

        receipt = send_image(
            session_id=session_id,
            source="xiaoyou_life_photo_delivery",
            image_path=share["path"],
            receiver=receiver,
            channel=e_context["channel"],
            context=context,
            record_memory=False,
        )
        if receipt.stale:
            self.discard_share(share)
            e_context.action = EventAction.BREAK_PASS
            return
        if not receipt.ok:
            logger.warning(
                "[XiaoyouLifePhoto] user-requested photo send failed action_id=%s error=%s",
                receipt.action_id,
                receipt.error,
            )
            self._attach_generation_failure_fact(context)
            return

        self._mark_sent(session_id, share, source="user_request")
        caption = str(share.get("caption") or "").strip()
        if caption:
            e_context["reply"] = Reply(ReplyType.TEXT, caption)
        e_context.action = EventAction.BREAK_PASS
        logger.info(
            "[XiaoyouLifePhoto] user-requested photo sent session=%s has_caption=%s",
            session_id,
            bool(caption),
        )

    def create_proactive_share(self, session_id, activity=None):
        session_id = str(session_id or "").strip()
        if not self._enabled() or not self._proactive_enabled():
            return None
        if not self._session_allowed(session_id) or not self._can_send_proactive(session_id):
            return None

        activity = activity if isinstance(activity, dict) else {}
        last_user_text = str(activity.get("last_user_text") or "").strip()
        proactive_intent = str(activity.get("proactive_intent") or "").strip()
        plan = self._plan_photo(
            mode="proactive",
            session_id=session_id,
            user_text=proactive_intent or last_user_text,
            activity=activity,
        )
        if not plan or not plan.get("should_generate"):
            return None
        return self._generate_share(session_id, plan, source="proactive")

    def mark_proactive_sent(self, session_id, share):
        self._mark_sent(session_id, share, source="proactive")

    def discard_share(self, share):
        path = os.path.realpath(str((share or {}).get("path") or ""))
        root = os.path.realpath(GENERATED_DIR)
        if not path or not (path == root or path.startswith(root + os.sep)):
            return False
        try:
            if os.path.isfile(path):
                os.remove(path)
            return True
        except Exception:
            logger.exception("[XiaoyouLifePhoto] failed to discard unsent photo")
            return False

    def _plan_photo(
        self,
        mode,
        session_id,
        user_text="",
        context_text="",
        activity=None,
        semantic_route=None,
    ):
        api_key = os.getenv("OPEN_AI_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            logger.warning("[XiaoyouLifePhoto] planner api key missing")
            return None

        activity = activity if isinstance(activity, dict) else {}
        query = user_text or (
            "小悠此刻可能自然分享给YoYo的近期生活、喜好与关系细节"
            if mode == "proactive"
            else ""
        )
        context_snapshot = build_context_snapshot(
            content=user_text,
            session_id=session_id,
            long_memory_query=query,
            long_memory_max_results=max(
                1,
                int(os.getenv("XIAOYOU_LIFE_PHOTO_MEMORY_TOP_N", "16")),
            ),
            include_character=False,
            include_short_memory=True,
            short_memory_max_chars=max(
                1200,
                int(os.getenv("XIAOYOU_LIFE_PHOTO_CONTEXT_MAX_CHARS", "9000")),
            ),
            component="XiaoyouLifePhoto",
        )

        context_block = "当前输入与框架上下文：\n%s\n\n最近聊天：\n%s\n\n相关长期记忆：\n%s" % (
            str(context_text or user_text or "暂无").strip(),
            context_snapshot.short_memory or "暂无",
            context_snapshot.long_memory or "暂无",
        )

        max_context = max(1000, int(os.getenv("XIAOYOU_LIFE_PHOTO_CONTEXT_MAX_CHARS", "9000")))
        context_block = context_block[-max_context:]
        recent_shares = self._format_recent_shares(session_id)
        profile_text = json.dumps(self.profile, ensure_ascii=False, indent=2)
        time_context = context_snapshot.time_context
        character_desc = os.getenv("CHARACTER_DESC", "").strip()

        if mode == "proactive":
            task = """统一主动决策中枢已经结合完整语境和小悠当前内在状态选择了photo。你现在负责把这份真实分享意图规划成照片，而不是重新用关键词判断媒介。若语境已经明显失效或意图不安全可返回should_generate=false，否则应忠实实现。不要按固定题材轮播，也不要为了完成任务硬凑自拍、美食或穿搭。

统一主动决策事实：
%s""" % json.dumps(
                {
                    "photo_intent": activity.get("proactive_intent", ""),
                    "decision_reason": activity.get("decision_reason", ""),
                    "inner_state": activity.get("inner_state", {}),
                },
                ensure_ascii=False,
            )
        else:
            task = """统一语义路由已经把当前输入判断为“此刻要求小悠生成一张新照片”。你仍需结合完整上下文复核：只有现在确实应该执行才生成；未来安排、回忆、假设、否定、转述或讨论拍照都必须should_generate=false。不要依靠任何单个词判断。语义路由事实：%s""" % json.dumps(
                {
                    "route": getattr(semantic_route, "route", ""),
                    "time_scope": getattr(semantic_route, "time_scope", ""),
                    "subject": getattr(semantic_route, "subject", ""),
                    "reason": getattr(semantic_route, "reason", ""),
                },
                ensure_ascii=False,
            )

        prompt = """你是小悠的视觉导演，也是小悠本人。你的工作不是套模板，而是结合她的人格、身体档案、与YoYo的关系、当前时间、聊天和记忆，自主决定一张她真正愿意发给男朋友的生活照片。

%s

人物档案：
%s

当前时间：
%s

可用上下文与记忆：
%s

近期已经分享过的照片，避免机械重复：
%s

YoYo当前相关原话：
%s

设计原则：
- visual_prompt必须是完整、连贯、可直接交给生图模型的画面描述，自由决定场景、动作、表情、服装、镜头、光线和生活细节。
- share_intent描述为什么发照片；“自己拍照报备、给你看看、分享生活”当然可以使用前置自拍，也可以根据动作、构图和当时语义选择镜子自拍、定时拍摄或第三人称拍摄。报备是交流目的，不单独锁定镜头方式，由模型结合完整语义自主判断。
- capture_mode必须准确描述成片由哪一个镜头拍摄，可选front_camera_selfie、mirror_selfie、timer_camera、third_person_camera、first_person_scene。普通“自拍”才默认front_camera_selfie；明确要求镜子时使用mirror_selfie；明确要求第三人称、第三视角、他拍或别人拍时必须使用third_person_camera。
- front_camera_selfie的成片镜头就是小悠手中手机的前置摄像头，因此手机、持机手、手机背面、屏幕、自拍杆和拍摄这张自拍的第二台相机都不可能出现在成片里。不要在visual_prompt里描述“画面中能看到她举着手机”。
- 双手托脸、双手比心、双手举物、张开双臂、转圈、奔跑和复杂全身pose需要双手自由或远距离构图，不能使用front_camera_selfie，应使用timer_camera或third_person_camera。hands_free_required必须如实反映动作是否需要双手自由。
- third_person_camera只表示由语境中合理存在的第三人称镜头拍摄；除非原话或上下文明确说明，不要凭空声称YoYo就在现场掌镜。
- emotion先概括当次照片真正想表达的情绪；expression、gaze和pose再把它落实为自然的眉眼、嘴型、视线、头部角度与身体动作。不要机械重复眨眼、吐舌、双手托脸、比心等典型卖萌动作。
- 图片的emotion、expression和caption必须语义一致。普通报备不必夸张卖萌；困倦、担心、吃醋、得意、害羞、开心等状态应有不同而克制的视觉表现，但具体表达仍由小悠结合语境自主决定。
- include_yoyo必须依据完整语义判断成片里是否真实出现YoYo本人。YoYo只是拍摄者、被提到或不在现场时为false；情侣合照、拥抱、公主抱等明确同框时为true。禁止靠单个词判断。
- physical_constraints必须由完整语义决定，可选princess_carry、hands_free_pose、distant_full_body；没有对应物理要求时返回空数组。不要因为命中某个词就选择约束。
- 遇到公主抱时必须写清真实受力：YoYo站立，用一只手臂托住小悠背部/肩背，另一只手臂托住她弯曲膝盖下方或大腿；小悠横向或斜向依偎在他胸前，双腿弯曲并完全离地。不能画成坐腿、跨坐、背抱或两人并排坐着。
- 记忆只用于维持真实关系与偏好，不要逐条复述，也不要把不相关或过时的记忆硬塞进画面。
- 小悠的年龄、脸部和身体设定以人物档案为唯一来源。可以自然、有吸引力、有亲密感，但不要把她画成未成年人。
- caption是小悠发图后自然想说的话，完全由她自己决定；不要解释生图、提示词、模型或系统，也不要机械复述举手机、镜头位置或构图过程。
- 若不该生成，should_generate=false，其他文本可为空。

只输出合法JSON，不要Markdown：
{
  "should_generate": true,
  "visual_prompt": "自由而具体的画面描述",
  "caption": "小悠随图发给YoYo的话",
  "share_intent": "check_in、requested_pose、outfit_showcase、scene_share、couple_moment或proactive_share",
  "aspect_ratio": "portrait、square或landscape",
  "capture_mode": "front_camera_selfie、mirror_selfie、timer_camera、third_person_camera或first_person_scene",
  "camera_operator": "xiaoyou_handheld、xiaoyou_mirror、timer_tripod、yoyo、friend、passerby、companion或unspecified_third_person",
  "emotion": "结合当次语义得出的真实情绪",
  "expression": "与情绪一致的眉眼和嘴型，不套固定卖萌模板",
  "gaze": "自然视线方向",
  "pose": "符合镜头方式和人体物理的动作",
  "include_yoyo": false,
  "hands_free_required": false,
  "physical_constraints": ["princess_carry | hands_free_pose | distant_full_body"],
  "decision_reason": "简短内部理由"
}""" % (
            task,
            profile_text,
            time_context,
            context_block or "暂无",
            recent_shares or "暂无",
            user_text or "无明确原话，由小悠自主判断",
        )

        model = os.getenv("XIAOYOU_LIFE_PHOTO_PLANNER_MODEL") or os.getenv("MODEL") or "qwen3.7-max"
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (character_desc + "\n\n" + "你只输出合法JSON。").strip(),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": float(os.getenv("XIAOYOU_LIFE_PHOTO_PLANNER_TEMPERATURE", "0.8")),
            "max_tokens": int(os.getenv("XIAOYOU_LIFE_PHOTO_PLANNER_MAX_TOKENS", "1200")),
            **build_thinking_payload("XIAOYOU_LIFE_PHOTO_PLANNER"),
        }
        try:
            result = chat_completion(
                component="XiaoyouLifePhoto",
                purpose="photo_planner",
                payload=payload,
                timeout=int(os.getenv("XIAOYOU_LIFE_PHOTO_PLANNER_TIMEOUT", "75")),
                api_key=api_key,
            )
            if not result.ok:
                return None

            data = self._parse_json(result.content)
            if not isinstance(data, dict):
                logger.warning("[XiaoyouLifePhoto] planner returned invalid JSON")
                return None

            should_generate = data.get("should_generate", False)
            if isinstance(should_generate, str):
                should_generate = should_generate.strip().lower() in ("true", "1", "yes")
            else:
                should_generate = bool(should_generate)

            visual_prompt = str(data.get("visual_prompt") or "").strip()[:2400]
            caption = str(data.get("caption") or "").strip()[:600]
            aspect_ratio = str(data.get("aspect_ratio") or "portrait").strip().lower()
            if aspect_ratio not in ("portrait", "square", "landscape"):
                aspect_ratio = "portrait"
            share_intent = normalize_choice(
                data.get("share_intent"),
                ALLOWED_SHARE_INTENTS,
                "proactive_share" if mode == "proactive" else "check_in",
            )
            emotion = str(data.get("emotion") or "").strip()[:300]
            expression = str(data.get("expression") or "").strip()[:500]
            gaze = str(data.get("gaze") or "").strip()[:300]
            pose = str(data.get("pose") or "").strip()[:600]
            include_yoyo = as_bool(data.get("include_yoyo", False))
            pose_constraints = normalize_constraints(data.get("physical_constraints"))
            hands_free_required = (
                as_bool(data.get("hands_free_required", False))
                or "hands_free_pose" in pose_constraints
            )
            distant_camera_required = "distant_full_body" in pose_constraints
            capture_mode = normalize_capture_mode(
                data.get("capture_mode"),
                hands_free_required=hands_free_required,
                distant_camera_required=distant_camera_required,
            )
            hands_free_required = action_requires_free_hands(
                declared=hands_free_required,
            )
            camera_operator = normalize_camera_operator(
                capture_mode,
                requested_operator=data.get("camera_operator"),
            )

            if hands_free_required and "hands_free_pose" not in pose_constraints:
                pose_constraints.append("hands_free_pose")
            if should_generate and not visual_prompt:
                return None
            return {
                "should_generate": should_generate,
                "visual_prompt": visual_prompt,
                "caption": caption,
                "aspect_ratio": aspect_ratio,
                "capture_mode": capture_mode,
                "camera_operator": camera_operator,
                "share_intent": share_intent,
                "emotion": emotion,
                "expression": expression,
                "gaze": gaze,
                "pose": pose,
                "include_yoyo": include_yoyo,
                "hands_free_required": hands_free_required,
                "pose_constraints": pose_constraints,
                "decision_reason": str(data.get("decision_reason") or "").strip()[:300],
            }
        except Exception:
            logger.exception("[XiaoyouLifePhoto] planner request failed")
            return None

    def _generate_share(self, session_id, plan, source):
        api_key = os.getenv("SEEDREAM_API_KEY") or os.getenv("ARK_API_KEY")
        if not api_key:
            logger.warning("[XiaoyouLifePhoto] Seedream api key missing")
            return None

        reference_images = self._reference_data_urls(
            include_yoyo=bool(plan.get("include_yoyo"))
        )
        if not reference_images:
            logger.warning("[XiaoyouLifePhoto] no valid identity reference image")
            return None

        prompt = self._build_seedream_prompt(plan)
        output_format = os.getenv("XIAOYOU_LIFE_PHOTO_OUTPUT_FORMAT", "jpeg").strip().lower()
        if output_format not in ("jpeg", "png"):
            output_format = "jpeg"
        payload = {
            "model": self._seedream_model(),
            "prompt": prompt,
            "image": reference_images,
            "size": os.getenv("XIAOYOU_LIFE_PHOTO_SIZE", "2K").strip() or "2K",
            "sequential_image_generation": "disabled",
            "response_format": "b64_json",
            "output_format": output_format,
            "watermark": False,
        }
        base = (os.getenv("SEEDREAM_API_BASE") or "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
        headers = {
            "Authorization": "Bearer %s" % api_key,
            "Content-Type": "application/json",
        }

        try:
            response = requests.post(
                base + "/images/generations",
                headers=headers,
                json=payload,
                timeout=int(os.getenv("XIAOYOU_LIFE_PHOTO_TIMEOUT", "240")),
            )
            if response.status_code >= 400:
                logger.warning(
                    "[XiaoyouLifePhoto] Seedream failed status=%s body=%s",
                    response.status_code,
                    response.text[:500],
                )
                return None

            data = response.json().get("data") or []
            if not data or not isinstance(data[0], dict):
                logger.warning("[XiaoyouLifePhoto] Seedream response has no image")
                return None
            image_bytes = self._decode_image_result(data[0])
            if not image_bytes:
                return None
            path = self._save_generated_image(image_bytes, output_format)
            if not path:
                return None
            return {
                "path": path,
                "caption": str(plan.get("caption") or "").strip(),
                "visual_prompt": str(plan.get("visual_prompt") or "").strip(),
                "aspect_ratio": plan.get("aspect_ratio") or "portrait",
                "capture_mode": plan.get("capture_mode") or "front_camera_selfie",
                "camera_operator": plan.get("camera_operator") or "xiaoyou_handheld",
                "share_intent": plan.get("share_intent") or "check_in",
                "emotion": str(plan.get("emotion") or "").strip(),
                "expression": str(plan.get("expression") or "").strip(),
                "gaze": str(plan.get("gaze") or "").strip(),
                "pose": str(plan.get("pose") or "").strip(),
                "include_yoyo": bool(plan.get("include_yoyo")),
                "hands_free_required": bool(plan.get("hands_free_required")),
                "pose_constraints": list(plan.get("pose_constraints") or []),
                "source": source,
                "session_id": session_id,
                "created_at": int(time.time()),
            }
        except Exception:
            logger.exception("[XiaoyouLifePhoto] Seedream generation failed")
            return None

    def _build_seedream_prompt(self, plan):
        aspect_text = {
            "portrait": "竖幅手机照片，适合微信查看",
            "square": "自然方形手机照片",
            "landscape": "横幅手机照片",
        }.get(plan.get("aspect_ratio"), "竖幅手机照片，适合微信查看")
        capture_mode = str(plan.get("capture_mode") or "front_camera_selfie")
        capture_rules = {
            "front_camera_selfie": (
                "这是前置摄像头自拍，最终图片本身就是小悠手中手机前置摄像头拍到的成像。"
                "手机位于成片镜头之外，手机机身、手机背面、屏幕、持机手、自拍杆、镜子和第二台摄影设备绝对不能出现在画面中。"
                "采用自然手臂长度的近距离广角自拍透视；小悠看向成片镜头，一条手臂可从肩部向镜头方向自然延伸，"
                "但该手的手掌和手机必须处于取景框外。禁止第三人称视角拍摄她举手机"
            ),
            "mirror_selfie": (
                "这是明确的镜子自拍。画面来自镜中的反射，手机可以在镜中出现；手机、手臂、人物和环境必须遵循正确镜面透视，"
                "不能再出现拍摄镜子的第二台设备"
            ),
            "timer_camera": (
                "这是小悠架好设备并设置定时拍摄后得到的照片。相机和手机均在画面外，不出现任何拍摄设备、自拍杆或第三只持机手。"
                "小悠的双手可以自由完成动作，构图距离必须足以容纳本次姿势"
            ),
            "third_person_camera": (
                "这是由当前语境中合理存在的第三人称拍摄者从自然距离拍下的照片，不是小悠手持手机的自拍。"
                "小悠不伸出自拍手臂，双手可以自由完成动作；拍摄者和拍摄设备默认位于画面外。"
                "除非本次画面明确需要，不要把拍摄者本人、手机、相机、自拍杆或镜子画进画面"
            ),
            "first_person_scene": (
                "这是从小悠当时眼睛与手持手机所在位置拍下的第一视角生活照片，拍摄手机本身不入镜，透视与她的真实站位或坐姿一致。"
                "小悠本人不应以完整第三人称人物出现在画面中，只能按需要自然露出手、袖口、腿部或身体局部"
            ),
        }.get(capture_mode, "拍摄设备位于画面外，保持真实手机摄影透视")

        pose_rules = []
        if "princess_carry" in (plan.get("pose_constraints") or []):
            carry_rule = (
                "公主抱必须符合真实人体力学：YoYo处于站立状态，一只手臂横向稳稳托住小悠的肩背或上背，"
                "另一只手臂从她两条弯曲膝盖下方/大腿下方托住；小悠的躯干横向或斜向贴近YoYo胸前，臀部和双腿完全离开地面，"
                "双膝自然弯曲并靠拢。她不是坐在YoYo腿上，不是跨坐，不是背抱，不是坐姿，也不是自己站立。"
                "YoYo的两条支撑手臂和其余肢体数量必须准确，不能多手、多腿、肢体穿插或支撑点悬空。"
            )
            if capture_mode == "front_camera_selfie":
                carry_rule += (
                    "小悠只能用一条手臂伸向前置镜头完成自拍，另一条手臂自然依附身体或YoYo；持机手掌和手机位于画外。"
                    "近距离构图可主要呈现小悠面部、上身、弯曲双腿以及YoYo托住她的双臂和部分胸肩"
                )
            else:
                carry_rule += (
                    "本次不是前置自拍时，不得凭空增加伸向镜头的自拍手臂；小悠双手按照当次语义自然依偎、搂抱或完成动作"
                )
            pose_rules.append(carry_rule)
        if "hands_free_pose" in (plan.get("pose_constraints") or []):
            pose_rules.append(
                "本次动作需要小悠双手自由，双手必须都用于画面中描述的动作，不得再增加持机手或自拍手臂；"
                "必须采用定时拍摄或第三人称拍摄所对应的自然透视"
            )
        current_age = self.relationship_profile.xiaoyou_current_age()
        identity = json.dumps(
            {
                "identity_age": (
                    "%s岁的成年女性" % current_age
                    if current_age is not None
                    else self.profile.get("identity_age")
                ),
                "visual_medium": self.profile.get("visual_medium"),
                "face": self.profile.get("face"),
                "hair": self.profile.get("hair"),
                "body": self.profile.get("body"),
                "immutable_identity": self.profile.get("immutable_identity"),
            },
            ensure_ascii=False,
        )
        reference_roles = []
        for index, entry in enumerate(self.profile.get("reference_images") or [], 1):
            if not isinstance(entry, dict):
                continue
            reference_roles.append(
                "参考图%s：%s；保留%s；默认不要复制%s" % (
                    index,
                    str(entry.get("role") or "人物身份参考"),
                    str(entry.get("preserve") or "人物身份"),
                    str(entry.get("do_not_copy_by_default") or "服装、表情、姿势和背景"),
                )
            )
        include_yoyo = bool(plan.get("include_yoyo"))
        yoyo_profile = self.relationship_profile.yoyo_visual_profile() if include_yoyo else {}
        if include_yoyo and self.relationship_profile.yoyo_reference_path():
            reference_roles.append(
                "最后一张额外参考图：YoYo永久人脸身份参考；只保留YoYo的脸型、五官关系和身份辨识度；"
                "眼镜、发型整理、衣服、表情、背景和自拍畸变不是固定设定。绝不能把YoYo参考图融合到小悠脸上"
            )
        semantic_direction = json.dumps(
            {
                "share_intent": plan.get("share_intent"),
                "emotion": plan.get("emotion"),
                "expression": plan.get("expression"),
                "gaze": plan.get("gaze"),
                "pose": plan.get("pose"),
                "caption_meaning": plan.get("caption"),
                "include_yoyo": include_yoyo,
            },
            ensure_ascii=False,
        )
        sharing_context = {
            "front_camera_selfie": "由小悠使用前置摄像头亲自拍下",
            "mirror_selfie": "由小悠通过镜面完成自拍",
            "timer_camera": "由小悠架好设备并定时拍下",
            "third_person_camera": "由当时语境中合理存在的第三人称拍摄者拍下",
            "first_person_scene": "由小悠从自己的第一视角拍下眼前场景",
        }.get(capture_mode, "按本次镜头方式拍下")
        identity_age = (
            "%s岁的成年女性" % current_age
            if current_age is not None
            else str(self.profile.get("identity_age") or "明确的成年女性")
        )
        couple_identity_rule = (
            "本次包含YoYo。前面的小悠参考图只定义小悠，最后的YoYo自拍只定义YoYo；必须生成两个清晰不同且各自身份一致的人，严禁换脸、融脸、性别混淆或把YoYo的眼镜与五官复制给小悠。YoYo视觉档案：%s。"
            % json.dumps(yoyo_profile, ensure_ascii=False)
            if include_yoyo
            else "本次不包含YoYo本人，不要因为聊天提到他或他可能是拍摄者就额外生成男性人物。"
        )
        return """输入的前几张参考图共同定义小悠的永久人物身份。必须保持同一个成年女性：脸部骨相、五官比例、灰紫色眼睛、黑色长发、年龄感、头身比例和整体辨识度与参考图一致。参考图只负责身份、角度和体态，不要默认复制其中的白色上衣、灰色短裤、中性表情、标准站姿或纯色背景。

情侣身份规则：%s

参考图分工：
%s

小悠身体档案：%s

本次画面：%s

本次语义与神态：%s。图片中的眉眼、嘴型、视线、头部角度和身体动作必须共同表达本次情绪，并与随图文字的语气一致；不要因为参考图是中性表情就复制中性表情，也不要机械套用眨眼、吐舌、托脸或比心。

拍摄与构图：%s，%s。画面是小悠准备发给男朋友YoYo的真实生活照片，而不是商业海报或摄影棚样片。保留自然手机镜头感、生活环境细节和真实光线。

最高优先级镜头约束：%s。

最高优先级动作约束：%s。

质量要求：人物年龄设定为%s；身份高度一致；人体比例、手部、手指、关节、镜面反射和透视正确；只生成一张完整图片；不要拼图，不要多画面，不要出现第二个相似人物；不要把caption画成文字；不要文字、界面、边框、Logo、签名或水印。若本次动态画面描述与“最高优先级镜头约束”或“最高优先级动作约束”冲突，必须忽略动态描述中的冲突部分，以最高优先级约束为准。""" % (
            couple_identity_rule,
            "\n".join(reference_roles) or "按输入顺序共同参考人物身份",
            identity,
            plan.get("visual_prompt", ""),
            semantic_direction,
            aspect_text,
            sharing_context,
            capture_rules,
            " ".join(pose_rules) if pose_rules else "按照本次画面保持真实、自然、无额外肢体",
            identity_age,
        )

    def _reference_paths(self):
        paths = []
        env_paths = [
            value.strip()
            for value in os.getenv("XIAOYOU_LIFE_PHOTO_REFERENCE_IMAGES", "").split(",")
            if value.strip()
        ]
        profile_refs = self.profile.get("reference_images") or []
        for entry in profile_refs:
            if isinstance(entry, dict) and entry.get("path"):
                env_paths.append(str(entry.get("path")))
        for path in env_paths:
            if not os.path.isabs(path):
                path = os.path.join(PLUGIN_DIR, path)
            real_path = os.path.realpath(path)
            if os.path.isfile(real_path) and real_path not in paths:
                paths.append(real_path)
        return paths[:4]

    def _reference_data_urls(self, include_yoyo=False):
        results = []
        max_bytes = max(1, int(os.getenv("XIAOYOU_LIFE_PHOTO_REFERENCE_MAX_MB", "8"))) * 1024 * 1024
        for path in self._reference_paths():
            try:
                if os.path.getsize(path) > max_bytes:
                    logger.warning("[XiaoyouLifePhoto] reference image too large path=%s", path)
                    continue
                with open(path, "rb") as handle:
                    raw = handle.read()
                mime = mimetypes.guess_type(path)[0] or "image/jpeg"
                results.append("data:%s;base64,%s" % (mime, base64.b64encode(raw).decode("ascii")))
            except Exception:
                logger.exception("[XiaoyouLifePhoto] failed to read reference image path=%s", path)
        if include_yoyo:
            path = self.relationship_profile.yoyo_reference_path()
            try:
                if path and os.path.getsize(path) <= max_bytes:
                    with open(path, "rb") as handle:
                        raw = handle.read()
                    mime = mimetypes.guess_type(path)[0] or "image/jpeg"
                    results.append("data:%s;base64,%s" % (mime, base64.b64encode(raw).decode("ascii")))
            except Exception:
                logger.exception("[XiaoyouLifePhoto] failed to read private YoYo reference")
        return results

    def get_vision_identity_context(self, session_id):
        """Expose read-only visual identity facts to QwenVision."""
        with LOCK:
            state = self._load_state()
            item = copy.deepcopy(state.setdefault("sessions", {}).get(session_id, {}))
        recent = []
        for entry in (item.get("recent") or [])[-4:]:
            if not isinstance(entry, dict):
                continue
            recent.append({
                "ts": int(entry.get("ts") or 0),
                "caption": str(entry.get("caption") or "")[:300],
                "visual_prompt": str(entry.get("visual_prompt") or "")[:500],
                "capture_mode": str(entry.get("capture_mode") or "")[:80],
                "emotion": str(entry.get("emotion") or "")[:160],
                "include_yoyo": bool(entry.get("include_yoyo")),
            })
        current_age = self.relationship_profile.xiaoyou_current_age()
        profile = {
            "identity_name": self.profile.get("identity_name"),
            "identity_age": (
                "%s岁的成年女性" % current_age
                if current_age is not None
                else self.profile.get("identity_age")
            ),
            "visual_medium": self.profile.get("visual_medium"),
            "face": self.profile.get("face"),
            "hair": self.profile.get("hair"),
            "body": self.profile.get("body"),
            "immutable_identity": self.profile.get("immutable_identity"),
        }
        face_references = [
            path
            for path in self._reference_paths()
            if "face" in os.path.basename(path).lower()
        ][:2]
        reference_images = [
            {"path": path, "identity": "xiaoyou", "label": "小悠人脸身份参考"}
            for path in face_references
        ]
        yoyo_reference = self.relationship_profile.yoyo_reference_path()
        if yoyo_reference:
            reference_images.append({
                "path": yoyo_reference,
                "identity": "yoyo",
                "label": "YoYo本人真实人脸身份参考",
            })
        return {
            "profile": profile,
            "yoyo_profile": self.relationship_profile.yoyo_visual_profile(),
            "recent_photos": recent,
            "reference_paths": face_references,
            "reference_images": reference_images,
        }

    def identify_incoming_image(self, session_id, image_path):
        """Recognize a recently generated Xiaoyou image without another model call."""
        incoming = self._image_fingerprints(image_path)
        if not incoming.get("sha256"):
            return {"matched": False}

        with LOCK:
            state = self._load_state()
            entries = copy.deepcopy(
                state.setdefault("sessions", {}).get(session_id, {}).get("recent") or []
            )

        best = None
        for entry in reversed(entries[-20:]):
            if not isinstance(entry, dict):
                continue
            fingerprints = {
                "sha256": str(entry.get("sha256") or ""),
                "dhash": str(entry.get("dhash") or ""),
            }
            if not fingerprints["sha256"]:
                fingerprints = self._image_fingerprints(entry.get("path"))
            if incoming["sha256"] == fingerprints.get("sha256"):
                best = ("exact_hash", 0, entry)
                break
            distance = self._hash_distance(incoming.get("dhash"), fingerprints.get("dhash"))
            threshold = max(
                0,
                min(64, int(os.getenv("VISION_IDENTITY_HASH_DISTANCE", "8"))),
            )
            if distance is not None and distance <= threshold:
                if best is None or distance < best[1]:
                    best = ("perceptual_hash", distance, entry)

        if best is None:
            return {"matched": False}
        match_type, distance, entry = best
        return {
            "matched": True,
            "match_type": match_type,
            "distance": distance,
            "fact": "这张图片与小悠近期亲自生成并发给YoYo的生活照一致，图中主体就是小悠本人。",
            "caption": str(entry.get("caption") or "")[:500],
            "visual_prompt": str(entry.get("visual_prompt") or "")[:800],
            "created_at": int(entry.get("ts") or 0),
        }

    def _image_fingerprints(self, path):
        path = os.path.realpath(str(path or ""))
        if not path or not os.path.isfile(path):
            return {"sha256": "", "dhash": ""}
        try:
            digest = hashlib.sha256()
            with open(path, "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            sha256 = digest.hexdigest()
        except Exception:
            logger.exception("[XiaoyouLifePhoto] failed to hash image")
            return {"sha256": "", "dhash": ""}

        dhash = ""
        try:
            from PIL import Image

            with Image.open(path) as image:
                resampling = getattr(Image, "Resampling", Image)
                pixels = list(
                    image.convert("L")
                    .resize((9, 8), resampling.LANCZOS)
                    .getdata()
                )
            value = 0
            for row in range(8):
                offset = row * 9
                for column in range(8):
                    value = (value << 1) | int(
                        pixels[offset + column] > pixels[offset + column + 1]
                    )
            dhash = "%016x" % value
        except Exception:
            logger.info("[XiaoyouLifePhoto] perceptual hash unavailable; exact hash remains active")
        return {"sha256": sha256, "dhash": dhash}

    def _hash_distance(self, left, right):
        try:
            if not left or not right:
                return None
            return (int(left, 16) ^ int(right, 16)).bit_count()
        except Exception:
            return None

    def _decode_image_result(self, item):
        encoded = item.get("b64_json") or item.get("b64")
        if encoded:
            try:
                encoded = str(encoded)
                if "," in encoded and encoded.startswith("data:"):
                    encoded = encoded.split(",", 1)[1]
                raw = base64.b64decode(encoded, validate=True)
                return raw if self._valid_image_bytes(raw) else None
            except Exception:
                logger.exception("[XiaoyouLifePhoto] invalid base64 image response")
                return None

        url = str(item.get("url") or "").strip()
        if not url:
            return None
        try:
            response = requests.get(
                url,
                timeout=int(os.getenv("XIAOYOU_LIFE_PHOTO_DOWNLOAD_TIMEOUT", "60")),
            )
            if response.status_code >= 400:
                return None
            raw = response.content
            return raw if self._valid_image_bytes(raw) else None
        except Exception:
            logger.exception("[XiaoyouLifePhoto] generated image download failed")
            return None

    def _valid_image_bytes(self, raw):
        if not raw or len(raw) < 1024 or len(raw) > 40 * 1024 * 1024:
            return False
        return raw.startswith(b"\xff\xd8\xff") or raw.startswith(b"\x89PNG\r\n\x1a\n")

    def _save_generated_image(self, raw, requested_format):
        extension = "png" if raw.startswith(b"\x89PNG") else "jpg"
        if requested_format == "png" and extension != "png":
            extension = "jpg"
        month_dir = os.path.join(GENERATED_DIR, datetime.now().strftime("%Y-%m"))
        os.makedirs(month_dir, exist_ok=True)
        path = os.path.join(month_dir, "%s.%s" % (uuid.uuid4().hex, extension))
        tmp = path + ".tmp"
        try:
            with open(tmp, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
            return path
        except Exception:
            logger.exception("[XiaoyouLifePhoto] failed to save generated image")
            return ""

    def _record_photo_memory(self, session_id, share, source):
        visual = re.sub(r"\s+", " ", str(share.get("visual_prompt") or "")).strip()[:500]
        caption = re.sub(r"\s+", " ", str(share.get("caption") or "")).strip()[:500]
        text = (
            "[内部媒体事件：照片已实际送达；该记录不是小悠的微信文字；画面：%s]"
            % (visual or "生活瞬间")
        )
        if caption:
            text += " " + caption
        record_assistant_message(
            session_id,
            text,
            source="xiaoyou_life_photo_%s" % source,
        )

    def _mark_sent(self, session_id, share, source):
        now = int(time.time())
        fingerprints = self._image_fingerprints(share.get("path"))
        with LOCK:
            state = self._load_state()
            sessions = state.setdefault("sessions", {})
            item = sessions.setdefault(session_id, {})
            today = datetime.now().strftime("%Y-%m-%d")
            if item.get("day") != today:
                item["day"] = today
                item["photos_today"] = 0
                item["proactive_photos_today"] = 0
            item["last_photo_ts"] = now
            item["photos_today"] = int(item.get("photos_today") or 0) + 1
            if source == "proactive":
                item["last_proactive_photo_ts"] = now
                item["proactive_photos_today"] = int(item.get("proactive_photos_today") or 0) + 1
            recent = item.get("recent") if isinstance(item.get("recent"), list) else []
            recent.append({
                "ts": now,
                "source": source,
                "caption": str(share.get("caption") or "")[:600],
                "visual_prompt": str(share.get("visual_prompt") or "")[:1000],
                "share_intent": str(share.get("share_intent") or "")[:80],
                "capture_mode": str(share.get("capture_mode") or "")[:80],
                "camera_operator": str(share.get("camera_operator") or "")[:80],
                "emotion": str(share.get("emotion") or "")[:300],
                "expression": str(share.get("expression") or "")[:500],
                "gaze": str(share.get("gaze") or "")[:300],
                "pose": str(share.get("pose") or "")[:500],
                "include_yoyo": bool(share.get("include_yoyo")),
                "path": str(share.get("path") or ""),
                "sha256": fingerprints.get("sha256", ""),
                "dhash": fingerprints.get("dhash", ""),
            })
            item["recent"] = recent[-20:]
            sessions[session_id] = item
            self.state = state
            self._save_state_locked()
        self._record_photo_memory(session_id, share, source)

    def _can_send_proactive(self, session_id):
        if os.getenv("XIAOYOU_UNIFIED_PROACTIVE_ENABLED", "true").strip().lower() in (
            "1", "true", "yes", "on"
        ):
            # 统一中枢已经依据语境、状态和近期分享作出媒介决策；旧的
            # 6小时/每日2张属于行为时间表，在统一模式下不再拦截。
            return True
        with LOCK:
            state = self._load_state()
            item = state.setdefault("sessions", {}).get(session_id, {})
        now = int(time.time())
        minimum_interval = max(
            0,
            int(os.getenv("XIAOYOU_LIFE_PHOTO_PROACTIVE_MIN_INTERVAL_SECONDS", "21600")),
        )
        if now - int(item.get("last_photo_ts") or 0) < minimum_interval:
            return False
        today = datetime.now().strftime("%Y-%m-%d")
        photos_today = int(item.get("proactive_photos_today") or 0) if item.get("day") == today else 0
        max_per_day = max(0, int(os.getenv("XIAOYOU_LIFE_PHOTO_PROACTIVE_MAX_PER_DAY", "2")))
        return max_per_day > 0 and photos_today < max_per_day

    def _format_recent_shares(self, session_id):
        with LOCK:
            item = self.state.setdefault("sessions", {}).get(session_id, {})
            recent = copy.deepcopy(item.get("recent") or [])
        lines = []
        for entry in recent[-6:]:
            if not isinstance(entry, dict):
                continue
            text = str(entry.get("visual_prompt") or entry.get("caption") or "").strip()
            if text:
                tags = "/".join(
                    value
                    for value in (
                        str(entry.get("share_intent") or "").strip(),
                        str(entry.get("capture_mode") or "").strip(),
                        str(entry.get("emotion") or "").strip(),
                        str(entry.get("expression") or "").strip(),
                        str(entry.get("pose") or "").strip(),
                    )
                    if value
                )
                lines.append("- [%s] %s" % (tags[:300] or "旧记录", text[:300]))
        return "\n".join(lines)

    def _load_profile(self):
        path = os.getenv("XIAOYOU_LIFE_PHOTO_PROFILE_FILE", PROFILE_FILE).strip() or PROFILE_FILE
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                return data
        except Exception:
            logger.exception("[XiaoyouLifePhoto] failed to load body profile path=%s", path)
        return {}

    def _load_state(self):
        data = STATE_STORE.load()
        if not isinstance(data, dict):
            data = {"schema_version": 2, "sessions": {}}
        data["schema_version"] = 2
        if not isinstance(data.get("sessions"), dict):
            data["sessions"] = {}
        return data

    def _save_state_locked(self):
        return STATE_STORE.save(self.state)

    def _parse_json(self, value):
        text = str(value or "").strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        try:
            return json.loads(text)
        except Exception:
            match = re.search(r"\{.*\}", text, re.S)
            if not match:
                return None
            try:
                return json.loads(match.group(0))
            except Exception:
                return None

    def _attach_generation_failure_fact(self, context):
        context.content = "%s\n\n[本轮图片发送状态事实]\n照片没有成功生成或发送。不要声称已经发出图片；由小悠结合当前聊天自行决定如何自然回应。" % str(
            context.content or ""
        )
        kwargs = getattr(context, "kwargs", {}) or {}
        kwargs["xiaoyou_life_photo_failed"] = True
        context.kwargs = kwargs

    def _session_allowed(self, session_id):
        canonical = os.getenv("XIAOYOU_CANONICAL_SESSION_ID", "yoyo").strip() or "yoyo"
        return str(session_id or "").strip() == canonical

    def _seedream_model(self):
        return os.getenv(
            "SEEDREAM_MODEL",
            "doubao-seedream-5-0-lite-260128",
        ).strip() or "doubao-seedream-5-0-lite-260128"

    def _enabled(self):
        return os.getenv("XIAOYOU_LIFE_PHOTO_ENABLED", "true").strip().lower() in (
            "1", "true", "yes", "on"
        )

    def _proactive_enabled(self):
        return os.getenv(
            "XIAOYOU_LIFE_PHOTO_PROACTIVE_ENABLED",
            "true",
        ).strip().lower() in ("1", "true", "yes", "on")
