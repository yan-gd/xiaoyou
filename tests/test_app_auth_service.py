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
        "APPDATA_DIR": str(tmp_path / "data"),
        "XIAOYOU_APP_EMAIL_DEBUG_CODE": "true",
    }


def _register(auth, username, email, password="private-password", device="phone"):
    challenge = auth.request_registration(username, email, password)
    return auth.verify_registration(
        username,
        email,
        challenge["debug_code"],
        device,
        remember=True,
    )


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


def test_review_test_password_always_opens_ephemeral_blank_scope(tmp_path):
    with patch.dict(os.environ, _environment(tmp_path), clear=True):
        auth = AppAuthService("yoyo")
        result = auth.login("test", "@testxiaoyou", "review-phone", remember=True)
        context = auth.authenticate(result["access_token"])

    assert result["test_mode"] is True
    assert context.session_id.startswith("app_test_")
    assert context.session_id != "yoyo"


def test_wrong_password_is_rejected(tmp_path):
    with patch.dict(os.environ, _environment(tmp_path), clear=True):
        auth = AppAuthService("yoyo")
        assert auth.login("yoyo", "wrong", "phone") is None
        assert auth.login("test", "wrong", "phone") is None


def test_registration_creates_username_password_account(tmp_path):
    with patch.dict(os.environ, _environment(tmp_path), clear=True):
        auth = AppAuthService("yoyo")
        created = _register(
            auth,
            "alice_01",
            "alice@example.com",
            "alice-password",
            "same-phone",
        )
        login = auth.login("alice_01", "alice-password", "same-phone")
        context = auth.authenticate(login["access_token"])

    assert created["account_id"] == "alice_01"
    assert login["account_id"] == "alice_01"
    assert context.user_id.startswith("usr_")
    assert context.session_id.startswith("app_user_")
    assert context.session_id != "yoyo"
    assert context.device_id.startswith("usr-")


def test_email_code_login_uses_verified_bound_email(tmp_path):
    with patch.dict(os.environ, _environment(tmp_path), clear=True):
        auth = AppAuthService("yoyo")
        registered = _register(
            auth,
            "mail_login",
            "mail-login@example.com",
            "private-password",
            "first-phone",
        )
        challenge = auth.request_email_login("mail-login@example.com")
        login = auth.verify_email_login(
            "mail-login@example.com",
            challenge["debug_code"],
            "second-phone",
            remember=True,
        )
        registered_context = auth.authenticate(registered["access_token"])
        login_context = auth.authenticate(login["access_token"])

    assert login["account_id"] == "mail_login"
    assert login_context.user_id == registered_context.user_id
    assert login_context.session_id == registered_context.session_id
    assert login_context.device_id.startswith("usr-")


def test_unknown_email_login_request_does_not_reveal_account(tmp_path):
    with patch.dict(os.environ, _environment(tmp_path), clear=True):
        auth = AppAuthService("yoyo")
        result = auth.request_email_login("nobody@example.com")

    assert result == {"accepted": True, "expires_in": 600}


def test_registered_users_get_distinct_stable_memory_scopes(tmp_path):
    with patch.dict(os.environ, _environment(tmp_path), clear=True):
        auth = AppAuthService("yoyo")
        first = _register(auth, "first_user", "first@example.com")
        second = _register(auth, "second_user", "second@example.com")
        first_context = auth.authenticate(first["access_token"])
        second_context = auth.authenticate(second["access_token"])
        relogin = auth.login("first_user", "private-password", "new-phone")
        relogin_context = auth.authenticate(relogin["access_token"])

    assert first_context.user_id != second_context.user_id
    assert first_context.session_id != second_context.session_id
    assert first_context.session_id == relogin_context.session_id
    assert first_context.user_id == relogin_context.user_id


