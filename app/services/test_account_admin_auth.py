from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import secrets
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Deque, Dict, Optional


TEST_ACCOUNT_ADMIN_SESSION_VERSION = 1
TEST_ACCOUNT_ADMIN_PASSWORD_SCHEME = "pbkdf2_sha256"
TEST_ACCOUNT_ADMIN_PASSWORD_ITERATIONS = 600_000
TEST_ACCOUNT_ADMIN_COOKIE_NAME = "dj_test_account_admin"


class TestAccountAdminAuthError(RuntimeError):
    pass


class TestAccountAdminAuthUnavailable(TestAccountAdminAuthError):
    pass


class TestAccountAdminAuthenticationFailed(TestAccountAdminAuthError):
    pass


class TestAccountAdminRateLimited(TestAccountAdminAuthError):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("administrator login is temporarily unavailable")
        self.retry_after_seconds = max(1, int(retry_after_seconds))


def encode_test_account_admin_password(
    password: str,
    *,
    salt: Optional[bytes] = None,
    iterations: int = TEST_ACCOUNT_ADMIN_PASSWORD_ITERATIONS,
) -> str:
    candidate = str(password or "")
    if len(candidate) < 16 or len(candidate) > 256:
        raise ValueError("administrator password must contain 16 to 256 characters")
    resolved_iterations = max(TEST_ACCOUNT_ADMIN_PASSWORD_ITERATIONS, int(iterations))
    resolved_salt = salt or secrets.token_bytes(16)
    if len(resolved_salt) < 16:
        raise ValueError("administrator password salt must contain at least 16 bytes")
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        candidate.encode("utf-8"),
        resolved_salt,
        resolved_iterations,
    )
    return ":".join(
        (
            TEST_ACCOUNT_ADMIN_PASSWORD_SCHEME,
            str(resolved_iterations),
            _b64encode(resolved_salt),
            _b64encode(digest),
        )
    )


def verify_test_account_admin_password(password: str, encoded: str) -> bool:
    candidate = str(password or "")
    if len(candidate) > 256:
        candidate = "invalid-oversized-password"
    try:
        scheme, iterations_text, salt_text, digest_text = str(encoded or "").split(
            ":", 3
        )
        if scheme != TEST_ACCOUNT_ADMIN_PASSWORD_SCHEME:
            return False
        iterations = int(iterations_text)
        if iterations < TEST_ACCOUNT_ADMIN_PASSWORD_ITERATIONS or iterations > 2_000_000:
            return False
        salt = _b64decode(salt_text)
        expected = _b64decode(digest_text)
        if len(salt) < 16 or len(expected) != 32:
            return False
    except (TypeError, ValueError):
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256",
        candidate.encode("utf-8"),
        salt,
        iterations,
    )
    return hmac.compare_digest(expected, actual)


class TestAccountAdminLoginLimiter:
    def __init__(self, *, max_attempts: int = 5, window_seconds: int = 900) -> None:
        self.max_attempts = max(1, int(max_attempts))
        self.window_seconds = max(60, int(window_seconds))
        self._attempts: Dict[str, Deque[datetime]] = {}
        self._lock = Lock()

    def retry_after(
        self,
        *,
        client_key: str,
        username: str,
        now: Optional[datetime] = None,
    ) -> int:
        observed_at = _utc(now)
        key = self._key(client_key, username)
        with self._lock:
            attempts = self._active_attempts(key, observed_at)
            if len(attempts) < self.max_attempts:
                return 0
            retry_at = attempts[0] + timedelta(seconds=self.window_seconds)
            return max(1, math.ceil((retry_at - observed_at).total_seconds()))

    def record_failure(
        self,
        *,
        client_key: str,
        username: str,
        now: Optional[datetime] = None,
    ) -> None:
        observed_at = _utc(now)
        key = self._key(client_key, username)
        with self._lock:
            attempts = self._active_attempts(key, observed_at)
            attempts.append(observed_at)
            self._attempts[key] = attempts

    def clear(self, *, client_key: str, username: str) -> None:
        key = self._key(client_key, username)
        with self._lock:
            self._attempts.pop(key, None)

    def _active_attempts(self, key: str, observed_at: datetime) -> Deque[datetime]:
        cutoff = observed_at - timedelta(seconds=self.window_seconds)
        attempts = self._attempts.get(key, deque())
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        if not attempts:
            self._attempts.pop(key, None)
        return attempts

    @staticmethod
    def _key(client_key: str, username: str) -> str:
        return hashlib.sha256(
            f"{str(client_key or '').strip()}:test-account-admin".encode(
                "utf-8"
            )
        ).hexdigest()


