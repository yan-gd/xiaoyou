# -*- coding: utf-8 -*-
"""Reliable system-push delivery for the Xiaoyou mobile app.

The durable App inbox remains the source of truth. This module only wakes the
phone for an already committed action, so a push outage can never lose a chat
message or block Xiaoyou's reply path.
"""

import hashlib
import json
import os
import queue
import threading
import time
import urllib.error
import urllib.request

from common.log import logger


def _truncate_display(value, maximum):
    result = []
    used = 0
    for char in str(value or "").strip():
        width = 1 if ord(char) < 128 else 2
        if used + width > maximum:
            break
        result.append(char)
        used += width
    return "".join(result)


class VivoPushGateway:
    """Minimal client for vivo Push's official server HTTP API."""

    def __init__(
        self,
        app_id=None,
        app_key=None,
        app_secret=None,
        api_base=None,
        timeout=6.0,
        http_json=None,
        clock=None,
    ):
        self.app_id = str(
            app_id
            if app_id is not None
            else os.getenv("XIAOYOU_VIVO_PUSH_APP_ID", "")
        ).strip()
        self.app_key = str(
            app_key
            if app_key is not None
            else os.getenv("XIAOYOU_VIVO_PUSH_APP_KEY", "")
        ).strip()
        self.app_secret = str(
            app_secret
            if app_secret is not None
            else os.getenv("XIAOYOU_VIVO_PUSH_APP_SECRET", "")
        ).strip()
        self.api_base = str(
            api_base
            if api_base is not None
            else os.getenv(
                "XIAOYOU_VIVO_PUSH_API_BASE",
                "https://api-push.vivo.com.cn",
            )
        ).strip().rstrip("/")
        self.timeout = max(1.0, float(timeout))
        self._http_json = http_json or self._request_json
        self._clock = clock or time.time
        self._lock = threading.Lock()
        self._auth_token = ""
        self._auth_expires_at = 0.0

    @property
    def enabled(self):
        return bool(
            self.app_id
            and self.app_key
            and self.app_secret
            and self.api_base
        )

    def _request_json(self, path, payload, headers=None):
        request = urllib.request.Request(
            self.api_base + path,
            data=json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8"),
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json; charset=utf-8",
                **(headers or {}),
            },
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.timeout,
            ) as response:
                body = response.read()
        except urllib.error.HTTPError as error:
            error.read()
            raise RuntimeError(
                "vivo_push_http_%s" % int(error.code)
            ) from error
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("vivo_push_invalid_response")
        return payload

    @staticmethod
    def _successful(payload):
        return payload.get("result") in (0, "0")

    def _authenticate(self, force=False):
        if not self.enabled:
            raise RuntimeError("vivo_push_not_configured")
        now = float(self._clock())
        with self._lock:
            if (
                not force
                and self._auth_token
                and now < self._auth_expires_at
            ):
                return self._auth_token
            timestamp = int(now * 1000)
            signature = hashlib.md5(
                (
                    self.app_id
                    + self.app_key
                    + str(timestamp)
                    + self.app_secret
                ).encode("utf-8")
            ).hexdigest()
            payload = self._http_json(
                "/message/auth",
                {
                    "appId": int(self.app_id),
                    "appKey": self.app_key,
                    "timestamp": timestamp,
                    "sign": signature,
                },
                {},
            )
            token = str(payload.get("authToken") or "").strip()
            if not self._successful(payload) or not token:
                raise RuntimeError(
                    "vivo_push_auth_failed:%s"
                    % str(payload.get("result", "unknown"))
                )
            self._auth_token = token
            # Official auth tokens are valid for one day. Refresh early.
            self._auth_expires_at = now + 23 * 60 * 60
            return token

    def send(
        self,
        *,
        reg_id,
        action_id,
        body,
        sound=True,
        vibration=True,
        kind="text",
    ):
        reg_id = str(reg_id or "").strip()
        if not reg_id:
            raise ValueError("missing_vivo_reg_id")
        notify_type = 1
        if sound and vibration:
            notify_type = 4
        elif vibration:
            notify_type = 3
        elif sound:
            notify_type = 2
        content = _truncate_display(body, 100)
        if not content:
            content = "小悠发来了一条新消息"
        request_id = hashlib.sha256(
            ("%s:%s" % (str(action_id or ""), reg_id)).encode("utf-8")
        ).hexdigest()
        payload = {
            "regId": reg_id,
            "notifyType": notify_type,
            "title": _truncate_display("小悠", 40),
            "content": content,
            "timeToLive": 24 * 60 * 60,
            "skipType": 1,
            "networkType": -1,
            "clientCustomMap": {
                "action_id": str(action_id or "")[:128],
                "kind": str(kind or "text")[:24],
            },
            "requestId": request_id,
        }
        for attempt in range(2):
            auth_token = self._authenticate(force=attempt > 0)
            response = self._http_json(
                "/message/send",
                payload,
                {"authToken": auth_token},
            )
            if self._successful(response):
                return True
            if attempt == 0:
                with self._lock:
                    self._auth_token = ""
                    self._auth_expires_at = 0.0
                continue
            raise RuntimeError(
                "vivo_push_send_failed:%s"
                % str(response.get("result", "unknown"))
            )
        return False


