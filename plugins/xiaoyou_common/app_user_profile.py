# -*- coding: utf-8 -*-
"""Physically isolated, human-readable profile documents for App users."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path

try:
    from plugins.xiaoyou_common.runtime_paths import app_user_runtime_path
except ModuleNotFoundError:  # Direct-file unit tests do not import the package root.
    def app_user_runtime_path(session_id, namespace, filename):
        session_id = str(session_id or "").strip()
        if not session_id.startswith("app_user_"):
            raise ValueError("registered App session required")
        user_id = "usr_" + session_id[len("app_user_"):]
        root = os.getenv("APPDATA_DIR", "").strip()
        if not root:
            root = str(Path(__file__).resolve().parents[2] / "data")
        path = os.path.join(root, "app_users", user_id, namespace, filename)
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        return path


def profile_document_path(session_id):
    return app_user_runtime_path(session_id, "profile", "user_profile.md")


def write_profile_document(session_id, profile):
    """Atomically materialize stable user facts for the conversation context."""

    path = profile_document_path(session_id)
    started_at = int(profile.get("relationship_started_at") or 0)
    started_text = (
        datetime.fromtimestamp(started_at).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
        if started_at
        else "未知"
    )
    lines = [
        "# 小悠的用户专属资料",
        "",
        "> 这份资料只属于当前登录用户。它是用户亲自填写或由账号系统确认的事实，不能与其他用户混用。",
        "",
        "## 基础资料",
        "",
        "- 登录账号：{}".format(str(profile.get("account_id") or "").strip()),
        "- 希望小悠称呼：{}".format(str(profile.get("display_name") or "").strip() or "未填写"),
        "- 生日：{}".format(str(profile.get("birthday") or "").strip() or "未填写"),
        "- 与小悠认识于：{}".format(started_text),
        "- 相识天数：{} 天".format(max(1, int(profile.get("relationship_days") or 1))),
        "",
        "## 用户自述",
        "",
        str(profile.get("about_me") or "").strip() or "未填写",
        "",
        "## 使用边界",
        "",
        "- 对话中自然使用这些资料，不要像表单一样逐项复述。",
        "- 用户在当前对话中给出的新信息优先于这份资料。",
        "- 不得把这里的任何事实归到其他用户身上。",
        "",
    ]
    content = "\n".join(lines)
    directory = os.path.dirname(path)
    fd, temporary = tempfile.mkstemp(prefix=".user-profile-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path


def load_profile_context(session_id, max_chars=5000):
    try:
        path = profile_document_path(session_id)
    except ValueError:
        return ""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            content = handle.read(max(0, int(max_chars)) + 1)
    except OSError:
        return ""
    return content[:max_chars].strip()
