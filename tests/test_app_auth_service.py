import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

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


def _environment(tmp_path):
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
        "XIAOYOU_APP_ACCOUNT_DB_PATH": str(tmp_path / "accounts.db"),
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


def test_owner_login_uses_canonical_data_scope(tmp_path):
    with patch.dict(os.environ, _environment(tmp_path), clear=True):
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


def test_test_login_is_blank_isolated_scope_per_login(tmp_path):
    with patch.dict(os.environ, _environment(tmp_path), clear=True):
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


def test_wrong_password_is_rejected(tmp_path):
    with patch.dict(os.environ, _environment(tmp_path), clear=True):
        auth = AppAuthService("yoyo")
        assert auth.login("yoyo", "wrong", "phone") is None
        assert auth.login("test", "wrong", "phone") is None


def _debug_email_environment(tmp_path):
    environment = _environment(tmp_path)
    environment["XIAOYOU_APP_EMAIL_DEBUG_CODE"] = "true"
    return environment


def test_email_otp_creates_stable_isolated_accounts(tmp_path):
    environment = _debug_email_environment(tmp_path)
    with patch.dict(os.environ, environment, clear=True):
        auth = AppAuthService("yoyo")
        first_request = auth.request_email_login("first@example.com")
        first = auth.verify_email_login(
            "first@example.com", first_request["debug_code"], "same-phone"
        )
        second_request = auth.request_email_login("second@example.com")
        second = auth.verify_email_login(
            "second@example.com", second_request["debug_code"], "same-phone"
        )
        first_context = auth.authenticate(first["access_token"])
        second_context = auth.authenticate(second["access_token"])

    assert first_context.session_id.startswith("app_user_")
    assert second_context.session_id.startswith("app_user_")
    assert first_context.session_id != second_context.session_id
    assert first_context.device_id != second_context.device_id
    assert first_context.user_id != second_context.user_id


def test_email_otp_reuses_existing_account_and_session(tmp_path):
    environment = _debug_email_environment(tmp_path)
    with patch.dict(os.environ, environment, clear=True):
        auth = AppAuthService("yoyo")
        first_request = auth.request_email_login("same@example.com")
        first = auth.verify_email_login(
            "same@example.com", first_request["debug_code"], "phone-a"
        )
        # Bypass resend cooldown in this deterministic unit test.
        with auth._connection() as db:
            db.execute(
                "UPDATE auth_challenges SET created_at=0 WHERE purpose='email_login' AND identifier=?",
                ("same@example.com",),
            )
            db.commit()
        second_request = auth.request_email_login("same@example.com")
        second = auth.verify_email_login(
            "same@example.com", second_request["debug_code"], "phone-b"
        )
        first_context = auth.authenticate(first["access_token"])
        second_context = auth.authenticate(second["access_token"])

    assert first_context.user_id == second_context.user_id
    assert first_context.session_id == second_context.session_id
    assert first_context.device_id != second_context.device_id
    assert first["account_id"] == "same@example.com"
    assert second["account_id"] == "same@example.com"


def test_email_otp_rejects_wrong_code(tmp_path):
    environment = _debug_email_environment(tmp_path)
    with patch.dict(os.environ, environment, clear=True):
        auth = AppAuthService("yoyo")
        auth.request_email_login("wrong@example.com")
        with pytest.raises(ValueError, match="invalid_or_expired_code"):
            auth.verify_email_login("wrong@example.com", "000000", "phone")


def test_email_otp_has_resend_cooldown(tmp_path):
    environment = _debug_email_environment(tmp_path)
    with patch.dict(os.environ, environment, clear=True):
        auth = AppAuthService("yoyo")
        auth.request_email_login("cooldown@example.com")
        with pytest.raises(ValueError, match="email_code_too_frequent"):
            auth.request_email_login("cooldown@example.com")


def test_public_config_only_exposes_email_login(tmp_path):
    environment = _debug_email_environment(tmp_path)
    with patch.dict(os.environ, environment, clear=True):
        config = AppAuthService("yoyo").public_config()

    assert config["email_login"] is True
    assert config["code_ttl"] == 600
    assert config["resend_interval"] == 60
    assert set(config) == {"email_login", "code_ttl", "resend_interval"}
