"""Default-off APNs registration and delivery foundation."""

from __future__ import annotations

from base64 import urlsafe_b64encode
from dataclasses import dataclass, replace
from hashlib import sha256
import json
import re
from threading import RLock
import time
from typing import Any, Callable, Mapping, Protocol
from uuid import NAMESPACE_URL, uuid5

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature


APNS_DELIVERY_SCHEMA_VERSION = 1
_TOKEN_PATTERN = re.compile(r"^[0-9a-f]{16,256}$")
_TOPIC_PATTERN = re.compile(r"^[A-Za-z0-9.-]{3,255}$")
_ENVIRONMENTS = frozenset({"sandbox", "production"})
_TERMINAL_STATES = frozenset({"accepted", "failed", "arrived"})
_APPLE_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9]{10}$")
_MAX_APNS_PAYLOAD_BYTES = 4096


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
    token_encryption_key_configured: bool = False
    team_id: str | None = None
    key_id: str | None = None
    private_key_path: str | None = None
    request_timeout_seconds: int = 15
    external_verified: bool = False

    def __post_init__(self) -> None:
        provider = str(self.provider or "disabled").strip()
        vault = str(self.token_vault_provider or "disabled").strip()
        environment = str(self.environment or "").strip().lower()
        topic = str(self.topic or "").strip() or None
        team_id = str(self.team_id or "").strip() or None
        key_id = str(self.key_id or "").strip() or None
        private_key_path = str(self.private_key_path or "").strip() or None
        if provider not in {"disabled", "fake", "appleToken"}:
            raise APNSDeliveryError("apnsProviderUnsupported")
        if vault not in {"disabled", "ephemeral", "postgresEncrypted"}:
            raise APNSDeliveryError("apnsTokenVaultUnsupported")
        if environment not in _ENVIRONMENTS:
            raise APNSDeliveryError("apnsEnvironmentInvalid")
        if provider != "disabled":
            if topic is None or _TOPIC_PATTERN.fullmatch(topic) is None:
                raise APNSDeliveryError("apnsTopicInvalid")
            if vault == "disabled":
                raise APNSDeliveryError("apnsTokenVaultRequired")
            if vault == "postgresEncrypted" and not self.token_encryption_key_configured:
                raise APNSDeliveryError("apnsTokenEncryptionKeyRequired")
        if provider == "appleToken":
            if vault != "postgresEncrypted":
                raise APNSDeliveryError("apnsDurableTokenVaultRequired")
            if team_id is None or _APPLE_IDENTIFIER_PATTERN.fullmatch(team_id) is None:
                raise APNSDeliveryError("apnsTeamIdInvalid")
            if key_id is None or _APPLE_IDENTIFIER_PATTERN.fullmatch(key_id) is None:
                raise APNSDeliveryError("apnsKeyIdInvalid")
            if private_key_path is None:
                raise APNSDeliveryError("apnsPrivateKeyPathRequired")
        if int(self.max_attempts) < 1:
            raise APNSDeliveryError("apnsMaxAttemptsInvalid")
        if int(self.request_timeout_seconds) < 1:
            raise APNSDeliveryError("apnsRequestTimeoutInvalid")
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "token_vault_provider", vault)
        object.__setattr__(self, "environment", environment)
        object.__setattr__(self, "topic", topic)
        object.__setattr__(self, "max_attempts", int(self.max_attempts))
        object.__setattr__(self, "team_id", team_id)
        object.__setattr__(self, "key_id", key_id)
        object.__setattr__(self, "private_key_path", private_key_path)
        object.__setattr__(self, "request_timeout_seconds", int(self.request_timeout_seconds))

    def public_descriptor(self) -> dict[str, Any]:
        enabled = self.provider != "disabled" and self.token_vault_provider != "disabled"
        real_provider = self.provider == "appleToken"
        if not enabled:
            reason = "apnsDisabled"
        elif self.provider == "fake":
            reason = "fakeProviderOnly"
        elif not self.external_verified:
            reason = "apnsExternalVerificationRequired"
        else:
            reason = "ready"
        return {
            "schemaVersion": APNS_DELIVERY_SCHEMA_VERSION,
            "implemented": True,
            "enabled": enabled,
            "provider": self.provider,
            "tokenVault": self.token_vault_provider,
            "environment": self.environment,
            "topicConfigured": self.topic is not None,
            "credentialConfigured": real_provider,
            "externalVerified": bool(real_provider and self.external_verified),
            "durableOutbox": self.token_vault_provider == "postgresEncrypted",
            "reason": reason,
        }


