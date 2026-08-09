"""Default-off APNs registration and delivery foundation.

Production token persistence deliberately stays behind a vault protocol. The
only bundled vault is ephemeral and the only bundled Provider is fake, so this
module can prove environment/topic isolation, retries and receipts without
claiming that Apple accepted or delivered a real notification.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import re
from threading import RLock
from typing import Any, Mapping, Protocol
from uuid import NAMESPACE_URL, uuid5


APNS_DELIVERY_SCHEMA_VERSION = 1
_TOKEN_PATTERN = re.compile(r"^[0-9a-f]{16,256}$")
_TOPIC_PATTERN = re.compile(r"^[A-Za-z0-9.-]{3,255}$")
_ENVIRONMENTS = frozenset({"sandbox", "production"})
_TERMINAL_STATES = frozenset({"accepted", "failed", "arrived"})


class APNSDeliveryError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class APNSConfiguration:
    provider: str = "disabled"
    token_vault_provider: str = "disabled"
    topic: str | None = None
    environment: str = "sandbox"
    max_attempts: int = 3

    def __post_init__(self) -> None:
        provider = str(self.provider or "disabled").strip()
        vault = str(self.token_vault_provider or "disabled").strip()
        environment = str(self.environment or "").strip().lower()
        topic = str(self.topic or "").strip() or None
        if provider not in {"disabled", "fake"}:
            raise APNSDeliveryError("apnsProviderUnsupported")
        if vault not in {"disabled", "ephemeral"}:
            raise APNSDeliveryError("apnsTokenVaultUnsupported")
        if environment not in _ENVIRONMENTS:
            raise APNSDeliveryError("apnsEnvironmentInvalid")
        if provider != "disabled":
            if topic is None or _TOPIC_PATTERN.fullmatch(topic) is None:
                raise APNSDeliveryError("apnsTopicInvalid")
            if vault == "disabled":
                raise APNSDeliveryError("apnsTokenVaultRequired")
        if int(self.max_attempts) < 1:
            raise APNSDeliveryError("apnsMaxAttemptsInvalid")
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "token_vault_provider", vault)
        object.__setattr__(self, "environment", environment)
        object.__setattr__(self, "topic", topic)
        object.__setattr__(self, "max_attempts", int(self.max_attempts))

    def public_descriptor(self) -> dict[str, Any]:
        enabled = self.provider != "disabled" and self.token_vault_provider != "disabled"
        return {
            "schemaVersion": APNS_DELIVERY_SCHEMA_VERSION,
            "implemented": True,
            "enabled": enabled,
            "provider": self.provider,
            "tokenVault": self.token_vault_provider,
            "environment": self.environment,
            "topicConfigured": self.topic is not None,
            "externalVerified": False,
            "reason": "fakeProviderOnly" if enabled else "apnsDisabled",
        }


def apns_runtime_descriptor(configuration: APNSConfiguration) -> dict[str, Any]:
    """Return a secret-free runtime contract for mobile capability gating."""

    return {
        **configuration.public_descriptor(),
        "registrationEndpoint": "/devices/push-token",
        "deliveryReceiptStates": ["accepted", "arrived", "failed", "unknown"],
        "realProviderReady": False,
        "defaultReleaseVisible": False,
    }


@dataclass(frozen=True)
class APNSDeviceRegistration:
    registration_id: str
    owner_user_id: str
    installation_id: str
    token_hash: str
    token_reference: str
    topic: str
    environment: str
    generation: int = 0
    active: bool = True

    def public_contract(self) -> dict[str, Any]:
        return {
            "schemaVersion": APNS_DELIVERY_SCHEMA_VERSION,
            "registrationId": self.registration_id,
            "installationDigest": _digest(self.installation_id),
            "environment": self.environment,
            "topic": self.topic,
            "generation": self.generation,
            "status": "active" if self.active else "revoked",
            "containsRawToken": False,
        }


class APNSTokenVault(Protocol):
    def store(self, *, registration_id: str, token: str) -> str:
        ...

    def resolve(self, *, token_reference: str) -> str:
        ...

    def delete(self, *, token_reference: str) -> None:
        ...


class EphemeralAPNSTokenVault:
    """Process-memory fake vault for tests; never a production credential store."""

    def __init__(self) -> None:
        self._tokens: dict[str, str] = {}
        self._lock = RLock()

    def store(self, *, registration_id: str, token: str) -> str:
        reference = f"apnsref_{_digest(registration_id + ':' + token)[:32]}"
        with self._lock:
            self._tokens[reference] = token
        return reference

    def resolve(self, *, token_reference: str) -> str:
        with self._lock:
            token = self._tokens.get(token_reference)
        if token is None:
            raise APNSDeliveryError("apnsTokenUnavailable")
        return token

    def delete(self, *, token_reference: str) -> None:
        with self._lock:
            self._tokens.pop(token_reference, None)


@dataclass(frozen=True)
class APNSProviderReceipt:
    state: str
    reason_code: str
    provider_receipt_id: str | None = None
    retryable: bool = False


class APNSProvider(Protocol):
    def send(
        self,
        *,
        device_token: str,
        topic: str,
        environment: str,
        payload: Mapping[str, Any],
        attempt: int,
    ) -> APNSProviderReceipt:
        ...


class FakeAPNSProvider:
    def __init__(self, receipts: list[APNSProviderReceipt] | None = None) -> None:
        self.receipts = list(receipts or [
            APNSProviderReceipt(
                state="accepted",
                reason_code="fakeProviderAccepted",
                provider_receipt_id="fake-apns-receipt",
            )
        ])
        self.calls: list[dict[str, Any]] = []

    def send(self, **kwargs: Any) -> APNSProviderReceipt:
        self.calls.append(dict(kwargs))
        if not self.receipts:
            return APNSProviderReceipt(
                state="unknown",
                reason_code="fakeProviderReceiptMissing",
                retryable=True,
            )
        return self.receipts.pop(0)


@dataclass(frozen=True)
class APNSDeliveryJob:
    job_id: str
    message_id: str
    registration: APNSDeviceRegistration
    payload: Mapping[str, Any]
    state: str = "queued"
    attempt: int = 0
    reason_code: str = "queued"
    provider_receipt_hash: str | None = None
    retryable: bool = False

    def public_contract(self) -> dict[str, Any]:
        return {
            "schemaVersion": APNS_DELIVERY_SCHEMA_VERSION,
            "jobId": self.job_id,
            "messageId": self.message_id,
            "registrationId": self.registration.registration_id,
            "environment": self.registration.environment,
            "topic": self.registration.topic,
            "state": self.state,
            "attempt": self.attempt,
            "reasonCode": self.reason_code,
            "providerReceiptPresent": self.provider_receipt_hash is not None,
            "retryable": self.retryable,
        }


class InMemoryAPNSDeliveryRepository:
    def __init__(self) -> None:
        self._jobs: dict[str, APNSDeliveryJob] = {}
        self._lock = RLock()

    def upsert(self, job: APNSDeliveryJob) -> APNSDeliveryJob:
        with self._lock:
            current = self._jobs.get(job.job_id)
            if current is not None:
                if current.message_id != job.message_id or current.registration != job.registration:
                    raise APNSDeliveryError("apnsJobConflict")
                return current
            self._jobs[job.job_id] = job
            return job

    def get(self, job_id: str) -> APNSDeliveryJob:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise APNSDeliveryError("apnsJobNotFound")
        return job

    def save(self, job: APNSDeliveryJob) -> APNSDeliveryJob:
        with self._lock:
            self._jobs[job.job_id] = job
        return job


class APNSDeliveryService:
    def __init__(
        self,
        *,
        configuration: APNSConfiguration,
        token_vault: APNSTokenVault,
        provider: APNSProvider,
        repository: InMemoryAPNSDeliveryRepository | None = None,
    ) -> None:
        self.configuration = configuration
        self.token_vault = token_vault
        self.provider = provider
        self.repository = repository or InMemoryAPNSDeliveryRepository()

    def register(
        self,
        *,
        owner_user_id: str,
        installation_id: str,
        device_token: str,
        topic: str,
        environment: str,
    ) -> APNSDeviceRegistration:
        self._require_enabled()
        owner = _required(owner_user_id, "apnsOwnerInvalid")
        installation = _required(installation_id, "apnsInstallationInvalid")
        token = str(device_token or "").replace(" ", "").lower()
        if _TOKEN_PATTERN.fullmatch(token) is None:
            raise APNSDeliveryError("apnsDeviceTokenInvalid")
        if topic != self.configuration.topic:
            raise APNSDeliveryError("apnsTopicMismatch")
        if environment != self.configuration.environment:
            raise APNSDeliveryError("apnsEnvironmentMismatch")
        registration_id = str(
            uuid5(NAMESPACE_URL, f"dreamjourney-apns:{owner}:{installation}")
        )
        token_reference = self.token_vault.store(
            registration_id=registration_id,
            token=token,
        )
        return APNSDeviceRegistration(
            registration_id=registration_id,
            owner_user_id=owner,
            installation_id=installation,
            token_hash=_digest(token),
            token_reference=token_reference,
            topic=topic,
            environment=environment,
        )

    def enqueue(
        self,
        *,
        message_id: str,
        registration: APNSDeviceRegistration,
        payload: Mapping[str, Any],
    ) -> APNSDeliveryJob:
        self._require_enabled()
        if not registration.active:
            raise APNSDeliveryError("apnsRegistrationRevoked")
        if registration.topic != self.configuration.topic:
            raise APNSDeliveryError("apnsTopicMismatch")
        if registration.environment != self.configuration.environment:
            raise APNSDeliveryError("apnsEnvironmentMismatch")
        normalized_message = _required(message_id, "apnsMessageInvalid")
        job = APNSDeliveryJob(
            job_id=str(
                uuid5(
                    NAMESPACE_URL,
                    f"dreamjourney-apns-job:{normalized_message}:"
                    f"{registration.registration_id}:{registration.generation}",
                )
            ),
            message_id=normalized_message,
            registration=registration,
            payload=dict(payload),
        )
        return self.repository.upsert(job)

    def dispatch(self, job_id: str) -> APNSDeliveryJob:
        job = self.repository.get(job_id)
        if job.state in _TERMINAL_STATES:
            return job
        if job.attempt >= self.configuration.max_attempts:
            return self.repository.save(
                replace(
                    job,
                    state="unknown",
                    reason_code="apnsManualReviewRequired",
                    retryable=False,
                )
            )
        token = self.token_vault.resolve(
            token_reference=job.registration.token_reference
        )
        receipt = self.provider.send(
            device_token=token,
            topic=job.registration.topic,
            environment=job.registration.environment,
            payload=job.payload,
            attempt=job.attempt + 1,
        )
        if receipt.state not in {"accepted", "failed", "unknown", "arrived"}:
            raise APNSDeliveryError("apnsProviderReceiptInvalid")
        provider_receipt_hash = (
            _digest(receipt.provider_receipt_id)
            if receipt.provider_receipt_id
            else None
        )
        updated = replace(
            job,
            state=receipt.state,
            attempt=job.attempt + 1,
            reason_code=receipt.reason_code,
            provider_receipt_hash=provider_receipt_hash,
            retryable=receipt.retryable,
        )
        return self.repository.save(updated)

    def retry(self, job_id: str) -> APNSDeliveryJob:
        job = self.repository.get(job_id)
        if job.state not in {"failed", "unknown"} or not job.retryable:
            raise APNSDeliveryError("apnsJobNotRetryable")
        return self.repository.save(
            replace(job, state="queued", reason_code="retryQueued", retryable=False)
        )

    def _require_enabled(self) -> None:
        if not self.configuration.public_descriptor()["enabled"]:
            raise APNSDeliveryError("apnsDeliveryDisabled")


def _required(value: Any, code: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 256:
        raise APNSDeliveryError(code)
    return normalized


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "APNSConfiguration",
    "APNSDeliveryError",
    "APNSDeliveryJob",
    "APNSDeliveryService",
    "APNSDeviceRegistration",
    "APNSProviderReceipt",
    "apns_runtime_descriptor",
    "EphemeralAPNSTokenVault",
    "FakeAPNSProvider",
    "InMemoryAPNSDeliveryRepository",
]
