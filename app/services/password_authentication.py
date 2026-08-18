from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app.observability.events import hash_evidence_identifier
from app.services.passwords import (
    make_password_credential,
    password_credential_needs_rehash,
    verify_password,
)


PASSWORD_AUTHENTICATION_CONTRACT_VERSION = 2
PASSWORD_ACTION_GRANT_CONTRACT_VERSION = 1
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128
PASSWORD_LOGIN_MAX_ATTEMPTS = 5
PASSWORD_LOGIN_LOCKOUT_SECONDS = 15 * 60
PASSWORD_ACTION_TTL_SECONDS = 10 * 60
_DUMMY_PASSWORD_CREDENTIAL = make_password_credential(
    "dreamjourney-enumeration-resistant-dummy-password"
)


class PasswordAuthenticationError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        status_code: int,
        retry_after_seconds: Optional[int] = None,
        commit_security_attempt: bool = False,
    ) -> None:
        self.code = code
        self.status_code = int(status_code)
        self.retry_after_seconds = retry_after_seconds
        self.commit_security_attempt = bool(commit_security_attempt)
        super().__init__(code)


class PasswordAuthenticationService:
    """Modern password authentication over the OTP subject/session boundary."""

    def __init__(
        self,
        store: Any,
        *,
        identity_binding_service: Any,
        auth_session_service: Any,
        token_hmac_key: str,
        max_attempts: int = PASSWORD_LOGIN_MAX_ATTEMPTS,
        lockout_seconds: int = PASSWORD_LOGIN_LOCKOUT_SECONDS,
        action_ttl_seconds: int = PASSWORD_ACTION_TTL_SECONDS,
        event_sink: Optional[Any] = None,
        environment: str = "unknown",
        evidence_retention_days: int = 30,
    ) -> None:
        key = str(token_hmac_key or "").encode("utf-8")
        if len(key) < 32:
            raise ValueError("password action token HMAC key must contain at least 32 bytes")
        self.store = store
        self.identity_binding_service = identity_binding_service
        self.auth_session_service = auth_session_service
        self._token_hmac_key = key
        self.max_attempts = max(1, int(max_attempts))
        self.lockout_seconds = max(30, int(lockout_seconds))
        self.action_ttl_seconds = max(60, int(action_ttl_seconds))
        self.event_sink = event_sink
        self.environment = str(environment or "unknown").strip() or "unknown"
        self.evidence_retention_days = max(1, int(evidence_retention_days))
        # A real PBKDF2 comparison is always performed for unknown accounts.
        # The process-level credential avoids deriving a second hash for every
        # request while preserving one full verification cost per attempt.
        self._dummy_credential = _DUMMY_PASSWORD_CREDENTIAL

    def login(
        self,
        *,
        identity_type: str,
        target: str,
        password: str,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        observed_at = self._utc(now)
        candidate_password = str(password or "")
        if not candidate_password or len(candidate_password) > PASSWORD_MAX_LENGTH:
            raise self._authentication_failed()
        try:
            reference = self.identity_binding_service.target_reference(
                identity_type=identity_type,
                target=target,
            )
        except (TypeError, ValueError):
            # Preserve the same public failure as an unknown account.
            verify_password(candidate_password, self._dummy_credential)
            raise self._authentication_failed()

        with self.store.password_target_operation(
            reference["targetHashKeyVersion"],
            reference["targetHash"],
        ):
            state = self.store.get_password_login_state(
                target_hash_key_version=reference["targetHashKeyVersion"],
                target_hash=reference["targetHash"],
            )
            retry_after = self._active_lock_retry_seconds(state, observed_at)
            if retry_after is not None:
                self._record_event(
                    operation="passwordLogin",
                    resource_id=reference["targetHash"],
                    state="denied",
                    reason="temporarilyLocked",
                    observed_at=observed_at,
                )
                raise PasswordAuthenticationError(
                    "password_temporarily_locked",
                    status_code=423,
                    retry_after_seconds=retry_after,
                    commit_security_attempt=True,
                )
            if state is not None and state.get("lockedUntil"):
                self.store.clear_password_login_state(
                    target_hash_key_version=reference["targetHashKeyVersion"],
                    target_hash=reference["targetHash"],
                )

            subject_id = self.identity_binding_service.subject_for_target(
                identity_type=identity_type,
                target=target,
            )
            credential = (
                self.store.get_password_credential(subject_id)
                if subject_id is not None
                else None
            )
            comparison_credential = credential or self._dummy_credential
            valid = verify_password(candidate_password, comparison_credential)
            if not valid or subject_id is None or credential is None:
                locked_until = observed_at + timedelta(seconds=self.lockout_seconds)
                failed_state = self.store.record_password_login_failure(
                    target_hash_key_version=reference["targetHashKeyVersion"],
                    target_hash=reference["targetHash"],
                    attempted_at_iso=observed_at.isoformat(),
                    max_attempts=self.max_attempts,
                    locked_until_iso=locked_until.isoformat(),
                )
                self._record_event(
                    operation="passwordLogin",
                    resource_id=reference["targetHash"],
                    state="denied",
                    reason="authenticationFailed",
                    observed_at=observed_at,
                )
                if failed_state.get("lockedUntil"):
                    raise PasswordAuthenticationError(
                        "password_temporarily_locked",
                        status_code=423,
                        retry_after_seconds=self.lockout_seconds,
                        commit_security_attempt=True,
                    )
                raise self._authentication_failed()

            self.store.clear_password_login_state(
                target_hash_key_version=reference["targetHashKeyVersion"],
                target_hash=reference["targetHash"],
            )
            if password_credential_needs_rehash(credential):
                upgraded = self._credential(
                    candidate_password,
                    revision=int(credential.get("passwordRevision") or 1),
                    now=observed_at,
                    configured_at=str(credential.get("configuredAt") or "") or None,
                )
                upgraded["rehashAt"] = observed_at.isoformat()
                self.store.save_password_credential(subject_id, upgraded)
            auth = self.auth_session_service.issue(subject_id, now=observed_at)

        self._record_event(
            operation="passwordLogin",
            resource_id=subject_id,
            state="succeeded",
            reason="authenticated",
            observed_at=observed_at,
        )
        return {
            "status": "authenticated",
            "user": {"id": subject_id},
            "auth": auth,
            "password": self._public_credential_state(credential),
            "contractVersion": PASSWORD_AUTHENTICATION_CONTRACT_VERSION,
        }

    def issue_action_grant(
        self,
        *,
        verification: Dict[str, Any],
        purpose: str,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        canonical_purpose = self._action_purpose(purpose)
        observed_at = self._utc(now)
        token = secrets.token_urlsafe(32)
        token_hash = self._token_hash(token)
        subject = verification.get("subject")
        subject_id = (
            str(subject.get("subjectId") or "").strip()
            if isinstance(subject, dict)
            else ""
        )
        proof_receipt_id = (
            str(subject.get("proofReceiptId") or "").strip()
            if isinstance(subject, dict)
            else ""
        )
        expires_at = observed_at + timedelta(seconds=self.action_ttl_seconds)
        self.store.save_password_action_grant(
            {
                "grantId": f"pag-{secrets.token_hex(16)}",
                "subjectId": subject_id,
                "purpose": canonical_purpose,
                "tokenHash": token_hash,
                "proofReceiptId": proof_receipt_id,
                "status": "active",
                "expiresAt": expires_at.isoformat(),
                "createdAt": observed_at.isoformat(),
                "updatedAt": observed_at.isoformat(),
                "contractVersion": PASSWORD_ACTION_GRANT_CONTRACT_VERSION,
            }
        )
        self._record_event(
            operation="passwordActionGrant",
            resource_id=subject_id or token_hash,
            state="succeeded",
            reason=canonical_purpose,
            observed_at=observed_at,
        )
        return {
            "status": "verified",
            "action": canonical_purpose,
            "actionToken": token,
            "expiresAt": expires_at.isoformat(),
            "contractVersion": PASSWORD_AUTHENTICATION_CONTRACT_VERSION,
        }

    def setup_password(
        self,
        *,
        principal_user_id: str,
        new_password: str,
        reauth_token: str,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        observed_at = self._utc(now)
        password = self._new_password(new_password)
        grant = self._consume_action_grant(
            token=reauth_token,
            purpose="sensitiveOperation",
            observed_at=observed_at,
        )
        if str(grant.get("subjectId") or "") != principal_user_id:
            self._record_event(
                operation="passwordSetup",
                resource_id=principal_user_id,
                state="denied",
                reason="reauthenticationFailed",
                observed_at=observed_at,
            )
            raise PasswordAuthenticationError(
                "password_reauthentication_failed",
                status_code=401,
                commit_security_attempt=True,
            )
        with self.store.auth_user_operation(principal_user_id):
            if self.store.get_password_credential(principal_user_id) is not None:
                raise PasswordAuthenticationError(
                    "password_already_configured",
                    status_code=409,
                )
            credential = self.store.save_password_credential(
                principal_user_id,
                self._credential(password, revision=1, now=observed_at),
            )
            revocation = self.auth_session_service.revoke_all_for_user(
                principal_user_id,
                reason="passwordConfigured",
            )
            auth = self.auth_session_service.issue(principal_user_id, now=observed_at)
        self._record_event(
            operation="passwordSetup",
            resource_id=principal_user_id,
            state="succeeded",
            reason="configured",
            observed_at=observed_at,
        )
        return self._mutation_response(
            status="configured",
            credential=credential,
            auth=auth,
            revocation=revocation,
        )

    def change_password(
        self,
        *,
        principal_user_id: str,
        current_password: str,
        new_password: str,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        observed_at = self._utc(now)
        password = self._new_password(new_password)
        with self.store.auth_user_operation(principal_user_id):
            credential = self.store.get_password_credential(principal_user_id)
            if credential is None:
                raise PasswordAuthenticationError(
                    "password_not_configured",
                    status_code=409,
                )
            retry_after = self._credential_change_lock_retry_seconds(
                credential,
                observed_at,
            )
            if retry_after is not None:
                raise PasswordAuthenticationError(
                    "password_temporarily_locked",
                    status_code=423,
                    retry_after_seconds=retry_after,
                    commit_security_attempt=True,
                )
            if not verify_password(str(current_password or ""), credential):
                failures = int(credential.get("changeFailedAttempts") or 0) + 1
                credential["changeFailedAttempts"] = failures
                credential["lastChangeFailedAt"] = observed_at.isoformat()
                if failures >= self.max_attempts:
                    credential["changeLockedUntil"] = (
                        observed_at + timedelta(seconds=self.lockout_seconds)
                    ).isoformat()
                self.store.save_password_credential(principal_user_id, credential)
                self._record_event(
                    operation="passwordChange",
                    resource_id=principal_user_id,
                    state="denied",
                    reason="currentCredentialInvalid",
                    observed_at=observed_at,
                )
                raise PasswordAuthenticationError(
                    "password_current_credential_invalid",
                    status_code=401,
                    retry_after_seconds=(
                        self.lockout_seconds if failures >= self.max_attempts else None
                    ),
                    commit_security_attempt=True,
                )
            revision = int(credential.get("passwordRevision") or 1) + 1
            updated = self._credential(
                password,
                revision=revision,
                now=observed_at,
                configured_at=str(credential.get("configuredAt") or "") or None,
            )
            updated["lastChangedAt"] = observed_at.isoformat()
            stored = self.store.save_password_credential(principal_user_id, updated)
            revocation = self.auth_session_service.revoke_all_for_user(
                principal_user_id,
                reason="passwordChanged",
            )
            auth = self.auth_session_service.issue(principal_user_id, now=observed_at)
        self._record_event(
            operation="passwordChange",
            resource_id=principal_user_id,
            state="succeeded",
            reason="changed",
            observed_at=observed_at,
        )
        return self._mutation_response(
            status="changed",
            credential=stored,
            auth=auth,
            revocation=revocation,
        )

    def reset_password(
        self,
        *,
        reset_token: str,
        new_password: str,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        observed_at = self._utc(now)
        password = self._new_password(new_password)
        grant = self._consume_action_grant(
            token=reset_token,
            purpose="passwordReset",
            observed_at=observed_at,
        )
        subject_id = str(grant.get("subjectId") or "").strip()
        if subject_id:
            with self.store.auth_user_operation(subject_id):
                existing = self.store.get_password_credential(subject_id)
                revision = int((existing or {}).get("passwordRevision") or 0) + 1
                receipt_id = f"prr-{secrets.token_hex(16)}"
                credential = self._credential(
                    password,
                    revision=revision,
                    now=observed_at,
                    configured_at=(
                        str((existing or {}).get("configuredAt") or "") or None
                    ),
                )
                credential["lastResetReceipt"] = {
                    "receiptId": receipt_id,
                    "grantId": str(grant.get("grantId") or ""),
                    "consumedAt": observed_at.isoformat(),
                    "passwordRevision": revision,
                }
                self.store.save_password_credential(subject_id, credential)
                self.auth_session_service.revoke_all_for_user(
                    subject_id,
                    reason="passwordReset",
                )
        self._record_event(
            operation="passwordReset",
            resource_id=subject_id or str(grant.get("grantId") or ""),
            state="succeeded",
            reason="accepted",
            observed_at=observed_at,
        )
        # The response is deliberately identical for bound and unbound OTPs.
        return {
            "status": "resetAccepted",
            "loginRequired": True,
            "contractVersion": PASSWORD_AUTHENTICATION_CONTRACT_VERSION,
        }

    def _consume_action_grant(
        self,
        *,
        token: str,
        purpose: str,
        observed_at: datetime,
    ) -> Dict[str, Any]:
        candidate = str(token or "").strip()
        if not candidate or len(candidate) > 256:
            raise self._invalid_action_grant(purpose)
        result = self.store.consume_password_action_grant(
            token_hash=self._token_hash(candidate),
            purpose=purpose,
            consumed_at_iso=observed_at.isoformat(),
        )
        if result.get("outcome") != "consumedNow":
            self._record_event(
                operation="passwordActionConsume",
                resource_id=self._token_hash(candidate),
                state="denied",
                reason=str(result.get("outcome") or "invalid"),
                observed_at=observed_at,
            )
            raise self._invalid_action_grant(purpose)
        return dict(result.get("grant") or {})

    @staticmethod
    def _new_password(value: str) -> str:
        candidate = str(value or "")
        if (
            len(candidate) < PASSWORD_MIN_LENGTH
            or len(candidate) > PASSWORD_MAX_LENGTH
            or not candidate.strip()
        ):
            raise PasswordAuthenticationError(
                "password_policy_rejected",
                status_code=400,
            )
        return candidate

    @staticmethod
    def _credential(
        password: str,
        *,
        revision: int,
        now: datetime,
        configured_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        credential = make_password_credential(password)
        credential.update(
            {
                "credentialSchemaVersion": 2,
                "passwordRevision": max(1, int(revision)),
                "configuredAt": configured_at or now.isoformat(),
                "updatedAt": now.isoformat(),
                "changeFailedAttempts": 0,
                "changeLockedUntil": None,
            }
        )
        return credential

    @staticmethod
    def _public_credential_state(credential: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "configured": True,
            "passwordRevision": int(credential.get("passwordRevision") or 1),
            "contractVersion": PASSWORD_AUTHENTICATION_CONTRACT_VERSION,
        }

    def _mutation_response(
        self,
        *,
        status: str,
        credential: Dict[str, Any],
        auth: Dict[str, Any],
        revocation: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "status": status,
            "password": self._public_credential_state(credential),
            "auth": auth,
            "sessionRevocation": revocation,
            "contractVersion": PASSWORD_AUTHENTICATION_CONTRACT_VERSION,
        }

    def _active_lock_retry_seconds(
        self,
        state: Optional[Dict[str, Any]],
        now: datetime,
    ) -> Optional[int]:
        locked_until = str((state or {}).get("lockedUntil") or "").strip()
        if not locked_until:
            return None
        expiry = self._utc_from_text(locked_until)
        if expiry <= now:
            return None
        return max(1, int((expiry - now).total_seconds()))

    def _credential_change_lock_retry_seconds(
        self,
        credential: Dict[str, Any],
        now: datetime,
    ) -> Optional[int]:
        locked_until = str(credential.get("changeLockedUntil") or "").strip()
        if not locked_until:
            return None
        expiry = self._utc_from_text(locked_until)
        if expiry <= now:
            credential["changeFailedAttempts"] = 0
            credential["changeLockedUntil"] = None
            return None
        return max(1, int((expiry - now).total_seconds()))

    def _token_hash(self, token: str) -> str:
        return hmac.new(
            self._token_hmac_key,
            f"password-action:v1:{token}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _action_purpose(value: str) -> str:
        normalized = str(value or "").strip().lower()
        mapping = {
            "passwordreset": "passwordReset",
            "sensitiveoperation": "sensitiveOperation",
        }
        if normalized not in mapping:
            raise PasswordAuthenticationError(
                "password_action_purpose_invalid",
                status_code=400,
            )
        return mapping[normalized]

    @staticmethod
    def _authentication_failed() -> PasswordAuthenticationError:
        return PasswordAuthenticationError(
            "password_authentication_failed",
            status_code=401,
            commit_security_attempt=True,
        )

    @staticmethod
    def _invalid_action_grant(purpose: str) -> PasswordAuthenticationError:
        code = (
            "password_reset_token_invalid_or_expired"
            if purpose == "passwordReset"
            else "password_reauthentication_failed"
        )
        return PasswordAuthenticationError(
            code,
            status_code=401,
            commit_security_attempt=True,
        )

    def _record_event(
        self,
        *,
        operation: str,
        resource_id: str,
        state: str,
        reason: str,
        observed_at: datetime,
    ) -> None:
        if not callable(self.event_sink):
            return
        route = {
            "passwordLogin": "POST /v2/auth/password/login",
            "passwordActionGrant": "POST /v2/auth/challenges/{challenge_id}/verify",
            "passwordActionConsume": "POST /v2/auth/password/reset",
            "passwordSetup": "POST /v2/auth/password/setup",
            "passwordChange": "POST /v2/auth/password/change",
            "passwordReset": "POST /v2/auth/password/reset",
        }.get(operation, "POST /v2/auth/password/change")
        event_id = f"evt-{secrets.token_hex(16)}"
        self.event_sink(
            {
                "type": "operation",
                "eventId": event_id,
                "schemaVersion": 1,
                "operationId": f"op-{secrets.token_hex(16)}",
                "correlationId": None,
                "principalHash": None,
                "resourceType": "passwordAuthentication",
                "resourceIdHash": hash_evidence_identifier(resource_id),
                "state": state,
                "reason": reason,
                "attempt": 1,
                "operation": operation,
                "route": route,
                "feature": "passwordAuthentication",
                "decision": reason,
                "occurredAt": observed_at.isoformat(),
                "env": self.environment,
                "build": "backend",
                "redactionVersion": 1,
            },
            retention_class="operationalTemporary",
            expires_at_iso=(
                observed_at + timedelta(days=self.evidence_retention_days)
            ).isoformat(),
        )

    @staticmethod
    def _utc(value: Optional[datetime]) -> datetime:
        candidate = value or datetime.now(timezone.utc)
        if candidate.tzinfo is None:
            candidate = candidate.replace(tzinfo=timezone.utc)
        return candidate.astimezone(timezone.utc)

    @staticmethod
    def _utc_from_text(value: str) -> datetime:
        candidate = str(value or "").replace("Z", "+00:00")
        parsed = datetime.fromisoformat(candidate)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)


def password_authentication_runtime_descriptor(
    *,
    configured: bool,
    production_recovery_ready: bool,
    test_recovery_ready: bool,
) -> Dict[str, Any]:
    recovery_ready = bool(configured and production_recovery_ready)
    test_only_recovery_ready = bool(
        configured and not recovery_ready and test_recovery_ready
    )
    return {
        "implemented": True,
        "enabled": bool(configured),
        "ready": bool(configured),
        "reason": "ready" if configured else "identityBindingKeyMissing",
        "loginReady": bool(configured),
        "changeReady": bool(configured),
        "setupReady": recovery_ready,
        "resetReady": recovery_ready,
        "reauthReady": recovery_ready,
        "testRecoveryReady": test_only_recovery_ready,
        "recoveryMode": (
            "production"
            if recovery_ready
            else ("testAllowlist" if test_only_recovery_ready else "unavailable")
        ),
        "recoveryReason": (
            "ready"
            if recovery_ready
            else (
                "testAllowlistOnly"
                if test_only_recovery_ready
                else (
                    "identityChallengeUnavailable"
                    if configured
                    else "passwordAuthenticationUnavailable"
                )
            )
        ),
        "loginEndpoint": "/v2/auth/password/login",
        "setupEndpoint": "/v2/auth/password/setup",
        "changeEndpoint": "/v2/auth/password/change",
        "resetEndpoint": "/v2/auth/password/reset",
        "resetChallengePurpose": "passwordReset",
        "reauthChallengePurpose": "sensitiveOperation",
        "minLength": PASSWORD_MIN_LENGTH,
        "maxLength": PASSWORD_MAX_LENGTH,
        "maxAttempts": PASSWORD_LOGIN_MAX_ATTEMPTS,
        "lockoutSeconds": PASSWORD_LOGIN_LOCKOUT_SECONDS,
        "sessionContractVersion": 2,
        "contractVersion": PASSWORD_AUTHENTICATION_CONTRACT_VERSION,
    }


__all__ = [
    "PASSWORD_ACTION_TTL_SECONDS",
    "PASSWORD_AUTHENTICATION_CONTRACT_VERSION",
    "PASSWORD_LOGIN_LOCKOUT_SECONDS",
    "PASSWORD_LOGIN_MAX_ATTEMPTS",
    "PASSWORD_MAX_LENGTH",
    "PASSWORD_MIN_LENGTH",
    "PasswordAuthenticationError",
    "PasswordAuthenticationService",
    "password_authentication_runtime_descriptor",
]
