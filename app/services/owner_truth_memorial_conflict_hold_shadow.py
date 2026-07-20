"""Default-off G0 assessment for future Memorial rights claims and scope holds.

The module represents only the fail-closed decision boundary. A future writer
must atomically persist a ``MemorialConflictHold``, advance the authority epoch
and suspend the affected capability. G0 intentionally does none of those
things, nor does it contact a Provider to stop or clean up generated assets.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import re
from uuid import UUID


OWNER_TRUTH_MEMORIAL_CONFLICT_HOLD_SHADOW_SCHEMA_VERSION = (
    "owner-truth-memorial-conflict-hold-shadow-v1"
)
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")


class MemorialConflictHoldContractError(ValueError):
    """Raised when a server-resolved Memorial hold envelope is malformed."""


class MemorialConflictHoldScope(str, Enum):
    VOICE_TRAINING = "voice_training"
    VOICE_SYNTHESIS_PRIVATE = "voice_synthesis_private"
    PORTRAIT_RENDERING = "portrait_rendering"
    DIGITAL_HUMAN_PRIVATE = "digital_human_private"
    PUBLICATION_TEXT = "publication_text"
    PUBLICATION_VOICE = "publication_voice"
    PUBLICATION_DIGITAL_HUMAN = "publication_digital_human"
    VAULT_CLOSURE = "vault_closure"


class MemorialConflictHoldTrigger(str, Enum):
    VERIFIED_CLOSE_RELATIVE_RIGHTS_CLAIM = "verified_close_relative_rights_claim"
    COURT_OR_REGULATOR_ORDER = "court_or_regulator_order"
    SOURCE_RIGHTS_DISPUTE = "source_rights_dispute"
    POLICY_CHANGE = "policy_change"
    UNKNOWN = "unknown"


class MemorialConflictHoldDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    INVALID_CONTEXT = "invalid_context"
    REPRESENTED_LOGIN_PRINCIPAL_FORBIDDEN = "represented_login_principal_forbidden"
    VERIFICATION_REQUIRED = "verification_required"
    HOLD_REQUIRED = "hold_required"


_TRIGGER_REASON_CODES = {
    MemorialConflictHoldTrigger.VERIFIED_CLOSE_RELATIVE_RIGHTS_CLAIM: (
        "verifiedCloseRelativeRightsClaimRequiresScopeHold"
    ),
    MemorialConflictHoldTrigger.COURT_OR_REGULATOR_ORDER: (
        "courtOrRegulatorOrderRequiresScopeHold"
    ),
    MemorialConflictHoldTrigger.SOURCE_RIGHTS_DISPUTE: "sourceRightsDisputeRequiresScopeHold",
    MemorialConflictHoldTrigger.POLICY_CHANGE: "policyChangeRequiresScopeHold",
    MemorialConflictHoldTrigger.UNKNOWN: "unknownConflictTriggerFailsClosed",
}


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise MemorialConflictHoldContractError(f"{field} must be an opaque identifier")
    return normalized


def _uuid(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    try:
        return str(UUID(normalized))
    except (TypeError, ValueError) as exc:
        raise MemorialConflictHoldContractError(f"{field} must be a UUID") from exc


def _non_negative(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MemorialConflictHoldContractError(f"{field} must be a non-negative integer")
    return value


def _digest(value: object) -> str:
    try:
        canonical = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise MemorialConflictHoldContractError(
            "memorial conflict-hold material must be JSON serializable"
        ) from exc
    return sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MemorialConflictHoldContext:
    """Read-only, server-resolved context for one hold scope and trigger."""

    vault_id: str
    represented_persona_id: str
    scope: MemorialConflictHoldScope
    trigger: MemorialConflictHoldTrigger
    authority_epoch: int
    represented_login_subject_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "vault_id", _identifier(self.vault_id, field="vault_id"))
        object.__setattr__(
            self,
            "represented_persona_id",
            _uuid(self.represented_persona_id, field="represented_persona_id"),
        )
        object.__setattr__(
            self,
            "authority_epoch",
            _non_negative(self.authority_epoch, field="authority_epoch"),
        )
        if self.represented_login_subject_id is not None:
            object.__setattr__(
                self,
                "represented_login_subject_id",
                _identifier(
                    self.represented_login_subject_id,
                    field="represented_login_subject_id",
                ),
            )
        try:
            scope = MemorialConflictHoldScope(self.scope)
        except ValueError as exc:
            raise MemorialConflictHoldContractError("scope is unsupported") from exc
        object.__setattr__(self, "scope", scope)
        try:
            trigger = MemorialConflictHoldTrigger(self.trigger)
        except ValueError as exc:
            raise MemorialConflictHoldContractError("trigger is unsupported") from exc
        object.__setattr__(self, "trigger", trigger)

    def scope_hash(self) -> str:
        return _digest(
            {
                "personaId": self.represented_persona_id,
                "scope": self.scope.value,
                "vaultId": self.vault_id,
            }
        )


@dataclass(frozen=True)
class MemorialConflictHoldClaims:
    """Synthetic proof flags; they cannot create a real claim or hold."""

    trigger_evidence_verified: bool = False
    scope_is_specific: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.trigger_evidence_verified, bool):
            raise MemorialConflictHoldContractError("trigger_evidence_verified must be a boolean")
        if not isinstance(self.scope_is_specific, bool):
            raise MemorialConflictHoldContractError("scope_is_specific must be a boolean")

    @property
    def prerequisites_asserted(self) -> bool:
        return self.trigger_evidence_verified and self.scope_is_specific


@dataclass(frozen=True)
class MemorialConflictHoldShadow:
    """Value-free G0 result; no effective hold exists until future persistence."""

    enabled: bool
    disposition: MemorialConflictHoldDisposition
    reason_codes: tuple[str, ...]
    scope_hash: str | None = None
    captured_authority_epoch: int | None = None
    conflict_hold_required: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise MemorialConflictHoldContractError("enabled must be a boolean")
        if not isinstance(self.disposition, MemorialConflictHoldDisposition):
            raise MemorialConflictHoldContractError("disposition is required")
        reasons = tuple(sorted({_identifier(reason, field="reason_code") for reason in self.reason_codes}))
        if not reasons:
            raise MemorialConflictHoldContractError("at least one reason code is required")
        object.__setattr__(self, "reason_codes", reasons)
        if self.scope_hash is not None and not re.fullmatch(r"[0-9a-f]{64}", self.scope_hash):
            raise MemorialConflictHoldContractError("scope_hash must be a SHA-256 digest")
        if self.captured_authority_epoch is not None:
            object.__setattr__(
                self,
                "captured_authority_epoch",
                _non_negative(self.captured_authority_epoch, field="captured_authority_epoch"),
            )
        if not isinstance(self.conflict_hold_required, bool):
            raise MemorialConflictHoldContractError("conflict_hold_required must be a boolean")
        if self.disposition is MemorialConflictHoldDisposition.HOLD_REQUIRED:
            if not self.conflict_hold_required or self.scope_hash is None:
                raise MemorialConflictHoldContractError(
                    "hold-required result needs a value-free scope hash"
                )
        elif self.conflict_hold_required:
            raise MemorialConflictHoldContractError(
                "only a hold-required result may require a conflict hold"
            )

    @property
    def records_written(self) -> bool:
        return False

    @property
    def conflict_hold_written(self) -> bool:
        return False

    @property
    def authority_epoch_changed(self) -> bool:
        return False

    @property
    def affected_capability_suspended(self) -> bool:
        return False

    @property
    def publication_blocked(self) -> bool:
        return False

    @property
    def provider_generation_or_playback_stopped(self) -> bool:
        return False

    @property
    def provider_cleanup_executed(self) -> bool:
        return False

    def value_free_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {
            "affectedCapabilitySuspended": self.affected_capability_suspended,
            "authorityEpochChanged": self.authority_epoch_changed,
            "authorityEpochIncrementRequired": self.conflict_hold_required,
            "conflictHoldRequired": self.conflict_hold_required,
            "conflictHoldWritten": self.conflict_hold_written,
            "enabled": self.enabled,
            "providerCleanupExecuted": self.provider_cleanup_executed,
            "providerGenerationOrPlaybackStopped": self.provider_generation_or_playback_stopped,
            "publicationBlocked": self.publication_blocked,
            "reasonCodes": list(self.reason_codes),
            "recordsWritten": self.records_written,
            "representedPersonaLoginPrincipal": False,
            "requiredExternalGates": ["G2", "G4"],
            "schemaVersion": OWNER_TRUTH_MEMORIAL_CONFLICT_HOLD_SHADOW_SCHEMA_VERSION,
            "shadowOnly": True,
            "status": self.disposition.value,
        }
        if self.scope_hash is not None:
            summary["scopeHash"] = self.scope_hash
        if self.captured_authority_epoch is not None:
            summary["capturedAuthorityEpoch"] = self.captured_authority_epoch
        return summary


def observe_memorial_conflict_hold(
    context: MemorialConflictHoldContext | object,
    *,
    claims: MemorialConflictHoldClaims | object | None = None,
    enabled: bool = False,
) -> MemorialConflictHoldShadow:
    """Assess the compulsory future scope hold without creating one in G0."""

    if not enabled:
        return MemorialConflictHoldShadow(
            enabled=False,
            disposition=MemorialConflictHoldDisposition.SHADOW_DISABLED,
            reason_codes=("shadowDisabled",),
        )
    if not isinstance(context, MemorialConflictHoldContext):
        return MemorialConflictHoldShadow(
            enabled=True,
            disposition=MemorialConflictHoldDisposition.INVALID_CONTEXT,
            reason_codes=("invalidMemorialConflictHoldContext",),
        )
    if context.represented_login_subject_id is not None:
        return MemorialConflictHoldShadow(
            enabled=True,
            disposition=(
                MemorialConflictHoldDisposition.REPRESENTED_LOGIN_PRINCIPAL_FORBIDDEN
            ),
            reason_codes=("representedPersonaCannotBeLoginPrincipal",),
        )
    if claims is None:
        claims = MemorialConflictHoldClaims()
    if not isinstance(claims, MemorialConflictHoldClaims):
        return MemorialConflictHoldShadow(
            enabled=True,
            disposition=MemorialConflictHoldDisposition.INVALID_CONTEXT,
            reason_codes=("invalidMemorialConflictHoldClaims",),
        )
    if not claims.prerequisites_asserted:
        reasons = []
        if not claims.trigger_evidence_verified:
            reasons.append("verifiedTriggerEvidenceRequiredForScopeHold")
        if not claims.scope_is_specific:
            reasons.append("specificCapabilityScopeRequiredForScopeHold")
        return MemorialConflictHoldShadow(
            enabled=True,
            disposition=MemorialConflictHoldDisposition.VERIFICATION_REQUIRED,
            reason_codes=tuple(reasons),
            scope_hash=context.scope_hash(),
            captured_authority_epoch=context.authority_epoch,
        )
    return MemorialConflictHoldShadow(
        enabled=True,
        disposition=MemorialConflictHoldDisposition.HOLD_REQUIRED,
        reason_codes=(
            _TRIGGER_REASON_CODES[context.trigger],
            "futureWriterMustAtomicallyAdvanceEpochAndSuspendScope",
            "futureWriterMustStopNewGenerationAndPlaybackBeforeProviderCleanup",
            "shadowConflictHoldDoesNotPersistOrCallProvider",
        ),
        scope_hash=context.scope_hash(),
        captured_authority_epoch=context.authority_epoch,
        conflict_hold_required=True,
    )


__all__ = [
    "OWNER_TRUTH_MEMORIAL_CONFLICT_HOLD_SHADOW_SCHEMA_VERSION",
    "MemorialConflictHoldClaims",
    "MemorialConflictHoldContext",
    "MemorialConflictHoldContractError",
    "MemorialConflictHoldDisposition",
    "MemorialConflictHoldScope",
    "MemorialConflictHoldShadow",
    "MemorialConflictHoldTrigger",
    "observe_memorial_conflict_hold",
]
