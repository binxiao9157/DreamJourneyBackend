"""Default-off G0 lifecycle observer for future Tencent Digital Human sessions.

Legacy ``digital_human_sessions`` leases are local runtime/quota guards. They
are not evidence that Tencent opened, retained, or closed a provider session.
This pure observer defines the missing server-authoritative vocabulary before a
real asset registry, session command/outbox, provider receipt store, and
cleanup reconciler are introduced. It accepts only opaque internal IDs,
hashes, timestamps, and lifecycle metadata; it never accepts a provider
session ID or credential and never invokes Tencent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import json
import re
from threading import RLock

from app.services.voice_dh_authority import VoiceDHPurpose


DIGITAL_HUMAN_SESSION_LIFECYCLE_SHADOW_SCHEMA_VERSION = "digital-human-session-lifecycle-shadow-v1"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_HASH_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class DigitalHumanSessionLifecycleError(ValueError):
    """Raised when the value-minimized lifecycle envelope is malformed."""


class DigitalHumanSessionLifecycleDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    INVALID_CONTEXT = "invalid_context"
    BLOCKED = "blocked"


class DigitalHumanSessionLifecycleOperation(str, Enum):
    OPEN = "open"
    HEARTBEAT = "heartbeat"
    CLOSE = "close"
    RECONCILE = "reconcile"


class DigitalHumanAssetState(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"
    UNKNOWN = "unknown"


class DigitalHumanAssetSource(str, Enum):
    BACKEND_REGISTRY = "backendRegistry"
    LOCAL_QA_OVERRIDE = "localQAOverride"


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise DigitalHumanSessionLifecycleError(f"{field} must be an opaque identifier")
    return normalized


def _hash(value: object, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _HASH_PATTERN.fullmatch(normalized):
        raise DigitalHumanSessionLifecycleError(f"{field} must be a lowercase SHA-256 digest")
    return normalized


def _utc(value: object, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise DigitalHumanSessionLifecycleError(f"{field} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _nonnegative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DigitalHumanSessionLifecycleError(f"{field} must be a non-negative integer")
    return value


def _canonical_json(value: dict[str, object]) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise DigitalHumanSessionLifecycleError("lifecycle material must be serializable") from error


@dataclass(frozen=True)
class DigitalHumanSessionLifecycleAuthorityContext:
    vault_id: str
    owner_subject_id: str
    actor_subject_id: str
    authority_epoch: int

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
        object.__setattr__(
            self,
            "authority_epoch",
            _nonnegative_int(self.authority_epoch, field="authority_epoch"),
        )


@dataclass(frozen=True)
class DigitalHumanSessionLifecycleCommand:
    """Opaque session lifecycle command; no provider session ID is accepted."""

    command_id: str
    runtime_id: str
    runtime_generation: int
    vault_id: str
    owner_subject_id: str
    actor_subject_id: str
    authority_epoch: int
    role_subject_id: str
    operation: DigitalHumanSessionLifecycleOperation
    asset_registry_id: str
    asset_source: DigitalHumanAssetSource
    asset_state: DigitalHumanAssetState
    provider_project_reference_hash: str
    purpose: VoiceDHPurpose
    issued_at: datetime
    lease_expires_at: datetime
    request_hash: str

    def __post_init__(self) -> None:
        for field in (
            "command_id",
            "runtime_id",
            "vault_id",
            "owner_subject_id",
            "actor_subject_id",
            "role_subject_id",
            "asset_registry_id",
        ):
            object.__setattr__(self, field, _identifier(getattr(self, field), field=field))
        object.__setattr__(
            self,
            "runtime_generation",
            _nonnegative_int(self.runtime_generation, field="runtime_generation"),
        )
        object.__setattr__(
            self,
            "authority_epoch",
            _nonnegative_int(self.authority_epoch, field="authority_epoch"),
        )
        if not isinstance(self.operation, DigitalHumanSessionLifecycleOperation):
            raise DigitalHumanSessionLifecycleError("operation is required")
        if not isinstance(self.asset_source, DigitalHumanAssetSource):
            raise DigitalHumanSessionLifecycleError("asset_source is required")
        if not isinstance(self.asset_state, DigitalHumanAssetState):
            raise DigitalHumanSessionLifecycleError("asset_state is required")
        if self.purpose is not VoiceDHPurpose.DH_AUDIO_DRIVE:
            raise DigitalHumanSessionLifecycleError("digital human lifecycle requires dh_audio_drive purpose")
        object.__setattr__(
            self,
            "provider_project_reference_hash",
            _hash(self.provider_project_reference_hash, field="provider_project_reference_hash"),
        )
        object.__setattr__(self, "request_hash", _hash(self.request_hash, field="request_hash"))
        issued_at = _utc(self.issued_at, field="issued_at")
        lease_expires_at = _utc(self.lease_expires_at, field="lease_expires_at")
        if lease_expires_at <= issued_at:
            raise DigitalHumanSessionLifecycleError("lease_expires_at must be after issued_at")
        object.__setattr__(self, "issued_at", issued_at)
        object.__setattr__(self, "lease_expires_at", lease_expires_at)

    @property
    def asset_binding_fingerprint(self) -> str:
        material = {
            "assetRegistryId": self.asset_registry_id,
            "assetSource": self.asset_source.value,
            "assetState": self.asset_state.value,
            "authorityEpoch": self.authority_epoch,
            "ownerSubjectId": self.owner_subject_id,
            "projectReferenceHash": self.provider_project_reference_hash,
            "purpose": self.purpose.value,
            "roleSubjectId": self.role_subject_id,
            "runtimeGeneration": self.runtime_generation,
            "runtimeId": self.runtime_id,
            "vaultId": self.vault_id,
        }
        return sha256(_canonical_json(material).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DigitalHumanSessionLifecycleResult:
    disposition: DigitalHumanSessionLifecycleDisposition
    operation: DigitalHumanSessionLifecycleOperation | None
    asset_candidate_eligible: bool
    clear_runtime: bool
    fallback_to_text: bool
    provider_session_known: bool
    provider_session_opened: bool
    provider_session_closed: bool
    cleanup_receipt_persisted: bool
    local_lease_provider_ready: bool
    runtime_generation_accepted: bool
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.disposition, DigitalHumanSessionLifecycleDisposition):
            raise TypeError("lifecycle disposition is required")
        if self.operation is not None and not isinstance(
            self.operation,
            DigitalHumanSessionLifecycleOperation,
        ):
            raise TypeError("operation is invalid")
        for field in (
            "asset_candidate_eligible",
            "clear_runtime",
            "fallback_to_text",
            "provider_session_known",
            "provider_session_opened",
            "provider_session_closed",
            "cleanup_receipt_persisted",
            "local_lease_provider_ready",
            "runtime_generation_accepted",
        ):
            if not isinstance(getattr(self, field), bool):
                raise DigitalHumanSessionLifecycleError(f"{field} must be a boolean")
        reasons = tuple(sorted({_identifier(value, field="reason_code") for value in self.reason_codes}))
        if not reasons:
            raise DigitalHumanSessionLifecycleError("at least one reason code is required")
        object.__setattr__(self, "reason_codes", reasons)

    def value_free_summary(self) -> dict[str, object]:
        return {
            "assetCandidateEligible": self.asset_candidate_eligible,
            "cleanupReceiptPersisted": self.cleanup_receipt_persisted,
            "clearRuntime": self.clear_runtime,
            "fallbackToText": self.fallback_to_text,
            "localLeaseProviderReady": self.local_lease_provider_ready,
            "operation": self.operation.value if self.operation is not None else None,
            "providerEffectAllowed": False,
            "providerEffectPerformed": False,
            "providerSessionClosed": self.provider_session_closed,
            "providerSessionKnown": self.provider_session_known,
            "providerSessionOpened": self.provider_session_opened,
            "releaseVisible": False,
            "runtimeGenerationAccepted": self.runtime_generation_accepted,
            "schemaVersion": DIGITAL_HUMAN_SESSION_LIFECYCLE_SHADOW_SCHEMA_VERSION,
            "status": self.disposition.value,
            "reasonCodes": list(self.reason_codes),
        }


class DigitalHumanSessionLifecycleShadow:
    """In-memory G0 observer; never opens, heartbeats, or closes Tencent state."""

    def __init__(self, *, max_lease_ttl_seconds: int = 300) -> None:
        self._max_lease_ttl_seconds = _nonnegative_int(
            max_lease_ttl_seconds,
            field="max_lease_ttl_seconds",
        )
        if self._max_lease_ttl_seconds < 1:
            raise DigitalHumanSessionLifecycleError("max_lease_ttl_seconds must be positive")
        self._lock = RLock()
        self._request_hashes: dict[tuple[str, str], str] = {}
        self._runtime_assets: dict[tuple[str, str], tuple[int, str]] = {}
        self._last_operations: dict[tuple[str, str], DigitalHumanSessionLifecycleOperation] = {}

    def observe(
        self,
        *,
        context: DigitalHumanSessionLifecycleAuthorityContext | object,
        command: DigitalHumanSessionLifecycleCommand | object,
        enabled: object = False,
        now: datetime | object | None = None,
    ) -> DigitalHumanSessionLifecycleResult:
        if enabled is not True:
            return self._result(
                DigitalHumanSessionLifecycleDisposition.SHADOW_DISABLED,
                None,
                reason_codes=("digitalHumanSessionLifecycleShadowDisabled",),
            )
        if not isinstance(context, DigitalHumanSessionLifecycleAuthorityContext) or not isinstance(
            command,
            DigitalHumanSessionLifecycleCommand,
        ):
            return self._result(
                DigitalHumanSessionLifecycleDisposition.INVALID_CONTEXT,
                None,
                clear_runtime=True,
                reason_codes=("invalidDigitalHumanSessionLifecycleContext",),
            )
        observed_at = datetime.now(timezone.utc) if now is None else _utc(now, field="now")
        reasons: set[str] = {
            "g0NoProviderSessionCommand",
            "g2SessionAndCleanupReceiptStoreRequired",
            "g3TencentSessionEvidenceRequired",
            "releasePolicyDefaultOff",
        }
        context_matches = (
            context.vault_id == command.vault_id
            and context.owner_subject_id == command.owner_subject_id
            and context.actor_subject_id == command.actor_subject_id
            and context.authority_epoch == command.authority_epoch
        )
        if context.actor_subject_id != context.owner_subject_id:
            reasons.add("contextActorOwnerMismatch")
        if not context_matches:
            reasons.add("ownerVaultAuthorityMismatch")
        role_subject_valid = command.role_subject_id == context.owner_subject_id
        if not role_subject_valid:
            reasons.add("roleSubjectNotOwnerForG0")
        if command.issued_at > observed_at:
            reasons.add("issuedAtInFuture")
        if command.lease_expires_at <= observed_at:
            reasons.add("leaseExpired")
        if (command.lease_expires_at - command.issued_at).total_seconds() > self._max_lease_ttl_seconds:
            reasons.add("leaseTtlExceedsShadowMaximum")

        generation_accepted = False
        if context_matches and context.actor_subject_id == context.owner_subject_id:
            generation_accepted, generation_reasons = self._observe_runtime_generation(command)
            reasons.update(generation_reasons)
        reasons.update(self._observe_request_replay(command))

        asset_candidate_eligible = False
        clear_runtime = not generation_accepted
        if not context_matches or context.actor_subject_id != context.owner_subject_id:
            clear_runtime = True
        elif not generation_accepted:
            reasons.add("staleOrConflictingRuntimeSession")
        elif not role_subject_valid:
            clear_runtime = True
        else:
            asset_candidate_eligible, asset_reasons = self._asset_candidate(command)
            reasons.update(asset_reasons)
            if not asset_candidate_eligible:
                clear_runtime = True
            reasons.update(self._operation_reasons(command))

        return self._result(
            DigitalHumanSessionLifecycleDisposition.BLOCKED,
            command.operation,
            asset_candidate_eligible=asset_candidate_eligible,
            clear_runtime=clear_runtime,
            runtime_generation_accepted=generation_accepted,
            reason_codes=tuple(reasons),
        )

    def _observe_request_replay(self, command: DigitalHumanSessionLifecycleCommand) -> set[str]:
        key = (command.vault_id, command.command_id)
        with self._lock:
            existing = self._request_hashes.get(key)
            if existing is None:
                self._request_hashes[key] = command.request_hash
                return set()
            if existing == command.request_hash:
                return {"stableSessionCommandReplayObserved"}
            return {"stableSessionCommandHashConflict"}

    def _observe_runtime_generation(
        self,
        command: DigitalHumanSessionLifecycleCommand,
    ) -> tuple[bool, set[str]]:
        key = (command.vault_id, command.runtime_id)
        fingerprint = command.asset_binding_fingerprint
        with self._lock:
            existing = self._runtime_assets.get(key)
            if existing is None or command.runtime_generation > existing[0]:
                self._runtime_assets[key] = (command.runtime_generation, fingerprint)
                return True, set()
            if command.runtime_generation < existing[0]:
                return False, {"staleRuntimeGeneration"}
            if fingerprint != existing[1]:
                return False, {"sameGenerationAssetSwitchConflict"}
            return True, {"stableRuntimeGenerationReplayObserved"}

    @staticmethod
    def _asset_candidate(command: DigitalHumanSessionLifecycleCommand) -> tuple[bool, set[str]]:
        reasons: set[str] = set()
        if command.asset_source is DigitalHumanAssetSource.LOCAL_QA_OVERRIDE:
            reasons.add("localQaAssetOverrideNotReleaseEligible")
        if command.asset_state is DigitalHumanAssetState.REVOKED:
            reasons.add("assetRevoked")
        elif command.asset_state is DigitalHumanAssetState.UNKNOWN:
            reasons.add("assetStateUnknown")
        return (not reasons, reasons)

    def _operation_reasons(self, command: DigitalHumanSessionLifecycleCommand) -> set[str]:
        key = (command.vault_id, command.runtime_id)
        with self._lock:
            previous = self._last_operations.get(key)
            self._last_operations[key] = command.operation
        if command.operation is DigitalHumanSessionLifecycleOperation.OPEN:
            return {"providerSessionOpenBlocked"}
        if command.operation is DigitalHumanSessionLifecycleOperation.HEARTBEAT:
            if previous is None:
                return {"heartbeatWithoutProviderSession"}
            return {"providerSessionHeartbeatBlocked"}
        if command.operation is DigitalHumanSessionLifecycleOperation.CLOSE:
            return {"providerSessionCloseUnknownRequiresReconcile"}
        return {"providerSessionUnknownRequiresReconcile"}

    @staticmethod
    def _result(
        disposition: DigitalHumanSessionLifecycleDisposition,
        operation: DigitalHumanSessionLifecycleOperation | None,
        *,
        asset_candidate_eligible: bool = False,
        clear_runtime: bool = False,
        runtime_generation_accepted: bool = False,
        reason_codes: tuple[str, ...],
    ) -> DigitalHumanSessionLifecycleResult:
        return DigitalHumanSessionLifecycleResult(
            disposition=disposition,
            operation=operation,
            asset_candidate_eligible=asset_candidate_eligible,
            clear_runtime=clear_runtime,
            fallback_to_text=True,
            provider_session_known=False,
            provider_session_opened=False,
            provider_session_closed=False,
            cleanup_receipt_persisted=False,
            local_lease_provider_ready=False,
            runtime_generation_accepted=runtime_generation_accepted,
            reason_codes=reason_codes,
        )


__all__ = [
    "DIGITAL_HUMAN_SESSION_LIFECYCLE_SHADOW_SCHEMA_VERSION",
    "DigitalHumanAssetSource",
    "DigitalHumanAssetState",
    "DigitalHumanSessionLifecycleAuthorityContext",
    "DigitalHumanSessionLifecycleCommand",
    "DigitalHumanSessionLifecycleDisposition",
    "DigitalHumanSessionLifecycleError",
    "DigitalHumanSessionLifecycleOperation",
    "DigitalHumanSessionLifecycleResult",
    "DigitalHumanSessionLifecycleShadow",
]
