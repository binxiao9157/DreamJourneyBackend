"""Default-off G0 boundary for deceased Memorial high-risk capabilities.

Memorial Vault management, family relationship and possession of old material
cannot authorize a deceased person's Voice, Portrait, Digital Human or
Publication capability. This module assesses only a future decision envelope.
It neither persists a ``MemorialCapabilityDecision`` nor calls a Provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import re
from uuid import UUID


OWNER_TRUTH_MEMORIAL_CAPABILITY_NON_ADMISSION_SHADOW_SCHEMA_VERSION = (
    "owner-truth-memorial-capability-non-admission-shadow-v1"
)
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")


class MemorialCapabilityNonAdmissionContractError(ValueError):
    """Raised when a server-resolved Memorial capability envelope is invalid."""


class MemorialCapabilityPurpose(str, Enum):
    VOICE_TRAINING = "voice_training"
    VOICE_SYNTHESIS_PRIVATE = "voice_synthesis_private"
    PORTRAIT_RENDERING = "portrait_rendering"
    DIGITAL_HUMAN_PRIVATE = "digital_human_private"
    PUBLICATION_TEXT = "publication_text"
    PUBLICATION_VOICE = "publication_voice"
    PUBLICATION_DIGITAL_HUMAN = "publication_digital_human"


class MemorialCapabilityDecisionState(str, Enum):
    NOT_REQUESTED = "not_requested"
    EVIDENCE_REVIEW = "evidence_review"
    APPROVED_PRIVATE = "approved_private"
    APPROVED_PUBLIC = "approved_public"
    SUSPENDED = "suspended"
    EXPIRED = "expired"
    REVOKED = "revoked"
    REJECTED = "rejected"


class MemorialCapabilitySubjectStatus(str, Enum):
    DECEASED = "deceased"
    LIVING = "living"
    UNKNOWN = "unknown"


class MemorialCapabilityNonAdmissionDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    INVALID_CONTEXT = "invalid_context"
    REPRESENTED_LOGIN_PRINCIPAL_FORBIDDEN = "represented_login_principal_forbidden"
    NON_DECEASED_CONTEXT_FORBIDDEN = "non_deceased_context_forbidden"
    NOT_REQUESTED_NO_INTENT_EVIDENCE = "not_requested_no_intent_evidence"
    REJECTED_PREREQUISITES = "rejected_prerequisites"
    EXTERNAL_M3_G2_G3_G4_REQUIRED = "external_m3_g2_g3_g4_required"


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise MemorialCapabilityNonAdmissionContractError(
            f"{field} must be an opaque identifier"
        )
    return normalized


def _uuid(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    try:
        return str(UUID(normalized))
    except (TypeError, ValueError) as exc:
        raise MemorialCapabilityNonAdmissionContractError(f"{field} must be a UUID") from exc


def _non_negative(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MemorialCapabilityNonAdmissionContractError(
            f"{field} must be a non-negative integer"
        )
    return value


def _digest(value: object) -> str:
    try:
        canonical = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise MemorialCapabilityNonAdmissionContractError(
            "memorial capability material must be JSON serializable"
        ) from exc
    return sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MemorialCapabilityNonAdmissionContext:
    """Read-only, server-resolved context for one independent purpose."""

    vault_id: str
    represented_persona_id: str
    represented_subject_status: MemorialCapabilitySubjectStatus
    purpose: MemorialCapabilityPurpose
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
            subject_status = MemorialCapabilitySubjectStatus(self.represented_subject_status)
        except ValueError as exc:
            raise MemorialCapabilityNonAdmissionContractError(
                "represented_subject_status is unsupported"
            ) from exc
        object.__setattr__(self, "represented_subject_status", subject_status)
        try:
            purpose = MemorialCapabilityPurpose(self.purpose)
        except ValueError as exc:
            raise MemorialCapabilityNonAdmissionContractError("purpose is unsupported") from exc
        object.__setattr__(self, "purpose", purpose)

    def scope_hash(self) -> str:
        return _digest(
            {
                "personaId": self.represented_persona_id,
                "purpose": self.purpose.value,
                "vaultId": self.vault_id,
            }
        )


@dataclass(frozen=True)
class MemorialCapabilityNonAdmissionClaims:
    """Synthetic assertions that cannot authorize a real capability decision."""

    memorial_vault_private_active: bool = False
    controller_appointment_active: bool = False
    death_and_kinship_verified: bool = False
    source_provenance_valid: bool = False
    deceased_intent_evidence_covers_exact_purpose: bool = False
    jurisdiction_policy_allowed: bool = False
    provider_contract_allowed: bool = False
    no_active_rights_claim_or_conflict_hold: bool = False
    ai_disclosure_and_labeling_ready: bool = False
    release_policy_enabled: bool = False
    m3_case_review_approved: bool = False

    def __post_init__(self) -> None:
        for field in (
            "memorial_vault_private_active",
            "controller_appointment_active",
            "death_and_kinship_verified",
            "source_provenance_valid",
            "deceased_intent_evidence_covers_exact_purpose",
            "jurisdiction_policy_allowed",
            "provider_contract_allowed",
            "no_active_rights_claim_or_conflict_hold",
            "ai_disclosure_and_labeling_ready",
            "release_policy_enabled",
            "m3_case_review_approved",
        ):
            if not isinstance(getattr(self, field), bool):
                raise MemorialCapabilityNonAdmissionContractError(f"{field} must be a boolean")

    @property
    def prerequisites_asserted(self) -> bool:
        return all(
            (
                self.memorial_vault_private_active,
                self.controller_appointment_active,
                self.death_and_kinship_verified,
                self.source_provenance_valid,
                self.deceased_intent_evidence_covers_exact_purpose,
                self.jurisdiction_policy_allowed,
                self.provider_contract_allowed,
                self.no_active_rights_claim_or_conflict_hold,
                self.ai_disclosure_and_labeling_ready,
                self.release_policy_enabled,
                self.m3_case_review_approved,
            )
        )

    def missing_reason_codes(self) -> tuple[str, ...]:
        requirements = (
            (self.memorial_vault_private_active, "memorialVaultPrivateActiveRequired"),
            (self.controller_appointment_active, "activeControllerAppointmentRequired"),
            (self.death_and_kinship_verified, "verifiedDeathAndKinshipRequired"),
            (self.source_provenance_valid, "validSourceProvenanceRequired"),
            (self.jurisdiction_policy_allowed, "jurisdictionPolicyApprovalRequired"),
            (self.provider_contract_allowed, "providerContractApprovalRequired"),
            (
                self.no_active_rights_claim_or_conflict_hold,
                "activeRightsClaimOrConflictHoldBlocksCapability",
            ),
            (self.ai_disclosure_and_labeling_ready, "aiDisclosureAndLabelingRequired"),
            (self.release_policy_enabled, "releasePolicyEnabledRequired"),
            (self.m3_case_review_approved, "m3CaseReviewApprovalRequired"),
        )
        return tuple(reason for asserted, reason in requirements if not asserted)


@dataclass(frozen=True)
class MemorialCapabilityNonAdmissionShadow:
    """Value-free output that never turns synthetic evidence into permission."""

    enabled: bool
    disposition: MemorialCapabilityNonAdmissionDisposition
    reason_codes: tuple[str, ...]
    proposed_decision_state: MemorialCapabilityDecisionState | None = None
    scope_hash: str | None = None
    captured_authority_epoch: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise MemorialCapabilityNonAdmissionContractError("enabled must be a boolean")
        if not isinstance(self.disposition, MemorialCapabilityNonAdmissionDisposition):
            raise MemorialCapabilityNonAdmissionContractError("disposition is required")
        reasons = tuple(sorted({_identifier(reason, field="reason_code") for reason in self.reason_codes}))
        if not reasons:
            raise MemorialCapabilityNonAdmissionContractError("at least one reason code is required")
        object.__setattr__(self, "reason_codes", reasons)
        if self.proposed_decision_state is not None:
            try:
                state = MemorialCapabilityDecisionState(self.proposed_decision_state)
            except ValueError as exc:
                raise MemorialCapabilityNonAdmissionContractError(
                    "proposed_decision_state is unsupported"
                ) from exc
            object.__setattr__(self, "proposed_decision_state", state)
        if self.scope_hash is not None and not re.fullmatch(r"[0-9a-f]{64}", self.scope_hash):
            raise MemorialCapabilityNonAdmissionContractError("scope_hash must be a SHA-256 digest")
        if self.captured_authority_epoch is not None:
            object.__setattr__(
                self,
                "captured_authority_epoch",
                _non_negative(self.captured_authority_epoch, field="captured_authority_epoch"),
            )

    @property
    def capability_admitted(self) -> bool:
        return False

    @property
    def capability_decision_written(self) -> bool:
        return False

    @property
    def provider_effect_allowed(self) -> bool:
        return False

    @property
    def voice_or_portrait_training_allowed(self) -> bool:
        return False

    @property
    def digital_human_session_allowed(self) -> bool:
        return False

    @property
    def publication_allowed(self) -> bool:
        return False

    @property
    def fallback_to_family_voice_allowed(self) -> bool:
        return False

    @property
    def default_system_voice_may_be_described_as_deceased(self) -> bool:
        return False

    @property
    def records_written(self) -> bool:
        return False

    def value_free_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {
            "capabilityAdmitted": self.capability_admitted,
            "capabilityDecisionWritten": self.capability_decision_written,
            "defaultSystemVoiceMayBeDescribedAsDeceased": (
                self.default_system_voice_may_be_described_as_deceased
            ),
            "digitalHumanSessionAllowed": self.digital_human_session_allowed,
            "enabled": self.enabled,
            "fallbackToFamilyVoiceAllowed": self.fallback_to_family_voice_allowed,
            "providerEffectAllowed": self.provider_effect_allowed,
            "publicationAllowed": self.publication_allowed,
            "reasonCodes": list(self.reason_codes),
            "recordsWritten": self.records_written,
            "representedPersonaLoginPrincipal": False,
            "requiredExternalGates": ["M3", "G2", "G3", "G4"],
            "schemaVersion": OWNER_TRUTH_MEMORIAL_CAPABILITY_NON_ADMISSION_SHADOW_SCHEMA_VERSION,
            "shadowOnly": True,
            "status": self.disposition.value,
            "voiceOrPortraitTrainingAllowed": self.voice_or_portrait_training_allowed,
        }
        if self.proposed_decision_state is not None:
            summary["proposedDecisionState"] = self.proposed_decision_state.value
        if self.scope_hash is not None:
            summary["scopeHash"] = self.scope_hash
        if self.captured_authority_epoch is not None:
            summary["capturedAuthorityEpoch"] = self.captured_authority_epoch
        return summary


def evaluate_memorial_capability_non_admission(
    context: MemorialCapabilityNonAdmissionContext | object,
    *,
    claims: MemorialCapabilityNonAdmissionClaims | object | None = None,
    enabled: bool = False,
) -> MemorialCapabilityNonAdmissionShadow:
    """Fail closed for every deceased high-risk capability in the current plan."""

    if not enabled:
        return MemorialCapabilityNonAdmissionShadow(
            enabled=False,
            disposition=MemorialCapabilityNonAdmissionDisposition.SHADOW_DISABLED,
            reason_codes=("shadowDisabled",),
        )
    if not isinstance(context, MemorialCapabilityNonAdmissionContext):
        return MemorialCapabilityNonAdmissionShadow(
            enabled=True,
            disposition=MemorialCapabilityNonAdmissionDisposition.INVALID_CONTEXT,
            reason_codes=("invalidMemorialCapabilityContext",),
        )
    if context.represented_login_subject_id is not None:
        return MemorialCapabilityNonAdmissionShadow(
            enabled=True,
            disposition=(
                MemorialCapabilityNonAdmissionDisposition.REPRESENTED_LOGIN_PRINCIPAL_FORBIDDEN
            ),
            reason_codes=("representedPersonaCannotBeLoginPrincipal",),
        )
    if context.represented_subject_status is not MemorialCapabilitySubjectStatus.DECEASED:
        return MemorialCapabilityNonAdmissionShadow(
            enabled=True,
            disposition=MemorialCapabilityNonAdmissionDisposition.NON_DECEASED_CONTEXT_FORBIDDEN,
            reason_codes=("memorialCapabilityBoundaryRequiresDeceasedRepresentedPersona",),
        )
    if claims is None:
        claims = MemorialCapabilityNonAdmissionClaims()
    if not isinstance(claims, MemorialCapabilityNonAdmissionClaims):
        return MemorialCapabilityNonAdmissionShadow(
            enabled=True,
            disposition=MemorialCapabilityNonAdmissionDisposition.INVALID_CONTEXT,
            reason_codes=("invalidMemorialCapabilityClaims",),
        )

    scope_hash = context.scope_hash()
    if not claims.deceased_intent_evidence_covers_exact_purpose:
        return MemorialCapabilityNonAdmissionShadow(
            enabled=True,
            disposition=(
                MemorialCapabilityNonAdmissionDisposition.NOT_REQUESTED_NO_INTENT_EVIDENCE
            ),
            reason_codes=(
                "deceasedIntentEvidenceMustCoverExactPurpose",
                "memorialVoicePortraitAndDigitalHumanRemainNoGoWithoutIntentEvidence",
            ),
            proposed_decision_state=MemorialCapabilityDecisionState.NOT_REQUESTED,
            scope_hash=scope_hash,
            captured_authority_epoch=context.authority_epoch,
        )
    if not claims.prerequisites_asserted:
        return MemorialCapabilityNonAdmissionShadow(
            enabled=True,
            disposition=MemorialCapabilityNonAdmissionDisposition.REJECTED_PREREQUISITES,
            reason_codes=claims.missing_reason_codes()
            + ("syntheticClaimsCannotAuthorizeMemorialCapability",),
            proposed_decision_state=MemorialCapabilityDecisionState.REJECTED,
            scope_hash=scope_hash,
            captured_authority_epoch=context.authority_epoch,
        )
    return MemorialCapabilityNonAdmissionShadow(
        enabled=True,
        disposition=(
            MemorialCapabilityNonAdmissionDisposition.EXTERNAL_M3_G2_G3_G4_REQUIRED
        ),
        reason_codes=(
            "futureMemorialCapabilityDecisionMustBePurposeSpecific",
            "futureMemorialCapabilityWriterRequiresIndependentEvidence",
            "noCurrentLivingSelfVoiceOrDigitalHumanRuntimeMayBeReused",
            "shadowClaimsCannotAuthorizeMemorialCapability",
        ),
        proposed_decision_state=MemorialCapabilityDecisionState.EVIDENCE_REVIEW,
        scope_hash=scope_hash,
        captured_authority_epoch=context.authority_epoch,
    )


__all__ = [
    "OWNER_TRUTH_MEMORIAL_CAPABILITY_NON_ADMISSION_SHADOW_SCHEMA_VERSION",
    "MemorialCapabilityDecisionState",
    "MemorialCapabilityNonAdmissionClaims",
    "MemorialCapabilityNonAdmissionContext",
    "MemorialCapabilityNonAdmissionContractError",
    "MemorialCapabilityNonAdmissionDisposition",
    "MemorialCapabilityPurpose",
    "MemorialCapabilitySubjectStatus",
    "MemorialCapabilityNonAdmissionShadow",
    "evaluate_memorial_capability_non_admission",
]
