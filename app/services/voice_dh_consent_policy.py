"""Default-deny G0 policy for future Voice/DH purpose consent.

This module is intentionally a pure evaluator.  It models the immutable
receipt and purpose-grant shape needed by ``WI-V0-01-01`` without adding a
route, database table, provider call, release-policy promotion, or UI path.
Consequently even a synthetically complete envelope is only
``syntheticPreconditionsSatisfied``: it still cannot authorize a provider
effect or make Voice/DH visible in a release build.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, StrictBool, field_validator, model_validator

from app.services.safety_policy import (
    HighRiskCapability,
    SubjectEligibilityDecision,
    SubjectEligibilityReason,
)


VOICE_DH_PURPOSE_CONSENT_SCHEMA_VERSION = "voice-dh-purpose-consent-v1"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PROVIDER_REGION_PATTERN = re.compile(r"^[a-z][A-Za-z0-9_.-]{0,63}$")


class VoiceDHPurpose(str, Enum):
    TRAINING = "training"
    PREVIEW = "preview"
    PRIVATE_SYNTHESIS = "private_synthesis"
    MEMOIR = "memoir"
    DH_AUDIO_DRIVE = "dh_audio_drive"
    VISITOR_PUBLIC_VOICE = "visitor_public_voice"


class VoiceDHPurposeConsentDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    INVALID_CONTEXT = "invalid_context"
    DENIED = "denied"


class ProcessingBasis(BaseModel):
    """Immutable policy basis; no consent text, audio, or identity proof."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal[1] = 1
    basis: Literal["explicitInformedConsent"] = "explicitInformedConsent"
    policyVersion: str

    @field_validator("policyVersion")
    @classmethod
    def _validate_policy_version(cls, value: str) -> str:
        return _identifier(value, field="policyVersion")