@dataclass(frozen=True)
class TestAccountAdminSession:
    username: str
    expires_at: datetime
    token: str


class TestAccountAdminAuthService:
    def __init__(
        self,
        *,
        enabled: bool,
        username: str,
        password_hash: str,
        session_hmac_key: str,
        session_ttl_seconds: int = 7200,
        cookie_name: str = TEST_ACCOUNT_ADMIN_COOKIE_NAME,
        cookie_path: str = "/ops/test-accounts",
        cookie_secure: bool = True,
        limiter: Optional[TestAccountAdminLoginLimiter] = None,
    ) -> None:
        self.enabled = bool(enabled)
        self.username = str(username or "").strip()
        self.password_hash = str(password_hash or "").strip()
        self._session_hmac_key = str(session_hmac_key or "").encode("utf-8")
        self.session_ttl_seconds = max(900, min(int(session_ttl_seconds), 28_800))
        self.cookie_name = str(cookie_name or TEST_ACCOUNT_ADMIN_COOKIE_NAME).strip()
        self.cookie_path = self._cookie_path(cookie_path)
        self.cookie_secure = bool(cookie_secure)
        self.limiter = limiter or TestAccountAdminLoginLimiter()

    @property
    def configured(self) -> bool:
        return bool(
            self.enabled
            and 3 <= len(self.username) <= 80
            and len(self._session_hmac_key) >= 32
            and self.cookie_name
            and self._password_hash_shape_valid()
        )

    def authenticate(
        self,
        *,
        username: str,
        password: str,
        client_key: str,
        now: Optional[datetime] = None,
    ) -> TestAccountAdminSession:
        self._require_configured()
        attempted_at = _utc(now)
        candidate_username = str(username or "").strip()[:128]
        retry_after = self.limiter.retry_after(
            client_key=client_key,
            username=candidate_username,
            now=attempted_at,
        )
        if retry_after:
            raise TestAccountAdminRateLimited(retry_after)

        username_matches = hmac.compare_digest(candidate_username, self.username)
        password_matches = verify_test_account_admin_password(
            password,
            self.password_hash,
        )
        if not username_matches or not password_matches:
            self.limiter.record_failure(
                client_key=client_key,
                username=candidate_username,
                now=attempted_at,
            )
            raise TestAccountAdminAuthenticationFailed(
                "administrator credentials are invalid"
            )

        self.limiter.clear(client_key=client_key, username=candidate_username)
        expires_at = attempted_at + timedelta(seconds=self.session_ttl_seconds)
        payload = {
            "v": TEST_ACCOUNT_ADMIN_SESSION_VERSION,
            "sub": self._subject_hash(),
            "iat": int(attempted_at.timestamp()),
            "exp": int(expires_at.timestamp()),
            "nonce": secrets.token_hex(16),
        }
        encoded_payload = _b64encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        signature = hmac.new(
            self._session_hmac_key,
            f"v1.{encoded_payload}".encode("ascii"),
            hashlib.sha256,
        ).digest()
        return TestAccountAdminSession(
            username=self.username,
            expires_at=expires_at,
            token=f"v1.{encoded_payload}.{_b64encode(signature)}",
        )

    def resolve_session(
        self,
        token: str,
        *,
        now: Optional[datetime] = None,
    ) -> Optional[TestAccountAdminSession]:
        if not self.configured:
            return None
        candidate = str(token or "").strip()
        if not candidate or len(candidate) > 2048:
            return None
        try:
            version, encoded_payload, encoded_signature = candidate.split(".", 2)
            if version != "v1":
                return None
            expected_signature = hmac.new(
                self._session_hmac_key,
                f"v1.{encoded_payload}".encode("ascii"),
                hashlib.sha256,
            ).digest()
            if not hmac.compare_digest(
                expected_signature,
                _b64decode(encoded_signature),
            ):
                return None
            payload = json.loads(_b64decode(encoded_payload).decode("utf-8"))
            if set(payload) != {"v", "sub", "iat", "exp", "nonce"}:
                return None
            if int(payload["v"]) != TEST_ACCOUNT_ADMIN_SESSION_VERSION:
                return None
            if not hmac.compare_digest(str(payload["sub"]), self._subject_hash()):
                return None
            issued_at = datetime.fromtimestamp(int(payload["iat"]), tz=timezone.utc)
            expires_at = datetime.fromtimestamp(int(payload["exp"]), tz=timezone.utc)
            observed_at = _utc(now)
            if issued_at > observed_at + timedelta(seconds=60):
                return None
            if expires_at <= observed_at:
                return None
            if expires_at - issued_at > timedelta(seconds=self.session_ttl_seconds):
                return None
        except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return TestAccountAdminSession(
            username=self.username,
            expires_at=expires_at,
            token=candidate,
        )

    def _require_configured(self) -> None:
        if not self.configured:
            raise TestAccountAdminAuthUnavailable(
                "test account administrator login is unavailable"
            )

    def _subject_hash(self) -> str:
        return hmac.new(
            self._session_hmac_key,
            f"test-account-admin:v1:{self.username}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _password_hash_shape_valid(self) -> bool:
        try:
            scheme, iterations_text, salt_text, digest_text = self.password_hash.split(
                ":", 3
            )
            return bool(
                scheme == TEST_ACCOUNT_ADMIN_PASSWORD_SCHEME
                and TEST_ACCOUNT_ADMIN_PASSWORD_ITERATIONS
                <= int(iterations_text)
                <= 2_000_000
                and len(_b64decode(salt_text)) >= 16
                and len(_b64decode(digest_text)) == 32
            )
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _cookie_path(value: Any) -> str:
        candidate = str(value or "").strip()
        if not candidate.startswith("/") or len(candidate) > 180 or ".." in candidate:
            return "/ops/test-accounts"
        return candidate.rstrip("/") or "/"


def make_test_account_admin_auth_service(
    settings: Any,
    *,
    limiter: Optional[TestAccountAdminLoginLimiter] = None,
) -> TestAccountAdminAuthService:
    return TestAccountAdminAuthService(
        enabled=bool(getattr(settings, "test_account_admin_enabled", False)),
        username=str(getattr(settings, "test_account_admin_username", "") or ""),
        password_hash=str(
            getattr(settings, "test_account_admin_password_hash", "") or ""
        ),
        session_hmac_key=str(
            getattr(settings, "test_account_admin_session_hmac_key", "") or ""
        ),
        session_ttl_seconds=int(
            getattr(settings, "test_account_admin_session_ttl_seconds", 7200)
        ),
        cookie_name=str(
            getattr(
                settings,
                "test_account_admin_cookie_name",
                TEST_ACCOUNT_ADMIN_COOKIE_NAME,
            )
            or TEST_ACCOUNT_ADMIN_COOKIE_NAME
        ),
        cookie_path=str(
            getattr(settings, "test_account_admin_cookie_path", "/ops/test-accounts")
            or "/ops/test-accounts"
        ),
        cookie_secure=bool(
            getattr(settings, "test_account_admin_cookie_secure", True)
        ),
        limiter=limiter,
    )


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    candidate = str(value or "")
    return base64.b64decode(
        candidate + ("=" * (-len(candidate) % 4)),
        altchars=b"-_",
        validate=True,
    )


def _utc(value: Optional[datetime]) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)
