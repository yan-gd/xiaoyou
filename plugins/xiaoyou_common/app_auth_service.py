# -*- coding: utf-8 -*-
"""Persistent account/password authentication for the Xiaoyou mobile App.

Registered users sign in with a stable username and password. Email is only a
verified recovery channel used during registration and password reset.
"""

import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import smtplib
import sqlite3
import ssl
import time
import unicodedata
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime
from email.message import EmailMessage
from email.utils import formataddr
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


PASSWORD_ITERATIONS = 310_000
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_BYTES = 72
OWNER_TOKEN_TTL = 30 * 24 * 60 * 60
SESSION_TOKEN_TTL = 12 * 60 * 60
CHALLENGE_TTL = 10 * 60
EMAIL_RESEND_COOLDOWN = 60
EMAIL_REQUEST_EMAIL_HOUR_LIMIT = 5
EMAIL_REQUEST_EMAIL_DAY_LIMIT = 20
EMAIL_REQUEST_IP_HOUR_LIMIT = 20
EMAIL_REQUEST_IP_DAY_LIMIT = 60
EMAIL_REQUEST_RETENTION = 24 * 60 * 60
OAUTH_TRANSACTION_TTL = 10 * 60
OAUTH_HTTP_TIMEOUT = 15
REVIEW_TEST_PASSWORD_HASH = (
    "pbkdf2_sha256$310000$eGlhb3lvdS10ZXN0LXJldmlldw$"
    "55dqot5RoAR5aSfNo_aSwI0fRY7Ha0BejzNoF9dxIMI"
)


def _b64encode(value):
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value):
    value = str(value or "")
    return base64.urlsafe_b64decode(value + ("=" * (-len(value) % 4)))


def hash_password(password, *, salt=None, iterations=PASSWORD_ITERATIONS):
    """Hash a password with PBKDF2-HMAC-SHA256 and a per-password salt."""
    password = str(password or "")
    if not password:
        raise ValueError("empty_password")
    salt = salt or secrets.token_bytes(18)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, int(iterations)
    )
    return "pbkdf2_sha256${}${}${}".format(
        int(iterations), _b64encode(salt), _b64encode(digest)
    )


def verify_password(password, encoded):
    encoded = str(encoded or "")
    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            str(password or "").encode("utf-8"),
            _b64decode(salt),
            int(iterations),
        )
        return hmac.compare_digest(digest, _b64decode(expected))
    except (TypeError, ValueError):
        return False


@dataclass(frozen=True)
class AppAuthContext:
    account_id: str
    user_id: str
    session_id: str
    device_id: str
    test_mode: bool
    expires_at: int
    legacy: bool = False


