"""Server-owned adult identity and liveness receipts for voice cloning.

Voice cloning may only be admitted after a server-side verifier has bound an
authenticated owner to a current living-adult and liveness result.  Mobile
payload fields, QA launch arguments, and local profile state are deliberately
outside this boundary.

The first production adapter is an HTTPS JSON port.  It remains disabled until
an approved strong-identity/liveness provider is configured; configuration
failure is intentionally indistinguishable from provider unavailability to
mobile callers.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Protocol
from urllib.parse import urlparse

from app.core.config import Settings


VOICE_IDENTITY_ELIGIBILITY_CONTRACT_VERSION = 1
_ALLOWED_AGE_STATUSES = frozenset({"adult", "minor", "unknown"})
_ALLOWED_LIVING_STATUSES = frozenset({"living", "deceased", "unknown"})


class VoiceIdentityEligibilityConfigurationError(RuntimeError):
    """The selected verifier lacks a valid server-side configuration."""


class VoiceIdentityEligibilityProviderUnavailable(RuntimeError):
    """The verifier cannot safely issue a current eligibility receipt."""


class VoiceIdentityEligibilityProviderResponseError(ValueError):
    """A verifier response is malformed or fails its binding invariants."""


class VoiceIdentityEligibilityTransport(Protocol):
    def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        ...


class UrllibVoiceIdentityEligibilityTransport:
    """Small HTTPS transport that never surfaces provider bodies to callers."""

    def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
            headers=dict(headers),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                status = int(getattr(response, "status", response.getcode()))
                raw_payload = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            exc.read()
            raise VoiceIdentityEligibilityProviderUnavailable() from exc
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise VoiceIdentityEligibilityProviderUnavailable() from exc

        if status < 200 or status >= 300:
            raise VoiceIdentityEligibilityProviderUnavailable()
        try:
            decoded = json.loads(raw_payload) if raw_payload else {}
        except json.JSONDecodeError as exc:
            raise VoiceIdentityEligibilityProviderUnavailable() from exc
        if not isinstance(decoded, Mapping):
            raise VoiceIdentityEligibilityProviderUnavailable()
        return decoded


@dataclass(frozen=True)
class VoiceIdentityEligibilityReceipt:
    """Value-minimized, server-owned result of a strong identity verification."""

    provider_kind: str
    receipt_id_hash: str
    actor_user_id: str
    subject_user_id: str
    age_status: str
    living_status: str
    liveness_verified: bool
    issued_at: datetime
    expires_at: datetime

    def is_current(self, *, now: datetime) -> bool:
        evaluated_at = _aware(now)
        return self.issued_at <= evaluated_at < self.expires_at

    def persistence_summary(self) -> dict[str, Any]:
        """Safe internal metadata; never retain raw provider IDs or evidence."""

        return {
            "schemaVersion": VOICE_IDENTITY_ELIGIBILITY_CONTRACT_VERSION,
            "providerKind": self.provider_kind,
            "receiptHash": self.receipt_id_hash,
            "issuedAt": self.issued_at.isoformat(),
            "expiresAt": self.expires_at.isoformat(),
        }

    @classmethod
    def from_provider_payload(
        cls,
        payload: Mapping[str, Any],
        *,
        provider_kind: str,
    ) -> "VoiceIdentityEligibilityReceipt":
        receipt_id = _required_text(payload.get("receiptId"), field="receiptId", limit=512)
        actor_user_id = _required_text(payload.get("actorUserId"), field="actorUserId", limit=96)
        subject_user_id = _required_text(payload.get("subjectUserId"), field="subjectUserId", limit=96)
        age_status = str(payload.get("ageStatus") or "").strip()
        living_status = str(payload.get("livingStatus") or "").strip()
        if age_status not in _ALLOWED_AGE_STATUSES:
            raise VoiceIdentityEligibilityProviderResponseError("invalid age status")
        if living_status not in _ALLOWED_LIVING_STATUSES:
            raise VoiceIdentityEligibilityProviderResponseError("invalid living status")
        if not isinstance(payload.get("livenessVerified"), bool):
            raise VoiceIdentityEligibilityProviderResponseError("invalid liveness status")
        issued_at = _parse_timestamp(payload.get("issuedAt"), field="issuedAt")
        expires_at = _parse_timestamp(payload.get("expiresAt"), field="expiresAt")
        if expires_at <= issued_at:
            raise VoiceIdentityEligibilityProviderResponseError("invalid receipt validity window")
        return cls(
            provider_kind=str(provider_kind or "unavailable"),
            receipt_id_hash="sha256:" + hashlib.sha256(receipt_id.encode("utf-8")).hexdigest(),
            actor_user_id=actor_user_id,
            subject_user_id=subject_user_id,
            age_status=age_status,
            living_status=living_status,
            liveness_verified=payload["livenessVerified"],
            issued_at=issued_at,
            expires_at=expires_at,
        )


class VoiceIdentityEligibilityProvider(Protocol):
    provider_kind: str
    is_configured: bool

    def resolve(
        self,
        *,
        actor_user_id: str,
        subject_user_id: str,
        now: datetime,
    ) -> VoiceIdentityEligibilityReceipt:
        ...


class UnavailableVoiceIdentityEligibilityProvider:
    provider_kind = "unavailable"
    is_configured = False

    def resolve(
        self,
        *,
        actor_user_id: str,
        subject_user_id: str,
        now: datetime,
    ) -> VoiceIdentityEligibilityReceipt:
        raise VoiceIdentityEligibilityProviderUnavailable()


class HttpJsonVoiceIdentityEligibilityProvider:
    """HTTPS adapter for an approved identity/liveness verifier.

    The server sends only opaque application account identifiers and expects a
    verifier-issued receipt bound to the same identifiers.  Raw documents,
    biometrics, provider response bodies, and external receipt IDs stay outside
    application persistence and mobile responses.
    """

    provider_kind = "httpJson"
    is_configured = True

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        timeout_seconds: float,
        transport: Optional[VoiceIdentityEligibilityTransport] = None,
    ) -> None:
        self._endpoint = _https_endpoint(endpoint)
        self._api_key = _required_text(api_key, field="api key", limit=1024)
        if not 1 <= float(timeout_seconds) <= 60:
            raise VoiceIdentityEligibilityConfigurationError(
                "voice identity verifier timeout must be between 1 and 60 seconds"
            )
        self._timeout_seconds = float(timeout_seconds)
        self._transport = transport or UrllibVoiceIdentityEligibilityTransport()

    def resolve(
        self,
        *,
        actor_user_id: str,
        subject_user_id: str,
        now: datetime,
    ) -> VoiceIdentityEligibilityReceipt:
        try:
            response = self._transport.post_json(
                url=self._endpoint,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                payload={
                    "contractVersion": VOICE_IDENTITY_ELIGIBILITY_CONTRACT_VERSION,
                    "capability": "clonedVoice",
                    "actorUserId": actor_user_id,
                    "subjectUserId": subject_user_id,
                },
                timeout_seconds=self._timeout_seconds,
            )
        except VoiceIdentityEligibilityProviderUnavailable:
            raise
        except Exception as exc:  # Normalize all provider/transport details.
            raise VoiceIdentityEligibilityProviderUnavailable() from exc
        return VoiceIdentityEligibilityReceipt.from_provider_payload(
            response,
            provider_kind=self.provider_kind,
        )


def make_voice_identity_eligibility_provider(
    settings: Settings,
    *,
    transport: Optional[VoiceIdentityEligibilityTransport] = None,
) -> VoiceIdentityEligibilityProvider:
    requested = str(settings.voice_identity_eligibility_provider or "disabled").strip().lower()
    if requested in {"httpjson", "http_json"}:
        try:
            return HttpJsonVoiceIdentityEligibilityProvider(
                endpoint=str(settings.voice_identity_eligibility_http_json_url or ""),
                api_key=str(settings.voice_identity_eligibility_http_json_api_key or ""),
                timeout_seconds=settings.voice_identity_eligibility_http_json_timeout_seconds,
                transport=transport,
            )
        except VoiceIdentityEligibilityConfigurationError:
            pass
    return UnavailableVoiceIdentityEligibilityProvider()


def voice_identity_eligibility_runtime_descriptor(settings: Settings) -> dict[str, Any]:
    provider = make_voice_identity_eligibility_provider(settings)
    ready = bool(provider.is_configured)
    return {
        "provider": provider.provider_kind,
        "ready": ready,
        "reason": "ready" if ready else "identityLivenessProviderUnavailable",
        "contractVersion": VOICE_IDENTITY_ELIGIBILITY_CONTRACT_VERSION,
    }


def _required_text(value: object, *, field: str, limit: int) -> str:
    candidate = str(value or "").strip()
    if not candidate or len(candidate) > limit:
        raise VoiceIdentityEligibilityProviderResponseError(f"invalid {field}")
    return candidate


def _parse_timestamp(value: object, *, field: str) -> datetime:
    candidate = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError as exc:
        raise VoiceIdentityEligibilityProviderResponseError(f"invalid {field}") from exc
    if parsed.tzinfo is None:
        raise VoiceIdentityEligibilityProviderResponseError(f"invalid {field}")
    return parsed.astimezone(timezone.utc)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _https_endpoint(value: object) -> str:
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
        raise VoiceIdentityEligibilityConfigurationError(
            "voice identity verifier endpoint must be a clean HTTPS URL"
        )
    return candidate


__all__ = [
    "HttpJsonVoiceIdentityEligibilityProvider",
    "UnavailableVoiceIdentityEligibilityProvider",
    "VOICE_IDENTITY_ELIGIBILITY_CONTRACT_VERSION",
    "VoiceIdentityEligibilityConfigurationError",
    "VoiceIdentityEligibilityProvider",
    "VoiceIdentityEligibilityProviderResponseError",
    "VoiceIdentityEligibilityProviderUnavailable",
    "VoiceIdentityEligibilityReceipt",
    "VoiceIdentityEligibilityTransport",
    "make_voice_identity_eligibility_provider",
    "voice_identity_eligibility_runtime_descriptor",
]