def test_registration_rejects_duplicate_username_and_email(tmp_path):
    with patch.dict(os.environ, _environment(tmp_path), clear=True):
        auth = AppAuthService("yoyo")
        _register(auth, "taken_user", "taken@example.com")
        with pytest.raises(ValueError, match="username_taken"):
            auth.request_registration(
                "taken_user", "other@example.com", "another-password"
            )
        with pytest.raises(ValueError, match="email_already_registered"):
            auth.request_registration(
                "other_user", "taken@example.com", "another-password"
            )


def test_registration_accepts_chinese_unique_username(tmp_path):
    with patch.dict(os.environ, _environment(tmp_path), clear=True):
        auth = AppAuthService("yoyo")
        created = _register(auth, "小林同学", "xiaolin@example.com")
        assert auth.login("小林同学", "private-password", "phone") is not None
        with pytest.raises(ValueError, match="username_taken"):
            auth.request_registration(
                "小林同学", "other@example.com", "another-password"
            )

    assert created["account_id"] == "小林同学"


def test_profile_uses_registration_time_and_writes_isolated_document(tmp_path):
    with patch.dict(os.environ, _environment(tmp_path), clear=True):
        auth = AppAuthService("yoyo")
        created = _register(auth, "profile_user", "profile@example.com")
        context = auth.authenticate(created["access_token"])
        initial = auth.get_profile(context)
        updated = auth.update_profile(
            context,
            "阿林",
            birthday="2000-02-03",
            about_me="喜欢摄影和夜跑。",
        )
        document = (
            tmp_path
            / "data"
            / "app_users"
            / context.user_id
            / "profile"
            / "user_profile.md"
        ).read_text(encoding="utf-8")

    assert initial["profile_completed"] is False
    assert initial["relationship_started_at"] > 0
    assert updated["profile_completed"] is True
    assert updated["display_name"] == "阿林"
    assert updated["birthday"] == "2000-02-03"
    assert "阿林" in document
    assert "喜欢摄影和夜跑" in document
    assert "相识天数" in document


def test_registration_rejects_wrong_code(tmp_path):
    with patch.dict(os.environ, _environment(tmp_path), clear=True):
        auth = AppAuthService("yoyo")
        auth.request_registration("wrong_code", "wrong@example.com", "valid-password")
        with pytest.raises(ValueError, match="invalid_or_expired_code"):
            auth.verify_registration(
                "wrong_code", "wrong@example.com", "000000", "phone"
            )


def test_registration_has_resend_cooldown(tmp_path):
    with patch.dict(os.environ, _environment(tmp_path), clear=True):
        auth = AppAuthService("yoyo")
        auth.request_registration("cooldown", "cooldown@example.com", "valid-password")
        with pytest.raises(ValueError, match="email_code_too_frequent"):
            auth.request_registration(
                "cooldown", "cooldown@example.com", "valid-password"
            )


def test_password_reset_uses_verified_email_but_login_stays_password_based(tmp_path):
    with patch.dict(os.environ, _environment(tmp_path), clear=True):
        auth = AppAuthService("yoyo")
        _register(auth, "reset_user", "reset@example.com", "before-password")
        reset = auth.request_password_reset("reset_user")
        auth.confirm_password_reset(
            "reset_user", reset["debug_code"], "after-password"
        )

        assert auth.login("reset_user", "before-password", "phone") is None
        assert auth.login("reset_user", "after-password", "phone") is not None


def test_password_reset_can_start_from_bound_email(tmp_path):
    with patch.dict(os.environ, _environment(tmp_path), clear=True):
        auth = AppAuthService("yoyo")
        _register(auth, "mail_reset", "mail-reset@example.com", "before-password")
        reset = auth.request_password_reset("mail-reset@example.com")
        auth.confirm_password_reset(
            "mail-reset@example.com", reset["debug_code"], "after-password"
        )

        assert auth.login("mail_reset", "after-password", "phone") is not None


def test_unknown_password_reset_does_not_reveal_account(tmp_path):
    with patch.dict(os.environ, _environment(tmp_path), clear=True):
        auth = AppAuthService("yoyo")
        result = auth.request_password_reset("nobody")

    assert result == {"accepted": True, "expires_in": 600}


