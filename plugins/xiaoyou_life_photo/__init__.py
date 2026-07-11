# -*- coding:utf-8 -*-
import base64
import copy
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
from plugins.xiaoyou_common.outbound_dispatcher import (
    record_assistant_message,
    send_image,
)
from plugins.xiaoyou_common.state_store import JsonStateStore


PLUGIN_DIR = os.path.dirname(__file__)
PROFILE_FILE = os.path.join(PLUGIN_DIR, "assets", "xiaoyou_body_profile.json")
APPDATA_DIR = os.getenv("APPDATA_DIR", "").strip() or PLUGIN_DIR
DATA_DIR = os.path.join(APPDATA_DIR, "xiaoyou_life_photo")
STATE_FILE = os.path.join(DATA_DIR, "state.json")
BACKUP_FILE = STATE_FILE + ".backup"
STATE_STORE = JsonStateStore(
    STATE_FILE,
    backup_path=BACKUP_FILE,
    name="xiaoyou_life_photo",
    default_factory=lambda: {"schema_version": 1, "sessions": {}},
)
GENERATED_DIR = os.path.join(DATA_DIR, "generated")
LOCK = threading.RLock()


@plugins.register(
    name="XiaoyouLifePhoto",
    desc="Memory-aware daily-life photos shared by Xiaoyou",
    version="0.6-trace-runtime",
    author="yoyo",
    desire_priority=31,
)
class XiaoyouLifePhoto(Plugin):
    def __init__(self):
        super().__init__()
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
        if not current_text or not self._looks_like_photo_candidate(current_text):
            return

        plan = self._plan_photo(
            mode="user_request",
            session_id=session_id,
            user_text=current_text,
            context_text=str(context.content or ""),
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
        plan = self._plan_photo(
            mode="proactive",
            session_id=session_id,
            user_text=last_user_text,
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

    def _plan_photo(self, mode, session_id, user_text="", context_text="", activity=None):
        api_key = os.getenv("OPEN_AI_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            logger.warning("[XiaoyouLifePhoto] planner api key missing")
            return None

        activity = activity if isinstance(activity, dict) else {}
        query = ""
        if mode == "proactive":
            query = user_text or "小悠此刻可能自然分享给YoYo的近期生活、喜好与关系细节"
        context_snapshot = build_context_snapshot(
            content=user_text,
            session_id=session_id,
            long_memory_query=query,
            long_memory_max_results=max(
                1,
                int(os.getenv("XIAOYOU_LIFE_PHOTO_MEMORY_TOP_N", "8")),
            ),
            include_character=False,
            include_short_memory=mode == "proactive",
            component="XiaoyouLifePhoto",
        )

        if mode == "proactive":
            long_memory = context_snapshot.long_memory
            short_memory = context_snapshot.short_memory
            context_block = "长期记忆：\n%s\n\n最近聊天：\n%s" % (
                long_memory or "暂无",
                short_memory or "暂无",
            )
        else:
            context_block = str(context_text or "").strip()

        max_context = max(1000, int(os.getenv("XIAOYOU_LIFE_PHOTO_CONTEXT_MAX_CHARS", "9000")))
        context_block = context_block[-max_context:]
        recent_shares = self._format_recent_shares(session_id)
        profile_text = json.dumps(self.profile, ensure_ascii=False, indent=2)
        time_context = context_snapshot.time_context
        character_desc = os.getenv("CHARACTER_DESC", "").strip()

        if mode == "proactive":
            task = """这是一次主动分享判断。你可以决定不发照片；只有当此刻像真实女朋友一样自然想把某个生活瞬间拍给YoYo看时才生成。不要按固定题材轮播，也不要为了完成任务硬凑自拍、美食或穿搭。"""
        else:
            task = """这是YoYo当前提出的请求。判断他是否真的在让小悠拍照、自拍、展示穿搭或用照片分享眼前事物；若是，理解完整上下文后为这一次请求设计画面。否定、转述、讨论图片技术而非索要照片时不要生成。"""

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
- 这是小悠自己拍下并通过微信分享的照片。自拍、镜子自拍、定时拍摄、从她视角拍食物或物件等拍法都可以，具体方式由当下内容决定；透视必须符合拍摄方式。
- capture_mode必须准确描述成片由哪一个镜头拍摄。普通“自拍”默认是front_camera_selfie；只有YoYo明确要求镜子或对镜自拍时才使用mirror_selfie。
- front_camera_selfie的成片镜头就是小悠手中手机的前置摄像头，因此手机、持机手、手机背面、屏幕、自拍杆和拍摄这张自拍的第二台相机都不可能出现在成片里。不要在visual_prompt里描述“画面中能看到她举着手机”。
- 遇到公主抱时必须写清真实受力：YoYo站立，用一只手臂托住小悠背部/肩背，另一只手臂托住她弯曲膝盖下方或大腿；小悠横向或斜向依偎在他胸前，双腿弯曲并完全离地。不能画成坐腿、跨坐、背抱或两人并排坐着。
- 记忆只用于维持真实关系与偏好，不要逐条复述，也不要把不相关或过时的记忆硬塞进画面。
- 小悠是24岁的成年女性。可以自然、有吸引力、有亲密感，但不要把她画成未成年人。
- caption是小悠发图后自然想说的话，完全由她自己决定；不要解释生图、提示词、模型或系统，也不要机械复述举手机、镜头位置或构图过程。
- 若不该生成，should_generate=false，其他文本可为空。

只输出合法JSON，不要Markdown：
{
  "should_generate": true,
  "visual_prompt": "自由而具体的画面描述",
  "caption": "小悠随图发给YoYo的话",
  "aspect_ratio": "portrait、square或landscape",
  "capture_mode": "front_camera_selfie、mirror_selfie、timer_camera或first_person_scene",
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
                timeout=int(os.getenv("XIAOYOU_LIFE_PHOTO_PLANNER_TIMEOUT", "45")),
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
            capture_mode = str(data.get("capture_mode") or "").strip().lower()
            allowed_capture_modes = (
                "front_camera_selfie",
                "mirror_selfie",
                "timer_camera",
                "first_person_scene",
            )
            if capture_mode not in allowed_capture_modes:
                capture_mode = "front_camera_selfie"

            normalized_user_text = str(user_text or "").strip()
            if re.search(r"镜子自拍|镜前自拍|对镜自拍|对镜拍|镜子前", normalized_user_text):
                capture_mode = "mirror_selfie"
            elif re.search(r"自拍|自己拍(?:一|个|张|下)|拍(?:一|个|张)自己", normalized_user_text):
                capture_mode = "front_camera_selfie"

            pose_constraints = []
            if "公主抱" in normalized_user_text or "公主抱" in visual_prompt:
                pose_constraints.append("princess_carry")
                if not re.search(r"镜子|对镜|定时|他拍|别人拍", normalized_user_text):
                    capture_mode = "front_camera_selfie"
            if should_generate and not visual_prompt:
                return None
            return {
                "should_generate": should_generate,
                "visual_prompt": visual_prompt,
                "caption": caption,
                "aspect_ratio": aspect_ratio,
                "capture_mode": capture_mode,
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

        reference_images = self._reference_data_urls()
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
                timeout=int(os.getenv("XIAOYOU_LIFE_PHOTO_TIMEOUT", "180")),
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
                "这是小悠设置好定时拍摄后得到的照片。相机和手机均在画面外，不出现任何拍摄设备、自拍杆或第三只持机手"
            ),
            "first_person_scene": (
                "这是从小悠当时眼睛与手持手机所在位置拍下的第一视角生活照片，拍摄手机本身不入镜，透视与她的真实站位或坐姿一致"
            ),
        }.get(capture_mode, "拍摄设备位于画面外，保持真实手机摄影透视")

        pose_rules = []
        if "princess_carry" in (plan.get("pose_constraints") or []):
            pose_rules.append(
                "公主抱必须符合真实人体力学：YoYo处于站立状态，一只手臂横向稳稳托住小悠的肩背或上背，"
                "另一只手臂从她两条弯曲膝盖下方/大腿下方托住；小悠的躯干横向或斜向贴近YoYo胸前，臀部和双腿完全离开地面，"
                "双膝自然弯曲并靠拢。她不是坐在YoYo腿上，不是跨坐，不是背抱，不是坐姿，也不是自己站立。"
                "YoYo的两条支撑手臂、她伸向前置镜头的一条自拍手臂以及其余肢体数量必须准确，不能多手、多腿、肢体穿插或支撑点悬空。"
                "由于是近距离自拍，可以主要呈现小悠面部、上身、弯曲双腿以及YoYo托住她的双臂和部分胸肩；YoYo没有人脸参考时不必完整露出脸"
            )
        identity = json.dumps(
            {
                "identity_age": self.profile.get("identity_age"),
                "visual_medium": self.profile.get("visual_medium"),
                "face": self.profile.get("face"),
                "hair": self.profile.get("hair"),
                "body": self.profile.get("body"),
                "immutable_identity": self.profile.get("immutable_identity"),
            },
            ensure_ascii=False,
        )
        return """参考图1是小悠永久的人脸与基础画风参考。必须保持同一个成年女性身份：脸部骨相、五官比例、灰紫色眼睛、黑色长发、年龄感和整体辨识度与参考图1一致。只参考人物身份和画风，不要默认复制参考图里的婚纱、头纱、珍珠首饰、姿势、卧室背景、水印或文字。

小悠身体档案：%s

本次画面：%s

拍摄与构图：%s。画面应像小悠此刻亲自拍下并准备发给男朋友YoYo的真实生活照片，而不是商业海报或摄影棚样片。保留自然手机镜头感、生活环境细节和真实光线。

最高优先级镜头约束：%s。

最高优先级动作约束：%s。

质量要求：人物明确为24岁的成年女性；身份高度一致；人体比例、手部、手指、关节、镜面反射和透视正确；只生成一张完整图片；不要拼图，不要多画面，不要出现第二个相似人物；不要文字、界面、边框、Logo、签名或水印。若本次动态画面描述与“最高优先级镜头约束”或“最高优先级动作约束”冲突，必须忽略动态描述中的冲突部分，以最高优先级约束为准。""" % (
            identity,
            plan.get("visual_prompt", ""),
            aspect_text,
            capture_rules,
            " ".join(pose_rules) if pose_rules else "按照本次画面保持真实、自然、无额外肢体",
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

    def _reference_data_urls(self):
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
        return results

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
        text = "[小悠分享了一张日常照片；画面：%s]" % (visual or "生活瞬间")
        if caption:
            text += " " + caption
        record_assistant_message(
            session_id,
            text,
            source="xiaoyou_life_photo_%s" % source,
        )

    def _mark_sent(self, session_id, share, source):
        now = int(time.time())
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
                "path": str(share.get("path") or ""),
            })
            item["recent"] = recent[-20:]
            sessions[session_id] = item
            self.state = state
            self._save_state_locked()
        self._record_photo_memory(session_id, share, source)

    def _can_send_proactive(self, session_id):
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
                lines.append("- %s" % text[:300])
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
            data = {"schema_version": 1, "sessions": {}}
        data.setdefault("schema_version", 1)
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

    def _looks_like_photo_candidate(self, text):
        text = str(text or "").strip()
        patterns = (
            r"拍(?:一|个|张|点|些|下|给|给我|你的|套|身|照)",
            r"照片|自拍|合照|镜子照|镜子自拍|穿搭照",
            r"发(?:一|个|张|点|些)?.{0,6}(?:图|照片|自拍)",
            r"(?:给我|让我|想|要|来).{0,8}(?:看看|看下|看一眼).{0,10}(?:你|穿搭|衣服|吃的|那边|现在)",
            r"分享.{0,8}(?:日常|生活|穿搭|美食|照片)",
            r"(?:看看你|看你现在|看你今天|给我看看你)",
        )
        return any(re.search(pattern, text, re.I) for pattern in patterns)

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
