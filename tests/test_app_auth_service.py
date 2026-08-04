import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "plugins"
    / "xiaoyou_common"
    / "app_auth_service.py"
)
SPEC = importlib.util.spec_from_file_location(
    "app_auth_service_under_test",
    MODULE_PATH,
)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

AppAuthService = MODULE.AppAuthService
hash_password = MODULE.hash_password
verify_password = MODULE.verify_password


def _environment():
    return {
        "XIAOYOU_APP_AUTH_SECRET": "s" * 48,
        "XIAOYOU_APP_OWNER_USERNAME": "yoyo",
        "XIAOYOU_APP_OWNER_PASSWORD_HASH": hash_password(
            "owner-pass",
            salt=b"owner-auth-salt!!",
            iterations=10_000,
        ),
        "XIAOYOU_APP_TEST_USERNAME": "test",
        "XIAOYOU_APP_TEST_PASSWORD_HASH": hash_password(
            "test-pass",
            salt=b"test-auth-salt!!!",
            iterations=10_000,
        ),
    }


def test_password_hash_does_not_contain_plaintext():
    encoded = hash_password(
        "private-password",
        salt=b"deterministic-salt",
        iterations=10_000,
    )
    assert "private-password" not in encoded
    assert verify_password("private-password", encoded)
    assert not verify_password("wrong-password", encoded)


def test_owner_login_uses_canonical_data_scope():
    with patch.dict(os.environ, _environment(), clear=True):
        auth = AppAuthService("yoyo")
        result = auth.login("yoyo", "owner-pass", "owner-phone", remember=True)
        context = auth.authenticate(
            result["access_token"],
            requested_device_id="forged-device",
        )

    assert result["test_mode"] is False
    assert context.account_id == "yoyo"
    assert context.session_id == "yoyo"
    assert context.device_id == "owner-phone"
    assert context.test_mode is False


def test_test_login_is_blank_isolated_scope_per_login():
    with patch.dict(os.environ, _environment(), clear=True):
        auth = AppAuthService("yoyo")
        first = auth.login("test", "test-pass", "owner-phone", remember=True)
        second = auth.login("test", "test-pass", "owner-phone", remember=True)
        first_context = auth.authenticate(first["access_token"])
        second_context = auth.authenticate(second["access_token"])

    assert first["test_mode"] is True
    assert first_context.session_id.startswith("app_test_")
    assert first_context.device_id.startswith("test-")
    assert first_context.session_id != second_context.session_id
    assert first_context.session_id != "yoyo"
    assert first_context.device_id != "owner-phone"


def test_wrong_password_is_rejected():
    with patch.dict(os.environ, _environment(), clear=True):
        auth = AppAuthService("yoyo")
        assert auth.login("yoyo", "wrong", "phone") is None
        assert auth.login("test", "wrong", "phone") is None