def test_otp_only_legacy_email_account_can_attach_username_without_new_user(tmp_path):
    with patch.dict(os.environ, _environment(tmp_path), clear=True):
        auth = AppAuthService("yoyo")
        now = 1_700_000_000
        with auth._connection() as db:
            db.execute(
                "INSERT INTO users(id,session_id,nickname,avatar,vip_level,created_at,updated_at,last_login_at,status) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    "usr_legacy",
                    "app_user_legacy",
                    "legacy",
                    "",
                    0,
                    now,
                    now,
                    now,
                    "active",
                ),
            )
            db.execute(
                "INSERT INTO user_auth(id,user_id,auth_type,identifier,password_hash,verified,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    "auth_legacy_email",
                    "usr_legacy",
                    "email",
                    "legacy@example.com",
                    "",
                    1,
                    now,
                    now,
                ),
            )
            db.commit()

        result = _register(
            auth,
            "legacy_user",
            "legacy@example.com",
            "new-password",
        )
        context = auth.authenticate(result["access_token"])

    assert context.user_id == "usr_legacy"
    assert context.session_id == "app_user_legacy"
    assert auth.login("legacy_user", "new-password", "phone") is not None


def test_public_config_exposes_password_account_flow(tmp_path):
    with patch.dict(os.environ, _environment(tmp_path), clear=True):
        config = AppAuthService("yoyo").public_config()

    assert config == {
        "registration_enabled": True,
        "password_reset_enabled": True,
        "password_min_length": 8,
        "code_ttl": 600,
        "resend_interval": 60,
    }


def test_email_login_keeps_profile_incomplete_until_onboarding(tmp_path):
    with patch.dict(os.environ, _environment(tmp_path), clear=True):
        auth = AppAuthService("yoyo")
        registered = _register(
            auth,
            "mail_onboarding",
            "mail-onboarding@example.com",
            "private-password",
            "first-phone",
        )
        first_context = auth.authenticate(registered["access_token"])
        assert auth.get_profile(first_context)["profile_completed"] is False

        challenge = auth.request_email_login(
            "mail-onboarding@example.com",
            client_ip="203.0.113.10",
        )
        login = auth.verify_email_login(
            "mail-onboarding@example.com",
            challenge["debug_code"],
            "second-phone",
            remember=True,
        )
        login_context = auth.authenticate(login["access_token"])
        profile = auth.get_profile(login_context)

    assert profile["profile_completed"] is False
    assert login_context.user_id == first_context.user_id
    assert login_context.session_id == first_context.session_id


def test_email_request_rate_limit_by_identifier(tmp_path):
    with patch.dict(os.environ, _environment(tmp_path), clear=True):
        auth = AppAuthService("yoyo")
        for index in range(MODULE.EMAIL_REQUEST_EMAIL_HOUR_LIMIT):
            result = auth.request_email_login(
                "unknown-rate@example.com",
                client_ip="203.0.113.{}".format(index + 1),
            )
            assert result["accepted"] is True

        with pytest.raises(ValueError, match="email_request_rate_limited"):
            auth.request_email_login(
                "unknown-rate@example.com",
                client_ip="198.51.100.99",
            )


def test_email_request_rate_limit_by_ip(tmp_path):
    with patch.dict(os.environ, _environment(tmp_path), clear=True):
        auth = AppAuthService("yoyo")
        for index in range(MODULE.EMAIL_REQUEST_IP_HOUR_LIMIT):
            result = auth.request_email_login(
                "unknown-ip-{}@example.com".format(index),
                client_ip="203.0.113.77",
            )
            assert result["accepted"] is True

        with pytest.raises(ValueError, match="email_request_rate_limited"):
            auth.request_email_login(
                "unknown-ip-overflow@example.com",
                client_ip="203.0.113.77",
            )
