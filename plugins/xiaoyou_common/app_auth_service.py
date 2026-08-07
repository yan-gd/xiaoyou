# -*- coding: utf-8 -*-
"""Persistent, account-scoped authentication for the Xiaoyou mobile App."""

import base64
import hashlib
import hmac
import json
import os
import secrets
import smtplib
import sqlite3
import ssl
import time
import urllib.parse
import urllib.request
import sys
from contextlib import closing
from dataclasses import dataclass
from email.message import EmailMessage

if os.path.isdir("/app/python_packages") and "/app/python_packages" not in sys.path:
    sys.path.insert(0, "/app/python_packages")

try:
    import bcrypt
except ImportError:  # pragma: no cover - deployment validation reports this.
    bcrypt = None


PASSWORD_ITERATIONS = 310_000  # legacy deployment-account compatibility only
OWNER_TOKEN_TTL = 30 * 24 * 60 * 60
SESSION_TOKEN_TTL = 12 * 60 * 60
CHALLENGE_TTL = 10 * 60
QQ_LOGIN_TTL = 10 * 60


def _b64encode(value):
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value):
    value = str(value or "")
    return base64.urlsafe_b64decode(value + ("=" * (-len(value) % 4)))


def hash_password(password, *, salt=None, iterations=PASSWORD_ITERATIONS):
    """Legacy PBKDF2 helper retained for existing owner/test env accounts."""
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


def hash_account_password(password):
    """Hash registered-user passwords with bcrypt; plaintext is never stored."""
    password = str(password or "")
    if len(password) < 8 or len(password.encode("utf-8")) > 72:
        raise ValueError("invalid_password_length")
    if bcrypt is None:
        raise RuntimeError("bcrypt_unavailable")
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode(
        "ascii"
    )


