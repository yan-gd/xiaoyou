import pyotp

from app.config import Settings
from app.database import Database
from app.security import AuthenticationError, SecurityService


def make_services(tmp_path):
    settings = Settings(
        app_secret="b" * 64,
        database_path=tmp_path / "observatory.db",
        allowed_hosts="testserver,localhost",
        cookie_secure=False,
        mock_mode=True,
    )
    database = Database(settings.database_path)
    database.initialize()
    security = SecurityService(settings, database)
    return settings, database, security


def test_password_totp_session_and_csrf(tmp_path):
    _, database, security = make_services(tmp_path)
    secret, _ = security.generate_totp("yoyo")
    recovery = security.generate_recovery_codes(2)
    database.create_admin(
        "yoyo",
        security.hash_password("StrongPassword123"),
        security.encrypt_totp_secret(secret),
        [security.secret_hash(code) for code in recovery],
    )

    otp = pyotp.TOTP(secret).now()
    admin = security.authenticate("yoyo", "StrongPassword123", otp, "127.0.0.1")
    created = security.create_session(admin, "127.0.0.1", "pytest")
    resolved = security.resolve_session(created.token)
    assert resolved is not None
    assert resolved.csrf_token == created.csrf_token


def test_recovery_code_can_only_be_used_once(tmp_path):
    _, database, security = make_services(tmp_path)
    secret, _ = security.generate_totp("yoyo")
    recovery = security.generate_recovery_codes(1)[0]
    database.create_admin(
        "yoyo",
        security.hash_password("StrongPassword123"),
        security.encrypt_totp_secret(secret),
        [security.secret_hash(recovery)],
    )
    assert security.authenticate("yoyo", "StrongPassword123", recovery, "one")
    try:
        security.authenticate("yoyo", "StrongPassword123", recovery, "two")
    except AuthenticationError:
        pass
    else:
        raise AssertionError("used recovery code was accepted twice")


def test_guest_session_is_signed_and_read_only_role(tmp_path):
    _, _, security = make_services(tmp_path)
    created = security.create_guest_session("127.0.0.1")
    resolved = security.resolve_session(created.token)
    assert resolved is not None
    assert resolved.role == "guest"
    assert resolved.admin_id == 0
    assert resolved.csrf_token == created.csrf_token

    tampered = created.token[:-1] + ("A" if created.token[-1] != "A" else "B")
    assert security.resolve_session(tampered) is None