class AppAuthService:
    """Username/password accounts with verified email recovery."""

    def __init__(self, canonical_session_id):
        self.canonical_session_id = str(canonical_session_id or "yoyo")
        self.owner_username = _normalize_username(
            os.getenv("XIAOYOU_APP_OWNER_USERNAME", "yoyo")
        ) or "yoyo"
        self.owner_password_hash = os.getenv(
            "XIAOYOU_APP_OWNER_PASSWORD_HASH", ""
        ).strip()
        self.test_username = _normalize_username(
            os.getenv("XIAOYOU_APP_TEST_USERNAME", "test")
        ) or "test"
        self.test_password_hash = os.getenv(
            "XIAOYOU_APP_TEST_PASSWORD_HASH", ""
        ).strip()
        self.secret = os.getenv("XIAOYOU_APP_AUTH_SECRET", "").strip()
        self.legacy_token = os.getenv("XIAOYOU_APP_TOKEN", "").strip()
        self.database_path = os.getenv(
            "XIAOYOU_APP_ACCOUNT_DB_PATH", "/app/data/app_channel/accounts.db"
        ).strip()
        self.smtp_host = os.getenv("XIAOYOU_APP_SMTP_HOST", "").strip()
        self.smtp_port = _integer(os.getenv("XIAOYOU_APP_SMTP_PORT"), 465)
        self.smtp_username = os.getenv("XIAOYOU_APP_SMTP_USERNAME", "").strip()
        self.smtp_password = os.getenv("XIAOYOU_APP_SMTP_PASSWORD", "").strip()
        self.smtp_from = os.getenv(
            "XIAOYOU_APP_SMTP_FROM", self.smtp_username
        ).strip()
        self.smtp_ssl = _truthy(os.getenv("XIAOYOU_APP_SMTP_SSL", "true"))
        self.debug_email_code = _truthy(
            os.getenv("XIAOYOU_APP_EMAIL_DEBUG_CODE", "false")
        )
        self.oauth_public_base_url = os.getenv(
            "XIAOYOU_APP_OAUTH_PUBLIC_BASE_URL", ""
        ).strip().rstrip("/")
        self.github_client_id = os.getenv(
            "XIAOYOU_APP_GITHUB_CLIENT_ID", ""
        ).strip()
        self.github_client_secret = os.getenv(
            "XIAOYOU_APP_GITHUB_CLIENT_SECRET", ""
        ).strip()
        self._initialize_database()

    @property
    def enabled(self):
        return bool(
            len(self.secret) >= 32
            and (self.database_path or self.owner_password_hash)
        )

    @property
    def email_enabled(self):
        return bool(
            len(self.secret) >= 32
            and self.smtp_host
            and self.smtp_from
        )

    def public_config(self):
        email_verification = self.email_enabled or self.debug_email_code
        return {
            "registration_enabled": email_verification,
            "password_reset_enabled": email_verification,
            "password_min_length": PASSWORD_MIN_LENGTH,
            "code_ttl": CHALLENGE_TTL,
            "resend_interval": EMAIL_RESEND_COOLDOWN,
            "github_login_enabled": self.oauth_provider_enabled("github"),
        }

    def login(self, username, password, device_id, remember=True):
        username = _normalize_username(username)
        device_id = _safe_identifier(device_id, fallback="xiaoyou-phone")
        if not username:
            return None

        if username == self.owner_username and self.owner_password_hash:
            if not verify_password(password, self.owner_password_hash):
                return None
            return self._issue(
                account_id=self.owner_username,
                user_id="owner:" + self.owner_username,
                role="owner",
                session_id=self.canonical_session_id,
                device_id=device_id,
                remember=remember,
            )

        if username == self.test_username and self.test_password_hash:
            if not (
                verify_password(password, self.test_password_hash)
                or verify_password(password, REVIEW_TEST_PASSWORD_HASH)
            ):
                return None
            nonce = secrets.token_hex(12)
            return self._issue(
                account_id=self.test_username,
                user_id="test:" + nonce,
                role="test",
                session_id="app_test_" + nonce,
                device_id="test-" + nonce,
                remember=remember,
            )

        if username == self.test_username and verify_password(
            password, REVIEW_TEST_PASSWORD_HASH
        ):
            nonce = secrets.token_hex(12)
            return self._issue(
                account_id=self.test_username,
                user_id="test:" + nonce,
                role="test",
                session_id="app_test_" + nonce,
                device_id="test-" + nonce,
                remember=remember,
            )

        auth = self._auth_record("username", username)
        if (
            not auth
            or not int(auth["verified"] or 0)
            or auth["status"] != "active"
            or not verify_password(password, auth["password_hash"])
        ):
            return None
        self._touch_user(auth["user_id"])
        return self._issue_user(auth, device_id=device_id, remember=remember)

    def oauth_provider_enabled(self, provider):
        provider = str(provider or "").strip().lower()
        return bool(
            provider == "github"
            and len(self.secret) >= 32
            and self.oauth_public_base_url
            and self.github_client_id
            and self.github_client_secret
        )

    def start_oauth(self, provider, device_id, remember=True):
        provider = self._require_oauth_provider(provider)
        device_id = _safe_identifier(device_id, fallback="xiaoyou-phone")
        if not device_id:
            raise ValueError("invalid_device_id")
        state = secrets.token_urlsafe(32)
        poll_token = secrets.token_urlsafe(32)
        now = int(time.time())
        transaction_id = "oauth_" + secrets.token_hex(12)
        with self._connection() as db:
            db.execute("DELETE FROM oauth_transactions WHERE expires_at<?", (now,))
            db.execute(
                "INSERT INTO oauth_transactions(id,provider,state_digest,poll_digest,device_id,remember,status,result_json,error,created_at,expires_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    transaction_id,
                    provider,
                    self._oauth_digest("state", state),
                    self._oauth_digest("poll", poll_token),
                    device_id,
                    1 if bool(remember) else 0,
                    "pending",
                    "{}",
                    "",
                    now,
                    now + OAUTH_TRANSACTION_TTL,
                ),
            )
            db.commit()
        redirect_uri = self._oauth_redirect_uri(provider)
        query = {
            "client_id": self.github_client_id,
            "redirect_uri": redirect_uri,
            "scope": "read:user user:email",
            "state": state,
            "allow_signup": "true",
        }
        return {
            "provider": provider,
            "authorization_url": "https://github.com/login/oauth/authorize?" + urlencode(query),
            "poll_token": poll_token,
            "expires_in": OAUTH_TRANSACTION_TTL,
        }

    def complete_oauth(self, provider, state, code="", error=""):
        provider = self._require_oauth_provider(provider)
        state = str(state or "").strip()
        if not state:
            raise ValueError("invalid_oauth_state")
        now = int(time.time())
        with self._connection() as db:
            row = db.execute(
                "SELECT * FROM oauth_transactions WHERE provider=? AND state_digest=?",
                (provider, self._oauth_digest("state", state)),
            ).fetchone()
        if not row or int(row["expires_at"] or 0) <= now:
            raise ValueError("invalid_oauth_state")
        status = str(row["status"] or "pending")
        if status == "completed":
            return {"ok": True, "already_completed": True}
        if status == "failed":
            return {"ok": False, "error": str(row["error"] or "oauth_provider_failed")}
        if str(error or "").strip() or not str(code or "").strip():
            self._finish_oauth_transaction(row["id"], status="failed", error="oauth_cancelled")
            return {"ok": False, "error": "oauth_cancelled"}
        try:
            identity = self._oauth_exchange_identity(provider, str(code).strip())
            result = self._oauth_login_or_create(
                provider,
                identity,
                device_id=str(row["device_id"]),
                remember=bool(row["remember"]),
            )
        except ValueError as exc:
            error_code = str(exc or "oauth_provider_failed")[:80]
            if not error_code.startswith("oauth_"):
                error_code = "oauth_provider_failed"
            self._finish_oauth_transaction(row["id"], status="failed", error=error_code)
            return {"ok": False, "error": error_code}
        except RuntimeError:
            self._finish_oauth_transaction(row["id"], status="failed", error="oauth_provider_failed")
            return {"ok": False, "error": "oauth_provider_failed"}
        self._finish_oauth_transaction(row["id"], status="completed", result=result)
        return {"ok": True, "account_id": result.get("account_id", "")}

    def poll_oauth(self, provider, poll_token):
        provider = self._require_oauth_provider(provider)
        poll_token = str(poll_token or "").strip()
        if not poll_token:
            raise ValueError("invalid_oauth_poll")
        now = int(time.time())
        with self._connection() as db:
            db.execute("DELETE FROM oauth_transactions WHERE expires_at<?", (now,))
            row = db.execute(
                "SELECT * FROM oauth_transactions WHERE provider=? AND poll_digest=?",
                (provider, self._oauth_digest("poll", poll_token)),
            ).fetchone()
            db.commit()
        if not row:
            raise ValueError("oauth_expired")
        status = str(row["status"] or "pending")
        if status == "pending":
            return {"status": "pending"}
        if status == "failed":
            return {"status": "failed", "error": str(row["error"] or "oauth_provider_failed")}
        if status != "completed":
            raise ValueError("oauth_provider_failed")
        try:
            result = json.loads(row["result_json"] or "{}")
        except json.JSONDecodeError:
            raise ValueError("oauth_provider_failed")
        if not isinstance(result, dict) or not result.get("access_token"):
            raise ValueError("oauth_provider_failed")
        return {"status": "completed", **result}

    def _require_oauth_provider(self, provider):
        provider = str(provider or "").strip().lower()
        if provider != "github":
            raise ValueError("invalid_oauth_provider")
        if not self.oauth_provider_enabled(provider):
            raise ValueError("oauth_provider_unavailable")
        return provider

    def _oauth_redirect_uri(self, provider):
        return "{}/v1/auth/oauth/{}/callback".format(self.oauth_public_base_url, provider)

    def _oauth_digest(self, purpose, value):
        return hmac.new(
            self.secret.encode("utf-8"),
            "oauth:{}:{}".format(purpose, str(value or "")).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _finish_oauth_transaction(self, transaction_id, *, status, result=None, error=""):
        with self._connection() as db:
            db.execute(
                "UPDATE oauth_transactions SET status=?,result_json=?,error=? WHERE id=?",
                (
                    str(status),
                    json.dumps(result or {}, ensure_ascii=False),
                    str(error or "")[:80],
                    str(transaction_id),
                ),
            )
            db.commit()

    def _oauth_exchange_identity(self, provider, code):
        self._require_oauth_provider(provider)
        redirect_uri = self._oauth_redirect_uri(provider)
        token = self._oauth_http_json(
            "https://github.com/login/oauth/access_token",
            method="POST",
            form={
                "client_id": self.github_client_id,
                "client_secret": self.github_client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
        access_token = str(token.get("access_token") or "").strip()
        if not access_token:
            raise RuntimeError("oauth_provider_failed")
        headers = {
            "Authorization": "Bearer " + access_token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        profile = self._oauth_http_json("https://api.github.com/user", headers=headers)
        subject = str(profile.get("id") or "").strip()
        if not subject:
            raise RuntimeError("oauth_provider_failed")
        emails = self._oauth_http_json("https://api.github.com/user/emails", headers=headers)
        email = ""
        verified = False
        if isinstance(emails, list):
            preferred = None
            for item in emails:
                if not isinstance(item, dict) or item.get("verified") is not True:
                    continue
                if item.get("primary") is True:
                    preferred = item
                    break
                if preferred is None:
                    preferred = item
            if preferred:
                email = _normalize_email(preferred.get("email"))
                verified = bool(email)
        return {
            "subject": subject,
            "email": email,
            "email_verified": verified,
            "display_name": str(profile.get("name") or profile.get("login") or "").strip(),
            "username": str(profile.get("login") or "").strip(),
            "avatar": str(profile.get("avatar_url") or "").strip(),
        }

    @staticmethod
    def _oauth_http_json(url, *, method="GET", form=None, headers=None):
        request_headers = {
            "Accept": "application/json",
            "User-Agent": "Xiaoyou-App-OAuth/1.0",
        }
        request_headers.update(headers or {})
        data = None
        if form is not None:
            data = urlencode(form).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        request = Request(str(url), data=data, headers=request_headers, method=str(method or "GET").upper())
        try:
            with urlopen(request, timeout=OAUTH_HTTP_TIMEOUT) as response:
                raw = response.read(1024 * 1024)
        except (HTTPError, URLError, TimeoutError, OSError):
            raise RuntimeError("oauth_provider_failed")
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise RuntimeError("oauth_provider_failed")

    def _oauth_login_or_create(self, provider, identity, *, device_id, remember=True):
        provider = self._require_oauth_provider(provider)
        subject = str(identity.get("subject") or "").strip()
        if not subject:
            raise ValueError("oauth_invalid_identity")
        auth_type = "oauth_github"
        email = _normalize_email(identity.get("email"))
        email_verified = bool(identity.get("email_verified") and email)
        display_name = _clean_display_name(identity.get("display_name"))
        avatar = str(identity.get("avatar") or "").strip()[:1000]
        now = int(time.time())
        with self._connection() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                oauth_row = self._auth_record_with_connection(db, auth_type, subject)
                if oauth_row:
                    if str(oauth_row["status"] or "") != "active":
                        raise ValueError("account_disabled")
                    user_id = str(oauth_row["user_id"])
                else:
                    user_id = ""
                    if email_verified:
                        email_row = self._auth_record_with_connection(db, "email", email)
                        if email_row and str(email_row["status"] or "") == "active":
                            user_id = str(email_row["user_id"])
                    if not user_id:
                        user_id = "usr_" + secrets.token_hex(16)
                        session_id = "app_user_" + user_id[4:]
                        username = self._oauth_unique_username(db, provider, identity)
                        safe_display = display_name or username
                        db.execute(
                            "INSERT INTO users(id,session_id,nickname,avatar,vip_level,created_at,updated_at,last_login_at,status,display_name,relationship_started_at) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            (user_id, session_id, safe_display, avatar, 0, now, now, now, "active", safe_display, now),
                        )
                        db.execute(
                            "INSERT INTO user_auth(id,user_id,auth_type,identifier,password_hash,verified,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                            ("auth_" + secrets.token_hex(12), user_id, "username", username, "", 1, now, now),
                        )
                    else:
                        user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
                        if not user or str(user["status"] or "") != "active":
                            raise ValueError("account_disabled")
                    db.execute(
                        "INSERT INTO user_auth(id,user_id,auth_type,identifier,password_hash,verified,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                        ("auth_" + secrets.token_hex(12), user_id, auth_type, subject, "", 1, now, now),
                    )
                username_auth = self._user_auth_record_with_connection(db, user_id, "username")
                if not username_auth:
                    username = self._oauth_unique_username(db, provider, identity)
                    db.execute(
                        "INSERT INTO user_auth(id,user_id,auth_type,identifier,password_hash,verified,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                        ("auth_" + secrets.token_hex(12), user_id, "username", username, "", 1, now, now),
                    )
                if email_verified:
                    email_row = self._auth_record_with_connection(db, "email", email)
                    if email_row is None:
                        db.execute(
                            "INSERT INTO user_auth(id,user_id,auth_type,identifier,password_hash,verified,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                            ("auth_" + secrets.token_hex(12), user_id, "email", email, "", 1, now, now),
                        )
                if display_name or avatar:
                    db.execute(
                        "UPDATE users SET "
                        "nickname=CASE WHEN nickname='' THEN ? ELSE nickname END,"
                        "display_name=CASE WHEN display_name='' THEN ? ELSE display_name END,"
                        "avatar=CASE WHEN avatar='' THEN ? ELSE avatar END,"
                        "last_login_at=?,updated_at=? WHERE id=?",
                        (display_name, display_name, avatar, now, now, user_id),
                    )
                else:
                    db.execute(
                        "UPDATE users SET last_login_at=?,updated_at=? WHERE id=?",
                        (now, now, user_id),
                    )
                db.commit()
            except Exception:
                db.rollback()
                raise
        username_auth = self._user_auth_record(user_id, "username")
        result = self._issue_user(
            username_auth,
            device_id=_safe_identifier(device_id, fallback="xiaoyou-phone"),
            remember=remember,
        )
        self._sync_profile_document(user_id)
        return result

    def _oauth_unique_username(self, db, provider, identity):
        self._require_oauth_provider(provider)
        subject = str(identity.get("subject") or "").strip()
        hint = str(identity.get("username") or "").strip()
        if not hint:
            email = _normalize_email(identity.get("email"))
            hint = email.split("@", 1)[0] if email else ""
        hint = unicodedata.normalize("NFKC", hint)
        hint = re.sub(r"[^A-Za-z0-9_.-]+", "-", hint).strip("._-")
        seed = hashlib.sha256("github:{}".format(subject).encode("utf-8")).hexdigest()[:6]
        core = hint[:20] or seed
        base = _normalize_username("gh_{}".format(core))
        if not _valid_username(base):
            base = "gh_{}".format(seed)
        candidate = base[:32]
        for index in range(100):
            suffix = "" if index == 0 else "_{}".format(index)
            value = candidate[: 32 - len(suffix)] + suffix
            if value in {self.owner_username, self.test_username}:
                continue
            if self._auth_record_with_connection(db, "username", value) is None:
                return value
        raise ValueError("username_taken")

    def request_email_login(self, email, client_ip=""):
        self._require_email_service()
        email = _normalize_email(email)
        if not email:
            raise ValueError("invalid_email")
        self._enforce_email_request_limits(
            email,
            client_ip=client_ip,
            purpose="email_login",
        )

        email_auth = self._auth_record("email", email)
        username_auth = None
        if (
            email_auth
            and int(email_auth["verified"] or 0)
            and email_auth["status"] == "active"
        ):
            username_auth = self._user_auth_record(
                email_auth["user_id"], "username"
            )

        # Keep the request response non-enumerating: unknown/unusable emails
        # receive the same public shape, but no message is sent.
        if (
            not username_auth
            or not int(username_auth["verified"] or 0)
            or username_auth["status"] != "active"
        ):
            return {"accepted": True, "expires_in": CHALLENGE_TTL}

        if self._challenge_is_fresh(
            "email_login", email, EMAIL_RESEND_COOLDOWN
        ):
            raise ValueError("email_code_too_frequent")

        code = self._create_challenge(
            "email_login",
            email,
            {
                "user_id": str(username_auth["user_id"]),
                "username": str(username_auth["identifier"]),
            },
        )
        self._send_code(
            email,
            code,
            "小悠登录验证码",
            action="登录小悠",
        )
        return self._challenge_response(code)

    def verify_email_login(
        self, email, code, device_id, remember=True
    ):
        email = _normalize_email(email)
        if not email:
            raise ValueError("invalid_email")
        payload = self._consume_challenge("email_login", email, code)
        if payload is None:
            raise ValueError("invalid_or_expired_code")

        email_auth = self._auth_record("email", email)
        if (
            not email_auth
            or not int(email_auth["verified"] or 0)
            or email_auth["status"] != "active"
            or str(payload.get("user_id") or "")
            != str(email_auth["user_id"])
        ):
            raise ValueError("invalid_or_expired_code")

        username_auth = self._user_auth_record(
            email_auth["user_id"], "username"
        )
        if (
            not username_auth
            or not int(username_auth["verified"] or 0)
            or username_auth["status"] != "active"
        ):
            raise ValueError("invalid_or_expired_code")

        self._touch_user(username_auth["user_id"])
        return self._issue_user(
            username_auth,
            device_id=_safe_identifier(
                device_id, fallback="xiaoyou-phone"
            ),
            remember=remember,
        )

    def request_registration(self, username, email, password, client_ip=""):
        self._require_email_service()
        username = self._require_username(username)
        email = _normalize_email(email)
        if not email:
            raise ValueError("invalid_email")
        self._validate_password(password)
        self._enforce_email_request_limits(
            email,
            client_ip=client_ip,
            purpose="register",
        )
        self._ensure_username_available(username)

        email_auth = self._auth_record("email", email)
        if email_auth and self._user_auth_record(email_auth["user_id"], "username"):
            raise ValueError("email_already_registered")

        if self._challenge_is_fresh("register", email, EMAIL_RESEND_COOLDOWN):
            raise ValueError("email_code_too_frequent")

        payload = {
            "username": username,
            "password_hash": hash_password(password),
        }
        code = self._create_challenge("register", email, payload)
        self._send_code(email, code, "小悠注册验证码", action="完成账号注册")
        return self._challenge_response(code)

    def verify_registration(self, username, email, code, device_id, remember=True):
        username = self._require_username(username)
        email = _normalize_email(email)
        if not email:
            raise ValueError("invalid_email")
        payload = self._consume_challenge("register", email, code)
        if payload is None:
            raise ValueError("invalid_or_expired_code")
        if _normalize_username(payload.get("username")) != username:
            raise ValueError("registration_mismatch")
        password_hash = str(payload.get("password_hash") or "")
        if not password_hash:
            raise ValueError("registration_mismatch")

        now = int(time.time())
        with self._connection() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                username_row = self._auth_record_with_connection(
                    db, "username", username
                )
                if username_row:
                    raise ValueError("username_taken")

                email_row = self._auth_record_with_connection(db, "email", email)
                if email_row:
                    existing_username = self._user_auth_record_with_connection(
                        db, email_row["user_id"], "username"
                    )
                    if existing_username:
                        raise ValueError("email_already_registered")
                    if email_row["status"] != "active":
                        raise ValueError("account_disabled")
                    user_id = str(email_row["user_id"])
                    db.execute(
                        "INSERT INTO user_auth(id,user_id,auth_type,identifier,password_hash,verified,created_at,updated_at) "
                        "VALUES(?,?,?,?,?,?,?,?)",
                        (
                            "auth_" + secrets.token_hex(12),
                            user_id,
                            "username",
                            username,
                            password_hash,
                            1,
                            now,
                            now,
                        ),
                    )
                    db.execute(
                        "UPDATE users SET nickname=?,updated_at=?,last_login_at=? WHERE id=?",
                        (username, now, now, user_id),
                    )
                else:
                    user_id = "usr_" + secrets.token_hex(16)
                    session_id = "app_user_" + user_id[4:]
                    db.execute(
                        "INSERT INTO users(id,session_id,nickname,avatar,vip_level,created_at,updated_at,last_login_at,status,display_name,relationship_started_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            user_id,
                            session_id,
                            username,
                            "",
                            0,
                            now,
                            now,
                            now,
                            "active",
                            username,
                            now,
                        ),
                    )
                    db.execute(
                        "INSERT INTO user_auth(id,user_id,auth_type,identifier,password_hash,verified,created_at,updated_at) "
                        "VALUES(?,?,?,?,?,?,?,?)",
                        (
                            "auth_" + secrets.token_hex(12),
                            user_id,
                            "username",
                            username,
                            password_hash,
                            1,
                            now,
                            now,
                        ),
                    )
                    db.execute(
                        "INSERT INTO user_auth(id,user_id,auth_type,identifier,password_hash,verified,created_at,updated_at) "
                        "VALUES(?,?,?,?,?,?,?,?)",
                        (
                            "auth_" + secrets.token_hex(12),
                            user_id,
                            "email",
                            email,
                            "",
                            1,
                            now,
                            now,
                        ),
                    )
                db.commit()
            except Exception:
                db.rollback()
                raise

        auth = self._auth_record("username", username)
        result = self._issue_user(
            auth,
            device_id=_safe_identifier(device_id, "xiaoyou-phone"),
            remember=remember,
        )
        self._sync_profile_document(auth["user_id"])
        return result

    def get_profile(self, auth_context):
        if auth_context.test_mode:
            now = int(time.time())
            return {
                "account_id": auth_context.account_id,
                "display_name": "测试用户",
                "birthday": "",
                "about_me": "",
                "relationship_started_at": now,
                "relationship_days": 1,
                "profile_completed": True,
                "test_mode": True,
            }
        if auth_context.session_id == self.canonical_session_id:
            return {
                "account_id": auth_context.account_id,
                "display_name": "YoYo",
                "birthday": "",
                "about_me": "",
                "relationship_started_at": 0,
                "relationship_days": 1,
                "profile_completed": True,
                "test_mode": False,
            }
        user = self._user(auth_context.user_id)
        if not user:
            raise ValueError("account_not_found")
        return self._profile_payload(user, auth_context.account_id)

    def update_profile(self, auth_context, display_name, birthday="", about_me=""):
        if auth_context.test_mode or auth_context.session_id == self.canonical_session_id:
            return self.get_profile(auth_context)
        display_name = _clean_display_name(display_name)
        birthday = _clean_birthday(birthday)
        about_me = _clean_about(about_me)
        if not display_name:
            raise ValueError("invalid_display_name")
        now = int(time.time())
        with self._connection() as db:
            changed = db.execute(
                "UPDATE users SET display_name=?,nickname=?,birthday=?,about_me=?,"
                "profile_completed_at=?,updated_at=? WHERE id=? AND status='active'",
                (display_name, display_name, birthday, about_me, now, now, auth_context.user_id),
            ).rowcount
            db.commit()
        if not changed:
            raise ValueError("account_not_found")
        profile = self.get_profile(auth_context)
        self._write_profile_document(auth_context.session_id, profile)
        return profile

    def request_password_reset(self, identifier, client_ip=""):
        self._require_email_service()
        auth, email = self._resolve_account_and_email(identifier)
        self._enforce_email_request_limits(
            email or identifier,
            client_ip=client_ip,
            purpose="password_reset",
        )
        # Do not reveal account existence. Unknown accounts get the same public response.
        if not auth or not email:
            return {"accepted": True, "expires_in": CHALLENGE_TTL}
        if self._challenge_is_fresh("reset", email, EMAIL_RESEND_COOLDOWN):
            raise ValueError("email_code_too_frequent")
        code = self._create_challenge(
            "reset",
            email,
            {"user_id": str(auth["user_id"]), "username": str(auth["identifier"])},
        )
        self._send_code(email, code, "小悠密码重置验证码", action="重置账号密码")
        return self._challenge_response(code)

    def confirm_password_reset(self, identifier, code, password):
        self._validate_password(password)
        auth, email = self._resolve_account_and_email(identifier)
        if not auth or not email:
            raise ValueError("invalid_or_expired_code")
        payload = self._consume_challenge("reset", email, code)
        if payload is None or str(payload.get("user_id") or "") != str(auth["user_id"]):
            raise ValueError("invalid_or_expired_code")
        encoded = hash_password(password)
        with self._connection() as db:
            changed = db.execute(
                "UPDATE user_auth SET password_hash=?,updated_at=? "
                "WHERE user_id=? AND auth_type='username'",
                (encoded, int(time.time()), auth["user_id"]),
            ).rowcount
            db.commit()
        if not changed:
            raise ValueError("account_not_found")
        return {"ok": True, "username": str(auth["identifier"])}

    def authenticate(self, token, requested_device_id=""):
        token = str(token or "").strip()
        if self.legacy_token and hmac.compare_digest(token, self.legacy_token):
            return AppAuthContext(
                account_id=self.owner_username,
                user_id="owner:" + self.owner_username,
                session_id=self.canonical_session_id,
                device_id=_safe_identifier(requested_device_id, "yoyo-phone"),
                test_mode=False,
                expires_at=0,
                legacy=True,
            )
        payload = self._decode(token) if self.enabled else None
        if not payload:
            return None
        try:
            expires_at = int(payload["exp"])
        except (KeyError, TypeError, ValueError):
            return None
        if expires_at <= int(time.time()):
            return None
        role = str(payload.get("role") or "")
        account_id = str(payload.get("sub") or "")
        user_id = str(payload.get("uid") or "")
        if role == "owner":
            if account_id != self.owner_username:
                return None
        elif role == "test":
            if account_id != self.test_username:
                return None
        elif role == "user":
            user = self._user(user_id)
            username_auth = self._user_auth_record(user_id, "username")
            if (
                not user
                or user["status"] != "active"
                or user["session_id"] != payload.get("sid")
                or not username_auth
                or username_auth["identifier"] != account_id
            ):
                return None
        else:
            return None
        session_id = _safe_identifier(payload.get("sid"))
        device_id = _safe_identifier(payload.get("did"))
        if not session_id or not device_id:
            return None
        return AppAuthContext(
            account_id=account_id,
            user_id=user_id,
            session_id=session_id,
            device_id=device_id,
            test_mode=role == "test",
            expires_at=expires_at,
        )

    def _issue_user(self, auth, device_id, remember=True):
        if auth is None:
            raise ValueError("account_not_found")
        raw_device = _safe_identifier(device_id, "xiaoyou-phone")
        device_scope = hashlib.sha256(
            str(auth["user_id"]).encode("utf-8")
        ).hexdigest()[:12]
        scoped_device = "usr-{}-{}".format(device_scope, raw_device)[:128]
        return self._issue(
            account_id=auth["identifier"],
            user_id=auth["user_id"],
            role="user",
            session_id=auth["session_id"],
            device_id=scoped_device,
            remember=remember,
        )

    def _issue(self, *, account_id, user_id, role, session_id, device_id, remember):
        now = int(time.time())
        expires_at = now + (OWNER_TOKEN_TTL if bool(remember) else SESSION_TOKEN_TTL)
        payload = {
            "sub": account_id,
            "uid": user_id,
            "role": role,
            "sid": session_id,
            "did": device_id,
            "iat": now,
            "exp": expires_at,
            "jti": secrets.token_hex(12),
        }
        return {
            "access_token": self._encode(payload),
            "token_type": "Bearer",
            "account_id": account_id,
            "user_id": user_id,
            "test_mode": role == "test",
            "device_id": device_id,
            "expires_at": expires_at,
        }

    def _require_email_service(self):
        if len(self.secret) < 32:
            raise RuntimeError("auth_service_unavailable")
        if not self.debug_email_code and not self.email_enabled:
            raise RuntimeError("email_service_unavailable")

    def _require_username(self, value):
        username = _normalize_username(value)
        if not _valid_username(username):
            raise ValueError("invalid_username")
        if username in {self.owner_username, self.test_username}:
            raise ValueError("username_taken")
        return username

    @staticmethod
    def _validate_password(password):
        password = str(password or "")
        if (
            len(password) < PASSWORD_MIN_LENGTH
            or len(password.encode("utf-8")) > PASSWORD_MAX_BYTES
        ):
            raise ValueError("invalid_password_length")

    def _ensure_username_available(self, username):
        if self._auth_record("username", username):
            raise ValueError("username_taken")

    def _resolve_account_and_email(self, identifier):
        raw = str(identifier or "").strip()
        auth = None
        if "@" in raw:
            email = _normalize_email(raw)
            email_auth = self._auth_record("email", email) if email else None
            if email_auth:
                auth = self._user_auth_record(email_auth["user_id"], "username")
        else:
            username = _normalize_username(raw)
            auth = self._auth_record("username", username) if username else None
        if not auth:
            return None, ""
        email_auth = self._user_auth_record(auth["user_id"], "email")
        email = str(email_auth["identifier"] if email_auth else "")
        return auth, email

    def _rate_limit_digest(self, scope, value):
        value = str(value or "").strip().lower()
        if not value:
            return ""
        return hmac.new(
            self.secret.encode("utf-8"),
            "{}\n{}".format(scope, value).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _enforce_email_request_limits(
        self,
        identifier,
        *,
        client_ip="",
        purpose="email",
    ):
        now = int(time.time())
        hour_cutoff = now - 60 * 60
        day_cutoff = now - 24 * 60 * 60
        checks = []

        identifier_key = self._rate_limit_digest("email_identifier", identifier)
        if identifier_key:
            checks.append(
                (
                    "identifier",
                    identifier_key,
                    EMAIL_REQUEST_EMAIL_HOUR_LIMIT,
                    EMAIL_REQUEST_EMAIL_DAY_LIMIT,
                )
            )

        ip_key = self._rate_limit_digest("client_ip", client_ip)
        if ip_key:
            checks.append(
                (
                    "ip",
                    ip_key,
                    EMAIL_REQUEST_IP_HOUR_LIMIT,
                    EMAIL_REQUEST_IP_DAY_LIMIT,
                )
            )

        if not checks:
            return

        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "DELETE FROM auth_rate_events WHERE created_at<?",
                (now - EMAIL_REQUEST_RETENTION,),
            )
            for scope, key_digest, hour_limit, day_limit in checks:
                hour_count = db.execute(
                    "SELECT COUNT(*) FROM auth_rate_events "
                    "WHERE scope=? AND key_digest=? AND created_at>=?",
                    (scope, key_digest, hour_cutoff),
                ).fetchone()[0]
                day_count = db.execute(
                    "SELECT COUNT(*) FROM auth_rate_events "
                    "WHERE scope=? AND key_digest=? AND created_at>=?",
                    (scope, key_digest, day_cutoff),
                ).fetchone()[0]
                if int(hour_count) >= int(hour_limit) or int(day_count) >= int(day_limit):
                    db.rollback()
                    raise ValueError("email_request_rate_limited")

            for scope, key_digest, _, _ in checks:
                db.execute(
                    "INSERT INTO auth_rate_events(scope,key_digest,purpose,created_at) "
                    "VALUES(?,?,?,?)",
                    (scope, key_digest, str(purpose or "email"), now),
                )
            db.commit()

    def _challenge_is_fresh(self, purpose, identifier, seconds):
        cutoff = int(time.time()) - max(0, int(seconds))
        with self._connection() as db:
            row = db.execute(
                "SELECT created_at FROM auth_challenges WHERE purpose=? AND identifier=?",
                (purpose, identifier),
            ).fetchone()
        return bool(row and int(row["created_at"] or 0) > cutoff)

    def _create_challenge(self, purpose, identifier, payload):
        now = int(time.time())
        code = "{:06d}".format(secrets.randbelow(1_000_000))
        digest = self._code_digest(purpose, identifier, code)
        with self._connection() as db:
            db.execute(
                "DELETE FROM auth_challenges WHERE purpose=? AND identifier=?",
                (purpose, identifier),
            )
            db.execute(
                "INSERT INTO auth_challenges(id,purpose,identifier,code_digest,payload,attempts,created_at,expires_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    "challenge_" + secrets.token_hex(12),
                    purpose,
                    identifier,
                    digest,
                    json.dumps(payload),
                    0,
                    now,
                    now + CHALLENGE_TTL,
                ),
            )
            db.commit()
        return code

    def _consume_challenge(self, purpose, identifier, code):
        now = int(time.time())
        with self._connection() as db:
            row = db.execute(
                "SELECT id,code_digest,payload,attempts,expires_at FROM auth_challenges "
                "WHERE purpose=? AND identifier=?",
                (purpose, identifier),
            ).fetchone()
            if (
                not row
                or int(row["expires_at"] or 0) <= now
                or int(row["attempts"] or 0) >= 6
            ):
                return None
            if not hmac.compare_digest(
                row["code_digest"], self._code_digest(purpose, identifier, code)
            ):
                db.execute(
                    "UPDATE auth_challenges SET attempts=attempts+1 WHERE id=?",
                    (row["id"],),
                )
                db.commit()
                return None
            db.execute("DELETE FROM auth_challenges WHERE id=?", (row["id"],))
            db.commit()
        try:
            return json.loads(row["payload"] or "{}")
        except json.JSONDecodeError:
            return {}

    def _code_digest(self, purpose, identifier, code):
        return hmac.new(
            self.secret.encode("utf-8"),
            "{}\n{}\n{}".format(
                purpose, identifier, str(code or "").strip()
            ).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _send_code(self, recipient, code, title, *, action):
        if self.debug_email_code:
            return
        if not self.email_enabled:
            raise RuntimeError("email_service_unavailable")
        safe_code = html.escape(str(code))
        message = EmailMessage()
        message["Subject"] = title
        message["From"] = formataddr(("小悠", self.smtp_from))
        message["To"] = recipient
        message.set_content(
            "你的验证码是：{}\n\n验证码 10 分钟内有效，用于{}。如非本人操作，请忽略此邮件。".format(
                code, action
            )
        )
        message.add_alternative(
            """<!doctype html><html><body style=\"margin:0;background:#f7f1f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;color:#34272e\"><div style=\"max-width:520px;margin:32px auto;padding:0 18px\"><div style=\"background:#fff;border-radius:24px;padding:34px 30px;box-shadow:0 10px 34px rgba(121,78,102,.10)\"><div style=\"font-size:22px;font-weight:700;margin-bottom:6px\">小悠</div><div style=\"font-size:14px;color:#806f78;margin-bottom:28px\">{title}</div><div style=\"font-size:13px;color:#806f78\">你的验证码</div><div style=\"font-size:34px;letter-spacing:8px;font-weight:800;color:#ad4f7d;margin:12px 0 24px\">{code}</div><div style=\"font-size:14px;line-height:1.8;color:#5d4c55\">验证码 10 分钟内有效，用于{action}。<br>如果不是你本人操作，可以忽略这封邮件。</div></div><div style=\"text-align:center;color:#a4939b;font-size:12px;margin-top:18px\">小悠 · yoyoyan.cn</div></div></body></html>""".format(
                title=html.escape(title), code=safe_code, action=html.escape(action)
            ),
            subtype="html",
        )
        context = ssl.create_default_context()
        if self.smtp_ssl:
            client = smtplib.SMTP_SSL(
                self.smtp_host,
                self.smtp_port,
                timeout=15,
                context=context,
            )
        else:
            client = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=15)
            client.starttls(context=context)
        try:
            if self.smtp_username:
                client.login(self.smtp_username, self.smtp_password)
            client.send_message(message)
        finally:
            client.quit()

    def _challenge_response(self, code):
        result = {"accepted": True, "expires_in": CHALLENGE_TTL}
        if self.debug_email_code:
            result["debug_code"] = code
        return result

    def _auth_record(self, auth_type, identifier):
        with self._connection() as db:
            return self._auth_record_with_connection(db, auth_type, identifier)

    @staticmethod
    def _auth_record_with_connection(db, auth_type, identifier):
        return db.execute(
            "SELECT a.*,u.session_id,u.nickname,u.avatar,u.status "
            "FROM user_auth a JOIN users u ON u.id=a.user_id "
            "WHERE a.auth_type=? AND a.identifier=?",
            (auth_type, identifier),
        ).fetchone()

    def _user_auth_record(self, user_id, auth_type):
        with self._connection() as db:
            return self._user_auth_record_with_connection(db, user_id, auth_type)

    @staticmethod
    def _user_auth_record_with_connection(db, user_id, auth_type):
        return db.execute(
            "SELECT a.*,u.session_id,u.nickname,u.avatar,u.status "
            "FROM user_auth a JOIN users u ON u.id=a.user_id "
            "WHERE a.user_id=? AND a.auth_type=? ORDER BY a.created_at LIMIT 1",
            (user_id, auth_type),
        ).fetchone()

    def _user(self, user_id):
        with self._connection() as db:
            return db.execute(
                "SELECT * FROM users WHERE id=?", (user_id,)
            ).fetchone()

    def _touch_user(self, user_id):
        now = int(time.time())
        with self._connection() as db:
            db.execute(
                "UPDATE users SET last_login_at=?,updated_at=? WHERE id=?",
                (now, now, user_id),
            )
            db.commit()

    def _connection(self):
        folder = os.path.dirname(os.path.abspath(self.database_path))
        os.makedirs(folder, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=8)
        connection.row_factory = sqlite3.Row
        return closing(connection)

    def _initialize_database(self):
        with self._connection() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users(
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL UNIQUE,
                    nickname TEXT NOT NULL DEFAULT '', avatar TEXT NOT NULL DEFAULT '',
                    vip_level INTEGER NOT NULL DEFAULT 0, vip_expire_at INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
                    last_login_at INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active'
                );
                CREATE TABLE IF NOT EXISTS user_auth(
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, auth_type TEXT NOT NULL,
                    identifier TEXT NOT NULL, password_hash TEXT NOT NULL DEFAULT '',
                    verified INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
                    UNIQUE(auth_type,identifier), FOREIGN KEY(user_id) REFERENCES users(id)
                );
                CREATE INDEX IF NOT EXISTS idx_user_auth_user ON user_auth(user_id);
                CREATE TABLE IF NOT EXISTS auth_challenges(
                    id TEXT PRIMARY KEY, purpose TEXT NOT NULL, identifier TEXT NOT NULL,
                    code_digest TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}', attempts INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, UNIQUE(purpose,identifier)
                );
                CREATE TABLE IF NOT EXISTS auth_rate_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL,
                    key_digest TEXT NOT NULL,
                    purpose TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_auth_rate_events_scope_key_time
                    ON auth_rate_events(scope,key_digest,created_at);
                CREATE TABLE IF NOT EXISTS oauth_transactions(
                    id TEXT PRIMARY KEY, provider TEXT NOT NULL,
                    state_digest TEXT NOT NULL UNIQUE, poll_digest TEXT NOT NULL UNIQUE,
                    device_id TEXT NOT NULL, remember INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'pending', result_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_oauth_transactions_expiry
                    ON oauth_transactions(expires_at);
                """
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(users)").fetchall()}
            migrations = {
                "display_name": "TEXT NOT NULL DEFAULT ''",
                "birthday": "TEXT NOT NULL DEFAULT ''",
                "about_me": "TEXT NOT NULL DEFAULT ''",
                "relationship_started_at": "INTEGER NOT NULL DEFAULT 0",
                "profile_completed_at": "INTEGER NOT NULL DEFAULT 0",
            }
            for name, definition in migrations.items():
                if name not in columns:
                    db.execute("ALTER TABLE users ADD COLUMN {} {}".format(name, definition))
            db.execute(
                "UPDATE users SET relationship_started_at=created_at "
                "WHERE relationship_started_at IS NULL OR relationship_started_at<=0"
            )
            db.commit()

    def _profile_payload(self, user, account_id=""):
        started_at = int(user["relationship_started_at"] or user["created_at"] or time.time())
        days = max(1, (int(time.time()) - started_at) // 86400 + 1)
        return {
            "account_id": str(account_id or ""),
            "display_name": str(user["display_name"] or user["nickname"] or account_id),
            "birthday": str(user["birthday"] or ""),
            "about_me": str(user["about_me"] or ""),
            "relationship_started_at": started_at,
            "relationship_days": days,
            "profile_completed": bool(int(user["profile_completed_at"] or 0)),
            "test_mode": False,
        }

    def _sync_profile_document(self, user_id):
        user = self._user(user_id)
        auth = self._user_auth_record(user_id, "username")
        if user and auth:
            self._write_profile_document(
                str(user["session_id"]),
                self._profile_payload(user, str(auth["identifier"])),
            )

    @staticmethod
    def _write_profile_document(session_id, profile):
        try:
            from plugins.xiaoyou_common.app_user_profile import write_profile_document
        except ModuleNotFoundError:
            import importlib.util

            module_path = os.path.join(os.path.dirname(__file__), "app_user_profile.py")
            spec = importlib.util.spec_from_file_location(
                "app_user_profile_under_test", module_path
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            write_profile_document = module.write_profile_document

        write_profile_document(session_id, profile)

    def _encode(self, payload):
        encoded = _b64encode(
            json.dumps(
                payload,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
        signature = hmac.new(
            self.secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256
        ).digest()
        return "{}.{}".format(encoded, _b64encode(signature))

    def _decode(self, token):
        try:
            encoded, signature = token.split(".", 1)
            expected = hmac.new(
                self.secret.encode("utf-8"),
                encoded.encode("ascii"),
                hashlib.sha256,
            ).digest()
            if not hmac.compare_digest(expected, _b64decode(signature)):
                return None
            value = json.loads(_b64decode(encoded).decode("utf-8"))
            return value if isinstance(value, dict) else None
        except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return None


def _normalize_email(value):
    value = str(value or "").strip().lower()
    if len(value) > 254 or value.count("@") != 1:
        return ""
    local, domain = value.split("@", 1)
    if not local or not domain or "." not in domain or " " in value:
        return ""
    return value


def _normalize_username(value):
    return unicodedata.normalize("NFKC", str(value or "")).strip().casefold()


def _valid_username(value):
    if not 2 <= len(value) <= 32 or not value[0].isalnum():
        return False
    return all(char.isalnum() or char in "._-" for char in value)


def _clean_display_name(value):
    value = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not 1 <= len(value) <= 32 or any(unicodedata.category(char).startswith("C") for char in value):
        return ""
    return value


def _clean_birthday(value):
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError("invalid_birthday")
    if parsed > date.today() or parsed.year < 1900:
        raise ValueError("invalid_birthday")
    return parsed.isoformat()


def _clean_about(value):
    value = unicodedata.normalize("NFKC", str(value or "")).strip()
    if len(value) > 300 or any(char == "\x00" for char in value):
        raise ValueError("invalid_about_me")
    return value


def _safe_identifier(value, fallback=""):
    value = str(value or "").strip()
    if not value:
        return str(fallback or "")
    if len(value) > 128:
        return ""
    allowed = set(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:"
    )
    return value if all(char in allowed for char in value) else ""


def _integer(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _truthy(value):
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")
