"""P1-S2 contracts for VoiceProfile samples and same-slot retry."""

from __future__ import annotations

from array import array
import base64
from datetime import datetime, timezone
import io
import math
import unittest
from unittest.mock import patch
import wave

from fastapi.testclient import TestClient

from app import main as main_module
from app.core.config import Settings
from app.main import app
from app.services.in_memory_store import InMemoryStore
from app.services.release_policy import ReleasePolicyCommandGate, ReleasePolicyService
from app.services.safety_policy import (
    HighRiskCapability,
    SubjectEligibilityDecision,
    SubjectEligibilityReason,
)
from app.services.voice_profile_eligibility import synthetic_test_resolution
from app.services.voice_profile_lifecycle import (
    VoiceProfileLifecycleState,
    apply_voice_profile_lifecycle,
    make_voice_profile_consent,
)
from app.services.voice_sample_assessment import (
    VoiceSampleAssessmentError,
    assess_voice_sample,
)
from app.services.voice_sample_authorization import (
    VoiceSampleAuthorizationError,
    issue_voice_sample_authorization_challenge,
    verify_voice_sample_authorization_receipt,
)


def _eligible_self() -> SubjectEligibilityDecision:
    return SubjectEligibilityDecision(
        capability=HighRiskCapability.CLONED_VOICE,
        allowed=True,
        decision="allow",
        reason=SubjectEligibilityReason.ELIGIBLE_LIVING_ADULT_SELF,
    )


def _voice_sample_base64(duration_seconds: int = 12) -> str:
    sample_rate_hz = 16_000
    samples = array("h")
    for index in range(sample_rate_hz * duration_seconds):
        if index < sample_rate_hz * 2:
            samples.append(0)
        else:
            samples.append(int(7_000 * math.sin(2 * math.pi * 220 * index / sample_rate_hz)))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate_hz)
        writer.writeframes(samples.tobytes())
    return base64.b64encode(buffer.getvalue()).decode("ascii")


class VoiceSampleAssessmentTests(unittest.TestCase):
    def test_wav_assessment_keeps_hash_private_and_reports_measurements(self) -> None:
        assessment = assess_voice_sample(
            audio_base64=_voice_sample_base64(),
            audio_format="wav",
            sample_version="voice-sample-v1",
        )

        self.assertEqual(assessment.duration_milliseconds, 12_000)
        self.assertGreaterEqual(assessment.estimated_snr_db, 12)
        self.assertTrue(assessment.sample_hash.startswith("sha256:"))
        self.assertNotIn("sampleHash", assessment.public_projection())

    def test_unsupported_or_short_samples_are_rejected_without_provider_fallback(self) -> None:
        with self.assertRaisesRegex(VoiceSampleAssessmentError, "unsupportedSampleFormat"):
            assess_voice_sample(
                audio_base64=_voice_sample_base64(),
                audio_format="m4a",
                sample_version="voice-sample-v1",
            )
        with self.assertRaisesRegex(VoiceSampleAssessmentError, "sampleTooShort"):
            assess_voice_sample(
                audio_base64=_voice_sample_base64(duration_seconds=2),
                audio_format="wav",
                sample_version="voice-sample-v1",
            )

    def test_authorization_receipt_is_profile_bound_and_expiring(self) -> None:
        now = datetime(2026, 8, 5, tzinfo=timezone.utc)
        challenge = issue_voice_sample_authorization_challenge(
            secret="sample-authority-test-key",
            user_id="owner-a",
            voice_profile_id="vp-a",
            now=now,
        )
        verified = verify_voice_sample_authorization_receipt(
            secret="sample-authority-test-key",
            receipt_id=challenge.receipt_id,
            user_id="owner-a",
            voice_profile_id="vp-a",
            now=now,
        )

        self.assertEqual(verified.statement_id, challenge.statement_id)
        with self.assertRaisesRegex(VoiceSampleAuthorizationError, "sampleAuthorizationOwnerMismatch"):
            verify_voice_sample_authorization_receipt(
                secret="sample-authority-test-key",
                receipt_id=challenge.receipt_id,
                user_id="owner-b",
                voice_profile_id="vp-a",
                now=now,
            )


class VoiceSampleRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._previous_release_policy_service = main_module.RELEASE_POLICY_SERVICE
        self._previous_release_policy_gate = main_module.RELEASE_POLICY_COMMAND_GATE
        service = ReleasePolicyService(
            shadow_mode=True,
            enforce_default_closed_stages=False,
        )
        main_module.RELEASE_POLICY_SERVICE = service
        main_module.RELEASE_POLICY_COMMAND_GATE = ReleasePolicyCommandGate(service)
        self.store = InMemoryStore()
        self.settings = Settings(
            volcengine_voice_clone_api_key="provider-key",
            volcengine_voice_clone_speaker_id_mode="trialSpeakerIdPool",
            volcengine_voice_clone_speaker_ids="S_retry_slot",
            identity_binding_hmac_key="sample-authority-test-key",
        )
        self.client = TestClient(app)

    def tearDown(self) -> None:
        main_module.RELEASE_POLICY_SERVICE = self._previous_release_policy_service
        main_module.RELEASE_POLICY_COMMAND_GATE = self._previous_release_policy_gate
        super().tearDown()

    def _issue_receipt(self, user_id: str, voice_profile_id: str) -> str:
        response = self.client.post(
            f"/voice/profiles/{user_id}/sample-authorization",
            json={"voiceProfileId": voice_profile_id},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["sampleAuthorization"]["receiptId"]

    def test_rejected_media_never_reaches_training_provider(self) -> None:
        class RecordingProvider:
            is_configured = True
            provider_mode = "volcengineVoiceCloneV3"

            def __init__(self) -> None:
                self.calls = 0

            def submit_training(self, **_kwargs):
                self.calls += 1
                return {"providerStatus": "pending", "sampleStatus": "pending"}

        provider = RecordingProvider()
        payload = {
            "userId": "owner-a",
            "voiceProfileId": "vp-invalid-sample",
            "sampleStatus": "pending",
            "sampleCount": 1,
            "authorizationConfirmed": True,
            "purpose": "training",
            "consentVersion": "voice-consent-v1",
            "personaScope": "personal",
            "digitalHumanId": "owner-a",
            "audioBase64": "U09VTkQ=",
            "audioFormat": "wav",
            "sampleVersion": "voice-sample-v1",
            "privacyMetadata": {"scope": "generationAllowed"},
        }
        with patch("app.main.store", self.store), patch("app.main.settings", self.settings), patch(
            "app.main.VoiceCloneProviderFactory"
        ) as factory, patch(
            "app.main._resolve_trusted_voice_profile_eligibility",
            return_value=synthetic_test_resolution(_eligible_self()),
        ):
            factory.return_value.make.return_value = provider
            response = self.client.post("/voice/profiles", json=payload)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "invalidWavSample")
        self.assertEqual(provider.calls, 0)

    def test_failed_profile_retries_same_profile_and_slot_once_per_generation(self) -> None:
        class PendingTrainingProvider:
            is_configured = True
            provider_mode = "volcengineVoiceCloneV3"

            def __init__(self) -> None:
                self.profile_ids: list[str] = []

            def submit_training(self, *, voice_profile_id, **_kwargs):
                self.profile_ids.append(voice_profile_id)
                return {"providerStatus": "pending", "sampleStatus": "pending"}

        owner_id = "owner-a"
        profile_id = "vp-retry-a"
        self.store.allocate_voice_clone_slot(
            ["S_retry_slot"],
            user_id=owner_id,
            voice_profile_id=profile_id,
            persona_scope="personal",
            digital_human_id=owner_id,
        )
        self.store.update_voice_clone_slot(profile_id, status="failed")
        profile = apply_voice_profile_lifecycle(
            {
                "voiceProfileId": profile_id,
                "userId": owner_id,
                "providerSpeakerId": "S_retry_slot",
                "providerBindingMode": "exclusiveSlot",
                "providerSlotManaged": True,
                "providerSlotState": "failed",
                "realCloneProviderReady": True,
                "retryGeneration": 2,
                "privacyMetadata": {"scope": "generationAllowed"},
            },
            state=VoiceProfileLifecycleState.FAILED,
            consent=make_voice_profile_consent(
                purpose="training",
                version="voice-consent-v1",
                now=datetime.now(timezone.utc),
            ),
            eligibility_decision=_eligible_self(),
            eligibility_provenance="syntheticTest",
            now=datetime.now(timezone.utc),
        )
        self.store.save_voice_profile(owner_id, profile)
        expected_version = int(profile["profileVersion"])
        provider = PendingTrainingProvider()

        with patch("app.main.store", self.store), patch("app.main.settings", self.settings), patch(
            "app.main.VoiceCloneProviderFactory"
        ) as factory, patch(
            "app.main._resolve_trusted_voice_profile_eligibility",
            return_value=synthetic_test_resolution(_eligible_self()),
        ):
            factory.return_value.make.return_value = provider
            receipt = self._issue_receipt(owner_id, profile_id)
            payload = {
                "expectedProfileVersion": expected_version,
                "retryGeneration": 3,
                "audioBase64": _voice_sample_base64(),
                "audioFormat": "wav",
                "sampleVersion": "voice-sample-v1",
                "sampleAuthorizationReceiptId": receipt,
            }
            retried = self.client.post(
                f"/voice/profiles/{owner_id}/{profile_id}/retry",
                json=payload,
            )
            stale = self.client.post(
                f"/voice/profiles/{owner_id}/{profile_id}/retry",
                json=payload,
            )

        self.assertEqual(retried.status_code, 200)
        result = retried.json()["profile"]
        self.assertEqual(result["voiceProfileId"], profile_id)
        self.assertEqual(result["retryGeneration"], 3)
        self.assertEqual(result["lifecycleState"], "training")
        self.assertEqual(result["sampleStatus"], "pending")
        self.assertNotIn("synthesize", result["allowedOperations"])
        self.assertNotIn("sampleHash", result["sampleAssessment"])
        self.assertEqual(provider.profile_ids, ["S_retry_slot"])
        slot = self.store.get_voice_clone_slot(profile_id)
        self.assertEqual(slot["providerSpeakerId"], "S_retry_slot")
        self.assertEqual(slot["trainingAttempts"], 1)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(provider.profile_ids, ["S_retry_slot"])


if __name__ == "__main__":
    unittest.main()
