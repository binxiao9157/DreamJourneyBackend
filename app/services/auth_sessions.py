import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional


AUTH_SESSION_CONTRACT_VERSION = 1


class AuthSessionError(ValueError):
    pass


def auth_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthSessionService:
    def __init__(
        self,
        store: Any,
        *,
        access_ttl_seconds: int,
        refresh_ttl_seconds: int,
    ):
        self.store = store
        self.access_ttl_seconds = max(1, int(access_ttl_seconds))
        self.refresh_ttl_seconds = max(self.access_ttl_seconds + 1, int(refresh_ttl_seconds))

    def issue(self, user_id: str, *, now: Optional[datetime] = None) -> Dict[str, Any]:
        issued_at = self._utc(now)
        access_expires_at = issued_at + timedelta(seconds=self.access_ttl_seconds)
        refresh_expires_at = issued_at + timedelta(seconds=self.refresh_ttl_seconds)
        access_token = "dja_" + secrets.token_urlsafe(32)
        refresh_token = "djr_" + secrets.token_urlsafe(40)
        session_id = "auth_" + secrets.token_urlsafe(18)

        record = {
            "sessionId": session_id,
            "userId": user_id,
            "accessTokenHash": auth_token_hash(access_token),
            "refreshTokenHash": auth_token_hash(refresh_token),
            "status": "active",
            "createdAt": issued_at.isoformat(),
            "accessExpiresAt": access_expires_at.isoformat(),
            "refreshExpiresAt": refresh_expires_at.isoformat(),
            "contractVersion": AUTH_SESSION_CONTRACT_VERSION,
        }
        self.store.save_auth_session(record)
        return {
            "sessionId": session_id,
            "userId": user_id,
            "tokenType": "Bearer",
            "accessToken": access_token,
            "refreshToken": refresh_token,
            "accessExpiresInSeconds": self.access_ttl_seconds,
            "refreshExpiresInSeconds": self.refresh_ttl_seconds,
            "accessExpiresAt": access_expires_at.isoformat(),
            "refreshExpiresAt": refresh_expires_at.isoformat(),
            "contractVersion": AUTH_SESSION_CONTRACT_VERSION,
        }

    def resolve_access_token(
        self,
        access_token: str,
        *,
        now: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        token = str(access_token or "").strip()
        if not token:
            return None
        record = self.store.get_auth_session_by_access_token_hash(auth_token_hash(token))
        if record is None or record.get("status") != "active":
            return None
        if self._parse_datetime(record.get("accessExpiresAt")) <= self._utc(now):
            return None
        return record

    def refresh(self, refresh_token: str, *, now: Optional[datetime] = None) -> Dict[str, Any]:
        token = str(refresh_token or "").strip()
        if not token:
            raise AuthSessionError("refresh token is required")
        refreshed_at = self._utc(now)
        record = self.store.consume_auth_session_refresh(
            auth_token_hash(token),
            refreshed_at.isoformat(),
        )
        if record is None:
            raise AuthSessionError("invalid or expired refresh token")
        if self._parse_datetime(record.get("refreshExpiresAt")) <= refreshed_at:
            raise AuthSessionError("invalid or expired refresh token")
        return self.issue(str(record.get("userId") or ""), now=refreshed_at)

    def revoke_access_token(
        self,
        access_token: str,
        *,
        reason: str = "logout",
        now: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        token = str(access_token or "").strip()
        if not token:
            return None
        return self.store.revoke_auth_session_by_access_token_hash(
            auth_token_hash(token),
            self._utc(now).isoformat(),
            reason,
        )

    @staticmethod
    def _utc(value: Optional[datetime]) -> datetime:
        result = value or datetime.now(timezone.utc)
        if result.tzinfo is None:
            return result.replace(tzinfo=timezone.utc)
        return result.astimezone(timezone.utc)

    @staticmethod
    def _parse_datetime(value: Any) -> datetime:
        text = str(value or "").strip()
        if not text:
            return datetime.min.replace(tzinfo=timezone.utc)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
