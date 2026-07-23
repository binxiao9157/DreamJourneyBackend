"""Default-deny G0 preflight for future self voice-training commands.

This module is deliberately narrower than a training workflow. It accepts
only value-minimized synthetic evidence references and always blocks before a
provider effect. It does not create a SampleObject, retain audio, call a
provider, or promote a VoiceProfile to usable.
"""

from __future__ import annotations

from enum import Enum
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.services.safety_policy import (
    HighRiskCapability,
    SubjectEligibilityDecision,
    SubjectEligibilityReason,
)
from app.services.voice_dh_consent_policy import (
    VoiceDHPurpose,
    VoiceDHPurposeConsentDecision,
)


VOICE_TRAINING_PREFLIGHT_SHADOW_SCHEMA_VERSION = "voice-training-preflight-shadow-v1"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class VoiceTrainingPreflightDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    INVALID_CONTEXT = "invalid_context"
    BLOCKED = "blocked"


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise ValueError(f"{field} must be an opaque identifier")
    return normalized


def _sha256(value: object, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _HASH_PATTERN.fullmatch(normalized):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return normalized


class VoiceTrainingEvidenceReference(BaseModel):
    """Opaque evidence reference; it carries neither evidence content nor media."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal[1] = 1
    referenceHash: str

    @field_validator("referenceHash")
    @classmethod
    def _validate_reference_hash(cls, value: str) -> str:
        return _sha256(value, field="referenceHash")


class VoiceTrainingSampleDescriptor(BaseModel):
    """Unverified descriptor, never a persisted or accepted sample object."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal[1] = 1
    sourceObjectReferenceHash: str
    sampleHash: str
    mediaFormat: Literal["wav", "mp3", "m4a"]
    durationMilliseconds: int
    byteLength: int
    verificationState: Literal["unverified"] = "unverified"

    @field_validator("sourceObjectReferenceHash", "sampleHash")
    @classmethod
    def _validate_hash(cls, value: str) -> str:
        return _sha256(value, field="sample hash")

    @field_validator("durationMilliseconds", "byteLength")
    @classmethod
    def _validate_positive_int(cls, value: int) -> int:
        if isinstance(value, bool) or value < 1:
            raise ValueError("sample descriptor numeric values must be positive")
        return value


class VoiceTrainingProfileReference(BaseModel):
    """Future Authority profile reference; legacy speaker IDs are rejected."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal[1] = 1
    profileId: str
    profileVersion: int
    status: Literal["blocked"] = "blocked"

    @field_validator("profileId")
    @classmethod
    def _validate_profile_id(cls, value: str) -> str:
        profile_id = _identifier(value, field="profileId")
        if profile_id.startswith("S_"):
            raise ValueError("provider speaker IDs cannot be used as Authority profile IDs")
        return profile_id

    @field_validator("profileVersion")
    @classmethod
    def _validate_profile_version(cls, value: int) -> int:
        if isinstance(value, bool) or value < 1:
            raise ValueError("profileVersion must be positive")
        return value


class VoiceTrainingPreflightRequest(BaseModel):
    """Synthetic-only input used to prove that no provider effect is admitted."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal[1] = 1
    evaluationMode: Literal["syntheticG0"]
    vaultId: str
    ownerSubjectId: str
    actorSubjectId: str
    subjectId: str
    authorityEpoch: int
    purpose: Literal[VoiceDHPurpose.TRAINING]
    provider: Literal["volcengineVoiceClone"]
    region: Literal["cn-mainland"]
    requestHash: str
    profileReference: VoiceTrainingProfileReference
    consentDecision: VoiceDHPurposeConsentDecision
    subjectEligibility: SubjectEligibilityDecision
    randomConsentStatementReference: VoiceTrainingEvidenceReference
    livenessReference: VoiceTrainingEvidenceReference
    qualityReference: VoiceTrainingEvidenceReference
    sampleDescriptor: VoiceTrainingSampleDescriptor

    @field_validator("vaultId", "ownerSubjectId", "actorSubjectId", "subjectId")
    @classmethod
    def _validate_identifier(cls, value: str) -> str:
        return _identifier(value, field="training preflight identifier")

    @field_validator("requestHash")
    @classmethod
    def _validate_request_hash(cls, value: str) -> str:
        return _sha256(value, field="requestHash")

    @field_validator("authorityEpoch")
    @classmethod
    def _validate_epoch(cls, value: int) -> int:
        if isinstance(value, bool) or value < 0:
            raise ValueError("authorityEpoch must be non-negative")
        return value

    @model_validator(mode="after")
    def _validate_training_scope(self) -> "VoiceTrainingPreflightRequest":
        if self.consentDecision.purpose is not VoiceDHPurpose.TRAINING:
            raise ValueError("consentDecision must be scoped to training")
        return self


class VoiceTrainingPreflightDecision(BaseModel):
    """Value-free result that can never authorize or create a training command."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal[1] = 1
    policyVersion: str
    status: VoiceTrainingPreflightDisposition
    reasonCodes: tuple[str, ...]
    shadowOnly: Literal[True] = True
    providerEffectAllowed: Literal[False] = False
    providerEffectPerformed: Literal[False] = False
    trainingCommandCreated: Literal[False] = False
    sampleObjectCreated: Literal[False] = False
    releaseVisible: Literal[False] = False
    syntheticPreconditionsObserved: bool = False
    requiredExternalGates: tuple[Literal["G2", "G3", "G4"], ...] = ("G2", "G3", "G4")

    @field_validator("policyVersion")
    @classmethod
    def _validate_policy_version(cls, value: str) -> str:
        return _identifier(value, field="policyVersion")

    @field_validator("reasonCodes")
    @classmethod
    def _validate_reason_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({_identifier(reason, field="reasonCode") for reason in value}))
        if not normalized:
            raise ValueError("at least one reason code is required")
        return normalized

    def value_free_summary(self) -> dict[str, object]:
        return {
            "providerEffectAllowed": self.providerEffectAllowed,
            "providerEffectPerformed": self.providerEffectPerformed,
            "reasonCodes": list(self.reasonCodes),
            "releaseVisible": self.releaseVisible,
            "requiredExternalGates": list(self.requiredExternalGates),
            "sampleObjectCreated": self.sampleObjectCreated,
            "schemaVersion": VOICE_TRAINING_PREFLIGHT_SHADOW_SCHEMA_VERSION,
            "shadowOnly": self.shadowOnly,
            "status": self.status.value,
            "syntheticPreconditionsObserved": self.syntheticPreconditionsObserved,
            "trainingCommandCreated": self.trainingCommandCreated,
        }


class VoiceTrainingCommandPreflightShadow:
    """Default-deny observer with no training, storage, or provider side effects."""

    POLICY_VERSION = "voice-training-preflight-shadow-policy-v1"

    def __init__(self) -> None:
        self._observed_request_hashes: set[str] = set()

    def observe(
        self,
        request: VoiceTrainingPreflightRequest | object,
        *,
        enabled: object = False,
    ) -> VoiceTrainingPreflightDecision:
        if enabled is not True:
            return _decision(
                status=VoiceTrainingPreflightDisposition.SHADOW_DISABLED,
                reason_codes=("voiceTrainingPreflightShadowDisabled",),
            )
        if not isinstance(request, VoiceTrainingPreflightRequest):
            return _decision(
                status=VoiceTrainingPreflightDisposition.INVALID_CONTEXT,
                reason_codes=("invalidVoiceTrainingPreflightContext",),
            )

        reasons: set[str] = {
            "g0ShadowNoProviderEffect",
            "g2VerifiedSourceObjectRequired",
            "g3G4TrainingGatesRequired",
            "releasePolicyDefaultOff",
        }
        synthetic_preconditions_observed = True

        if request.actorSubjectId != request.ownerSubjectId or request.subjectId != request.ownerSubjectId:
            reasons.update({"familyProxyDenied", "subjectActorOwnerMismatch"})
            synthetic_preconditions_observed = False

        eligibility = request.subjectEligibility
        if eligibility.capability is not HighRiskCapability.CLONED_VOICE:
            reasons.add("subjectEligibilityCapabilityMismatch")
            synthetic_preconditions_observed = False
        if not eligibility.allowed:
            reasons.add(f"subjectEligibilityHardDeny:{eligibility.reason.value}")
            synthetic_preconditions_observed = False
        elif eligibility.reason is not SubjectEligibilityReason.ELIGIBLE_LIVING_ADULT_SELF:
            reasons.add("subjectEligibilityDecisionInconsistent")
            synthetic_preconditions_observed = False

        consent = request.consentDecision
        if not consent.syntheticPreconditionsSatisfied:
            reasons.add("consentSyntheticPreconditionsMissing")
            synthetic_preconditions_observed = False
        if consent.providerEffectAllowed or consent.effectAllowed:
            reasons.add("consentDecisionMustNotAllowProviderEffect")
            synthetic_preconditions_observed = False

        if request.requestHash in self._observed_request_hashes:
            reasons.add("duplicateTrainingCommandDenied")
            synthetic_preconditions_observed = False
        else:
            self._observed_request_hashes.add(request.requestHash)

        return _decision(
            status=VoiceTrainingPreflightDisposition.BLOCKED,
            reason_codes=tuple(reasons),
            synthetic_preconditions_observed=synthetic_preconditions_observed,
        )


def observe_voice_training_command_preflight(
    request: VoiceTrainingPreflightRequest | object,
    *,
    enabled: object = False,
) -> VoiceTrainingPreflightDecision:
    """One-shot helper for a synthetic G0 preflight without lasting state."""

    return VoiceTrainingCommandPreflightShadow().observe(request, enabled=enabled)


def _decision(
    *,
    status: VoiceTrainingPreflightDisposition,
    reason_codes: tuple[str, ...],
    synthetic_preconditions_observed: bool = False,
) -> VoiceTrainingPreflightDecision:
    return VoiceTrainingPreflightDecision(
        policyVersion=VoiceTrainingCommandPreflightShadow.POLICY_VERSION,
        status=status,
        reasonCodes=reason_codes,
        syntheticPreconditionsObserved=synthetic_preconditions_observed,
    )


__all__ = [
    "VOICE_TRAINING_PREFLIGHT_SHADOW_SCHEMA_VERSION",
    "VoiceTrainingCommandPreflightShadow",
    "VoiceTrainingEvidenceReference",
    "VoiceTrainingPreflightDecision",
    "VoiceTrainingPreflightDisposition",
    "VoiceTrainingPreflightRequest",
    "VoiceTrainingProfileReference",
    "VoiceTrainingSampleDescriptor",
    "observe_voice_training_command_preflight",
]
