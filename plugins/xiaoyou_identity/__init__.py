# -*- coding:utf-8 -*-
import os
import threading
import time

import plugins
from plugins import *
from common.log import logger
from lib import itchat
from plugins.xiaoyou_common.state_store import JsonStateStore
from plugins.xiaoyou_common.runtime_paths import appdata_root, runtime_path
from plugins.xiaoyou_common.conversation_coordinator import note_user_activity
from plugins.xiaoyou_common.trace_service import (
    attach_input_trace,
    rebind_trace_session,
)


STATE_FILE = runtime_path(
    "xiaoyou_identity",
    "state.json",
    env_var="XIAOYOU_IDENTITY_STATE_PATH",
    legacy_paths=(
        os.path.join(appdata_root(), "xiaoyou_identity.json"),
        os.path.join(os.path.dirname(__file__), "xiaoyou_identity.json"),
    ),
)
BACKUP_FILE = STATE_FILE + ".backup"
STATE_STORE = JsonStateStore(
    STATE_FILE,
    backup_path=BACKUP_FILE,
    name="xiaoyou_identity",
    default_factory=dict,
)
LOCK = threading.RLock()
THREAD_STARTED = False


@plugins.register(
    name="XiaoyouIdentity",
    desc="Bind temporary WeChat UserName values to Xiaoyou's stable YoYo identity",
    version="0.4-trace-runtime",
    author="yoyo",
    desire_priority=10000,
)
class XiaoyouIdentity(Plugin):
    def __init__(self):
        global THREAD_STARTED
        super().__init__()
        self.handlers[Event.ON_RECEIVE_MESSAGE] = self.on_receive_message
        self.canonical_id = os.getenv("XIAOYOU_CANONICAL_SESSION_ID", "yoyo").strip() or "yoyo"
        self.legacy_ids = self._env_list("XIAOYOU_LEGACY_SESSION_IDS")
        self.configured_profile = {
            "wechat_alias": os.getenv("XIAOYOU_TARGET_WECHAT_ALIAS", "").strip(),
            "remark_name": os.getenv("XIAOYOU_TARGET_REMARK_NAME", "").strip(),
            "nickname": os.getenv("XIAOYOU_TARGET_NICKNAME", "").strip(),
        }
        self.state = self._load_state()
        self._migrate_state_defaults()

        if self._enabled() and not THREAD_STARTED:
            THREAD_STARTED = True
            threading.Thread(
                target=self._refresh_loop,
                daemon=True,
                name="XiaoyouIdentityRefresh",
            ).start()

        logger.info(
            "[XiaoyouIdentity] inited canonical=%s known_sessions=%s profile=%s",
            self.canonical_id,
            len(self.state.get("known_session_ids", [])),
            self._profile_log(self.state),
        )

    def on_receive_message(self, e_context: EventContext):
        if not self._enabled():
            return

        context = e_context["context"]
        if context is None:
            return

        kwargs = getattr(context, "kwargs", {}) or {}
        if kwargs.get("isgroup"):
            return

        temporary_session = str(
            kwargs.get("session_id")
            or kwargs.get("receiver")
            or ""
        ).strip()
        receiver = str(kwargs.get("receiver") or temporary_session).strip()
        if not temporary_session or not receiver:
            return

        profile = self._extract_profile(kwargs.get("msg"), context)

        if not self._matches_target(temporary_session, profile):
            return

        attach_input_trace(context, source="wechat_receive")

        self._bind(temporary_session, receiver, profile, source="incoming")

        kwargs = getattr(context, "kwargs", {}) or {}
        kwargs["xiaoyou_original_session_id"] = temporary_session
        kwargs["xiaoyou_canonical_session_id"] = self.canonical_id
        kwargs["session_id"] = self.canonical_id
        kwargs["receiver"] = receiver
        context.kwargs = kwargs

        rebind_trace_session(kwargs.get("xiaoyou_trace_id", ""), self.canonical_id)

        note_user_activity(
            self.canonical_id,
            activity_ts=time.time(),
            source="wechat_input",
            turn_id=kwargs.get("xiaoyou_input_version", ""),
            trace_id=kwargs.get("xiaoyou_trace_id", ""),
            input_id=kwargs.get("xiaoyou_input_id", ""),
        )

        logger.info(
            "[XiaoyouIdentity] canonicalized session temporary=%s canonical=%s receiver=%s",
            self._mask(temporary_session),
            self.canonical_id,
            self._mask(receiver),
        )

    def resolve_receiver(self, session_id=None):
        session_id = str(session_id or self.canonical_id).strip()
        if session_id != self.canonical_id:
            return ""
        if self._enabled():
            self._refresh_from_contacts()
        with LOCK:
            receiver = str(self.state.get("current_receiver") or "").strip()
        if receiver and not self._receiver_is_current(receiver):
            logger.warning(
                "[XiaoyouIdentity] stored receiver is no longer in current contact map; send skipped"
            )
            return ""
        return receiver

    def is_canonical_session(self, session_id):
        return str(session_id or "").strip() == self.canonical_id

    def _matches_target(self, temporary_session, profile):
        temporary_session = str(temporary_session or "").strip()
        with LOCK:
            known_ids = set(self.state.get("known_session_ids") or [])
            known_ids.update(self.legacy_ids)
            if temporary_session in known_ids:
                return True

            stored_profile = {
                "wechat_alias": self.state.get("wechat_alias", ""),
                "remark_name": self.state.get("remark_name", ""),
                "nickname": self.state.get("nickname", ""),
            }

        target_profile = {
            key: self.configured_profile.get(key) or stored_profile.get(key)
            for key in ("wechat_alias", "remark_name", "nickname")
        }
        matched_key = self._profile_match_key(profile, target_profile)
        if matched_key == "nickname":
            return self._nickname_is_unique_target(temporary_session, profile.get("nickname"))
        return bool(matched_key)

    def _bind(self, temporary_session, receiver, profile, source):
        temporary_session = str(temporary_session or "").strip()
        receiver = str(receiver or temporary_session).strip()
        if not temporary_session or not receiver:
            return False

        with LOCK:
            known = list(self.state.get("known_session_ids") or [])
            if temporary_session not in known:
                known.append(temporary_session)
            self.state["known_session_ids"] = known[-20:]
            self.state["current_receiver"] = receiver
            self.state["canonical_session_id"] = self.canonical_id
            self.state["updated_at"] = int(time.time())
            self.state["last_bind_source"] = str(source or "")[:40]

            for key in ("wechat_alias", "remark_name", "nickname"):
                value = str(profile.get(key) or "").strip()
                if value:
                    self.state[key] = value

            self._save_state_locked()
        return True

    def _refresh_loop(self):
        while True:
            try:
                interval = max(10, int(os.getenv("XIAOYOU_IDENTITY_REFRESH_SECONDS", "30")))
                time.sleep(interval)
                self._refresh_from_contacts()
            except Exception:
                logger.exception("[XiaoyouIdentity] contact refresh failed")
                time.sleep(30)

    def _refresh_from_contacts(self):
        try:
            friends = itchat.get_friends(update=False) or []
        except Exception:
            return
        if not isinstance(friends, list) or not friends:
            return

        with LOCK:
            target_profile = {
                key: self.configured_profile.get(key) or self.state.get(key, "")
                for key in ("wechat_alias", "remark_name", "nickname")
            }

        # First seed stable profile fields by finding the configured legacy UserName.
        if not any(str(value or "").strip() for value in target_profile.values()):
            with LOCK:
                known_ids = set(self.state.get("known_session_ids") or [])
            known_ids.update(self.legacy_ids)
            legacy_matches = []
            for friend in friends:
                if not isinstance(friend, dict):
                    continue
                temporary_session = str(friend.get("UserName") or friend.get("user_name") or "").strip()
                if temporary_session in known_ids:
                    legacy_matches.append((friend, self._contact_profile(friend)))
            if len(legacy_matches) == 1:
                friend, profile = legacy_matches[0]
                temporary_session = str(friend.get("UserName") or friend.get("user_name") or "").strip()
                self._bind(temporary_session, temporary_session, profile, source="legacy_contact_seed")
            return

        matches = []
        for friend in friends:
            if not isinstance(friend, dict):
                continue
            profile = self._contact_profile(friend)
            if self._profile_matches(profile, target_profile):
                matches.append((friend, profile))

        if len(matches) != 1:
            if len(matches) > 1:
                logger.warning("[XiaoyouIdentity] contact profile is not unique; receiver refresh skipped")
            return

        friend, profile = matches[0]
        temporary_session = str(friend.get("UserName") or friend.get("user_name") or "").strip()
        if not temporary_session:
            return

        with LOCK:
            current = str(self.state.get("current_receiver") or "").strip()
        if temporary_session == current:
            return

        self._bind(temporary_session, temporary_session, profile, source="contact_refresh")
        logger.info(
            "[XiaoyouIdentity] refreshed current receiver=%s canonical=%s",
            self._mask(temporary_session),
            self.canonical_id,
        )

    def _profile_matches(self, actual, target):
        # Alias 最稳定，其次备注名；昵称仅在联系人列表中唯一时才会被接受。
        return bool(self._profile_match_key(actual, target))

    def _profile_match_key(self, actual, target):
        for key in ("wechat_alias", "remark_name", "nickname"):
            expected = self._normalize(target.get(key))
            observed = self._normalize(actual.get(key))
            if expected and observed:
                return key if expected == observed else ""
        return ""

    def _nickname_is_unique_target(self, temporary_session, nickname):
        expected = self._normalize(nickname)
        if not expected:
            return False
        try:
            friends = itchat.get_friends(update=False) or []
        except Exception:
            return False
        matches = [
            str(friend.get("UserName") or friend.get("user_name") or "").strip()
            for friend in friends
            if isinstance(friend, dict)
            and self._normalize(friend.get("NickName") or friend.get("nickname")) == expected
        ]
        return len(matches) == 1 and matches[0] == temporary_session

    def _receiver_is_current(self, receiver):
        try:
            friends = itchat.get_friends(update=False) or []
        except Exception:
            return True
        if not isinstance(friends, list) or not friends:
            return True
        return any(
            isinstance(friend, dict)
            and str(friend.get("UserName") or friend.get("user_name") or "").strip() == receiver
            for friend in friends
        )

    def _contact_profile(self, friend):
        return {
            "wechat_alias": str(friend.get("Alias") or friend.get("alias") or "").strip(),
            "remark_name": str(friend.get("RemarkName") or friend.get("remark_name") or "").strip(),
            "nickname": str(friend.get("NickName") or friend.get("nickname") or "").strip(),
        }

    def _extract_profile(self, msg, context):
        sources = [
            msg,
            self._nested_value(msg, "User"),
            self._nested_value(msg, "ActualUser"),
            getattr(msg, "raw", None) if msg is not None else None,
            getattr(msg, "_rawmsg", None) if msg is not None else None,
            getattr(msg, "user", None) if msg is not None else None,
            getattr(msg, "other_user", None) if msg is not None else None,
        ]

        alias = self._first_value(sources, (
            "Alias", "alias", "wechat_account", "WeChatAccount", "wechatAccount",
        ))
        remark = self._first_value(sources, (
            "RemarkName", "remark_name", "remarkName", "from_user_remark_name",
        ))
        nickname = self._first_value(sources, (
            "other_user_nickname", "from_user_nickname", "actual_user_nickname",
            "NickName", "nickname", "nick_name",
        ))

        if not nickname:
            context_kwargs = getattr(context, "kwargs", {}) or {}
            nickname = str(
                context_kwargs.get("from_user_nickname")
                or context_kwargs.get("other_user_nickname")
                or ""
            ).strip()

        return {
            "wechat_alias": alias,
            "remark_name": remark,
            "nickname": nickname,
        }

    def _first_value(self, sources, keys):
        for source in sources:
            if source is None:
                continue
            for key in keys:
                try:
                    if isinstance(source, dict):
                        value = source.get(key)
                    else:
                        value = getattr(source, key, None)
                        if value is None:
                            value = source[key]
                except Exception:
                    value = None
                value = str(value or "").strip()
                if value:
                    return value
        return ""

    def _nested_value(self, source, key):
        if source is None:
            return None
        try:
            if isinstance(source, dict):
                return source.get(key)
            value = getattr(source, key, None)
            if value is not None:
                return value
            return source[key]
        except Exception:
            return None

    def _migrate_state_defaults(self):
        with LOCK:
            self.state.setdefault("schema_version", 1)
            self.state["canonical_session_id"] = self.canonical_id
            known = list(self.state.get("known_session_ids") or [])
            for legacy_id in self.legacy_ids:
                if legacy_id not in known:
                    known.append(legacy_id)
            self.state["known_session_ids"] = known[-20:]
            if not self.state.get("current_receiver") and self.legacy_ids:
                self.state["current_receiver"] = self.legacy_ids[0]
            for key, value in self.configured_profile.items():
                if value and not self.state.get(key):
                    self.state[key] = value
            self._save_state_locked()

    def _load_state(self):
        data = STATE_STORE.load()
        return data if isinstance(data, dict) else {}

    def _save_state_locked(self):
        return STATE_STORE.save(self.state)

    def _env_list(self, key):
        raw = str(os.getenv(key, "") or "")
        return [value.strip() for value in raw.split(",") if value.strip()]

    def _enabled(self):
        return os.getenv("XIAOYOU_IDENTITY_ENABLED", "true").strip().lower() in (
            "1", "true", "yes", "on"
        )

    def _normalize(self, value):
        return str(value or "").strip().casefold()

    def _profile_log(self, profile):
        return {
            "has_alias": bool(profile.get("wechat_alias")),
            "has_remark": bool(profile.get("remark_name")),
            "has_nickname": bool(profile.get("nickname")),
        }

    def _mask(self, value):
        value = str(value or "")
        if len(value) <= 14:
            return value
        return value[:7] + "***" + value[-6:]
