from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

from .config import Settings
from .database import AdminRecord, Database, SessionRecord


password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)


class AuthenticationError(Exception):
    pass


class RateLimitError(AuthenticationError):
    pass


class LoginRateLimiter:
    def __init__(self, limit: int, window_seconds: int = 600):
        self.limit = limit
        self.window_seconds = window_seconds
        self.attempts: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        queue = self.attempts[key]
        while queue and now - queue[0] > self.window_seconds:
            queue.popleft()
        if len(queue) >= self.limit:
            raise RateLimitError("登录尝试过多，请稍后再试")

    def fail(self, key: str) -> None:
        self.attempts[key].append(time.monotonic())

    def success(self, key: str) -> None:
        self.attempts.pop(key, None)


@dataclass(slots=True)
class CreatedSession:
    token: str
    csrf_token: str
    expires_at: int
    admin: AdminRecord


@dataclass(slots=True)
class CreatedGuestSession:
    token: str
    csrf_token: str
    expires_at: int


class SecurityService:
    def __init__(self, settings: Settings, database: Database):
        self.settings = settings
        self.database = database
        self.rate_limiter = LoginRateLimiter(settings.login_attempts_per_10_minutes)
        self.guest_limiter = LoginRateLimiter(limit=30, window_seconds=600)
        digest = hashlib.sha256(settings.app_secret.encode("utf-8")).digest()
        self.fernet = Fernet(base64.urlsafe_b64encode(digest))

    def hash_password(self, password: str) -> str:
        return password_hasher.hash(password)

    def verify_password(self, password_hash: str, password: str) -> bool:
        try:
            return password_hasher.verify(password_hash, password)
        except (VerifyMismatchError, InvalidHashError):
            return False

    def encrypt_totp_secret(self, secret: str) -> str:
        return self.fernet.encrypt(secret.encode("ascii")).decode("ascii")

    def decrypt_totp_secret(self, encrypted: str) -> str:
        try:
            return self.fernet.decrypt(encrypted.encode("ascii")).decode("ascii")
        except InvalidToken as exc:
            raise AuthenticationError("TOTP密钥无法解密，请检查APP_SECRET") from exc

    def secret_hash(self, value: str) -> str:
        return hmac.new(
            self.settings.app_secret.encode("utf-8"),
            value.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def authenticate(self, username: str, password: str, otp: str, ip_address: str) -> AdminRecord:
        key = f"{ip_address}:{username.lower()}"
        self.rate_limiter.check(key)
        admin = self.database.get_admin(username)
        if not admin or not self.verify_password(admin.password_hash, password):
            self.rate_limiter.fail(key)
            raise AuthenticationError("用户名、密码或动态验证码错误")

        normalized_otp = otp.replace(" ", "").upper()
        totp_secret = self.decrypt_totp_secret(admin.totp_secret_encrypted)
        totp_ok = normalized_otp.isdigit() and pyotp.TOTP(totp_secret).verify(
            normalized_otp,
            valid_window=1,
        )
        recovery_ok = False
        if not totp_ok and len(normalized_otp) >= 8:
            recovery_ok = self.database.use_recovery_code(
                admin.id,
                self.secret_hash(normalized_otp),
            )
        if not totp_ok and not recovery_ok:
            self.rate_limiter.fail(key)
            raise AuthenticationError("用户名、密码或动态验证码错误")

        self.rate_limiter.success(key)
        return admin

    def create_session(
        self,
        admin: AdminRecord,
        ip_address: str,
        user_agent: str,
    ) -> CreatedSession:
        token = secrets.token_urlsafe(48)
        csrf_token = secrets.token_urlsafe(32)
        expires_at = int(time.time()) + self.settings.session_minutes * 60
        self.database.create_session(
            token_hash=self.secret_hash(token),
            admin_id=admin.id,
            csrf_token=csrf_token,
            ip_address=ip_address,
            user_agent=user_agent,
            expires_at=expires_at,
        )
        return CreatedSession(
            token=token,
            csrf_token=csrf_token,
            expires_at=expires_at,
            admin=admin,
        )

    def resolve_session(self, token: str | None) -> SessionRecord | None:
        if not token:
            return None
        if token.startswith("guest."):
            return self._resolve_guest_session(token)
        return self.database.get_session(self.secret_hash(token))

    def delete_session(self, token: str | None) -> None:
        if token and not token.startswith("guest."):
            self.database.delete_session(self.secret_hash(token))

    @staticmethod
    def _b64encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

    @staticmethod
    def _b64decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    def create_guest_session(self, ip_address: str) -> CreatedGuestSession:
        key = f"guest:{ip_address}"
        self.guest_limiter.check(key)
        self.guest_limiter.fail(key)
        csrf_token = secrets.token_urlsafe(24)
        expires_at = int(time.time()) + self.settings.session_minutes * 60
        payload = self._b64encode(
            json.dumps(
                {"exp": expires_at, "csrf": csrf_token, "nonce": secrets.token_urlsafe(12)},
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        )
        signed = f"guest.{payload}"
        signature = self._b64encode(
            hmac.new(
                self.settings.app_secret.encode("utf-8"),
                signed.encode("ascii"),
                hashlib.sha256,
            ).digest()
        )
        return CreatedGuestSession(
            token=f"{signed}.{signature}",
            csrf_token=csrf_token,
            expires_at=expires_at,
        )

    def _resolve_guest_session(self, token: str) -> SessionRecord | None:
        try:
            prefix, payload, signature = token.split(".", 2)
            if prefix != "guest":
                return None
            expected = self._b64encode(
                hmac.new(
                    self.settings.app_secret.encode("utf-8"),
                    f"guest.{payload}".encode("ascii"),
                    hashlib.sha256,
                ).digest()
            )
            if not hmac.compare_digest(signature, expected):
                return None
            value = json.loads(self._b64decode(payload).decode("utf-8"))
            expires_at = int(value["exp"])
            csrf_token = str(value["csrf"])
            if expires_at <= int(time.time()) or not csrf_token:
                return None
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            return None
        return SessionRecord(
            token_hash="",
            admin_id=0,
            username="访客",
            csrf_token=csrf_token,
            expires_at=expires_at,
            role="guest",
        )

    def generate_totp(self, username: str) -> tuple[str, str]:
        secret = pyotp.random_base32()
        uri = pyotp.TOTP(secret).provisioning_uri(
            name=username,
            issuer_name="小悠·命轨观测台",
        )
        return secret, uri

    @staticmethod
    def generate_recovery_codes(count: int = 8) -> list[str]:
        return [
            f"{secrets.token_hex(3).upper()}-{secrets.token_hex(3).upper()}"
            for _ in range(count)
        ]
