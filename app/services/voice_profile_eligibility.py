"""Server-trusted eligibility boundary for private VoiceProfiles.

Voice cloning is a high-risk capability. A mobile client may describe a
workflow, but it must never assert that somebody is a living adult, that
liveness passed, or that the subject matches the authenticated owner. The
first V4 implementation deliberately fails closed until a server-side identity
and liveness receipt provider is installed.

The small ``synthetic_test_resolution`` helper is intentionally test-only: the
production resolver never reads caller-supplied eligibility data.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.safety_policy import (
    HighRiskCapability,
    SubjectEligibilityDecision,
    SubjectEligibilityReason,
)


VOICE_PROFILE_ELIGIBILITY_PROVENANCE_SERVER_VERIFIED = "serverVerified"
VOICE_PROFILE_ELIGIBILITY_PROVENANCE_SYNTHETIC_TEST = "syntheticTest"


@dataclass(frozen=True)
class VoiceProfileEligibilityResolution:
    """A value-minimized decision and the trusted service that made it."""

    decision: SubjectEligibilityDecision
    provenance: str


class VoiceProfileEligibilityResolver:
    """Default-deny resolver until a real server-side verifier is connected."""

    def resolve(
        self,
        *,
        actor_user_id: str,
        profile_user_id: str,
    ) -> VoiceProfileEligibilityResolution:
        if str(actor_user_id or "").strip() != str(profile_user_id or "").strip():
            reason = SubjectEligibilityReason.SUBJECT_MISMATCH
        else:
            # Phone/session authentication is not an age or liveness receipt.
            # Never promote caller-provided JSON claims into this decision.
            reason = SubjectEligibilityReason.AGE_VERIFICATION_MISSING
        return VoiceProfileEligibilityResolution(
            decision=SubjectEligibilityDecision(
                capability=HighRiskCapability.CLONED_VOICE,
                allowed=False,
                decision="hardDeny",
                reason=reason,
            ),
            provenance="unavailable",
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
    )


__all__ = [
    "VOICE_PROFILE_ELIGIBILITY_PROVENANCE_SERVER_VERIFIED",
    "VOICE_PROFILE_ELIGIBILITY_PROVENANCE_SYNTHETIC_TEST",
    "VoiceProfileEligibilityResolution",
    "VoiceProfileEligibilityResolver",
    "synthetic_test_resolution",
]
