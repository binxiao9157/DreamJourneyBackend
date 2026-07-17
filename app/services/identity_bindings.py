import hashlib
import hmac
import math
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app.core.config import Settings
from app.observability.events import hash_evidence_identifier


IDENTITY_BINDING_CONTRACT_VERSION = 1
IDENTITY_CHALLENGE_PURPOSES = {"login", "register", "restore", "invitation"}
INTERNAL_ADAPTER_ENVIRONMENTS = {"development", "local", "test", "testing"}


class IdentityChallengeConfigurationError(RuntimeError):
    pass


class IdentityChallengeValidationError(ValueError):
    pass


class IdentityChallengeVerificationFailed(ValueError):
    def __init__(self) -> None:
        super().__init__("challenge could not be verified")


class IdentityChallengeRateLimited(ValueError):
    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = max(1, int(retry_after_seconds))
        super().__init__("identity challenge rate limited")


class IdentityChallengeAdapter:
    provider_mode = "unavailable"
    internal_verification_enabled = False
    production_ready = False

    def verification_code(self) -> str:
        return f"{secrets.randbelow(1_000_000):06d}"


class SyntheticIdentityChallengeAdapter(IdentityChallengeAdapter):
    provider_mode = "synthetic"
    internal_verification_enabled = True
    production_ready = False

    def __init__(self, code: str):
        candidate = str(code or "").strip()
        if not candidate or len(candidate) > 32:
            raise IdentityChallengeConfigurationError(
                "synthetic identity challenge code must be configured"
            )
        self._code = candidate

    def verification_code(self) -> str:
        return self._code


class UnavailableIdentityChallengeAdapter(IdentityChallengeAdapter):
    pass


def make_identity_challenge_adapter(settings: Settings) -> IdentityChallengeAdapter:
    requested = str(settings.identity_challenge_adapter or "disabled").strip().lower()
    environment = str(settings.environment or "development").strip().lower()
    if (
        requested in {"synthetic", "test"}
        and environment in INTERNAL_ADAPTER_ENVIRONMENTS
        and settings.identity_challenge_synthetic_code
    ):
        return SyntheticIdentityChallengeAdapter(
            settings.identity_challenge_synthetic_code
        )
    return UnavailableIdentityChallengeAdapter()


def identity_challenge_runtime_descriptor(settings: Settings) -> Dict[str, Any]:
    adapter = make_identity_challenge_adapter(settings)
    key_configured = len(str(settings.identity_binding_hmac_key or "").encode("utf-8")) >= 32
    try:
        hash_key_version = _hash_key_version(settings.identity_binding_hmac_key_version)
        key_version_valid = True
    except IdentityChallengeConfigurationError:
        hash_key_version = "invalid"
        key_version_valid = False
    internal_enabled = (
        adapter.internal_verification_enabled
        and key_configured
        and key_version_valid
    )
    challenge_enabled = bool(
        (adapter.internal_verification_enabled or adapter.production_ready)
        and key_configured
        and key_version_valid
    )
    return {
        "enabled": challenge_enabled,
        "challengeEndpoint": "/v2/auth/challenges",
        "verifyEndpointTemplate": "/v2/auth/challenges/{challengeId}/verify",
        "providerMode": adapter.provider_mode,
        "internalVerificationEnabled": internal_enabled,
        "productionReady": bool(
            adapter.production_ready and key_configured and key_version_valid
        ),
        "clientFlowEnabled": challenge_enabled,
        "deliverySemantics": "acceptedOnly",
        "challengeTTLSeconds": max(30, int(settings.identity_challenge_ttl_seconds)),
        "maxAttempts": max(1, int(settings.identity_challenge_max_attempts)),
        "retryAfterSeconds": max(1, int(settings.identity_challenge_retry_after_seconds)),
        "hashKeyVersion": hash_key_version,
        "legacyPhoneLoginEnabled": legacy_phone_login_enabled(settings),
        "contractVersion": IDENTITY_BINDING_CONTRACT_VERSION,
    }


