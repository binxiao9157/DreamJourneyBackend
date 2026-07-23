"""Runtime smoke for the default-deny Voice Training G0 preflight.

This file intentionally lives under ``scripts/`` because production API images
contain application and script code, but not the unit-test package.
"""

from __future__ import annotations

import json
from hashlib import sha256

from app.services.safety_policy import (
    HighRiskCapability,
    SubjectEligibilityDecision,
    SubjectEligibilityReason,
)
from app.services.voice_dh_consent_policy import (
    VoiceDHPurpose,
    VoiceDHPurposeConsentDecision,
    VoiceDHPurposeConsentDisposition,
)
from app.services.voice_training_preflight_shadow import (
    VoiceTrainingCommandPreflightShadow,
    VoiceTrainingEvidenceReference,
    VoiceTrainingPreflightDisposition,
    VoiceTrainingPreflightRequest,
    VoiceTrainingProfileReference,
    VoiceTrainingSampleDescriptor,
)


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _reference(value: str) -> VoiceTrainingEvidenceReference:
    return VoiceTrainingEvidenceReference(referenceHash=_digest(value))


def main() -> None:
    request = VoiceTrainingPreflightRequest(
        evaluationMode="syntheticG0",
        vaultId="runtime-smoke-vault",
        ownerSubjectId="runtime-smoke-owner",
        actorSubjectId="runtime-smoke-owner",
        subjectId="runtime-smoke-owner",
        authorityEpoch=0,
        purpose=VoiceDHPurpose.TRAINING,
        provider="volcengineVoiceClone",
        region="cn-mainland",
        requestHash=_digest("runtime-smoke-request"),
        profileReference=VoiceTrainingProfileReference(
            profileId="runtime-smoke-profile",
            profileVersion=1,
        ),
        consentDecision=VoiceDHPurposeConsentDecision(
            policyVersion="runtime-smoke-policy",
            purpose=VoiceDHPurpose.TRAINING,
            status=VoiceDHPurposeConsentDisposition.DENIED,
            reasonCodes=("syntheticG0Only",),
            syntheticPreconditionsSatisfied=True,
            requiredExternalGates=("G1", "G3", "G4"),
        ),
        subjectEligibility=SubjectEligibilityDecision(
            capability=HighRiskCapability.CLONED_VOICE,
            allowed=True,
            decision="allow",
            reason=SubjectEligibilityReason.ELIGIBLE_LIVING_ADULT_SELF,
        ),
        randomConsentStatementReference=_reference("statement"),
        livenessReference=_reference("liveness"),
        qualityReference=_reference("quality"),
        sampleDescriptor=VoiceTrainingSampleDescriptor(
            sourceObjectReferenceHash=_digest("source-object"),
            sampleHash=_digest("sample"),
            mediaFormat="wav",
            durationMilliseconds=12_000,
            byteLength=128_000,
        ),
    )
    result = VoiceTrainingCommandPreflightShadow().observe(request, enabled=True)
    assert result.status is VoiceTrainingPreflightDisposition.BLOCKED
    assert result.syntheticPreconditionsObserved is True
    assert result.providerEffectAllowed is False
    assert result.providerEffectPerformed is False
    assert result.trainingCommandCreated is False
    assert result.sampleObjectCreated is False
    assert "g2VerifiedSourceObjectRequired" in result.reasonCodes

    print(
        json.dumps(
            {
                "providerEffectPerformed": result.providerEffectPerformed,
                "sampleObjectCreated": result.sampleObjectCreated,
                "status": "passed",
                "trainingCommandCreated": result.trainingCommandCreated,
                "voiceTrainingPreflight": result.status.value,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
