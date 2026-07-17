import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional


AUTH_SESSION_CONTRACT_VERSION = 2
AUTH_TOKEN_FAMILY_CONTRACT_VERSION = 1


class AuthSessionError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "invalid_refresh_token",
        commit_state_change: bool = False,
    ):
        super().__init__(message)
        self.code = code
        self.commit_state_change = bool(commit_state_change)


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
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id:
            raise AuthSessionError("user id is required", code="user_id_required")
        with self.store.auth_user_operation(normalized_user_id):
            get_user = getattr(self.store, "get_user", None)
            account = get_user(normalized_user_id) if callable(get_user) else None
            account_state = str((account or {}).get("deletionState") or "active")
            if account_state != "active":
                raise AuthSessionError(
                    "account cannot issue a session in its current state",
                    code="account_session_issuance_blocked",
                )

            issued_at = self._utc(now)
            token_family_id = self._opaque_id("tf", 18)
            public, record = self._new_session(
                user_id=normalized_user_id,
                token_family_id=token_family_id,
                session_version=1,
                parent_session_id=None,
                issued_at=issued_at,
            )
            family = {
                "tokenFamilyId": token_family_id,
                "userId": record["userId"],
                "status": "active",
                "currentSessionVersion": 1,
                "contractVersion": AUTH_TOKEN_FAMILY_CONTRACT_VERSION,
                "createdAt": issued_at.isoformat(),
                "updatedAt": issued_at.isoformat(),
            }
            receipt_id = self._opaque_id("ase", 18)
            self.store.create_auth_token_family(
                family,
                record,
                self._session_event(
                    receipt_id,
                    family=family,
                    session=record,
                    event_type="sessionIssued",
                    reason="identityVerified",
                    occurred_at=issued_at,
                ),
            )
            public["sessionReceiptId"] = receipt_id
            return public

    def resolve_access_token(
        self,
        access_token: str,
        *,
        now: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        token = str(access_token or "").strip()
        if not token or len(token) > 256 or not token.startswith("dja_"):
            return None
        record = self.store.get_auth_session_by_access_token_hash(auth_token_hash(token))
        if record is None or record.get("status") != "active":
            return None
        if record.get("tokenFamilyId") and record.get("familyStatus") != "active":
            return None
        try:
            expires_at = self._parse_datetime(record.get("accessExpiresAt"))
        except (TypeError, ValueError):
            return None
        if expires_at <= self._utc(now):
            return None
        return record

    def refresh(self, refresh_token: str, *, now: Optional[datetime] = None) -> Dict[str, Any]:
        token = str(refresh_token or "").strip()
        if not token:
            raise AuthSessionError(
                "refresh token is required",
                code="refresh_token_required",
            )
        if len(token) > 256 or not token.startswith("djr_"):
            raise AuthSessionError(
                "invalid or expired refresh token",
                code="invalid_or_expired_refresh_token",
            )
        refreshed_at = self._utc(now)
        successor_access = "dja_" + secrets.token_urlsafe(32)
        successor_refresh = "djr_" + secrets.token_urlsafe(40)
        successor = {
            "sessionId": self._opaque_id("auth", 18),
            "accessTokenHash": auth_token_hash(successor_access),
            "refreshTokenHash": auth_token_hash(successor_refresh),
            "status": "active",
            "createdAt": refreshed_at.isoformat(),
            "accessExpiresAt": (
                refreshed_at + timedelta(seconds=self.access_ttl_seconds)
            ).isoformat(),
            "refreshExpiresAt": (
                refreshed_at + timedelta(seconds=self.refresh_ttl_seconds)
            ).isoformat(),
            "contractVersion": AUTH_SESSION_CONTRACT_VERSION,
        }
        rotation_receipt_id = self._opaque_id("ase", 18)
        reuse_receipt_id = self._opaque_id("ase", 18)
        result = self.store.rotate_auth_session_refresh(
            auth_token_hash(token),
            successor=successor,
            rotated_at_iso=refreshed_at.isoformat(),
            rotation_receipt_id=rotation_receipt_id,
            reuse_receipt_id=reuse_receipt_id,
        )
        outcome = str(result.get("outcome") or "invalid")
        if outcome == "legacyReauthRequired":
            raise AuthSessionError(
                "legacy session requires reauthentication",
                code="legacy_session_reauth_required",
            )
        if outcome == "reuseDetected":
            raise AuthSessionError(
                "refresh token reuse detected",
                code="refresh_token_reuse_detected",
                commit_state_change=True,
            )
        if outcome == "expired":
            raise AuthSessionError(
                "invalid or expired refresh token",
                code="invalid_or_expired_refresh_token",
                commit_state_change=True,
            )
        if outcome != "rotated":
            raise AuthSessionError(
                "invalid or expired refresh token",
                code="invalid_or_expired_refresh_token",
            )

        record = dict(result["session"])
        return {
            **self._public_session(record),
            "accessToken": successor_access,
            "refreshToken": successor_refresh,
            "rotationReceiptId": rotation_receipt_id,
        }

    def revoke_access_token(
        self,
        access_token: str,
        *,
        scope: str = "session",
        reason: str = "logout",
        now: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        token = str(access_token or "").strip()
        if not token or len(token) > 256 or not token.startswith("dja_"):
            return None
        normalized_scope = str(scope or "session").strip()
        revoked_at_iso = self._utc(now).isoformat()
        receipt_id = self._opaque_id("ase", 18)
        token_hash = auth_token_hash(token)
        if normalized_scope == "session":
            return self.store.revoke_auth_session_by_access_token_hash(
                token_hash,
                revoked_at_iso,
                reason,
                receipt_id=receipt_id,
            )
        if normalized_scope == "family":
            return self.store.revoke_auth_token_family_by_access_token_hash(
                token_hash,
                revoked_at_iso,
                reason,
                receipt_id=receipt_id,
            )
        if normalized_scope == "allDevices":
            current = self.resolve_access_token(token, now=now)
            if current is None:
                return None
            user_id = str(current.get("userId") or "")
            with self.store.auth_user_operation(user_id):
                locked_current = self.resolve_access_token(token, now=now)
                if (
                    locked_current is None
                    or str(locked_current.get("userId") or "") != user_id
                ):
                    return None
                get_user = getattr(self.store, "get_user", None)
                account = get_user(user_id) if callable(get_user) else None
                if str((account or {}).get("deletionState") or "active") == "purged":
                    return None
                return self.revoke_all_for_user(
                    user_id,
                    reason=reason,
                    now=now,
                    receipt_id=receipt_id,
                )
        raise AuthSessionError(
            "unsupported revocation scope",
            code="unsupported_revocation_scope",
        )

    def revoke_all_for_user(
        self,
        user_id: str,
        *,
        reason: str,
        now: Optional[datetime] = None,
        receipt_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id:
            raise AuthSessionError("user id is required", code="user_id_required")
        return self.store.revoke_all_auth_token_families(
            normalized_user_id,
            self._utc(now).isoformat(),
            str(reason or "securityEvent").strip()[:80] or "securityEvent",
            receipt_id=receipt_id or self._opaque_id("ase", 18),
        )

    def _new_session(
        self,
        *,
        user_id: str,
        token_family_id: str,
        session_version: int,
        parent_session_id: Optional[str],
        issued_at: datetime,
    ):
        if not user_id:
            raise AuthSessionError("user id is required", code="user_id_required")
        access_token = "dja_" + secrets.token_urlsafe(32)
        refresh_token = "djr_" + secrets.token_urlsafe(40)
        record = {
            "sessionId": self._opaque_id("auth", 18),
            "userId": user_id,
            "tokenFamilyId": token_family_id,
            "parentSessionId": parent_session_id,
            "sessionVersion": session_version,
            "accessTokenHash": auth_token_hash(access_token),
            "refreshTokenHash": auth_token_hash(refresh_token),
            "status": "active",
            "createdAt": issued_at.isoformat(),
            "accessExpiresAt": (
                issued_at + timedelta(seconds=self.access_ttl_seconds)
            ).isoformat(),
            "refreshExpiresAt": (
                issued_at + timedelta(seconds=self.refresh_ttl_seconds)
            ).isoformat(),
            "contractVersion": AUTH_SESSION_CONTRACT_VERSION,
        }
        return (
            {
                **self._public_session(record),
                "accessToken": access_token,
                "refreshToken": refresh_token,
            },
            record,
        )

    def _public_session(self, record: Dict[str, Any]) -> Dict[str, Any]:
        public = {
            "sessionId": str(record["sessionId"]),
            "userId": str(record["userId"]),
            "subjectId": str(record["userId"]),
            "tokenFamilyId": str(record["tokenFamilyId"]),
            "sessionVersion": int(record["sessionVersion"]),
            "tokenType": "Bearer",
            "accessExpiresInSeconds": self.access_ttl_seconds,
            "refreshExpiresInSeconds": self.refresh_ttl_seconds,
            "accessExpiresAt": str(record["accessExpiresAt"]),
            "refreshExpiresAt": str(record["refreshExpiresAt"]),
            "contractVersion": AUTH_SESSION_CONTRACT_VERSION,
        }
        parent_session_id = str(record.get("parentSessionId") or "").strip()
        if parent_session_id:
            public["parentSessionId"] = parent_session_id
        return public

    @staticmethod
    def _session_event(
        event_id: str,
        *,
        family: Dict[str, Any],
        session: Dict[str, Any],
        event_type: str,
        reason: str,
        occurred_at: datetime,
    ) -> Dict[str, Any]:
        return {
            "eventId": event_id,
            "tokenFamilyId": family["tokenFamilyId"],
            "sessionId": session.get("sessionId"),
            "userId": family["userId"],
            "eventType": event_type,
            "reason": reason,
            "sessionVersion": int(session.get("sessionVersion") or 0),
            "occurredAt": occurred_at.isoformat(),
            "contractVersion": AUTH_TOKEN_FAMILY_CONTRACT_VERSION,
        }

    @staticmethod
    def _opaque_id(prefix: str, size: int) -> str:
        return f"{prefix}_" + secrets.token_urlsafe(size)

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
