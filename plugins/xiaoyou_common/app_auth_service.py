# -*- coding: utf-8 -*-
"""Account-scoped authentication for the Xiaoyou mobile App.

Passwords are never stored in the repository.  Deployments provide PBKDF2
hashes through environment variables and this service returns short,
HMAC-signed bearer tokens containing the account's data scope.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass


PASSWORD_ITERATIONS = 310_000
OWNER_TOKEN_TTL = 30 * 24 * 60 * 60
SESSION_TOKEN_TTL = 12 * 60 * 60


def _b64encode(value):
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value):
    value = str(value or "")
    return base64.urlsafe_b64decode(value + ("=" * (-len(value) % 4)))


def hash_password(password, *, salt=None, iterations=PASSWORD_ITERATIONS):
    """Return a deployment-safe PBKDF2 password hash."""
    password = str(password or "")
    if not password:
        raise ValueError("empty_password")
    salt = salt or secrets.token_bytes(18)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        int(iterations),
    )
    return "pbkdf2_sha256${}${}${}".format(
        int(iterations),
        _b64encode(salt),
        _b64encode(digest),
    )


def verify_password(password, encoded):
    try:
        algorithm, iterations, salt, expected = str(encoded or "").split(
            "$",
            3,
        )
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
    session_id: str
    device_id: str
    test_mode: bool
    expires_at: int
    legacy: bool = False


class AppAuthService:
    """Verify App credentials and issue account-scoped bearer tokens."""

    def __init__(self, canonical_session_id):
        self.canonical_session_id = str(canonical_session_id or "yoyo")
        self.owner_username = (
            os.getenv("XIAOYOU_APP_OWNER_USERNAME", "yoyo").strip() or "yoyo"
        )
        self.owner_password_hash = os.getenv(
            "XIAOYOU_APP_OWNER_PASSWORD_HASH",
            "",
        ).strip()
        self.test_username = (
            os.getenv("XIAOYOU_APP_TEST_USERNAME", "test").strip() or "test"
        )
        self.test_password_hash = os.getenv(
            "XIAOYOU_APP_TEST_PASSWORD_HASH",
            "",
        ).strip()
        self.secret = os.getenv("XIAOYOU_APP_AUTH_SECRET", "").strip()
        self.legacy_token = os.getenv("XIAOYOU_APP_TOKEN", "").strip()

    @property
    def enabled(self):
        return bool(
            len(self.secret) >= 32
            and self.owner_password_hash
            and self.test_password_hash
        )

    def login(self, username, password, device_id, remember=True):
        username = str(username or "").strip()
        device_id = _safe_identifier(device_id, fallback="yoyo-phone")
        if hmac.compare_digest(username, self.owner_username):
            if not verify_password(password, self.owner_password_hash):
                return None
            role = "owner"
            account_id = self.owner_username
            session_id = self.canonical_session_id
            scoped_device_id = device_id
        elif hmac.compare_digest(username, self.test_username):
            if not verify_password(password, self.test_password_hash):
                return None
            role = "test"
            account_id = self.test_username
            nonce = secrets.token_hex(12)
            session_id = "app_test_" + nonce
            scoped_device_id = "test-" + nonce
        else:
            return None

        now = int(time.time())
        expires_at = now + (
            OWNER_TOKEN_TTL if bool(remember) else SESSION_TOKEN_TTL
        )
        payload = {
            "sub": account_id,
            "role": role,
            "sid": session_id,
            "did": scoped_device_id,
            "iat": now,
            "exp": expires_at,
            "jti": secrets.token_hex(12),
        }
        token = self._encode(payload)
        return {
            "access_token": token,
            "token_type": "Bearer",
            "account_id": account_id,
            "test_mode": role == "test",
            "device_id": scoped_device_id,
            "expires_at": expires_at,
        }

    def authenticate(self, token, requested_device_id=""):
        token = str(token or "").strip()
        if (
            self.legacy_token
            and hmac.compare_digest(token, self.legacy_token)
        ):
            return AppAuthContext(
                account_id=self.owner_username,
                session_id=self.canonical_session_id,
                device_id=_safe_identifier(
                    requested_device_id,
                    fallback="yoyo-phone",
                ),
                test_mode=False,
                expires_at=0,
                legacy=True,
            )
        if not self.enabled:
            return None
        payload = self._decode(token)
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
        if role == "owner":
            if account_id != self.owner_username:
                return None
        elif role == "test":
            if account_id != self.test_username:
                return None
        else:
            return None
        session_id = _safe_identifier(payload.get("sid"))
        device_id = _safe_identifier(payload.get("did"))
        if not session_id or not device_id:
            return None
        return AppAuthContext(
            account_id=account_id,
            session_id=session_id,
            device_id=device_id,
            test_mode=role == "test",
            expires_at=expires_at,
        )

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
            self.secret.encode("utf-8"),
            encoded.encode("ascii"),
            hashlib.sha256,
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


def _safe_identifier(value, fallback=""):
    value = str(value or "").strip()
    if not value:
        return str(fallback or "")
    if len(value) > 128:
        return ""
    allowed = set(
        "abcdefghijklmnopqrstuvwxyz"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "0123456789-_.:"
    )
    return value if all(char in allowed for char in value) else ""