def verify_password(password, encoded):
    encoded = str(encoded or "")
    if encoded.startswith("$2"):
        if bcrypt is None:
            return False
        try:
            return bool(
                bcrypt.checkpw(str(password or "").encode("utf-8"), encoded.encode("ascii"))
            )
        except (ValueError, TypeError):
            return False
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
    """Email/QQ accounts plus compatible owner/test deployment accounts."""

    def __init__(self, canonical_session_id):
        self.canonical_session_id = str(canonical_session_id or "yoyo")
        self.owner_username = os.getenv("XIAOYOU_APP_OWNER_USERNAME", "yoyo").strip() or "yoyo"
        self.owner_password_hash = os.getenv("XIAOYOU_APP_OWNER_PASSWORD_HASH", "").strip()
        self.test_username = os.getenv("XIAOYOU_APP_TEST_USERNAME", "test").strip() or "test"
        self.test_password_hash = os.getenv("XIAOYOU_APP_TEST_PASSWORD_HASH", "").strip()
        self.secret = os.getenv("XIAOYOU_APP_AUTH_SECRET", "").strip()
        self.legacy_token = os.getenv("XIAOYOU_APP_TOKEN", "").strip()
        self.database_path = os.getenv(
            "XIAOYOU_APP_ACCOUNT_DB_PATH", "/app/data/app_channel/accounts.db"
        ).strip()
        self.qq_app_id = os.getenv("XIAOYOU_QQ_APP_ID", "").strip()
        self.qq_app_secret = os.getenv("XIAOYOU_QQ_APP_SECRET", "").strip()
        self.qq_redirect_uri = os.getenv("XIAOYOU_QQ_REDIRECT_URI", "").strip()
        self.smtp_host = os.getenv("XIAOYOU_APP_SMTP_HOST", "").strip()
        self.smtp_port = _integer(os.getenv("XIAOYOU_APP_SMTP_PORT"), 465)
        self.smtp_username = os.getenv("XIAOYOU_APP_SMTP_USERNAME", "").strip()
        self.smtp_password = os.getenv("XIAOYOU_APP_SMTP_PASSWORD", "").strip()
        self.smtp_from = os.getenv("XIAOYOU_APP_SMTP_FROM", self.smtp_username).strip()
        self.smtp_ssl = _truthy(os.getenv("XIAOYOU_APP_SMTP_SSL", "true"))
        self.debug_email_code = _truthy(os.getenv("XIAOYOU_APP_EMAIL_DEBUG_CODE", "false"))
        self._initialize_database()

    @property
    def enabled(self):
        return bool(len(self.secret) >= 32 and (self.database_path or self.owner_password_hash))

    @property
    def email_enabled(self):
        return bool(bcrypt is not None and self.smtp_host and self.smtp_from)

    @property
    def qq_enabled(self):
        return bool(self.qq_app_id and self.qq_app_secret and self.qq_redirect_uri)

    def public_config(self):
        return {
            "email_registration": self.email_enabled or self.debug_email_code,
            "qq_login": self.qq_enabled,
            "password_min_length": 8,
        }

    def login(self, username, password, device_id, remember=True):
        username = str(username or "").strip()
        device_id = _safe_identifier(device_id, fallback="xiaoyou-phone")
        if hmac.compare_digest(username, self.owner_username) and self.owner_password_hash:
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
        if hmac.compare_digest(username, self.test_username) and self.test_password_hash:
            if not verify_password(password, self.test_password_hash):
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

        email = _normalize_email(username)
        if not email:
            return None
        auth = self._auth_record("email", email)
        if not auth or not int(auth["verified"] or 0):
            return None
        if not verify_password(password, auth["password_hash"]):
            return None
        self._touch_user(auth["user_id"])
        return self._issue_user(auth, device_id=device_id, remember=remember)

    def request_registration(self, email, password):
        email = _normalize_email(email)
        if not email:
            raise ValueError("invalid_email")
        password_hash = hash_account_password(password)
        if self._auth_record("email", email):
            raise ValueError("email_already_registered")
        code = self._create_challenge("register", email, {"password_hash": password_hash})
        self._send_code(email, code, "注册小悠账号")
        return self._challenge_response(code)

    def verify_registration(self, email, code, device_id, remember=True):
        email = _normalize_email(email)
        payload = self._consume_challenge("register", email, code)
        if not payload:
            raise ValueError("invalid_or_expired_code")
        now = int(time.time())
        user_id = "usr_" + secrets.token_hex(16)
        session_id = "app_user_" + user_id[4:]
        with self._connection() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "INSERT INTO users(id,session_id,nickname,avatar,vip_level,created_at,updated_at,last_login_at,status) VALUES(?,?,?,?,?,?,?,?,?)",
                    (user_id, session_id, email.split("@", 1)[0][:40], "", 0, now, now, now, "active"),
                )
                db.execute(
                    "INSERT INTO user_auth(id,user_id,auth_type,identifier,password_hash,verified,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                    ("auth_" + secrets.token_hex(12), user_id, "email", email, payload["password_hash"], 1, now, now),
                )
                db.commit()
            except Exception:
                db.rollback()
                raise
        auth = self._auth_record("email", email)
        return self._issue_user(auth, device_id=_safe_identifier(device_id, "xiaoyou-phone"), remember=remember)

    def request_password_reset(self, email):
        email = _normalize_email(email)
        auth = self._auth_record("email", email) if email else None
        if auth:
            code = self._create_challenge("reset", email, {})
            self._send_code(email, code, "重置小悠账号密码")
            return self._challenge_response(code)
        return {"accepted": True, "expires_in": CHALLENGE_TTL}

    def confirm_password_reset(self, email, code, password):
        email = _normalize_email(email)
        if not self._consume_challenge("reset", email, code):
            raise ValueError("invalid_or_expired_code")
        encoded = hash_account_password(password)
        with self._connection() as db:
            changed = db.execute(
                "UPDATE user_auth SET password_hash=?,updated_at=? WHERE auth_type='email' AND identifier=?",
                (encoded, int(time.time()), email),
            ).rowcount
            db.commit()
        if not changed:
            raise ValueError("account_not_found")
        return {"ok": True}

    def start_qq_login(self):
        if not self.qq_enabled:
            raise ValueError("qq_login_unavailable")
        login_id = secrets.token_urlsafe(24)
        state = secrets.token_urlsafe(24)
        now = int(time.time())
        with self._connection() as db:
            db.execute(
                "INSERT INTO qq_login_requests(login_id,state,status,user_id,created_at,expires_at) VALUES(?,?,?,?,?,?)",
                (login_id, state, "pending", "", now, now + QQ_LOGIN_TTL),
            )
            db.commit()
        query = urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": self.qq_app_id,
                "redirect_uri": self.qq_redirect_uri,
                "state": state,
                "scope": "get_user_info",
                "display": "mobile",
            }
        )
        return {
            "login_id": login_id,
            "authorization_url": "https://graph.qq.com/oauth2.0/authorize?" + query,
            "expires_in": QQ_LOGIN_TTL,
        }

    def complete_qq_callback(self, code, state):
        with self._connection() as db:
            row = db.execute(
                "SELECT login_id,expires_at,status FROM qq_login_requests WHERE state=?",
                (str(state or ""),),
            ).fetchone()
        if not row or row["status"] != "pending" or int(row["expires_at"]) <= int(time.time()):
            raise ValueError("invalid_qq_state")
        profile = self._fetch_qq_profile(code)
        auth = self._auth_record("qq", profile["openid"])
        now = int(time.time())
        if not auth:
            user_id = "usr_" + secrets.token_hex(16)
            session_id = "app_user_" + user_id[4:]
            with self._connection() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "INSERT INTO users(id,session_id,nickname,avatar,vip_level,created_at,updated_at,last_login_at,status) VALUES(?,?,?,?,?,?,?,?,?)",
                    (user_id, session_id, profile["nickname"], profile["avatar"], 0, now, now, now, "active"),
                )
                db.execute(
                    "INSERT INTO user_auth(id,user_id,auth_type,identifier,password_hash,verified,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                    ("auth_" + secrets.token_hex(12), user_id, "qq", profile["openid"], "", 1, now, now),
                )
                db.commit()
            auth = self._auth_record("qq", profile["openid"])
        with self._connection() as db:
            db.execute(
                "UPDATE qq_login_requests SET status='ready',user_id=? WHERE login_id=?",
                (auth["user_id"], row["login_id"]),
            )
            db.commit()
        return row["login_id"]

    def exchange_qq_login(self, login_id, device_id, remember=True):
        with self._connection() as db:
            row = db.execute(
                "SELECT status,user_id,expires_at FROM qq_login_requests WHERE login_id=?",
                (str(login_id or ""),),
            ).fetchone()
        if not row or int(row["expires_at"] or 0) <= int(time.time()):
            raise ValueError("qq_login_expired")
        if row["status"] != "ready":
            return None
        auth = self._user_auth(row["user_id"])
        if not auth:
            raise ValueError("qq_account_missing")
        with self._connection() as db:
            db.execute("DELETE FROM qq_login_requests WHERE login_id=?", (login_id,))
            db.commit()
        return self._issue_user(auth, _safe_identifier(device_id, "xiaoyou-phone"), remember)

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
            if not user or user["status"] != "active" or user["session_id"] != payload.get("sid"):
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

    def _create_challenge(self, purpose, identifier, payload):
        now = int(time.time())
        code = "{:06d}".format(secrets.randbelow(1_000_000))
        digest = self._code_digest(purpose, identifier, code)
        with self._connection() as db:
            db.execute("DELETE FROM auth_challenges WHERE purpose=? AND identifier=?", (purpose, identifier))
            db.execute(
                "INSERT INTO auth_challenges(id,purpose,identifier,code_digest,payload,attempts,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?)",
                ("challenge_" + secrets.token_hex(12), purpose, identifier, digest, json.dumps(payload), 0, now, now + CHALLENGE_TTL),
            )
            db.commit()
        return code

    def _consume_challenge(self, purpose, identifier, code):
        now = int(time.time())
        with self._connection() as db:
            row = db.execute(
                "SELECT id,code_digest,payload,attempts,expires_at FROM auth_challenges WHERE purpose=? AND identifier=?",
                (purpose, identifier),
            ).fetchone()
            if not row or int(row["expires_at"] or 0) <= now or int(row["attempts"] or 0) >= 6:
                return None
            if not hmac.compare_digest(row["code_digest"], self._code_digest(purpose, identifier, code)):
                db.execute("UPDATE auth_challenges SET attempts=attempts+1 WHERE id=?", (row["id"],))
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
            "{}\n{}\n{}".format(purpose, identifier, str(code or "").strip()).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _send_code(self, recipient, code, title):
        if self.debug_email_code:
            return
        if not self.email_enabled:
            raise RuntimeError("email_service_unavailable")
        message = EmailMessage()
        message["Subject"] = title
        message["From"] = self.smtp_from
        message["To"] = recipient
        message.set_content("你的验证码是：{}\n\n验证码 10 分钟内有效。如非本人操作，请忽略此邮件。".format(code))
        context = ssl.create_default_context()
        if self.smtp_ssl:
            client = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=15, context=context)
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

    def _fetch_qq_profile(self, code):
        token_payload = _http_json(
            "https://graph.qq.com/oauth2.0/token?" + urllib.parse.urlencode(
                {
                    "grant_type": "authorization_code",
                    "client_id": self.qq_app_id,
                    "client_secret": self.qq_app_secret,
                    "code": str(code or ""),
                    "redirect_uri": self.qq_redirect_uri,
                    "fmt": "json",
                }
            )
        )
        access_token = str(token_payload.get("access_token") or "")
        if not access_token:
            raise ValueError("qq_token_exchange_failed")
        identity = _http_json(
            "https://graph.qq.com/oauth2.0/me?" + urllib.parse.urlencode({"access_token": access_token, "fmt": "json"})
        )
        openid = str(identity.get("openid") or "")
        if not openid:
            raise ValueError("qq_identity_failed")
        profile = _http_json(
            "https://graph.qq.com/user/get_user_info?" + urllib.parse.urlencode(
                {"access_token": access_token, "oauth_consumer_key": self.qq_app_id, "openid": openid, "fmt": "json"}
            )
        )
        return {
            "openid": openid,
            "nickname": str(profile.get("nickname") or "QQ用户")[:80],
            "avatar": str(profile.get("figureurl_qq_2") or profile.get("figureurl_qq_1") or "")[:500],
        }

    def _auth_record(self, auth_type, identifier):
        with self._connection() as db:
            return db.execute(
                "SELECT a.*,u.session_id,u.nickname,u.avatar,u.status FROM user_auth a JOIN users u ON u.id=a.user_id WHERE a.auth_type=? AND a.identifier=?",
                (auth_type, identifier),
            ).fetchone()

    def _user_auth(self, user_id):
        with self._connection() as db:
            return db.execute(
                "SELECT a.*,u.session_id,u.nickname,u.avatar,u.status FROM user_auth a JOIN users u ON u.id=a.user_id WHERE a.user_id=? ORDER BY a.created_at LIMIT 1",
                (user_id,),
            ).fetchone()

    def _user(self, user_id):
        with self._connection() as db:
            return db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()

    def _touch_user(self, user_id):
        with self._connection() as db:
            db.execute("UPDATE users SET last_login_at=?,updated_at=? WHERE id=?", (int(time.time()), int(time.time()), user_id))
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
                CREATE TABLE IF NOT EXISTS qq_login_requests(
                    login_id TEXT PRIMARY KEY, state TEXT NOT NULL UNIQUE, status TEXT NOT NULL,
                    user_id TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL
                );
                """
            )
            db.commit()

    def _encode(self, payload):
        encoded = _b64encode(json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        signature = hmac.new(self.secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
        return "{}.{}".format(encoded, _b64encode(signature))

    def _decode(self, token):
        try:
            encoded, signature = token.split(".", 1)
            expected = hmac.new(self.secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
            if not hmac.compare_digest(expected, _b64decode(signature)):
                return None
            value = json.loads(_b64decode(encoded).decode("utf-8"))
            return value if isinstance(value, dict) else None
        except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return None


def _http_json(url):
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Xiaoyou/1.0"})
    with urllib.request.urlopen(request, timeout=15) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("invalid_remote_response")
    return value


def _normalize_email(value):
    value = str(value or "").strip().lower()
    if len(value) > 254 or value.count("@") != 1:
        return ""
    local, domain = value.split("@", 1)
    if not local or not domain or "." not in domain or " " in value:
        return ""
    return value


def _safe_identifier(value, fallback=""):
    value = str(value or "").strip()
    if not value:
        return str(fallback or "")
    if len(value) > 128:
        return ""
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:")
    return value if all(char in allowed for char in value) else ""


def _integer(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _truthy(value):
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")
