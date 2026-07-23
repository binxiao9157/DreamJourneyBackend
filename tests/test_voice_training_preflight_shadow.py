"""G0 tests for the default-deny V4 self voice-training preflight."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import unittest

from pydantic import ValidationError

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


def _consent(*, complete: bool = True) -> VoiceDHPurposeConsentDecision:
    return VoiceDHPurposeConsentDecision(
        policyVersion="voice-policy-v1",
        purpose=VoiceDHPurpose.TRAINING,
        status=VoiceDHPurposeConsentDisposition.DENIED,
        reasonCodes=("syntheticG0Only",),
        syntheticPreconditionsSatisfied=complete,
        requiredExternalGates=("G1", "G3", "G4"),
    )


def _eligibility(
    *,
    allowed: bool = True,
    reason: SubjectEligibilityReason | None = None,
) -> SubjectEligibilityDecision:
    selected_reason = reason or SubjectEligibilityReason.ELIGIBLE_LIVING_ADULT_SELF
    return SubjectEligibilityDecision(
        capability=HighRiskCapability.CLONED_VOICE,
        allowed=allowed,
        decision="allow" if allowed else "hardDeny",
        reason=selected_reason,
    )


def _reference(name: str) -> VoiceTrainingEvidenceReference:
    return VoiceTrainingEvidenceReference(referenceHash=_digest(name))


def _request(**changes: object) -> VoiceTrainingPreflightRequest:
    values: dict[str, object] = {
        "evaluationMode": "syntheticG0",
        "vaultId": "vault-owner-a",
        "ownerSubjectId": "owner-a",
        "actorSubjectId": "owner-a",
        "subjectId": "owner-a",
        "authorityEpoch": 3,
        "purpose": VoiceDHPurpose.TRAINING,
        "provider": "volcengineVoiceClone",
        "region": "cn-mainland",
        "requestHash": _digest("training-request-a"),
        "profileReference": VoiceTrainingProfileReference(profileId="voice-profile-owner-a", profileVersion=1),
        "consentDecision": _consent(),
        "subjectEligibility": _eligibility(),
        "randomConsentStatementReference": _reference("statement"),
        "livenessReference": _reference("liveness"),
        "qualityReference": _reference("quality"),
        "sampleDescriptor": VoiceTrainingSampleDescriptor(
            sourceObjectReferenceHash=_digest("source-object"),
            sampleHash=_digest("sample"),
            mediaFormat="wav",
            durationMilliseconds=12_000,
            byteLength=128_000,
        ),
    }
    values.update(changes)
    return VoiceTrainingPreflightRequest(**values)  # type: ignore[arg-type]


class VoiceTrainingCommandPreflightShadowTests(unittest.TestCase):
    def test_disabled_path_does_not_inspect_request_or_record_a_hash(self) -> None:
        preflight = VoiceTrainingCommandPreflightShadow()

        result = preflight.observe(object())

        self.assertEqual(result.status, VoiceTrainingPreflightDisposition.SHADOW_DISABLED)
        self.assertFalse(result.trainingCommandCreated)
        self.assertFalse(result.providerEffectAllowed)
        enabled = preflight.observe(_request(), enabled=True)
        self.assertNotIn("duplicateTrainingCommandDenied", enabled.reasonCodes)

    def test_complete_synthetic_envelope_remains_blocked_without_provider_or_sample_effect(self) -> None:
        result = VoiceTrainingCommandPreflightShadow().observe(_request(), enabled=True)

        self.assertEqual(result.status, VoiceTrainingPreflightDisposition.BLOCKED)
        self.assertTrue(result.syntheticPreconditionsObserved)
        self.assertIn("g2VerifiedSourceObjectRequired", result.reasonCodes)
        self.assertIn("g3G4TrainingGatesRequired", result.reasonCodes)
        self.assertFalse(result.providerEffectAllowed)
        self.assertFalse(result.providerEffectPerformed)
        self.assertFalse(result.trainingCommandCreated)
        self.assertFalse(result.sampleObjectCreated)
        self.assertFalse(result.releaseVisible)

    def test_wrong_subject_or_family_proxy_is_denied_before_any_effect(self) -> None:
        result = VoiceTrainingCommandPreflightShadow().observe(
            _request(actorSubjectId="family-member", subjectId="family-member"),
            enabled=True,
        )

        self.assertEqual(result.status, VoiceTrainingPreflightDisposition.BLOCKED)
        self.assertFalse(result.syntheticPreconditionsObserved)
        self.assertIn("familyProxyDenied", result.reasonCodes)
        self.assertIn("subjectActorOwnerMismatch", result.reasonCodes)
        self.assertFalse(result.providerEffectPerformed)

    def test_minor_deceased_and_missing_consent_stay_blocked(self) -> None:
        cases = {
            "minor": _request(
                subjectEligibility=_eligibility(
                    allowed=False,
                    reason=SubjectEligibilityReason.MINOR,
                )
            ),
            "deceased": _request(
                subjectEligibility=_eligibility(
                    allowed=False,
                    reason=SubjectEligibilityReason.DECEASED_SUBJECT,
                )
            ),
            "consent": _request(consentDecision=_consent(complete=False)),
        }
        for name, request in cases.items():
            with self.subTest(name=name):
                result = VoiceTrainingCommandPreflightShadow().observe(request, enabled=True)
                self.assertEqual(result.status, VoiceTrainingPreflightDisposition.BLOCKED)
                self.assertFalse(result.syntheticPreconditionsObserved)
                self.assertFalse(result.providerEffectAllowed)
                self.assertFalse(result.trainingCommandCreated)

    def test_duplicate_request_is_not_a_second_training_command(self) -> None:
        preflight = VoiceTrainingCommandPreflightShadow()
        first = preflight.observe(_request(), enabled=True)
        replay = preflight.observe(_request(), enabled=True)

        self.assertNotIn("duplicateTrainingCommandDenied", first.reasonCodes)
        self.assertIn("duplicateTrainingCommandDenied", replay.reasonCodes)
        self.assertFalse(replay.trainingCommandCreated)
        self.assertFalse(replay.providerEffectPerformed)

    def test_legacy_speaker_ids_and_raw_audio_or_urls_cannot_enter_the_contract(self) -> None:
        with self.assertRaises(ValidationError):
            VoiceTrainingProfileReference(profileId="S_legacySpeaker", profileVersion=1)

        base = _request().model_dump(mode="python")
        sample = dict(base["sampleDescriptor"])
        sample["audioBase64"] = "not-allowed"
        base["sampleDescriptor"] = sample
        with self.assertRaises(ValidationError):
            VoiceTrainingPreflightRequest(**base)

        base = _request().model_dump(mode="python")
        sample = dict(base["sampleDescriptor"])
        sample["objectUrl"] = "https://example.invalid/audio.wav"
        base["sampleDescriptor"] = sample
        with self.assertRaises(ValidationError):
            VoiceTrainingPreflightRequest(**base)

    def test_module_does_not_import_legacy_provider_or_network_clients(self) -> None:
        source = Path(__file__).parents[1] / "app/services/voice_training_preflight_shadow.py"
        text = source.read_text(encoding="utf-8")
        for forbidden in (
            "app.services.voice_clone",
            "requests",
            "httpx",
            "boto3",
            "urllib.request",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
