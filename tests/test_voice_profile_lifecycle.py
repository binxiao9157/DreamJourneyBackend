"""P1-S1 contract coverage for the private VoiceProfile lifecycle.

These tests keep the M1 lifecycle separate from legacy provider sample states:
provider readiness can make a profile previewable, but only an explicit owner
acceptance can make it synthesizable.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main as main_module
from app.core.config import Settings
from app.main import app
from app.services.safety_policy import (
    HighRiskCapability,
    SubjectEligibilityDecision,
    SubjectEligibilityReason,
)
from app.services.voice_profile_lifecycle import (
    VoiceProfileLifecycleState,
    apply_voice_profile_lifecycle,
    canonical_lifecycle_state,
    make_voice_profile_consent,
    profile_public_projection,
    provider_observed_lifecycle_state,
)
from app.services.voice_profile_eligibility import synthetic_test_resolution
from app.services.in_memory_store import InMemoryStore
from app.services.release_policy import ReleasePolicyCommandGate, ReleasePolicyService


NOW = datetime(2026, 8, 5, 8, 0, tzinfo=timezone.utc)


def eligible_self() -> SubjectEligibilityDecision:
    return SubjectEligibilityDecision(
        capability=HighRiskCapability.CLONED_VOICE,
        allowed=True,
        decision="allow",
        reason=SubjectEligibilityReason.ELIGIBLE_LIVING_ADULT_SELF,
    )


def eligible_self_payload() -> dict[str, object]:
    return {
        "capability": "clonedVoice",
        "subjectKind": "self",
        "ageStatus": "adult",
        "livingStatus": "living",
        "ageVerified": True,
        "livenessVerified": True,
        "subjectMatchesActor": True,
        "consentVerified": True,
        "consentPurpose": "clonedVoice",
    }


class VoiceProfileLifecycleTests(unittest.TestCase):
    def test_provider_ready_is_preview_ready_not_accepted(self) -> None:
        profile = apply_voice_profile_lifecycle(
            {
                "voiceProfileId": "vp-owner-a",
                "sampleStatus": "pending",
                "qualityAcceptanceRequired": True,
            },
            state=VoiceProfileLifecycleState.TRAINING,
            consent=make_voice_profile_consent(
                purpose="training",
                version="voice-clone-consent-v1",
                now=NOW,
            ),
            eligibility_decision=eligible_self(),
            eligibility_provenance="syntheticTest",
            now=NOW,
        )

        refreshed = apply_voice_profile_lifecycle(
            profile,
            state=provider_observed_lifecycle_state(profile, "ready"),
            now=NOW + timedelta(minutes=2),
        )
        projection = profile_public_projection(refreshed, now=NOW + timedelta(minutes=2))

        self.assertEqual(refreshed["lifecycleState"], "previewReady")
        self.assertEqual(refreshed["sampleStatus"], "ready")
        self.assertTrue(refreshed["qualityAcceptanceRequired"])
        self.assertFalse(refreshed["isEnabled"])
        self.assertEqual(projection["lifecycleState"], "previewReady")
        self.assertIn("preview", projection["allowedOperations"])
        self.assertIn("accept", projection["allowedOperations"])
        self.assertNotIn("synthesize", projection["allowedOperations"])

    def test_explicit_acceptance_is_the_only_synthesizable_state(self) -> None:
        profile = apply_voice_profile_lifecycle(
            {"voiceProfileId": "vp-owner-a", "sampleStatus": "ready"},
            state=VoiceProfileLifecycleState.PREVIEW_READY,
            consent=make_voice_profile_consent(
                purpose="training",
                version="voice-clone-consent-v1",
                now=NOW,
            ),
            eligibility_decision=eligible_self(),
            eligibility_provenance="syntheticTest",
            now=NOW,
        )
        accepted = apply_voice_profile_lifecycle(
            profile,
            state=VoiceProfileLifecycleState.ACCEPTED,
            consent=make_voice_profile_consent(
                purpose="private_synthesis",
                version="voice-clone-consent-v1",
                now=NOW + timedelta(minutes=3),
                expires_at=NOW + timedelta(days=30),
            ),
            now=NOW + timedelta(minutes=3),
        )
        projection = profile_public_projection(accepted, now=NOW + timedelta(minutes=3))

        self.assertEqual(accepted["lifecycleState"], "accepted")
        self.assertTrue(accepted["isEnabled"])
        self.assertFalse(accepted["qualityAcceptanceRequired"])
        self.assertTrue(projection["eligibility"]["allowed"])
        self.assertEqual(projection["consent"]["purpose"], "private_synthesis")
        self.assertIn("synthesize", projection["allowedOperations"])

    def test_expired_consent_fails_closed_and_hides_raw_decision(self) -> None:
        profile = apply_voice_profile_lifecycle(
            {"voiceProfileId": "vp-owner-a", "sampleStatus": "ready"},
            state=VoiceProfileLifecycleState.ACCEPTED,
            consent=make_voice_profile_consent(
                purpose="private_synthesis",
                version="voice-clone-consent-v1",
                now=NOW - timedelta(days=2),
                expires_at=NOW - timedelta(days=1),
            ),
            eligibility_decision=eligible_self(),
            eligibility_provenance="syntheticTest",
            now=NOW - timedelta(days=2),
        )

        projection = profile_public_projection(profile, now=NOW)

        self.assertEqual(projection["lifecycleState"], "accepted")
        self.assertFalse(projection["eligibility"]["allowed"])
        self.assertEqual(projection["consent"]["state"], "expired")
        self.assertNotIn("synthesize", projection["allowedOperations"])
        self.assertNotIn("subjectEligibilityDecision", projection)

    def test_legacy_ready_profile_is_never_promoted_without_current_consent(self) -> None:
        profile = {
            "voiceProfileId": "legacy-ready-profile",
            "sampleStatus": "ready",
            "isEnabled": True,
            "qualityAcceptanceRequired": False,
        }

        projection = profile_public_projection(profile, now=NOW)

        self.assertEqual(canonical_lifecycle_state(profile), VoiceProfileLifecycleState.ACCEPTED)
        self.assertFalse(projection["eligibility"]["allowed"])
        self.assertEqual(projection["consent"]["state"], "missing")
        self.assertNotIn("synthesize", projection["allowedOperations"])

    def test_hard_denied_subject_cannot_become_accepted(self) -> None:
        denied = SubjectEligibilityDecision(
            capability=HighRiskCapability.CLONED_VOICE,
            allowed=False,
            decision="hardDeny",
            reason=SubjectEligibilityReason.FAMILY_SUBJECT,
        )
        profile = apply_voice_profile_lifecycle(
            {"voiceProfileId": "family-proxy", "sampleStatus": "ready"},
            state=VoiceProfileLifecycleState.ACCEPTED,
            consent=make_voice_profile_consent(
                purpose="private_synthesis",
                version="voice-clone-consent-v1",
                now=NOW,
            ),
            eligibility_decision=denied,
            eligibility_provenance="syntheticTest",
            now=NOW,
        )
        projection = profile_public_projection(profile, now=NOW)

        self.assertFalse(profile["isEnabled"])
        self.assertFalse(projection["eligibility"]["allowed"])
        self.assertEqual(projection["eligibility"]["reasonCode"], "familySubjectHardDeny")
        self.assertNotIn("synthesize", projection["allowedOperations"])

    def test_legacy_decision_without_trusted_provenance_is_not_usable(self) -> None:
        profile = {
            "voiceProfileId": "legacy-untrusted-profile",
            "sampleStatus": "ready",
            "qualityAcceptanceRequired": False,
            "consent": make_voice_profile_consent(
                purpose="private_synthesis",
                version="voice-clone-consent-v1",
                now=NOW,
            ),
            "subjectEligibilityDecision": eligible_self().model_dump(mode="json"),
        }

        projection = profile_public_projection(profile, now=NOW)

        self.assertFalse(projection["eligibility"]["allowed"])
        self.assertEqual(
            projection["eligibility"]["reasonCode"],
            "ageVerificationMissingHardDeny",
        )
        self.assertNotIn("synthesize", projection["allowedOperations"])


class VoiceProfileLifecycleAPITests(unittest.TestCase):
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

    def tearDown(self) -> None:
        main_module.RELEASE_POLICY_SERVICE = self._previous_release_policy_service
        main_module.RELEASE_POLICY_COMMAND_GATE = self._previous_release_policy_gate
        super().tearDown()

    def test_provider_ready_requires_explicit_acceptance_before_synthesis(self) -> None:
        class ReadyTrainingProvider:
            is_configured = True
            provider_mode = "volcengineVoiceCloneV3"

            def submit_training(self, *, voice_profile_id, audio_base64, audio_format, language):
                return {
                    "voiceProfileId": voice_profile_id,
                    "providerStatus": "4",
                    "sampleStatus": "ready",
                }

        class PreviewTTSProvider:
            is_configured = True
            provider_mode = "volcengineVoiceCloneV1TTS"

            def synthesize(
                self,
                *,
                text,
                user_id,
                voice_profile_id,
                audio_format,
                sample_rate,
                speech_rate,
                loudness_rate,
            ):
                return {
                    "audioBase64": "U09VTkQ=",
                    "audioFormat": audio_format,
                    "byteCount": 5,
                    "providerMode": self.provider_mode,
                    "voiceProfileId": voice_profile_id,
                    "visemeTimeline": None,
                }

        store = InMemoryStore()
        settings = Settings(volcengine_voice_clone_api_key="test-voice-clone-key")
        client = TestClient(app)
        payload = {
            "userId": "voice-lifecycle-owner",
            "voiceProfileId": "voice-lifecycle-profile",
            "sampleStatus": "pending",
            "sampleCount": 1,
            "authorizationConfirmed": True,
            "purpose": "training",
            "consentVersion": "voice-clone-consent-v1",
            "personaScope": "personal",
            "digitalHumanId": "voice-lifecycle-owner",
            "audioBase64": "RAW_SAMPLE_BASE64",
            "audioFormat": "wav",
            "privacyMetadata": {"scope": "generationAllowed"},
            "subjectEligibility": eligible_self_payload(),
        }

        with patch("app.main.store", store), patch("app.main.settings", settings), patch(
            "app.main.VoiceCloneProviderFactory"
        ) as training_factory, patch("app.main.VoiceCloneTTSProviderFactory") as tts_factory, patch(
            "app.main._resolve_trusted_voice_profile_eligibility",
            return_value=synthetic_test_resolution(eligible_self()),
        ):
            training_factory.return_value.make.return_value = ReadyTrainingProvider()
            tts_factory.return_value.make.return_value = PreviewTTSProvider()
            created = client.post("/voice/profiles", json=payload)

            self.assertEqual(created.status_code, 200)
            created_profile = created.json()["profile"]
            self.assertEqual(created_profile["lifecycleState"], "previewReady")
            self.assertFalse(created_profile["isEnabled"])
            self.assertEqual(created_profile["allowedOperations"], ["preview", "accept", "delete"])
            self.assertNotIn("subjectEligibilityDecision", created_profile)
            self.assertNotIn("authorizationText", created_profile)
            self.assertEqual(created_profile["consent"]["purpose"], "training")

            direct_echo = client.post(
                "/voice/synthesis",
                json={
                    "userId": payload["userId"],
                    "voiceProfileId": payload["voiceProfileId"],
                    "text": "尚未确认的音色不能进入回响。",
                },
            )
            preview = client.post(
                "/voice/synthesis",
                json={
                    "userId": payload["userId"],
                    "voiceProfileId": payload["voiceProfileId"],
                    "text": "请确认试听效果。",
                    "requestPurpose": "qualityPreview",
                },
            )

            self.assertEqual(direct_echo.status_code, 409)
            self.assertEqual(preview.status_code, 200)
            receipt_id = preview.json()["qualityPreviewReceiptId"]

            accepted = client.post(
                "/voice/profiles/voice-lifecycle-owner/voice-lifecycle-profile/quality-acceptance",
                json={"previewReceiptId": receipt_id},
            )

        self.assertEqual(accepted.status_code, 200)
        accepted_profile = accepted.json()["profile"]
        self.assertEqual(accepted_profile["lifecycleState"], "accepted")
        self.assertTrue(accepted_profile["isEnabled"])
        self.assertIn("synthesize", accepted_profile["allowedOperations"])
        self.assertEqual(accepted_profile["consent"]["purpose"], "private_synthesis")

    def test_client_claim_cannot_start_training_without_server_eligibility(self) -> None:
        class RecordingProvider:
            is_configured = True
            provider_mode = "volcengineVoiceCloneV3"

            def __init__(self) -> None:
                self.submit_count = 0

            def submit_training(self, **_kwargs):
                self.submit_count += 1
                return {"providerStatus": "pending", "sampleStatus": "pending"}

        store = InMemoryStore()
        provider = RecordingProvider()
        client = TestClient(app)
        payload = {
            "userId": "voice-lifecycle-owner",
            "voiceProfileId": "client-claim-must-not-authorize",
            "sampleStatus": "pending",
            "sampleCount": 1,
            "authorizationConfirmed": True,
            "purpose": "training",
            "consentVersion": "voice-clone-consent-v1",
            "personaScope": "personal",
            "digitalHumanId": "voice-lifecycle-owner",
            "audioBase64": "RAW_SAMPLE_BASE64",
            "audioFormat": "wav",
            "privacyMetadata": {"scope": "generationAllowed"},
            "subjectEligibility": eligible_self_payload(),
        }

        with patch("app.main.store", store), patch("app.main.VoiceCloneProviderFactory") as factory:
            factory.return_value.make.return_value = provider
            response = client.post("/voice/profiles", json=payload)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["code"], "subject_eligibility_hard_denied")
        self.assertEqual(
            response.json()["detail"]["eligibilityDecision"]["reason"],
            "ageVerificationMissingHardDeny",
        )
        self.assertEqual(provider.submit_count, 0)
        self.assertIsNone(store.get_voice_profile(payload["userId"], payload["voiceProfileId"]))


if __name__ == "__main__":
    unittest.main()
