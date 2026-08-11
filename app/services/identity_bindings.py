import hashlib
import hmac
import json
import math
import re
import secrets
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Protocol
from urllib.parse import urlparse

from app.core.config import Settings
from app.observability.events import hash_evidence_identifier
from app.services.test_account_allowlist import (
    TEST_ACCOUNT_PROVIDER_MODE,
    make_test_account_allowlist_service,
    test_account_allowlist_configured,
)


IDENTITY_BINDING_CONTRACT_VERSION = 1
IDENTITY_CHALLENGE_STATE_CONTRACT_VERSION = 1
IDENTITY_CHALLENGE_PURPOSES = {"login", "register", "restore", "invitation"}
INTERNAL_ADAPTER_ENVIRONMENTS = {"development", "local", "test", "testing"}
IDENTITY_CHALLENGE_DELIVERY_STATES = {
    "accepted",
    "delivered",
    "undeliverable",
    "unknown",
}
IDENTITY_CHALLENGE_RECOVERY_STATES = {
    "available",
    "notRequired",
    "pending",
    "terminal",
    "unsupported",
}


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


class IdentityChallengeDeliveryError(RuntimeError):
    """A provider could not accept an OTP without exposing provider details."""

    def __init__(self) -> None:
        super().__init__("identity challenge delivery failed")


class IdentityChallengeStateUnavailable(LookupError):
    def __init__(self) -> None:
        super().__init__("identity challenge state is unavailable")


@dataclass(frozen=True)
class IdentityChallengeDeliveryReceipt:
    """Provider-neutral delivery evidence.

    ``provider_receipt_id`` is accepted only long enough to derive a keyed
    hash. It is never returned to a client or persisted in raw form.
    """

    delivery_state: str
    recovery_state: str
    provider_receipt_id: Optional[str] = None
    retry_after_seconds: Optional[int] = None

    def __post_init__(self) -> None:
        if self.delivery_state not in IDENTITY_CHALLENGE_DELIVERY_STATES:
            raise IdentityChallengeDeliveryError()
        if self.recovery_state not in IDENTITY_CHALLENGE_RECOVERY_STATES:
            raise IdentityChallengeDeliveryError()
        if self.delivery_state == "delivered" and self.recovery_state != "notRequired":
            raise IdentityChallengeDeliveryError()
        if self.delivery_state == "undeliverable" and self.recovery_state != "terminal":
            raise IdentityChallengeDeliveryError()
        if self.provider_receipt_id is not None:
            candidate = str(self.provider_receipt_id).strip()
            if not candidate or len(candidate) > 512:
                raise IdentityChallengeDeliveryError()
            object.__setattr__(self, "provider_receipt_id", candidate)
        if self.retry_after_seconds is not None:
            candidate_retry = int(self.retry_after_seconds)
            if candidate_retry < 0 or candidate_retry > 86_400:
                raise IdentityChallengeDeliveryError()
            object.__setattr__(self, "retry_after_seconds", candidate_retry)


class IdentityChallengeDeliveryTransport(Protocol):
    """Minimal server-side boundary for an external OTP delivery provider."""

    def post_json(
        self,
        *,
        url: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
        timeout_seconds: float,
    ) -> Dict[str, Any]:
        ...