class SystemPushDispatcher:
    """Non-blocking, bounded push worker."""

    def __init__(self, gateway=None, queue_size=None):
        self.gateway = gateway or VivoPushGateway()
        self.queue = queue.Queue(
            maxsize=max(
                16,
                int(
                    queue_size
                    if queue_size is not None
                    else os.getenv("XIAOYOU_SYSTEM_PUSH_QUEUE_SIZE", "256")
                ),
            )
        )
        self._thread = None
        self._lock = threading.Lock()
        if self.gateway.enabled:
            self._ensure_worker()
            logger.info("[SystemPush] vivo gateway enabled")
        else:
            logger.info(
                "[SystemPush] vivo gateway not configured; "
                "background polling remains the fallback"
            )

    @property
    def enabled(self):
        return self.gateway.enabled

    def _ensure_worker(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._worker,
                daemon=True,
                name="XiaoyouSystemPush",
            )
            self._thread.start()

    def enqueue(
        self,
        *,
        action_id,
        device_id,
        reg_id,
        kind,
        text,
        preview=True,
        sound=True,
        vibration=True,
    ):
        if not self.enabled or not str(reg_id or "").strip():
            return False
        body = (
            str(text or "").strip()
            if preview
            else "小悠发来了一条新消息"
        )
        if not body:
            body = {
                "image": "小悠发来了一张图片",
                "sticker": "小悠发来了一个表情包",
                "voice": "小悠发来了一条语音",
            }.get(str(kind or ""), "小悠发来了一条新消息")
        item = {
            "action_id": str(action_id or ""),
            "device_id_hash": hashlib.sha256(
                str(device_id or "").encode("utf-8")
            ).hexdigest()[:12],
            "reg_id": str(reg_id or ""),
            "kind": str(kind or "text"),
            "body": body,
            "sound": bool(sound),
            "vibration": bool(vibration),
        }
        try:
            self.queue.put_nowait(item)
        except queue.Full:
            logger.warning(
                "[SystemPush] queue full action=%s device=%s",
                item["action_id"],
                item["device_id_hash"],
            )
            return False
        self._ensure_worker()
        return True

    def _worker(self):
        while True:
            item = self.queue.get()
            try:
                self.gateway.send(
                    reg_id=item["reg_id"],
                    action_id=item["action_id"],
                    body=item["body"],
                    sound=item["sound"],
                    vibration=item["vibration"],
                    kind=item["kind"],
                )
                logger.info(
                    "[SystemPush] delivered action=%s device=%s",
                    item["action_id"],
                    item["device_id_hash"],
                )
            except Exception as error:
                logger.warning(
                    "[SystemPush] failed action=%s device=%s error=%s",
                    item["action_id"],
                    item["device_id_hash"],
                    type(error).__name__,
                )
            finally:
                self.queue.task_done()