def apns_runtime_descriptor(configuration: APNSConfiguration) -> dict[str, Any]:
    """Return a secret-free runtime contract for mobile capability gating."""

    descriptor = configuration.public_descriptor()
    return {
        **descriptor,
        "registrationEndpoint": "/devices/push-token",
        "deliveryReceiptStates": ["accepted", "arrived", "failed", "unknown"],
        "realProviderReady": bool(
            configuration.provider == "appleToken"
            and descriptor["externalVerified"]
            and descriptor["durableOutbox"]
        ),
        "defaultReleaseVisible": False,
    }


@dataclass(frozen=True)
class APNSDeviceRegistration:
    registration_id: str
    owner_user_id: str
    installation_digest: str
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
            "installationDigest": self.installation_digest,
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


class AppleTokenAPNSProvider:
    """Send device notifications through Apple's token-authenticated HTTP/2 API."""

    def __init__(
        self,
        *,
        team_id: str,
        key_id: str,
        private_key_pem: bytes,
        timeout_seconds: int = 15,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._team_id = _required(team_id, "apnsTeamIdInvalid")
        self._key_id = _required(key_id, "apnsKeyIdInvalid")
        self._timeout_seconds = max(1, int(timeout_seconds))
        self._client_factory = client_factory
        self._clock = clock
        self._lock = RLock()
        self._cached_token: str | None = None
        self._cached_token_issued_at = 0
        self._client: httpx.Client | None = None
        try:
            private_key = serialization.load_pem_private_key(
                private_key_pem,
                password=None,
            )
        except (TypeError, ValueError) as exc:
            raise APNSDeliveryError("apnsPrivateKeyInvalid") from exc
        if not isinstance(private_key, ec.EllipticCurvePrivateKey) or not isinstance(
            private_key.curve,
            ec.SECP256R1,
        ):
            raise APNSDeliveryError("apnsPrivateKeyInvalid")
        self._private_key = private_key

    def send(
        self,
        *,
        device_token: str,
        topic: str,
        environment: str,
        payload: Mapping[str, Any],
        attempt: int,
    ) -> APNSProviderReceipt:
        del attempt
        token = str(device_token or "").strip().lower()
        if _TOKEN_PATTERN.fullmatch(token) is None:
            raise APNSDeliveryError("apnsDeviceTokenInvalid")
        normalized_topic = _required(topic, "apnsTopicInvalid")
        if _TOPIC_PATTERN.fullmatch(normalized_topic) is None:
            raise APNSDeliveryError("apnsTopicInvalid")
        normalized_environment = str(environment or "").strip().lower()
        if normalized_environment not in _ENVIRONMENTS:
            raise APNSDeliveryError("apnsEnvironmentInvalid")
        body = json.dumps(
            dict(payload),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(body) > _MAX_APNS_PAYLOAD_BYTES:
            return APNSProviderReceipt(
                state="failed",
                reason_code="apnsPayloadTooLarge",
                retryable=False,
            )
        push_type = _push_type(payload)
        host = (
            "api.sandbox.push.apple.com"
            if normalized_environment == "sandbox"
            else "api.push.apple.com"
        )
        headers = {
            "authorization": f"bearer {self._provider_token()}",
            "apns-topic": normalized_topic,
            "apns-push-type": push_type,
            "apns-priority": "5" if push_type == "background" else "10",
            "content-type": "application/json",
        }
        try:
            response = self._http_client().post(
                f"https://{host}/3/device/{token}",
                headers=headers,
                content=body,
            )
        except httpx.HTTPError:
            return APNSProviderReceipt(
                state="unknown",
                reason_code="apnsNetworkUnavailable",
                retryable=True,
            )
        provider_receipt_id = str(response.headers.get("apns-id") or "").strip() or None
        if response.status_code == 200:
            return APNSProviderReceipt(
                state="accepted",
                reason_code="appleAccepted",
                provider_receipt_id=provider_receipt_id,
                retryable=False,
            )
        reason = _apple_response_reason(response)
        if reason in {"ExpiredProviderToken", "TooManyProviderTokenUpdates"}:
            self._invalidate_provider_token()
        retryable = response.status_code in {429, 500, 503} or reason in {
            "ExpiredProviderToken",
            "TooManyProviderTokenUpdates",
        }
        return APNSProviderReceipt(
            state="unknown" if retryable else "failed",
            reason_code=_apple_reason_code(response.status_code, reason),
            provider_receipt_id=provider_receipt_id,
            retryable=retryable,
        )

    def _provider_token(self) -> str:
        now = int(self._clock())
        with self._lock:
            if self._cached_token is not None and now - self._cached_token_issued_at < 3000:
                return self._cached_token
            header = _base64url_json({"alg": "ES256", "kid": self._key_id})
            claims = _base64url_json({"iss": self._team_id, "iat": now})
            signing_input = f"{header}.{claims}".encode("ascii")
            der_signature = self._private_key.sign(
                signing_input,
                ec.ECDSA(hashes.SHA256()),
            )
            r, s = decode_dss_signature(der_signature)
            signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
            token = f"{header}.{claims}.{_base64url(signature)}"
            self._cached_token = token
            self._cached_token_issued_at = now
            return token

    def _http_client(self) -> httpx.Client:
        with self._lock:
            if self._client is None:
                self._client = self._client_factory(
                    http2=True,
                    timeout=self._timeout_seconds,
                )
            return self._client

    def close(self) -> None:
        with self._lock:
            client = self._client
            self._client = None
        if client is not None:
            client.close()

    def _invalidate_provider_token(self) -> None:
        with self._lock:
            self._cached_token = None
            self._cached_token_issued_at = 0


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

    def claim_due(
        self,
        *,
        worker_id: str,
        limit: int,
        lease_seconds: int,
    ) -> list[APNSDeliveryJob]:
        del worker_id, lease_seconds
        with self._lock:
            claimed = [
                replace(job, state="dispatching", reason_code="workerLeaseClaimed")
                for job in self._jobs.values()
                if job.state == "queued"
            ][: max(0, int(limit))]
            for job in claimed:
                self._jobs[job.job_id] = job
        return claimed


class APNSDeliveryRepository(Protocol):
    def upsert(self, job: APNSDeliveryJob) -> APNSDeliveryJob:
        ...

    def get(self, job_id: str) -> APNSDeliveryJob:
        ...

    def save(self, job: APNSDeliveryJob) -> APNSDeliveryJob:
        ...

    def claim_due(
        self,
        *,
        worker_id: str,
        limit: int,
        lease_seconds: int,
    ) -> list[APNSDeliveryJob]:
        ...


class APNSRegistrationRepository(Protocol):
    def upsert_registration(
        self,
        registration: APNSDeviceRegistration,
    ) -> APNSDeviceRegistration:
        ...

    def get_registration(self, registration_id: str) -> APNSDeviceRegistration | None:
        ...

    def list_active_registrations(self, owner_user_id: str) -> list[APNSDeviceRegistration]:
        ...


class InMemoryAPNSRegistrationRepository:
    def __init__(self) -> None:
        self._registrations: dict[str, APNSDeviceRegistration] = {}
        self._lock = RLock()

    def upsert_registration(
        self,
        registration: APNSDeviceRegistration,
    ) -> APNSDeviceRegistration:
        with self._lock:
            current = self._registrations.get(registration.registration_id)
            if current is not None:
                if (
                    current.owner_user_id != registration.owner_user_id
                    or current.installation_digest != registration.installation_digest
                    or current.topic != registration.topic
                    or current.environment != registration.environment
                ):
                    raise APNSDeliveryError("apnsRegistrationConflict")
                registration = replace(
                    registration,
                    generation=(
                        current.generation + 1
                        if current.token_hash != registration.token_hash
                        else current.generation
                    ),
                )
            self._registrations[registration.registration_id] = registration
            return registration

    def get_registration(self, registration_id: str) -> APNSDeviceRegistration | None:
        with self._lock:
            return self._registrations.get(registration_id)

    def list_active_registrations(self, owner_user_id: str) -> list[APNSDeviceRegistration]:
        with self._lock:
            return [
                item
                for item in self._registrations.values()
                if item.owner_user_id == owner_user_id and item.active
            ]


class APNSDeliveryService:
    def __init__(
        self,
        *,
        configuration: APNSConfiguration,
        token_vault: APNSTokenVault,
        provider: APNSProvider,
        repository: APNSDeliveryRepository | None = None,
        registration_repository: APNSRegistrationRepository | None = None,
    ) -> None:
        self.configuration = configuration
        self.token_vault = token_vault
        self.provider = provider
        self.repository = repository or InMemoryAPNSDeliveryRepository()
        self.registration_repository = (
            registration_repository or InMemoryAPNSRegistrationRepository()
        )

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
        previous = self.registration_repository.get_registration(registration_id)
        token_reference = self.token_vault.store(
            registration_id=registration_id,
            token=token,
        )
        registration = APNSDeviceRegistration(
            registration_id=registration_id,
            owner_user_id=owner,
            installation_digest=_digest(installation),
            token_hash=_digest(token),
            token_reference=token_reference,
            topic=topic,
            environment=environment,
        )
        try:
            persisted = self.registration_repository.upsert_registration(registration)
        except Exception:
            if previous is None or previous.token_reference != token_reference:
                self.token_vault.delete(token_reference=token_reference)
            raise
        if previous is not None and previous.token_reference != persisted.token_reference:
            self.token_vault.delete(token_reference=previous.token_reference)
        return persisted

    def list_active_registrations(self, owner_user_id: str) -> list[APNSDeviceRegistration]:
        self._require_enabled()
        return self.registration_repository.list_active_registrations(
            _required(owner_user_id, "apnsOwnerInvalid")
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
        current_registration = self.registration_repository.get_registration(
            job.registration.registration_id
        )
        if (
            current_registration is None
            or not current_registration.active
            or current_registration.generation != job.registration.generation
            or current_registration.token_reference != job.registration.token_reference
        ):
            return self.repository.save(
                replace(
                    job,
                    state="failed",
                    reason_code="apnsRegistrationSuperseded",
                    retryable=False,
                )
            )
        if job.attempt >= self.configuration.max_attempts:
            return self.repository.save(
                replace(
                    job,
                    state="unknown",
                    reason_code="apnsManualReviewRequired",
                    retryable=False,
                )
            )
        try:
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
        except APNSDeliveryError:
            return self.repository.save(
                replace(
                    job,
                    state="unknown",
                    attempt=job.attempt + 1,
                    reason_code="apnsTokenOrProviderUnavailable",
                    retryable=True,
                )
            )
        except Exception:
            return self.repository.save(
                replace(
                    job,
                    state="unknown",
                    attempt=job.attempt + 1,
                    reason_code="apnsProviderException",
                    retryable=True,
                )
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

    def dispatch_due(
        self,
        *,
        worker_id: str,
        limit: int = 25,
        lease_seconds: int = 60,
    ) -> list[APNSDeliveryJob]:
        self._require_enabled()
        claimed = self.repository.claim_due(
            worker_id=_required(worker_id, "apnsWorkerInvalid"),
            limit=max(1, min(int(limit), 100)),
            lease_seconds=max(5, int(lease_seconds)),
        )
        results: list[APNSDeliveryJob] = []
        for job in claimed:
            delivered = self.dispatch(job.job_id)
            results.append(delivered)
            if delivered.retryable and delivered.attempt < self.configuration.max_attempts:
                self.retry(job.job_id)
        return results

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

    def close(self) -> None:
        close = getattr(self.provider, "close", None)
        if callable(close):
            close()


def _required(value: Any, code: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 256:
        raise APNSDeliveryError(code)
    return normalized


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _base64url(value: bytes) -> str:
    return urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_json(value: Mapping[str, Any]) -> str:
    return _base64url(
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )


def _push_type(payload: Mapping[str, Any]) -> str:
    aps = payload.get("aps")
    if isinstance(aps, Mapping) and aps.get("content-available") == 1 and "alert" not in aps:
        return "background"
    return "alert"


def _apple_response_reason(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError):
        return "Unknown"
    if not isinstance(payload, Mapping):
        return "Unknown"
    reason = str(payload.get("reason") or "").strip()
    return reason if reason and len(reason) <= 128 else "Unknown"


def _apple_reason_code(status_code: int, reason: str) -> str:
    known = {
        "BadDeviceToken": "apnsBadDeviceToken",
        "DeviceTokenNotForTopic": "apnsDeviceTokenNotForTopic",
        "Unregistered": "apnsDeviceTokenUnregistered",
        "ExpiredProviderToken": "apnsProviderTokenExpired",
        "InvalidProviderToken": "apnsProviderTokenInvalid",
        "TooManyProviderTokenUpdates": "apnsProviderTokenRateLimited",
        "TooManyRequests": "apnsRateLimited",
        "PayloadEmpty": "apnsPayloadEmpty",
        "PayloadTooLarge": "apnsPayloadTooLarge",
        "TopicDisallowed": "apnsTopicDisallowed",
    }
    return known.get(reason, f"apnsRejected{int(status_code)}")


__all__ = [
    "APNSConfiguration",
    "APNSDeliveryError",
    "APNSDeliveryJob",
    "APNSDeliveryService",
    "APNSDeliveryRepository",
    "APNSDeviceRegistration",
    "APNSRegistrationRepository",
    "APNSProviderReceipt",
    "AppleTokenAPNSProvider",
    "apns_runtime_descriptor",
    "EphemeralAPNSTokenVault",
    "FakeAPNSProvider",
    "InMemoryAPNSDeliveryRepository",
    "InMemoryAPNSRegistrationRepository",
]