class UrllibIdentityChallengeDeliveryTransport:
    """HTTPS JSON transport; errors are normalized before reaching callers."""

    def post_json(
        self,
        *,
        url: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
        timeout_seconds: float,
    ) -> Dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        request = urllib.request.Request(
            url,
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                status = int(getattr(response, "status", response.getcode()))
                raw_payload = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            # Consume the body so the connection can close; provider content is
            # intentionally never surfaced to API callers or application logs.
            exc.read()
            if exc.code == 429:
                retry_after = str(exc.headers.get("Retry-After") or "").strip()
                try:
                    retry_after_seconds = int(retry_after)
                except ValueError:
                    retry_after_seconds = 30
                raise IdentityChallengeRateLimited(retry_after_seconds) from exc
            raise IdentityChallengeDeliveryError() from exc
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise IdentityChallengeDeliveryError() from exc

        if status < 200 or status >= 300:
            raise IdentityChallengeDeliveryError()
        try:
            decoded = json.loads(raw_payload) if raw_payload else {}
        except json.JSONDecodeError as exc:
            raise IdentityChallengeDeliveryError() from exc
        return decoded if isinstance(decoded, dict) else {}


class IdentityChallengeAdapter:
    provider_mode = "unavailable"
    internal_verification_enabled = False
    production_ready = False
    server_code_verification_enabled = False

    def verification_code(self) -> str:
        return f"{secrets.randbelow(1_000_000):06d}"

    def deliver(
        self,
        *,
        identity_type: str,
        target: str,
        purpose: str,
        challenge_id: str,
        verification_code: str,
    ) -> IdentityChallengeDeliveryReceipt:
        raise IdentityChallengeConfigurationError(
            "identity challenge provider is unavailable"
        )

    @property
    def delivery_recovery_supported(self) -> bool:
        return False

    def recover_delivery(
        self,
        *,
        challenge_id: str,
    ) -> IdentityChallengeDeliveryReceipt:
        raise IdentityChallengeConfigurationError(
            "identity challenge delivery recovery is unavailable"
        )


class SyntheticIdentityChallengeAdapter(IdentityChallengeAdapter):
    provider_mode = "synthetic"
    internal_verification_enabled = True
    production_ready = False
    server_code_verification_enabled = True

    def __init__(self, code: str):
        candidate = str(code or "").strip()
        if not candidate or len(candidate) > 32:
            raise IdentityChallengeConfigurationError(
                "synthetic identity challenge code must be configured"
            )
        self._code = candidate

    def verification_code(self) -> str:
        return self._code

    def deliver(
        self,
        *,
        identity_type: str,
        target: str,
        purpose: str,
        challenge_id: str,
        verification_code: str,
    ) -> IdentityChallengeDeliveryReceipt:
        # The synthetic adapter never emits an external message. It exists only
        # in local/test environments and verifies its configured code locally.
        return IdentityChallengeDeliveryReceipt(
            delivery_state="delivered",
            recovery_state="notRequired",
        )


class HttpJsonIdentityChallengeAdapter(IdentityChallengeAdapter):
    """Server-side OTP delivery adapter for an HTTPS JSON SMS gateway.

    The backend owns code generation and hash verification. The configured
    gateway only accepts a delivery request, so no SMS credential or OTP is
    ever exposed to the iOS client or stored in raw form.
    """

    provider_mode = "httpJson"
    internal_verification_enabled = False
    production_ready = True
    server_code_verification_enabled = True

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        timeout_seconds: float,
        status_endpoint: Optional[str] = None,
        transport: Optional[IdentityChallengeDeliveryTransport] = None,
    ) -> None:
        candidate_endpoint = self._endpoint(endpoint)
        candidate_status_endpoint = (
            self._endpoint(status_endpoint)
            if str(status_endpoint or "").strip()
            else None
        )
        candidate_api_key = str(api_key or "").strip()
        if not candidate_api_key:
            raise IdentityChallengeConfigurationError(
                "identity challenge HTTP JSON API key must be configured"
            )
        candidate_timeout = float(timeout_seconds)
        if candidate_timeout < 1 or candidate_timeout > 60:
            raise IdentityChallengeConfigurationError(
                "identity challenge HTTP JSON timeout must be between 1 and 60 seconds"
            )
        self._endpoint = candidate_endpoint
        self._status_endpoint = candidate_status_endpoint
        self._api_key = candidate_api_key
        self._timeout_seconds = candidate_timeout
        self._transport = transport or UrllibIdentityChallengeDeliveryTransport()

    def deliver(
        self,
        *,
        identity_type: str,
        target: str,
        purpose: str,
        challenge_id: str,
        verification_code: str,
    ) -> IdentityChallengeDeliveryReceipt:
        try:
            result = self._transport.post_json(
                url=self._endpoint,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                payload={
                    "challengeId": challenge_id,
                    "code": verification_code,
                    "identityType": identity_type,
                    "purpose": purpose,
                    "target": target,
                },
                timeout_seconds=self._timeout_seconds,
            )
        except (IdentityChallengeDeliveryError, IdentityChallengeRateLimited):
            raise
        except Exception as exc:  # Defensive normalization at the provider boundary.
            raise IdentityChallengeDeliveryError() from exc
        if result.get("accepted") is not True:
            raise IdentityChallengeDeliveryError()
        delivery_state = str(result.get("deliveryState") or "accepted").strip()
        if delivery_state not in {"accepted", "delivered"}:
            raise IdentityChallengeDeliveryError()
        return IdentityChallengeDeliveryReceipt(
            delivery_state=delivery_state,
            recovery_state=(
                "notRequired"
                if delivery_state == "delivered"
                else ("available" if self.delivery_recovery_supported else "unsupported")
            ),
            provider_receipt_id=(
                str(result.get("receiptId")).strip()
                if result.get("receiptId") is not None
                else None
            ),
            retry_after_seconds=self._retry_after(result.get("retryAfterSeconds")),
        )

    @property
    def delivery_recovery_supported(self) -> bool:
        return self._status_endpoint is not None

    def recover_delivery(
        self,
        *,
        challenge_id: str,
    ) -> IdentityChallengeDeliveryReceipt:
        if self._status_endpoint is None:
            raise IdentityChallengeConfigurationError(
                "identity challenge delivery recovery is unavailable"
            )
        try:
            result = self._transport.post_json(
                url=self._status_endpoint,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                payload={"challengeId": challenge_id},
                timeout_seconds=self._timeout_seconds,
            )
        except (IdentityChallengeDeliveryError, IdentityChallengeRateLimited):
            raise
        except Exception as exc:
            raise IdentityChallengeDeliveryError() from exc
        delivery_state = str(result.get("deliveryState") or "").strip()
        if delivery_state not in IDENTITY_CHALLENGE_DELIVERY_STATES:
            raise IdentityChallengeDeliveryError()
        if delivery_state == "delivered":
            recovery_state = "notRequired"
        elif delivery_state == "undeliverable":
            recovery_state = "terminal"
        elif delivery_state == "unknown":
            recovery_state = "pending"
        else:
            recovery_state = "available"
        return IdentityChallengeDeliveryReceipt(
            delivery_state=delivery_state,
            recovery_state=recovery_state,
            provider_receipt_id=(
                str(result.get("receiptId")).strip()
                if result.get("receiptId") is not None
                else None
            ),
            retry_after_seconds=self._retry_after(result.get("retryAfterSeconds")),
        )

    @staticmethod
    def _retry_after(value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            candidate = int(value)
        except (TypeError, ValueError) as exc:
            raise IdentityChallengeDeliveryError() from exc
        if candidate < 0 or candidate > 86_400:
            raise IdentityChallengeDeliveryError()
        return candidate

    @staticmethod
    def _endpoint(value: str) -> str:
        candidate = str(value or "").strip()
        parsed = urlparse(candidate)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise IdentityChallengeConfigurationError(
                "identity challenge HTTP JSON endpoint must be a clean HTTPS URL"
            )
        return candidate


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
    if requested in {"httpjson", "http_json"}:
        try:
            return HttpJsonIdentityChallengeAdapter(
                endpoint=str(settings.identity_challenge_http_json_url or ""),
                api_key=str(settings.identity_challenge_http_json_api_key or ""),
                timeout_seconds=settings.identity_challenge_http_json_timeout_seconds,
                status_endpoint=settings.identity_challenge_http_json_status_url,
            )
        except IdentityChallengeConfigurationError:
            # A partially configured production provider must be indistinguishable
            # from a disabled one to clients and must never open the OTP flow.
            return UnavailableIdentityChallengeAdapter()
    return UnavailableIdentityChallengeAdapter()


def identity_challenge_runtime_descriptor(settings: Settings) -> Dict[str, Any]:
    adapter = make_identity_challenge_adapter(settings)
    key_configured = len(str(settings.identity_binding_hmac_key or "").encode("utf-8")) >= 32
    test_account_enabled = test_account_allowlist_configured(settings)
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
    ) or (test_account_enabled and key_version_valid)
    challenge_enabled = bool(
        (adapter.internal_verification_enabled or adapter.production_ready)
        and key_configured
        and key_version_valid
    ) or bool(test_account_enabled and key_version_valid)
    provider_mode = adapter.provider_mode
    if provider_mode == "unavailable" and test_account_enabled:
        provider_mode = TEST_ACCOUNT_PROVIDER_MODE
    return {
        "enabled": challenge_enabled,
        "challengeEndpoint": "/v2/auth/challenges",
        "verifyEndpointTemplate": "/v2/auth/challenges/{challengeId}/verify",
        "providerMode": provider_mode,
        "internalVerificationEnabled": internal_enabled,
        "productionReady": bool(
            adapter.production_ready and key_configured and key_version_valid
        ),
        "clientFlowEnabled": challenge_enabled,
        "deliverySemantics": "acceptedOnly",
        "statusEndpointTemplate": "/v2/auth/challenges/{challengeId}",
        "stateContractVersion": IDENTITY_CHALLENGE_STATE_CONTRACT_VERSION,
        "deliveryReceiptSupported": bool(adapter.production_ready),
        "deliveryRecoverySupported": bool(adapter.delivery_recovery_supported),
        "testAccountFlowEnabled": bool(test_account_enabled and key_version_valid),
        "testAccountTargetRestricted": True,
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
        test_account_allowlist_service: Optional[Any] = None,
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
        self.test_account_allowlist_service = test_account_allowlist_service
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
        test_account = self._active_test_account(
            identity_type=normalized_type,
            target_hash=target_hash,
            now=created_at,
        )
        if test_account is None and not (
            self.adapter.internal_verification_enabled
            or self.adapter.production_ready
        ):
            raise IdentityChallengeConfigurationError(
                "identity challenge provider is unavailable"
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
        if test_account is not None:
            provider_mode = TEST_ACCOUNT_PROVIDER_MODE
            code_hash = self.test_account_allowlist_service.challenge_code_hash(
                account=test_account,
                challenge_id=challenge_id,
                keyed_hash=self._keyed_hash,
            )
            server_code_verification_enabled = True
            delivery_receipt = IdentityChallengeDeliveryReceipt(
                delivery_state="delivered",
                recovery_state="notRequired",
            )
        else:
            provider_mode = self.adapter.provider_mode
            verification_code = self.adapter.verification_code()
            code_hash = self._keyed_hash(
                f"code:v1:{challenge_id}:{verification_code}"
            )
            server_code_verification_enabled = bool(
                self.adapter.server_code_verification_enabled
            )
            try:
                delivery_receipt = self._normalize_delivery_receipt(
                    self.adapter.deliver(
                        identity_type=normalized_type,
                        target=normalized_target,
                        purpose=normalized_purpose,
                        challenge_id=challenge_id,
                        verification_code=verification_code,
                    )
                )
            except IdentityChallengeRateLimited as exc:
                self._record_event(
                    operation_id=challenge_id,
                    resource_id=target_hash,
                    state="denied",
                    reason="providerRateLimited",
                    decision="createProviderRateLimited",
                    occurred_at=created_at,
                )
                raise IdentityChallengeRateLimited(exc.retry_after_seconds) from exc
            except IdentityChallengeDeliveryError:
                self._record_event(
                    operation_id=challenge_id,
                    resource_id=target_hash,
                    state="failed",
                    reason="providerDeliveryFailed",
                    decision="createDeliveryFailed",
                    occurred_at=created_at,
                )
                raise
        record = {
            "challengeId": challenge_id,
            "identityType": normalized_type,
            "targetHashKeyVersion": self.hmac_key_version,
            "targetHash": target_hash,
            "codeHash": code_hash,
            "providerMode": provider_mode,
            "purpose": normalized_purpose,
            "status": "active",
            "attempts": 0,
            "maxAttempts": self.max_attempts,
            # This historical persistence field controls whether the server can
            # compare its OTP hash. It is distinct from the public runtime flag
            # `internalVerificationEnabled`, which only identifies synthetic use.
            "internalVerificationEnabled": server_code_verification_enabled,
            "deliveryState": delivery_receipt.delivery_state,
            "recoveryState": delivery_receipt.recovery_state,
            "providerReceiptHash": self._provider_receipt_hash(
                delivery_receipt.provider_receipt_id
            ),
            "providerRetryAfterSeconds": delivery_receipt.retry_after_seconds,
            "recoveryAttempts": 0,
            "providerCheckedAt": None,
            "providerDeliveredAt": (
                created_at.isoformat()
                if delivery_receipt.delivery_state == "delivered"
                else None
            ),
            "createdAt": created_at.isoformat(),
            "expiresAt": expires_at.isoformat(),
        }
        persisted = self.store.save_auth_challenge(record)
        self._record_event(
            operation_id=challenge_id,
            resource_id=challenge_id,
            state="succeeded",
            reason="accepted",
            decision="createAccepted",
            occurred_at=created_at,
        )
        return self._public_challenge_state(persisted, response_status="accepted")

    def challenge_state(
        self,
        challenge_id: str,
        *,
        recover_delivery: bool = False,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        normalized_challenge_id = str(challenge_id or "").strip()
        if not normalized_challenge_id or len(normalized_challenge_id) > 160:
            raise IdentityChallengeStateUnavailable()
        observed_at = self._utc(now)
        record = self.store.get_auth_challenge(normalized_challenge_id)
        if record is None or record.get("targetHashKeyVersion") != self.hmac_key_version:
            raise IdentityChallengeStateUnavailable()
        test_account_mode = record.get("providerMode") == TEST_ACCOUNT_PROVIDER_MODE
        if test_account_mode:
            if self._active_test_account(
                identity_type=str(record.get("identityType") or ""),
                target_hash=str(record.get("targetHash") or ""),
                now=observed_at,
            ) is None:
                raise IdentityChallengeStateUnavailable()
        elif record.get("providerMode") != self.adapter.provider_mode:
            raise IdentityChallengeStateUnavailable()

        if (
            record.get("status") == "active"
            and self._utc_from_text(record.get("expiresAt")) <= observed_at
        ):
            record = self.store.update_auth_challenge_delivery_state(
                normalized_challenge_id,
                challenge_status="expired",
                checked_at_iso=observed_at.isoformat(),
            )

        provider_checked_at = str(record.get("providerCheckedAt") or "").strip()
        recovery_retry_seconds = max(
            1,
            int(
                record.get("providerRetryAfterSeconds")
                if record.get("providerRetryAfterSeconds") is not None
                else self.retry_after_seconds
            ),
        )
        recovery_due = not provider_checked_at or (
            self._utc_from_text(provider_checked_at)
            + timedelta(seconds=recovery_retry_seconds)
            <= observed_at
        )
        can_recover = (
            not test_account_mode
            and recover_delivery
            and record.get("status") == "active"
            and record.get("recoveryState") in {"available", "pending"}
            and self.adapter.delivery_recovery_supported
            and recovery_due
        )
        if can_recover:
            try:
                receipt = self._normalize_delivery_receipt(
                    self.adapter.recover_delivery(challenge_id=normalized_challenge_id)
                )
                record = self.store.update_auth_challenge_delivery_state(
                    normalized_challenge_id,
                    delivery_state=receipt.delivery_state,
                    recovery_state=receipt.recovery_state,
                    provider_receipt_hash=self._provider_receipt_hash(
                        receipt.provider_receipt_id
                    ),
                    provider_retry_after_seconds=receipt.retry_after_seconds,
                    checked_at_iso=observed_at.isoformat(),
                    delivered_at_iso=(
                        observed_at.isoformat()
                        if receipt.delivery_state == "delivered"
                        else None
                    ),
                    increment_recovery_attempt=True,
                )
                self._record_event(
                    operation_id=normalized_challenge_id,
                    resource_id=normalized_challenge_id,
                    state=(
                        "succeeded"
                        if receipt.delivery_state == "delivered"
                        else "pending"
                    ),
                    reason=f"delivery{receipt.delivery_state[:1].upper()}{receipt.delivery_state[1:]}",
                    decision="deliveryRecoveryObserved",
                    occurred_at=observed_at,
                )
            except (IdentityChallengeDeliveryError, IdentityChallengeRateLimited):
                record = self.store.update_auth_challenge_delivery_state(
                    normalized_challenge_id,
                    recovery_state="pending",
                    checked_at_iso=observed_at.isoformat(),
                    increment_recovery_attempt=True,
                )
                self._record_event(
                    operation_id=normalized_challenge_id,
                    resource_id=normalized_challenge_id,
                    state="pending",
                    reason="deliveryRecoveryDeferred",
                    decision="deliveryRecoveryDeferred",
                    occurred_at=observed_at,
                )

        return self._public_challenge_state(record, response_status="available")

    def verify_challenge(
        self,
        challenge_id: str,
        code: str,
        *,
        nickname: str = "",
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
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
            persisted_challenge.get("targetHashKeyVersion") != self.hmac_key_version
            or persisted_challenge.get("deliveryState") == "undeliverable"
        ):
            raise IdentityChallengeVerificationFailed()
        test_account = None
        if persisted_challenge.get("providerMode") == TEST_ACCOUNT_PROVIDER_MODE:
            test_account = self._active_test_account(
                identity_type=str(persisted_challenge.get("identityType") or ""),
                target_hash=str(persisted_challenge.get("targetHash") or ""),
                now=attempted_at,
            )
            if test_account is None:
                raise IdentityChallengeVerificationFailed()
        else:
            if not (
                self.adapter.internal_verification_enabled
                or self.adapter.production_ready
            ):
                raise IdentityChallengeConfigurationError(
                    "identity challenge provider is unavailable"
                )
            if persisted_challenge.get("providerMode") != self.adapter.provider_mode:
                raise IdentityChallengeVerificationFailed()
        if len(candidate_code) > 128:
            candidate_code = "invalid-oversized-code"
        if test_account is not None and self.test_account_allowlist_service.verify_code(
            test_account,
            candidate_code,
        ):
            code_hash = self.test_account_allowlist_service.challenge_code_hash(
                account=test_account,
                challenge_id=normalized_challenge_id,
                keyed_hash=self._keyed_hash,
            )
        else:
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
        if test_account is not None and not self.test_account_allowlist_service.record_successful_login(
            str(test_account.get("accountId") or ""),
            subject_id=subject_id,
            now=attempted_at,
        ):
            raise IdentityChallengeVerificationFailed()
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

    def _public_challenge_state(
        self,
        record: Dict[str, Any],
        *,
        response_status: str,
    ) -> Dict[str, Any]:
        attempts = max(0, int(record.get("attempts") or 0))
        max_attempts = max(1, int(record.get("maxAttempts") or self.max_attempts))
        provider_retry = record.get("providerRetryAfterSeconds")
        retry_after_seconds = self.retry_after_seconds
        if provider_retry is not None:
            retry_after_seconds = max(retry_after_seconds, int(provider_retry))
        challenge_status = str(record.get("status") or "unknown")
        public_status = {
            "active": "active",
            "consumed": "verified",
            "expired": "expired",
            "locked": "locked",
        }.get(challenge_status, "unavailable")
        return {
            "status": response_status,
            "challenge": {
                "challengeId": str(record.get("challengeId") or ""),
                "purpose": str(record.get("purpose") or ""),
                "deliveryMode": "acceptedOnly",
                "challengeState": public_status,
                "deliveryState": str(record.get("deliveryState") or "accepted"),
                "attempt": attempts,
                "maxAttempts": max_attempts,
                "remainingAttempts": max(0, max_attempts - attempts),
                "retryAfterSeconds": retry_after_seconds,
                "recoveryState": str(record.get("recoveryState") or "unsupported"),
                "recoveryAttempt": max(0, int(record.get("recoveryAttempts") or 0)),
                "statusEndpoint": (
                    f"/v2/auth/challenges/{str(record.get('challengeId') or '')}"
                ),
                "expiresAt": str(record.get("expiresAt") or ""),
                "productionReady": bool(self.adapter.production_ready),
                "stateContractVersion": IDENTITY_CHALLENGE_STATE_CONTRACT_VERSION,
                "contractVersion": IDENTITY_BINDING_CONTRACT_VERSION,
            },
        }

    @staticmethod
    def _normalize_delivery_receipt(
        value: Any,
    ) -> IdentityChallengeDeliveryReceipt:
        if value is None:
            return IdentityChallengeDeliveryReceipt(
                delivery_state="accepted",
                recovery_state="unsupported",
            )
        if not isinstance(value, IdentityChallengeDeliveryReceipt):
            raise IdentityChallengeDeliveryError()
        return value

    def _provider_receipt_hash(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return self._keyed_hash(f"provider-receipt:v1:{value}")

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

    def _active_test_account(
        self,
        *,
        identity_type: str,
        target_hash: str,
        now: datetime,
    ) -> Optional[Dict[str, Any]]:
        service = self.test_account_allowlist_service
        if service is None:
            return None
        return service.active_account_for_target_hash(
            identity_type=identity_type,
            target_hash_key_version=self.hmac_key_version,
            target_hash=target_hash,
            now=now,
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
        test_account_allowlist_service=make_test_account_allowlist_service(
            store,
            settings,
        ),
        environment=settings.environment,
        evidence_retention_days=settings.evidence_rollout_retention_days,
    )
