"""Server-trusted eligibility boundary for private VoiceProfiles.

Voice cloning is a high-risk capability. A mobile client may describe a
workflow, but it must never assert that somebody is a living adult, that
liveness passed, or that the subject matches the authenticated owner.  Allowed
decisions therefore require a current receipt from the server-owned identity
provider port.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from app.services.safety_policy import (
    HighRiskCapability,
    SubjectEligibilityDecision,
    SubjectEligibilityReason,
)
from app.services.voice_identity_eligibility import (
    UnavailableVoiceIdentityEligibilityProvider,
    VoiceIdentityEligibilityProvider,
    VoiceIdentityEligibilityProviderResponseError,
    VoiceIdentityEligibilityProviderUnavailable,
)


VOICE_PROFILE_ELIGIBILITY_PROVENANCE_SERVER_VERIFIED = "serverVerified"
VOICE_PROFILE_ELIGIBILITY_PROVENANCE_SYNTHETIC_TEST = "syntheticTest"


@dataclass(frozen=True)
class VoiceProfileEligibilityResolution:
    """A value-minimized decision and the trusted service that made it."""

    decision: SubjectEligibilityDecision
    provenance: str
    availability: str = "ready"
    receipt_summary: Optional[Mapping[str, Any]] = None


class VoiceProfileEligibilityResolver:
    """Map a server-owned adult/liveness receipt into the common policy type."""

    def __init__(
        self,
        provider: Optional[VoiceIdentityEligibilityProvider] = None,
    ) -> None:
        self._provider = provider or UnavailableVoiceIdentityEligibilityProvider()

    def resolve(
        self,
        *,
        actor_user_id: str,
        profile_user_id: str,
    ) -> VoiceProfileEligibilityResolution:
        normalized_actor = str(actor_user_id or "").strip()
        normalized_profile = str(profile_user_id or "").strip()
        if normalized_actor != normalized_profile:
            return _deny(SubjectEligibilityReason.SUBJECT_MISMATCH)
        try:
            receipt = self._provider.resolve(
                actor_user_id=normalized_actor,
                subject_user_id=normalized_profile,
                now=datetime.now(timezone.utc),
            )
        except VoiceIdentityEligibilityProviderUnavailable:
            return VoiceProfileEligibilityResolution(
                decision=_decision(SubjectEligibilityReason.AGE_VERIFICATION_MISSING),
                provenance="unavailable",
                availability="providerUnavailable",
            )
        except VoiceIdentityEligibilityProviderResponseError:
            return VoiceProfileEligibilityResolution(
                decision=_decision(SubjectEligibilityReason.AGE_VERIFICATION_MISSING),
                provenance="unavailable",
                availability="receiptInvalid",
            )

        summary = receipt.persistence_summary()
        if (
            receipt.actor_user_id != normalized_actor
            or receipt.subject_user_id != normalized_profile
        ):
            return _deny(SubjectEligibilityReason.SUBJECT_MISMATCH, receipt_summary=summary)
        if not receipt.is_current(now=datetime.now(timezone.utc)):
            return _deny(SubjectEligibilityReason.AGE_VERIFICATION_MISSING, receipt_summary=summary)
        if receipt.age_status == "minor":
            return _deny(SubjectEligibilityReason.MINOR, receipt_summary=summary)
        if receipt.age_status != "adult":
            return _deny(SubjectEligibilityReason.AGE_UNKNOWN, receipt_summary=summary)
        if receipt.living_status == "deceased":
            return _deny(SubjectEligibilityReason.DECEASED_SUBJECT, receipt_summary=summary)
        if receipt.living_status != "living":
            return _deny(SubjectEligibilityReason.LIVING_STATUS_UNKNOWN, receipt_summary=summary)
        if not receipt.liveness_verified:
            return _deny(SubjectEligibilityReason.LIVENESS_MISSING, receipt_summary=summary)
        return VoiceProfileEligibilityResolution(
            decision=SubjectEligibilityDecision(
                capability=HighRiskCapability.CLONED_VOICE,
                allowed=True,
                decision="allow",
                reason=SubjectEligibilityReason.ELIGIBLE_LIVING_ADULT_SELF,
            ),
            provenance=VOICE_PROFILE_ELIGIBILITY_PROVENANCE_SERVER_VERIFIED,
            receipt_summary=summary,
        )


def _decision(reason: SubjectEligibilityReason) -> SubjectEligibilityDecision:
    return SubjectEligibilityDecision(
        capability=HighRiskCapability.CLONED_VOICE,
        allowed=False,
        decision="hardDeny",
        reason=reason,
    )


def _deny(
    reason: SubjectEligibilityReason,
    *,
    receipt_summary: Optional[Mapping[str, Any]] = None,
) -> VoiceProfileEligibilityResolution:
    return VoiceProfileEligibilityResolution(
        decision=_decision(reason),
        provenance=VOICE_PROFILE_ELIGIBILITY_PROVENANCE_SERVER_VERIFIED,
        receipt_summary=receipt_summary,
    )


def synthetic_test_resolution(
    decision: SubjectEligibilityDecision,
) -> VoiceProfileEligibilityResolution:
    """Build an explicit test fixture without adding a production bypass."""

    if decision.capability is not HighRiskCapability.CLONED_VOICE:
        raise ValueError("voice profile eligibility must be for cloned voice")
    return VoiceProfileEligibilityResolution(
        decision=decision,
        provenance=VOICE_PROFILE_ELIGIBILITY_PROVENANCE_SYNTHETIC_TEST,
        availability="testOnly",
    )


def server_verified_test_resolution(
    decision: SubjectEligibilityDecision,
) -> VoiceProfileEligibilityResolution:
    """Explicit unit-test fixture representing a server-issued receipt.

    This helper is only imported by tests.  Production code always obtains the
    equivalent result from ``VoiceIdentityEligibilityProvider``.
    """

    if decision.capability is not HighRiskCapability.CLONED_VOICE:
        raise ValueError("voice profile eligibility must be for cloned voice")
    return VoiceProfileEligibilityResolution(
        decision=decision,
        provenance=VOICE_PROFILE_ELIGIBILITY_PROVENANCE_SERVER_VERIFIED,
        receipt_summary={
            "schemaVersion": 1,
            "providerKind": "testReceipt",
            "receiptHash": "sha256:" + ("0" * 64),
            "issuedAt": "2026-01-01T00:00:00+00:00",
            "expiresAt": "2030-01-01T00:00:00+00:00",
        },
    )


__all__ = [
    "VOICE_PROFILE_ELIGIBILITY_PROVENANCE_SERVER_VERIFIED",
    "VOICE_PROFILE_ELIGIBILITY_PROVENANCE_SYNTHETIC_TEST",
    "VoiceProfileEligibilityResolution",
    "VoiceProfileEligibilityResolver",
    "server_verified_test_resolution",
    "synthetic_test_resolution",
]
