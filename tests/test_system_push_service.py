import hashlib
import importlib.util
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_module(monkeypatch):
    common = types.ModuleType("common")
    common_log = types.ModuleType("common.log")
    common_log.logger = types.SimpleNamespace(
        info=lambda *_args, **_kwargs: None,
        warning=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setitem(sys.modules, "common", common)
    monkeypatch.setitem(sys.modules, "common.log", common_log)
    spec = importlib.util.spec_from_file_location(
        "xiaoyou_system_push_test_module",
        ROOT / "plugins" / "xiaoyou_common" / "system_push_service.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_vivo_gateway_authenticates_and_sends_expected_notification(
    monkeypatch,
):
    module = _load_module(monkeypatch)
    calls = []

    def http_json(path, payload, headers):
        calls.append((path, payload, headers))
        if path == "/message/auth":
            return {"result": 0, "authToken": "auth-token"}
        return {"result": 0}

    gateway = module.VivoPushGateway(
        app_id="12345",
        app_key="app-key",
        app_secret="app-secret",
        http_json=http_json,
        clock=lambda: 1712345.678,
    )

    assert gateway.send(
        reg_id="12345678901234567890123",
        action_id="action-1",
        body="想你啦",
        sound=True,
        vibration=False,
        kind="text",
    )

    auth_path, auth, auth_headers = calls[0]
    assert auth_path == "/message/auth"
    assert auth_headers == {}
    assert auth["timestamp"] == 1712345678
    assert auth["sign"] == hashlib.md5(
        b"12345app-key1712345678app-secret"
    ).hexdigest()

    send_path, message, send_headers = calls[1]
    assert send_path == "/message/send"
    assert send_headers == {"authToken": "auth-token"}
    assert message["regId"] == "12345678901234567890123"
    assert message["notifyType"] == 2
    assert message["title"] == "小悠"
    assert message["content"] == "想你啦"
    assert message["skipType"] == 1
    assert message["clientCustomMap"]["action_id"] == "action-1"
    assert len(message["requestId"]) == 64


def test_vivo_gateway_refreshes_auth_once_after_send_rejection(monkeypatch):
    module = _load_module(monkeypatch)
    calls = []

    def http_json(path, payload, headers):
        calls.append((path, payload, headers))
        if path == "/message/auth":
            return {
                "result": 0,
                "authToken": "token-%d"
                % sum(item[0] == "/message/auth" for item in calls),
            }
        send_count = sum(item[0] == "/message/send" for item in calls)
        return {"result": 10301 if send_count == 1 else 0}

    gateway = module.VivoPushGateway(
        app_id="9",
        app_key="key",
        app_secret="secret",
        http_json=http_json,
        clock=lambda: 1000,
    )

    assert gateway.send(
        reg_id="reg-id",
        action_id="action-2",
        body="晚安",
    )
    assert [item[0] for item in calls] == [
        "/message/auth",
        "/message/send",
        "/message/auth",
        "/message/send",
    ]
