"""Default-deny G0 observer for future Voice/DH scoped capabilities.

This is not a capability issuer. It accepts only opaque identifiers, hashes,
and timestamps to make expiry, replay, audience, and owner/Vault mismatch
semantics testable before a real broker exists. Its replay observations are
in-memory only, never a substitute for a persistent one-time capability
ledger. Every result remains blocked; no database, network, Provider, session,
media, or credential effect occurs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import re
from threading import RLock

from app.services.voice_dh_authority import VoiceDHProvider, VoiceDHPurpose


VOICE_DH_SCOPED_CAPABILITY_SHADOW_SCHEMA_VERSION = "voice-dh-scoped-capability-shadow-v1"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_HASH_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class ScopedCapabilityShadowError(ValueError):
    """Raised when the value-minimized shadow contract is malformed."""


class ScopedCapabilityShadowDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    INVALID_CONTEXT = "invalid_context"
    BLOCKED = "blocked"


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise ScopedCapabilityShadowError(f"{field} must be an opaque identifier")
    return normalized


def _hash(value: object, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _HASH_PATTERN.fullmatch(normalized):
        raise ScopedCapabilityShadowError(f"{field} must be a lowercase SHA-256 digest")
    return normalized


def _utc(value: object, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ScopedCapabilityShadowError(f"{field} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class ScopedCapabilityAuthorityContext:
    vault_id: str
    owner_subject_id: str
    actor_subject_id: str
    authority_epoch: int
    audience: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "vault_id", _identifier(self.vault_id, field="vault_id"))
        object.__setattr__(
            self,
            "owner_subject_id",
            _identifier(self.owner_subject_id, field="owner_subject_id"),
        )
        object.__setattr__(
            self,
            "actor_subject_id",
            _identifier(self.actor_subject_id, field="actor_subject_id"),
        )
        if isinstance(self.authority_epoch, bool) or not isinstance(self.authority_epoch, int):
            raise ScopedCapabilityShadowError("authority_epoch must be a non-negative integer")
        if self.authority_epoch < 0:
            raise ScopedCapabilityShadowError("authority_epoch must be a non-negative integer")
        object.__setattr__(self, "audience", _identifier(self.audience, field="audience"))


@dataclass(frozen=True)
class ScopedCapabilityAdmissionRequest:
    """Hash-only capability request vocabulary; it cannot carry an issued value."""

    request_id: str
    vault_id: str
    owner_subject_id: str
    actor_subject_id: str
    subject_id: str
    authority_epoch: int
    purpose: VoiceDHPurpose
    provider: VoiceDHProvider
    resource: str
    nonce_hash: str
    issued_at: datetime
    expires_at: datetime
    audience: str
    one_time: bool
    request_hash: str

    def __post_init__(self) -> None:
        for field in (
            "request_id",
            "vault_id",
            "owner_subject_id",
            "actor_subject_id",
            "subject_id",
            "resource",
            "audience",
        ):
            object.__setattr__(self, field, _identifier(getattr(self, field), field=field))
        if isinstance(self.authority_epoch, bool) or not isinstance(self.authority_epoch, int):
            raise ScopedCapabilityShadowError("authority_epoch must be a non-negative integer")
        if self.authority_epoch < 0:
            raise ScopedCapabilityShadowError("authority_epoch must be a non-negative integer")
        if not isinstance(self.purpose, VoiceDHPurpose):
            raise ScopedCapabilityShadowError("purpose is required")
        if not isinstance(self.provider, VoiceDHProvider):
            raise ScopedCapabilityShadowError("provider is required")
        if not isinstance(self.one_time, bool):
            raise ScopedCapabilityShadowError("one_time must be a boolean")
        object.__setattr__(self, "nonce_hash", _hash(self.nonce_hash, field="nonce_hash"))
        object.__setattr__(self, "request_hash", _hash(self.request_hash, field="request_hash"))
        issued_at = _utc(self.issued_at, field="issued_at")
        expires_at = _utc(self.expires_at, field="expires_at")
        if expires_at <= issued_at:
            raise ScopedCapabilityShadowError("expires_at must be after issued_at")
        object.__setattr__(self, "issued_at", issued_at)
        object.__setattr__(self, "expires_at", expires_at)


@dataclass(frozen=True)
class ScopedCapabilityAdmissionResult:
    disposition: ScopedCapabilityShadowDisposition
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.disposition, ScopedCapabilityShadowDisposition):
            raise TypeError("capability disposition is required")
        normalized = tuple(sorted({_identifier(code, field="reason_code") for code in self.reason_codes}))
        if not normalized:
            raise ScopedCapabilityShadowError("at least one reason code is required")
        object.__setattr__(self, "reason_codes", normalized)

    def value_free_summary(self) -> dict[str, object]:
        return {
            "capabilityIssued": False,
            "nonceConsumed": False,
            "providerEffectAllowed": False,
            "providerEffectPerformed": False,
            "reasonCodes": list(self.reason_codes),
            "replayProtectionPersistent": False,
            "releaseVisible": False,
            "schemaVersion": VOICE_DH_SCOPED_CAPABILITY_SHADOW_SCHEMA_VERSION,
            "status": self.disposition.value,
        }


class ScopedCapabilityAdmissionShadow:
    """In-memory G0 observer; it is never an issuer or a persistence layer."""

    def __init__(self, *, max_ttl_seconds: int = 300) -> None:
        if isinstance(max_ttl_seconds, bool) or not isinstance(max_ttl_seconds, int):
            raise ScopedCapabilityShadowError("max_ttl_seconds must be a positive integer")
        if max_ttl_seconds < 1:
            raise ScopedCapabilityShadowError("max_ttl_seconds must be a positive integer")
        self._max_ttl_seconds = max_ttl_seconds
        self._lock = RLock()
        self._observed_request_hashes: dict[tuple[str, str], str] = {}
        self._observed_nonce_hashes: set[tuple[str, str]] = set()

    def observe(
        self,
        *,
        context: ScopedCapabilityAuthorityContext | object,
        request: ScopedCapabilityAdmissionRequest | object,
        enabled: object = False,
        now: datetime | object | None = None,
    ) -> ScopedCapabilityAdmissionResult:
        if enabled is not True:
            return ScopedCapabilityAdmissionResult(
                disposition=ScopedCapabilityShadowDisposition.SHADOW_DISABLED,
                reason_codes=("scopedCapabilityShadowDisabled",),
            )
        if not isinstance(context, ScopedCapabilityAuthorityContext) or not isinstance(
            request,
            ScopedCapabilityAdmissionRequest,
        ):
            return ScopedCapabilityAdmissionResult(
                disposition=ScopedCapabilityShadowDisposition.INVALID_CONTEXT,
                reason_codes=("invalidScopedCapabilityContext",),
            )
        observed_at = datetime.now(timezone.utc) if now is None else _utc(now, field="now")
        reasons: set[str] = {
            "g0NoCapabilityIssuer",
            "g2BrokerDeploymentRequired",
            "g3ProviderCredentialEvidenceRequired",
            "oneTimeReplayProtectionNotPersistent",
            "releasePolicyDefaultOff",
        }
        if context.actor_subject_id != context.owner_subject_id:
            reasons.add("contextActorOwnerMismatch")
        if (
            request.vault_id != context.vault_id
            or request.owner_subject_id != context.owner_subject_id
            or request.actor_subject_id != context.actor_subject_id
            or request.subject_id != context.owner_subject_id
            or request.authority_epoch != context.authority_epoch
        ):
            reasons.add("ownerVaultAuthorityMismatch")
        if request.audience != context.audience:
            reasons.add("audienceMismatch")
        if not request.one_time:
            reasons.add("oneTimeCapabilityRequired")
        if request.issued_at > observed_at:
            reasons.add("issuedAtInFuture")
        if request.expires_at <= observed_at:
            reasons.add("capabilityExpired")
        if (request.expires_at - request.issued_at).total_seconds() > self._max_ttl_seconds:
            reasons.add("ttlExceedsShadowMaximum")

        request_key = (request.vault_id, request.request_id)
        nonce_key = (request.vault_id, request.nonce_hash)
        with self._lock:
            existing_hash = self._observed_request_hashes.get(request_key)
            if existing_hash is None:
                self._observed_request_hashes[request_key] = request.request_hash
            elif existing_hash != request.request_hash:
                reasons.add("stableRequestHashConflict")
            else:
                reasons.add("stableRequestReplayObserved")
            if nonce_key in self._observed_nonce_hashes:
                reasons.add("nonceReplayObservedInMemory")
            else:
                self._observed_nonce_hashes.add(nonce_key)

        return ScopedCapabilityAdmissionResult(
            disposition=ScopedCapabilityShadowDisposition.BLOCKED,
            reason_codes=tuple(reasons),
        )


__all__ = [
    "ScopedCapabilityAdmissionRequest",
    "ScopedCapabilityAdmissionResult",
    "ScopedCapabilityAdmissionShadow",
    "ScopedCapabilityAuthorityContext",
    "ScopedCapabilityShadowDisposition",
    "ScopedCapabilityShadowError",
    "VOICE_DH_SCOPED_CAPABILITY_SHADOW_SCHEMA_VERSION",
]