def legacy_phone_login_enabled(settings: Settings) -> bool:
    environment = str(settings.environment or "development").strip().lower()
    return bool(
        settings.auth_legacy_phone_login_enabled
        and environment in INTERNAL_ADAPTER_ENVIRONMENTS
    )


def _hash_key_version(value: Any) -> str:
    candidate = str(value or "v1").strip()
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}", candidate) is None:
        raise IdentityChallengeConfigurationError(
            "identity binding HMAC key version is invalid"
        )
    return candidate


class IdentityBindingService:
    def __init__(
        self,
        store: Any,
        *,
        hmac_key: str,
        hmac_key_version: str,
        adapter: IdentityChallengeAdapter,
        challenge_ttl_seconds: int,
        max_attempts: int,
        retry_after_seconds: int = 30,
        auth_session_service: Optional[Any] = None,
        event_sink: Optional[Any] = None,
        environment: str = "test",
        evidence_retention_days: int = 30,
    ):
        key_bytes = str(hmac_key or "").encode("utf-8")
        if len(key_bytes) < 32:
            raise IdentityChallengeConfigurationError(
                "identity binding HMAC key must contain at least 32 bytes"
            )
        self.store = store
        self._hmac_key = key_bytes
        self.hmac_key_version = _hash_key_version(hmac_key_version)
        self._hmac_key_fingerprint = hmac.new(
            self._hmac_key,
            b"dreamjourney:identity-binding-key-fingerprint:v1",
            hashlib.sha256,
        ).hexdigest()
        self.adapter = adapter
        self.challenge_ttl_seconds = max(30, int(challenge_ttl_seconds))
        self.max_attempts = max(1, int(max_attempts))
        self.retry_after_seconds = max(1, int(retry_after_seconds))
        self.auth_session_service = auth_session_service
        self.event_sink = event_sink
        self.environment = self._machine_code(environment, fallback="unknown")
        self.evidence_retention_days = max(1, int(evidence_retention_days))

    def create_challenge(
        self,
        *,
        identity_type: str,
        target: str,
        purpose: str,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        if not (
            self.adapter.internal_verification_enabled
            or self.adapter.production_ready
        ):
            raise IdentityChallengeConfigurationError(
                "identity challenge provider is unavailable"
            )
        key_state = self.store.ensure_identity_hash_key_version(
            self.hmac_key_version,
            self._hmac_key_fingerprint,
        )
        if key_state.get("outcome") != "ready":
            raise IdentityChallengeConfigurationError(
                "identity binding HMAC key registration is not ready"
            )
        normalized_type = self._identity_type(identity_type)
        normalized_target = self._target(normalized_type, target)
        normalized_purpose = str(purpose or "login").strip().lower() or "login"
        if normalized_purpose not in IDENTITY_CHALLENGE_PURPOSES:
            raise IdentityChallengeValidationError("unsupported identity challenge purpose")

        created_at = self._utc(now)
        expires_at = created_at + timedelta(seconds=self.challenge_ttl_seconds)
        target_hash = self._keyed_hash(
            f"target:{self.hmac_key_version}:{normalized_type}:{normalized_target}"
        )
        latest = self.store.get_latest_auth_challenge(
            identity_type=normalized_type,
            target_hash_key_version=self.hmac_key_version,
            target_hash=target_hash,
            purpose=normalized_purpose,
        )
        if latest is not None:
            retry_at = self._utc_from_text(latest.get("createdAt")) + timedelta(
                seconds=self.retry_after_seconds
            )
            if retry_at > created_at:
                self._record_event(
                    operation_id=self._opaque_id("op"),
                    resource_id=target_hash,
                    state="denied",
                    reason="rateLimited",
                    decision="createDenied",
                    occurred_at=created_at,
                )
                raise IdentityChallengeRateLimited(
                    math.ceil((retry_at - created_at).total_seconds())
                )
        challenge_id = self._opaque_id("ach")
        verification_code = self.adapter.verification_code()
        record = {
            "challengeId": challenge_id,
            "identityType": normalized_type,
            "targetHashKeyVersion": self.hmac_key_version,
            "targetHash": target_hash,
            "codeHash": self._keyed_hash(
                f"code:v1:{challenge_id}:{verification_code}"
            ),
            "providerMode": self.adapter.provider_mode,
            "purpose": normalized_purpose,
            "status": "active",
            "attempts": 0,
            "maxAttempts": self.max_attempts,
            "internalVerificationEnabled": bool(
                self.adapter.internal_verification_enabled
            ),
            "createdAt": created_at.isoformat(),
            "expiresAt": expires_at.isoformat(),
        }
        self.store.save_auth_challenge(record)
        self._record_event(
            operation_id=challenge_id,
            resource_id=challenge_id,
            state="succeeded",
            reason="accepted",
            decision="createAccepted",
            occurred_at=created_at,
        )
        return {
            "status": "accepted",
            "challenge": {
                "challengeId": challenge_id,
                "purpose": normalized_purpose,
                "deliveryMode": "acceptedOnly",
                "expiresAt": expires_at.isoformat(),
                "retryAfterSeconds": self.retry_after_seconds,
                "productionReady": bool(self.adapter.production_ready),
                "contractVersion": IDENTITY_BINDING_CONTRACT_VERSION,
            },
        }

    def verify_challenge(
        self,
        challenge_id: str,
        code: str,
        *,
        nickname: str = "",
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        if not (
            self.adapter.internal_verification_enabled
            or self.adapter.production_ready
        ):
            raise IdentityChallengeConfigurationError(
                "identity challenge provider is unavailable"
            )
        key_state = self.store.ensure_identity_hash_key_version(
            self.hmac_key_version,
            self._hmac_key_fingerprint,
        )
        if key_state.get("outcome") != "ready":
            raise IdentityChallengeConfigurationError(
                "identity binding HMAC key registration is not ready"
            )
        normalized_challenge_id = str(challenge_id or "").strip()
        attempted_at = self._utc(now)
        candidate_code = str(code or "").strip()
        if not normalized_challenge_id or len(normalized_challenge_id) > 160:
            raise IdentityChallengeVerificationFailed()
        persisted_challenge = self.store.get_auth_challenge(normalized_challenge_id)
        if persisted_challenge is None or (
            persisted_challenge.get("providerMode") != self.adapter.provider_mode
            or persisted_challenge.get("targetHashKeyVersion")
            != self.hmac_key_version
        ):
            raise IdentityChallengeVerificationFailed()
        if len(candidate_code) > 128:
            candidate_code = "invalid-oversized-code"
        code_hash = self._keyed_hash(
            f"code:v1:{normalized_challenge_id}:{candidate_code}"
        )
        result = self.store.verify_auth_challenge(
            normalized_challenge_id,
            code_hash=code_hash,
            attempted_at_iso=attempted_at.isoformat(),
            subject_id=self._opaque_id("sub"),
            binding_id=self._opaque_id("idb"),
            proof_id=self._opaque_id("idp"),
        )
        if result.get("outcome") != "verified":
            self._record_event(
                operation_id=normalized_challenge_id or self._opaque_id("op"),
                resource_id=normalized_challenge_id or "missingChallenge",
                state="denied",
                reason="challengeVerificationFailed",
                decision="verifyDenied",
                occurred_at=attempted_at,
            )
            raise IdentityChallengeVerificationFailed()

        subject_id = str(result["subjectId"])
        response = {
            "status": "verified",
            "subject": {
                "subjectId": subject_id,
                "bindingId": str(result["bindingId"]),
                "proofReceiptId": str(result["proofReceiptId"]),
                "contractVersion": IDENTITY_BINDING_CONTRACT_VERSION,
            },
            "user": {
                "id": subject_id,
                "nickname": str(nickname or "").strip()[:80],
            },
            "contractVersion": IDENTITY_BINDING_CONTRACT_VERSION,
        }
        if self.auth_session_service is not None:
            response["auth"] = self.auth_session_service.issue(
                subject_id,
                now=attempted_at,
            )
        self._record_event(
            operation_id=normalized_challenge_id,
            resource_id=normalized_challenge_id,
            state="succeeded",
            reason="verified",
            decision="verifyAccepted",
            occurred_at=attempted_at,
        )
        return response

    def _record_event(
        self,
        *,
        operation_id: str,
        resource_id: str,
        state: str,
        reason: str,
        decision: str,
        occurred_at: datetime,
    ) -> None:
        if not callable(self.event_sink):
            return
        event_nonce = secrets.token_hex(16)
        expires_at = occurred_at + timedelta(days=self.evidence_retention_days)
        self.event_sink(
            {
                "type": "operation",
                "eventId": f"evt-{event_nonce}",
                "schemaVersion": 1,
                "operationId": self._machine_code(operation_id, fallback=f"op-{event_nonce}"),
                "correlationId": None,
                "principalHash": None,
                "resourceType": "identityChallenge",
                "resourceIdHash": hash_evidence_identifier(resource_id),
                "state": state,
                "reason": reason,
                "attempt": 1,
                "occurredAt": occurred_at.isoformat(),
                "env": self.environment,
                "build": "backend",
                "redactionVersion": 1,
                "operation": "identityChallenge",
                "route": (
                    "POST /v2/auth/challenges"
                    if decision.startswith("create")
                    else "POST /v2/auth/challenges/{challenge_id}/verify"
                ),
                "feature": "strongIdentity",
                "decision": decision,
            },
            retention_class="operationalTemporary",
            expires_at_iso=expires_at.isoformat(),
        )

    def _keyed_hash(self, value: str) -> str:
        return hmac.new(
            self._hmac_key,
            value.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _identity_type(value: str) -> str:
        normalized = str(value or "phone").strip().lower() or "phone"
        if normalized != "phone":
            raise IdentityChallengeValidationError("unsupported identity type")
        return normalized

    @staticmethod
    def _target(identity_type: str, value: str) -> str:
        if identity_type != "phone":
            raise IdentityChallengeValidationError("unsupported identity type")
        raw_value = str(value or "").strip()
        if not raw_value or re.fullmatch(r"\+?[0-9()\-\s]+", raw_value) is None:
            raise IdentityChallengeValidationError("invalid identity target")
        digits = "".join(character for character in raw_value if character.isdigit())
        if digits.startswith("0086"):
            digits = digits[2:]
        if len(digits) == 11 and digits.startswith("1"):
            digits = f"86{digits}"
        if len(digits) < 7 or len(digits) > 15:
            raise IdentityChallengeValidationError("invalid identity target")
        return digits

    @staticmethod
    def _opaque_id(prefix: str) -> str:
        return f"{prefix}_{secrets.token_urlsafe(24)}"

    @staticmethod
    def _machine_code(value: Any, *, fallback: str) -> str:
        candidate = re.sub(r"[^A-Za-z0-9.:\-]", "-", str(value or "").strip())
        candidate = candidate[:128].strip("-.")
        return candidate if candidate and candidate[0].isalnum() else fallback

    @staticmethod
    def _utc(value: Optional[datetime]) -> datetime:
        result = value or datetime.now(timezone.utc)
        if result.tzinfo is None:
            return result.replace(tzinfo=timezone.utc)
        return result.astimezone(timezone.utc)

    @staticmethod
    def _utc_from_text(value: Any) -> datetime:
        text = str(value or "").strip()
        if not text:
            return datetime.min.replace(tzinfo=timezone.utc)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return IdentityBindingService._utc(parsed)


def make_identity_binding_service(
    store: Any,
    settings: Settings,
    *,
    auth_session_service: Optional[Any] = None,
) -> IdentityBindingService:
    return IdentityBindingService(
        store,
        hmac_key=str(settings.identity_binding_hmac_key or ""),
        hmac_key_version=settings.identity_binding_hmac_key_version,
        adapter=make_identity_challenge_adapter(settings),
        challenge_ttl_seconds=settings.identity_challenge_ttl_seconds,
        max_attempts=settings.identity_challenge_max_attempts,
        retry_after_seconds=settings.identity_challenge_retry_after_seconds,
        auth_session_service=auth_session_service,
        event_sink=getattr(store, "append_evidence_event", None),
        environment=settings.environment,
        evidence_retention_days=settings.evidence_rollout_retention_days,
    )