class ConsentReceipt(BaseModel):
    """Immutable value-minimized consent receipt contract for one purpose."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal[1] = 1
    receiptHash: str
    subjectId: str
    actorId: str
    purpose: VoiceDHPurpose
    basis: ProcessingBasis
    policyVersion: str
    provider: str
    region: str
    issuedAt: datetime
    expiresAt: datetime
    revokedAt: Optional[datetime] = None
    supersedesReceiptHash: Optional[str] = None

    @field_validator("receiptHash", "supersedesReceiptHash")
    @classmethod
    def _validate_hash(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return _hash(value, field="receipt hash")

    @field_validator("subjectId", "actorId", "policyVersion")
    @classmethod
    def _validate_identifier(cls, value: str) -> str:
        return _identifier(value, field="receipt identifier")

    @field_validator("provider", "region")
    @classmethod
    def _validate_provider_region(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not _PROVIDER_REGION_PATTERN.fullmatch(normalized):
            raise ValueError("provider and region must be stable identifiers")
        return normalized

    @model_validator(mode="after")
    def _validate_lifecycle(self) -> "ConsentReceipt":
        _require_aware(self.issuedAt, field="issuedAt")
        _require_aware(self.expiresAt, field="expiresAt")
        if self.expiresAt <= self.issuedAt:
            raise ValueError("expiresAt must be after issuedAt")
        if self.revokedAt is not None:
            _require_aware(self.revokedAt, field="revokedAt")
            if self.revokedAt < self.issuedAt:
                raise ValueError("revokedAt must not predate issuedAt")
        if self.supersedesReceiptHash == self.receiptHash:
            raise ValueError("a receipt cannot supersede itself")
        if self.basis.policyVersion != self.policyVersion:
            raise ValueError("processing basis and receipt policyVersion must match")
        return self


class VoicePurposeGrant(BaseModel):
    """Immutable purpose grant that must bind a concrete ConsentReceipt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal[1] = 1
    grantHash: str
    receiptHash: str
    subjectId: str
    actorId: str
    purpose: VoiceDHPurpose
    policyVersion: str
    provider: str
    region: str
    issuedAt: datetime
    expiresAt: datetime
    revokedAt: Optional[datetime] = None
    supersedesGrantHash: Optional[str] = None

    @field_validator("grantHash", "receiptHash", "supersedesGrantHash")
    @classmethod
    def _validate_hash(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return _hash(value, field="grant hash")

    @field_validator("subjectId", "actorId", "policyVersion")
    @classmethod
    def _validate_identifier(cls, value: str) -> str:
        return _identifier(value, field="grant identifier")

    @field_validator("provider", "region")
    @classmethod
    def _validate_provider_region(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not _PROVIDER_REGION_PATTERN.fullmatch(normalized):
            raise ValueError("provider and region must be stable identifiers")
        return normalized

    @model_validator(mode="after")
    def _validate_lifecycle(self) -> "VoicePurposeGrant":
        _require_aware(self.issuedAt, field="issuedAt")
        _require_aware(self.expiresAt, field="expiresAt")
        if self.expiresAt <= self.issuedAt:
            raise ValueError("expiresAt must be after issuedAt")
        if self.revokedAt is not None:
            _require_aware(self.revokedAt, field="revokedAt")
            if self.revokedAt < self.issuedAt:
                raise ValueError("revokedAt must not predate issuedAt")
        if self.supersedesGrantHash == self.grantHash:
            raise ValueError("a grant cannot supersede itself")
        return self


class VoiceDHPurposeConsentRequest(BaseModel):
    """Server-resolved, synthetic-only policy input for the G0 evaluator."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    purpose: VoiceDHPurpose
    provider: str
    region: str
    evaluationMode: Literal["syntheticG0"]
    online: StrictBool
    legacyConsentObserved: StrictBool = False
    consentReceipt: Optional[ConsentReceipt] = None
    purposeGrant: Optional[VoicePurposeGrant] = None
    subjectEligibility: Optional[SubjectEligibilityDecision] = None

    @field_validator("provider", "region")
    @classmethod
    def _validate_provider_region(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not _PROVIDER_REGION_PATTERN.fullmatch(normalized):
            raise ValueError("provider and region must be stable identifiers")
        return normalized

class VoiceDHPurposeConsentDecision(BaseModel):
    """Value-free, non-admitting result for the Voice/DH policy shadow."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal[1] = 1
    policyVersion: str
    purpose: Optional[VoiceDHPurpose] = None
    status: VoiceDHPurposeConsentDisposition
    reasonCodes: tuple[str, ...]
    shadowOnly: Literal[True] = True
    effectAllowed: Literal[False] = False
    providerEffectAllowed: Literal[False] = False
    releaseVisible: Literal[False] = False
    consentReceiptWritten: Literal[False] = False
    purposeGrantWritten: Literal[False] = False
    legacyConsentPromoted: Literal[False] = False
    syntheticPreconditionsSatisfied: bool = False
    requiredExternalGates: tuple[Literal["G1", "G3", "G4"], ...]

    @field_validator("policyVersion")
    @classmethod
    def _validate_policy_version(cls, value: str) -> str:
        return _identifier(value, field="policyVersion")

    @field_validator("reasonCodes")
    @classmethod
    def _validate_reason_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({_identifier(reason, field="reason code") for reason in value}))
        if not normalized:
            raise ValueError("at least one reason code is required")
        return normalized

    @model_validator(mode="after")
    def _validate_default_deny(self) -> "VoiceDHPurposeConsentDecision":
        if self.status is not VoiceDHPurposeConsentDisposition.DENIED:
            if self.syntheticPreconditionsSatisfied:
                raise ValueError("only denied synthetic decisions may report complete preconditions")
        return self

    @property
    def would_be_eligible_for_future_promotion(self) -> bool:
        return False

    def value_free_summary(self) -> dict[str, object]:
        return {
            "consentReceiptWritten": self.consentReceiptWritten,
            "effectAllowed": self.effectAllowed,
            "legacyConsentPromoted": self.legacyConsentPromoted,
            "providerEffectAllowed": self.providerEffectAllowed,
            "purpose": self.purpose.value if self.purpose is not None else None,
            "purposeGrantWritten": self.purposeGrantWritten,
            "reasonCodes": list(self.reasonCodes),
            "releaseVisible": self.releaseVisible,
            "requiredExternalGates": list(self.requiredExternalGates),
            "schemaVersion": VOICE_DH_PURPOSE_CONSENT_SCHEMA_VERSION,
            "shadowOnly": self.shadowOnly,
            "status": self.status.value,
            "syntheticPreconditionsSatisfied": self.syntheticPreconditionsSatisfied,
            "wouldBeEligibleForFuturePromotion": self.would_be_eligible_for_future_promotion,
        }


class VoiceDHPurposeConsentPolicy:
    """Default-off observer for purpose-scoped Voice/DH consent evidence."""

    POLICY_VERSION = "voice-dh-purpose-consent-policy-v1"
    _KNOWN_PROVIDERS = frozenset({"volcengineVoiceClone", "tencentDigitalHuman"})
    _KNOWN_REGIONS = frozenset({"cn-mainland"})

    def observe(
        self,
        request: VoiceDHPurposeConsentRequest | object,
        *,
        enabled: object = False,
        now: datetime | None = None,
    ) -> VoiceDHPurposeConsentDecision:
        """Evaluate only synthetic evidence and never authorize an effect."""

        if enabled is not True:
            return _decision(
                status=VoiceDHPurposeConsentDisposition.SHADOW_DISABLED,
                reason_codes=("voiceDHPurposeConsentShadowDisabled",),
                purpose=None,
            )
        if not isinstance(request, VoiceDHPurposeConsentRequest):
            return _decision(
                status=VoiceDHPurposeConsentDisposition.INVALID_CONTEXT,
                reason_codes=("invalidVoiceDHPurposeConsentContext",),
                purpose=None,
            )

        try:
            evaluation_time = _evaluation_time(now)
        except ValueError:
            return _decision(
                status=VoiceDHPurposeConsentDisposition.INVALID_CONTEXT,
                reason_codes=("invalidEvaluationTime",),
                purpose=request.purpose,
            )
        reasons: set[str] = set()
        if not request.online:
            reasons.add("offlinePolicyStateDenied")
        if request.provider not in self._KNOWN_PROVIDERS:
            reasons.add("unknownProviderDenied")
        if request.region not in self._KNOWN_REGIONS:
            reasons.add("unknownRegionDenied")
        if request.legacyConsentObserved:
            reasons.add("legacyBooleanConsentNotAuthority")

        receipt = request.consentReceipt
        grant = request.purposeGrant
        eligibility = request.subjectEligibility
        if receipt is None:
            reasons.add("consentReceiptRequired")
        if grant is None:
            reasons.add("purposeGrantRequired")
        if eligibility is None:
            reasons.add("subjectEligibilityDecisionRequired")

        if receipt is not None:
            self._check_receipt(request, receipt, evaluation_time, reasons)
        if grant is not None:
            self._check_grant(request, grant, evaluation_time, reasons)
        if receipt is not None and grant is not None:
            self._check_receipt_grant_binding(receipt, grant, reasons)
        if eligibility is not None:
            self._check_eligibility(request, eligibility, reasons)

        if request.purpose is VoiceDHPurpose.VISITOR_PUBLIC_VOICE:
            reasons.add("visitorPublicVoiceRequiresM2G4Approval")

        if reasons:
            return _decision(
                status=VoiceDHPurposeConsentDisposition.DENIED,
                reason_codes=tuple(reasons),
                purpose=request.purpose,
            )

        return _decision(
            status=VoiceDHPurposeConsentDisposition.DENIED,
            reason_codes=(
                "g0SyntheticPreconditionsOnlyNoProviderEffect",
                "releasePolicyDefaultOff",
                "separateG1G3G4ApprovalRequired",
            ),
            purpose=request.purpose,
            synthetic_preconditions_satisfied=True,
        )

    def _check_receipt(
        self,
        request: VoiceDHPurposeConsentRequest,
        receipt: ConsentReceipt,
        evaluation_time: datetime,
        reasons: set[str],
    ) -> None:
        if receipt.subjectId != receipt.actorId:
            reasons.add("receiptSubjectActorMismatch")
        if receipt.purpose is not request.purpose:
            reasons.add("consentReceiptPurposeMismatch")
        if receipt.provider != request.provider:
            reasons.add("consentReceiptProviderMismatch")
        if receipt.region != request.region:
            reasons.add("consentReceiptRegionMismatch")
        if receipt.revokedAt is not None:
            reasons.add("consentReceiptRevoked")
        if receipt.issuedAt > evaluation_time:
            reasons.add("consentReceiptNotYetEffective")
        if receipt.expiresAt <= evaluation_time:
            reasons.add("consentReceiptExpired")

    def _check_grant(
        self,
        request: VoiceDHPurposeConsentRequest,
        grant: VoicePurposeGrant,
        evaluation_time: datetime,
        reasons: set[str],
    ) -> None:
        if grant.subjectId != grant.actorId:
            reasons.add("grantSubjectActorMismatch")
        if grant.purpose is not request.purpose:
            reasons.add("purposeGrantPurposeMismatch")
        if grant.provider != request.provider:
            reasons.add("purposeGrantProviderMismatch")
        if grant.region != request.region:
            reasons.add("purposeGrantRegionMismatch")
        if grant.revokedAt is not None:
            reasons.add("purposeGrantRevoked")
        if grant.issuedAt > evaluation_time:
            reasons.add("purposeGrantNotYetEffective")
        if grant.expiresAt <= evaluation_time:
            reasons.add("purposeGrantExpired")

    def _check_receipt_grant_binding(
        self,
        receipt: ConsentReceipt,
        grant: VoicePurposeGrant,
        reasons: set[str],
    ) -> None:
        if grant.receiptHash != receipt.receiptHash:
            reasons.add("purposeGrantReceiptBindingMismatch")
        if grant.subjectId != receipt.subjectId or grant.actorId != receipt.actorId:
            reasons.add("purposeGrantIdentityBindingMismatch")
        if grant.purpose is not receipt.purpose:
            reasons.add("purposeGrantPurposeBindingMismatch")
        if grant.provider != receipt.provider:
            reasons.add("purposeGrantProviderBindingMismatch")
        if grant.region != receipt.region:
            reasons.add("purposeGrantRegionBindingMismatch")
        if grant.policyVersion != receipt.policyVersion:
            reasons.add("purposeGrantPolicyBindingMismatch")

    def _check_eligibility(
        self,
        request: VoiceDHPurposeConsentRequest,
        decision: SubjectEligibilityDecision,
        reasons: set[str],
    ) -> None:
        expected_capability = (
            HighRiskCapability.DIGITAL_HUMAN
            if request.purpose is VoiceDHPurpose.DH_AUDIO_DRIVE
            else HighRiskCapability.CLONED_VOICE
        )
        if decision.capability is not expected_capability:
            reasons.add("subjectEligibilityCapabilityMismatch")
        if not decision.allowed:
            reasons.add(f"subjectEligibilityHardDeny:{decision.reason.value}")
        elif decision.reason is not SubjectEligibilityReason.ELIGIBLE_LIVING_ADULT_SELF:
            reasons.add("subjectEligibilityDecisionInconsistent")


def observe_voice_dh_purpose_consent(
    request: VoiceDHPurposeConsentRequest | object,
    *,
    enabled: object = False,
    now: datetime | None = None,
) -> VoiceDHPurposeConsentDecision:
    """Convenience API for the pure, default-deny G0 evaluator."""

    return VoiceDHPurposeConsentPolicy().observe(request, enabled=enabled, now=now)


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise ValueError(f"{field} must be an opaque identifier")
    return normalized


def _hash(value: object, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _HASH_PATTERN.fullmatch(normalized):
        raise ValueError(f"{field} must be a SHA-256 digest")
    return normalized


def _require_aware(value: datetime, *, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")


def _evaluation_time(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    _require_aware(value, field="evaluation time")
    return value.astimezone(timezone.utc)


def _decision(
    *,
    status: VoiceDHPurposeConsentDisposition,
    reason_codes: tuple[str, ...],
    purpose: VoiceDHPurpose | None,
    synthetic_preconditions_satisfied: bool = False,
) -> VoiceDHPurposeConsentDecision:
    return VoiceDHPurposeConsentDecision(
        policyVersion=VoiceDHPurposeConsentPolicy.POLICY_VERSION,
        purpose=purpose,
        status=status,
        reasonCodes=reason_codes,
        syntheticPreconditionsSatisfied=synthetic_preconditions_satisfied,
        requiredExternalGates=("G1", "G3", "G4"),
    )


__all__ = [
    "ConsentReceipt",
    "ProcessingBasis",
    "VOICE_DH_PURPOSE_CONSENT_SCHEMA_VERSION",
    "VoiceDHPurpose",
    "VoiceDHPurposeConsentDecision",
    "VoiceDHPurposeConsentDisposition",
    "VoiceDHPurposeConsentPolicy",
    "VoiceDHPurposeConsentRequest",
    "VoicePurposeGrant",
    "observe_voice_dh_purpose_consent",
]
