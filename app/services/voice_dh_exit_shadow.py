"""Default-off G0 rights-exit observer for future Voice and Digital Human cleanup.

Legacy voice-profile disable/delete routes can revoke their local profile state,
but they do not yet prove that samples, generated audio, provider assets,
Digital Human sessions, or retention copies were removed.  This pure observer
defines the fail-closed exit vocabulary before a real rights command/outbox,
provider adapter, receipt ledger, and retention worker are introduced.

It only accepts opaque internal identifiers, hashes, timestamps, and enum
states.  It never accepts raw samples, audio, provider IDs, credentials,
provider responses, or URLs; it never calls a provider or persists cleanup.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import json
import re
from threading import RLock
from typing import Any

from app.services.voice_dh_authority import VoiceDHPurpose


VOICE_DH_EXIT_SHADOW_SCHEMA_VERSION = "voice-dh-exit-shadow-v1"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_HASH_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class VoiceDHExitError(ValueError):
    """Raised when a value-minimized Voice/DH exit envelope is invalid."""


class VoiceDHExitDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    INVALID_CONTEXT = "invalid_context"
    BLOCKED = "blocked"


class VoiceDHExitAction(str, Enum):
    REVOKE_PURPOSE = "revokePurpose"
    PAUSE_PROFILE = "pauseProfile"
    DISABLE_PROFILE = "disableProfile"
    DELETE_PROFILE = "deleteProfile"
    ACCOUNT_PURGE = "accountPurge"


class VoiceDHExitResource(str, Enum):
    VOICE_PROFILE = "voiceProfile"
    VOICE_SAMPLE = "voiceSample"
    GENERATED_AUDIO = "generatedAudio"
    LOCAL_CACHE = "localCache"
    DIGITAL_HUMAN_SESSION = "digitalHumanSession"
    PROVIDER_ASSET = "providerAsset"
    BACKUP_RETENTION = "backupRetention"


class VoiceDHProviderExitState(str, Enum):
    UNKNOWN = "unknown"
    PENDING = "pending"
    ACCEPTED = "accepted"
    VERIFIED = "verified"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"


_DELETE_RESOURCES = frozenset(VoiceDHExitResource)
_REQUIRED_RESOURCES: dict[VoiceDHExitAction, frozenset[VoiceDHExitResource]] = {
    VoiceDHExitAction.REVOKE_PURPOSE: frozenset(
        {
            VoiceDHExitResource.VOICE_PROFILE,
            VoiceDHExitResource.GENERATED_AUDIO,
            VoiceDHExitResource.LOCAL_CACHE,
            VoiceDHExitResource.DIGITAL_HUMAN_SESSION,
        }
    ),
    VoiceDHExitAction.PAUSE_PROFILE: frozenset(
        {
            VoiceDHExitResource.VOICE_PROFILE,
            VoiceDHExitResource.LOCAL_CACHE,
            VoiceDHExitResource.DIGITAL_HUMAN_SESSION,
        }
    ),
    VoiceDHExitAction.DISABLE_PROFILE: frozenset(
        {
            VoiceDHExitResource.VOICE_PROFILE,
            VoiceDHExitResource.GENERATED_AUDIO,
            VoiceDHExitResource.LOCAL_CACHE,
            VoiceDHExitResource.DIGITAL_HUMAN_SESSION,
        }
    ),
    VoiceDHExitAction.DELETE_PROFILE: _DELETE_RESOURCES,
    VoiceDHExitAction.ACCOUNT_PURGE: _DELETE_RESOURCES,
}


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise VoiceDHExitError(f"{field} must be an opaque identifier")
    return normalized


def _private_identifier(value: object, *, field: str) -> str:
    normalized = _identifier(value, field=field)
    if normalized.startswith("S_"):
        raise VoiceDHExitError(f"{field} must not be a provider identifier")
    return normalized


def _hash(value: object, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _HASH_PATTERN.fullmatch(normalized):
        raise VoiceDHExitError(f"{field} must be a lowercase SHA-256 digest")
    return normalized


def _utc(value: object, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise VoiceDHExitError(f"{field} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _nonnegative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise VoiceDHExitError(f"{field} must be a non-negative integer")
    return value


def _positive_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise VoiceDHExitError(f"{field} must be a positive integer")
    return value


def _canonical_json(value: dict[str, object]) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise VoiceDHExitError("exit material must be serializable") from error


@dataclass(frozen=True)
class VoiceDHExitAuthorityContext:
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
class VoiceDHExitCommand:
    """Opaque command for a future multi-layer Voice/DH exit DAG.

    Resource names state the required cleanup layers.  They are not outcomes:
    a layer can only become ``verified`` after a future durable receipt exists.
    """

    command_id: str
    vault_id: str
    owner_subject_id: str
    actor_subject_id: str
    authority_epoch: int
    profile_id: str
    profile_version: int
    runtime_id: str
    runtime_generation: int
    action: VoiceDHExitAction
    purpose: VoiceDHPurpose
    requested_resources: tuple[VoiceDHExitResource, ...]
    issued_at: datetime
    request_hash: str

    def __post_init__(self) -> None:
        for field in ("command_id", "vault_id", "owner_subject_id", "actor_subject_id"):
            object.__setattr__(self, field, _identifier(getattr(self, field), field=field))
        object.__setattr__(self, "profile_id", _private_identifier(self.profile_id, field="profile_id"))
        object.__setattr__(self, "runtime_id", _private_identifier(self.runtime_id, field="runtime_id"))
        object.__setattr__(
            self,
            "authority_epoch",
            _nonnegative_int(self.authority_epoch, field="authority_epoch"),
        )
        object.__setattr__(
            self,
            "profile_version",
            _positive_int(self.profile_version, field="profile_version"),
        )
        object.__setattr__(
            self,
            "runtime_generation",
            _nonnegative_int(self.runtime_generation, field="runtime_generation"),
        )
        if not isinstance(self.action, VoiceDHExitAction):
            raise VoiceDHExitError("action is required")
        if not isinstance(self.purpose, VoiceDHPurpose):
            raise VoiceDHExitError("purpose is required")
        try:
            resources = tuple(VoiceDHExitResource(resource) for resource in self.requested_resources)
        except (TypeError, ValueError) as error:
            raise VoiceDHExitError("requested_resources contains an unsupported resource") from error
        if not resources or len(set(resources)) != len(resources):
            raise VoiceDHExitError("requested_resources must be a non-empty unique collection")
        required_resources = _REQUIRED_RESOURCES[self.action]
        if not required_resources.issubset(resources):
            raise VoiceDHExitError("requested_resources does not cover the required exit layers")
        object.__setattr__(self, "requested_resources", tuple(sorted(resources, key=lambda resource: resource.value)))
        object.__setattr__(self, "issued_at", _utc(self.issued_at, field="issued_at"))
        object.__setattr__(self, "request_hash", _hash(self.request_hash, field="request_hash"))

    @property
    def exit_fingerprint(self) -> str:
        material = {
            "action": self.action.value,
            "authorityEpoch": self.authority_epoch,
            "commandId": self.command_id,
            "profileId": self.profile_id,
            "profileVersion": self.profile_version,
            "purpose": self.purpose.value,
            "requestHash": self.request_hash,
            "requestedResources": [resource.value for resource in self.requested_resources],
            "runtimeGeneration": self.runtime_generation,
            "runtimeId": self.runtime_id,
            "vaultId": self.vault_id,
        }
        return sha256(_canonical_json(material).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class VoiceDHExitResult:
    disposition: VoiceDHExitDisposition
    reason_codes: tuple[str, ...]
    authority_context_valid: bool
    access_revocation_required: bool
    new_effects_must_be_denied: bool
    runtime_clear_required: bool
    provider_exit_state: VoiceDHProviderExitState
    provider_exit_receipt_persisted: bool
    local_cleanup_performed: bool
    server_cleanup_persisted: bool

    def __post_init__(self) -> None:
        if not isinstance(self.disposition, VoiceDHExitDisposition):
            raise TypeError("exit disposition is required")
        if not isinstance(self.provider_exit_state, VoiceDHProviderExitState):
            raise TypeError("provider exit state is required")
        reason_codes = tuple(sorted({_identifier(value, field="reason_code") for value in self.reason_codes}))
        if not reason_codes:
            raise VoiceDHExitError("at least one reason code is required")
        object.__setattr__(self, "reason_codes", reason_codes)

    def value_free_summary(self) -> dict[str, object]:
        return {
            "accessRevocationRequired": self.access_revocation_required,
            "authorityContextValid": self.authority_context_valid,
            "localCleanupPerformed": self.local_cleanup_performed,
            "newEffectsMustBeDenied": self.new_effects_must_be_denied,
            "providerExitReceiptPersisted": self.provider_exit_receipt_persisted,
            "providerExitState": self.provider_exit_state.value,
            "reasonCodes": list(self.reason_codes),
            "releaseVisible": False,
            "schemaVersion": VOICE_DH_EXIT_SHADOW_SCHEMA_VERSION,
            "serverCleanupPersisted": self.server_cleanup_persisted,
            "status": self.disposition.value,
        }


class VoiceDHExitShadow:
    """In-memory G0 observer; it cannot revoke, delete, persist, or dispatch."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._request_hashes: dict[tuple[str, str], str] = {}
        self._highest_authority_epochs: dict[tuple[str, str, str], int] = {}
        self._highest_runtime_generations: dict[tuple[str, str, str], int] = {}

    def observe(
        self,
        *,
        context: VoiceDHExitAuthorityContext | object,
        command: VoiceDHExitCommand | object,
        enabled: object = False,
        now: datetime | object | None = None,
    ) -> VoiceDHExitResult:
        if enabled is not True:
            return VoiceDHExitResult(
                disposition=VoiceDHExitDisposition.SHADOW_DISABLED,
                reason_codes=("voiceDHExitShadowDisabled",),
                authority_context_valid=False,
                access_revocation_required=False,
                new_effects_must_be_denied=False,
                runtime_clear_required=False,
                provider_exit_state=VoiceDHProviderExitState.UNKNOWN,
                provider_exit_receipt_persisted=False,
                local_cleanup_performed=False,
                server_cleanup_persisted=False,
            )
        if not isinstance(context, VoiceDHExitAuthorityContext) or not isinstance(command, VoiceDHExitCommand):
            return VoiceDHExitResult(
                disposition=VoiceDHExitDisposition.INVALID_CONTEXT,
                reason_codes=("invalidVoiceDHExitContext",),
                authority_context_valid=False,
                access_revocation_required=True,
                new_effects_must_be_denied=True,
                runtime_clear_required=True,
                provider_exit_state=VoiceDHProviderExitState.UNKNOWN,
                provider_exit_receipt_persisted=False,
                local_cleanup_performed=False,
                server_cleanup_persisted=False,
            )

        observed_at = datetime.now(timezone.utc) if now is None else _utc(now, field="now")
        reasons: set[str] = {
            "accessRevocationMustPrecedeCleanup",
            "g0NoExitPersistence",
            "g0NoProviderDeleteOrExit",
            "g2ExitDAGAndReceiptStoreRequired",
            "g3ProviderDeleteQueryRequired",
            "noCompletionClaimWithoutReceipt",
            "providerExitUnknownUntilReceipt",
            "releasePolicyDefaultOff",
        }
        authority_context_valid = True
        if command.vault_id != context.vault_id or command.owner_subject_id != context.owner_subject_id:
            reasons.add("ownerVaultAuthorityMismatch")
            authority_context_valid = False
        if command.actor_subject_id != context.actor_subject_id:
            reasons.add("commandActorMismatch")
            authority_context_valid = False
        if context.actor_subject_id != context.owner_subject_id:
            reasons.add("actorNotOwnerForG0")
            authority_context_valid = False
        if command.authority_epoch != context.authority_epoch:
            reasons.add("authorityEpochMismatch")
            authority_context_valid = False
        if command.issued_at > observed_at:
            reasons.add("issuedAtInFuture")
            authority_context_valid = False

        exit_key = (context.vault_id, context.owner_subject_id, command.profile_id)
        runtime_key = (context.vault_id, context.owner_subject_id, command.runtime_id)
        with self._lock:
            highest_epoch = self._highest_authority_epochs.get(exit_key)
            if highest_epoch is not None and command.authority_epoch < highest_epoch:
                reasons.add("staleAuthorityEpoch")
                authority_context_valid = False
            else:
                self._highest_authority_epochs[exit_key] = command.authority_epoch

            highest_runtime_generation = self._highest_runtime_generations.get(runtime_key)
            if highest_runtime_generation is not None and command.runtime_generation < highest_runtime_generation:
                reasons.add("staleRuntimeGeneration")
                authority_context_valid = False
            else:
                self._highest_runtime_generations[runtime_key] = command.runtime_generation

            request_key = (context.vault_id, command.command_id)
            existing_hash = self._request_hashes.get(request_key)
            if existing_hash is None:
                self._request_hashes[request_key] = command.request_hash
            elif existing_hash == command.request_hash:
                reasons.add("stableExitCommandReplayObserved")
            else:
                reasons.add("stableExitCommandHashConflict")
                authority_context_valid = False

        return VoiceDHExitResult(
            disposition=VoiceDHExitDisposition.BLOCKED,
            reason_codes=tuple(reasons),
            authority_context_valid=authority_context_valid,
            access_revocation_required=True,
            new_effects_must_be_denied=True,
            runtime_clear_required=VoiceDHExitResource.DIGITAL_HUMAN_SESSION in command.requested_resources,
            provider_exit_state=VoiceDHProviderExitState.UNKNOWN,
            provider_exit_receipt_persisted=False,
            local_cleanup_performed=False,
            server_cleanup_persisted=False,
        )


__all__ = [
    "VOICE_DH_EXIT_SHADOW_SCHEMA_VERSION",
    "VoiceDHExitAction",
    "VoiceDHExitAuthorityContext",
    "VoiceDHExitCommand",
    "VoiceDHExitDisposition",
    "VoiceDHExitError",
    "VoiceDHExitResource",
    "VoiceDHExitResult",
    "VoiceDHExitShadow",
    "VoiceDHProviderExitState",
]
