"""P1-S1 contract coverage for the private VoiceProfile lifecycle.

These tests keep the M1 lifecycle separate from legacy provider sample states:
provider readiness can make a profile previewable, but only an explicit owner
acceptance can make it synthesizable.
"""

from __future__ import annotations

from array import array
import base64
from datetime import datetime, timedelta, timezone
import io
import math
from threading import Event, Thread
import time
import unittest
from unittest.mock import patch
import wave

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


def valid_voice_sample_base64(duration_seconds: int = 12) -> str:
    sample_rate_hz = 16_000
    samples = array("h")
    silence_frames = sample_rate_hz * 2
    for index in range(sample_rate_hz * duration_seconds):
        if index < silence_frames:
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


def issue_sample_authorization_receipt(
    client: TestClient,
    *,
    user_id: str,
    voice_profile_id: str,
) -> str:
    response = client.post(
        f"/voice/profiles/{user_id}/sample-authorization",
        json={"voiceProfileId": voice_profile_id},
    )
    if response.status_code != 200:
        raise AssertionError(response.text)
    return response.json()["sampleAuthorization"]["receiptId"]


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

    def test_provider_ready_cannot_reactivate_paused_or_deleting_profile(self) -> None:
        accepted = apply_voice_profile_lifecycle(
            {"voiceProfileId": "vp-owner-a", "sampleStatus": "ready"},
            state=VoiceProfileLifecycleState.ACCEPTED,
            consent=make_voice_profile_consent(
                purpose="private_synthesis",
                version="voice-clone-consent-v1",
                now=NOW,
            ),
            eligibility_decision=eligible_self(),
            eligibility_provenance="syntheticTest",
            now=NOW,
        )

        paused = apply_voice_profile_lifecycle(
            accepted,
            state=VoiceProfileLifecycleState.PAUSED,
            now=NOW + timedelta(minutes=1),
        )
        deleting = apply_voice_profile_lifecycle(
            accepted,
            state=VoiceProfileLifecycleState.DELETING,
            now=NOW + timedelta(minutes=2),
        )

        self.assertEqual(
            provider_observed_lifecycle_state(paused, "ready"),
            VoiceProfileLifecycleState.PAUSED,
        )
        self.assertEqual(
            provider_observed_lifecycle_state(deleting, "ready"),
            VoiceProfileLifecycleState.DELETING,
        )

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
        settings = Settings(
            volcengine_voice_clone_api_key="test-voice-clone-key",
            identity_binding_hmac_key="test-sample-authorization-key",
        )
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
            "audioBase64": valid_voice_sample_base64(),
            "audioFormat": "wav",
            "sampleVersion": "voice-sample-v1",
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
            payload["sampleAuthorizationReceiptId"] = issue_sample_authorization_receipt(
                client,
                user_id=payload["userId"],
                voice_profile_id=payload["voiceProfileId"],
            )
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
            "audioBase64": valid_voice_sample_base64(),
            "audioFormat": "wav",
            "sampleVersion": "voice-sample-v1",
            "privacyMetadata": {"scope": "generationAllowed"},
            "subjectEligibility": eligible_self_payload(),
        }

        with patch("app.main.store", store), patch(
            "app.main.settings",
            Settings(identity_binding_hmac_key="test-sample-authorization-key"),
        ), patch("app.main.VoiceCloneProviderFactory") as factory:
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

    def test_paused_profile_rejects_synthesis_before_tts_provider(self) -> None:
        class RecordingTTSProvider:
            is_configured = True
            provider_mode = "testTTS"

            def __init__(self) -> None:
                self.synthesize_count = 0

            def synthesize(self, **_kwargs):
                self.synthesize_count += 1
                return {
                    "audioBase64": "U09VTkQ=",
                    "audioFormat": "mp3",
                    "byteCount": 5,
                    "providerMode": self.provider_mode,
                    "voiceProfileId": "speaker-paused",
                    "visemeTimeline": None,
                }

        user_id = "voice-paused-owner"
        voice_profile_id = "voice-paused-profile"
        now = datetime.now(timezone.utc)
        profile = apply_voice_profile_lifecycle(
            {
                "userId": user_id,
                "voiceProfileId": voice_profile_id,
                "providerSpeakerId": "speaker-paused",
                "realCloneProviderReady": True,
                "personaScope": "personal",
                "digitalHumanId": user_id,
            },
            state=VoiceProfileLifecycleState.ACCEPTED,
            consent=make_voice_profile_consent(
                purpose="private_synthesis",
                version="voice-clone-consent-v1",
                now=now,
            ),
            eligibility_decision=eligible_self(),
            eligibility_provenance="syntheticTest",
            now=now,
        )
        store = InMemoryStore()
        store.save_voice_profile(user_id, profile)
        provider = RecordingTTSProvider()
        client = TestClient(app)

        with patch("app.main.store", store), patch(
            "app.main.VoiceCloneTTSProviderFactory"
        ) as factory:
            factory.return_value.make.return_value = provider
            paused = client.post(f"/voice/profiles/{user_id}/{voice_profile_id}/disable")
            synthesis = client.post(
                "/voice/synthesis",
                json={
                    "userId": user_id,
                    "voiceProfileId": voice_profile_id,
                    "text": "paused profiles are not synthesizable",
                },
            )

        self.assertEqual(paused.status_code, 200)
        self.assertEqual(paused.json()["profile"]["lifecycleState"], "paused")
        self.assertEqual(synthesis.status_code, 409)
        self.assertEqual(provider.synthesize_count, 0)

    def test_delete_persists_pending_provider_receipt_and_fences_synthesis(self) -> None:
        class RecordingTTSProvider:
            is_configured = True
            provider_mode = "testTTS"

            def __init__(self) -> None:
                self.synthesize_count = 0

            def synthesize(self, **_kwargs):
                self.synthesize_count += 1
                raise AssertionError("deleted voice profile must not reach the provider")

        user_id = "voice-delete-owner"
        voice_profile_id = "voice-delete-profile"
        now = datetime.now(timezone.utc)
        profile = apply_voice_profile_lifecycle(
            {
                "userId": user_id,
                "voiceProfileId": voice_profile_id,
                "providerSpeakerId": "speaker-delete",
                "realCloneProviderReady": True,
                "personaScope": "personal",
                "digitalHumanId": user_id,
            },
            state=VoiceProfileLifecycleState.ACCEPTED,
            consent=make_voice_profile_consent(
                purpose="private_synthesis",
                version="voice-clone-consent-v1",
                now=now,
            ),
            eligibility_decision=eligible_self(),
            eligibility_provenance="syntheticTest",
            now=now,
        )
        store = InMemoryStore()
        store.save_voice_profile(user_id, profile)
        provider = RecordingTTSProvider()
        client = TestClient(app)

        with patch("app.main.store", store), patch(
            "app.main.VoiceCloneTTSProviderFactory"
        ) as factory:
            factory.return_value.make.return_value = provider
            deleted = client.delete(f"/voice/profiles/{user_id}/{voice_profile_id}")
            replayed = client.delete(f"/voice/profiles/{user_id}/{voice_profile_id}")
            synthesis = client.post(
                "/voice/synthesis",
                json={
                    "userId": user_id,
                    "voiceProfileId": voice_profile_id,
                    "text": "pending provider deletion must fence synthesis",
                },
            )

        self.assertEqual(deleted.status_code, 200)
        payload = deleted.json()
        self.assertEqual(payload["status"], "deletionPending")
        self.assertEqual(payload["profile"]["lifecycleState"], "deleting")
        self.assertEqual(payload["profile"]["deletionState"], "pending")
        self.assertEqual(payload["profile"]["exitState"], "partial")
        self.assertEqual(payload["profile"]["providerCleanupState"], "pending")
        self.assertFalse(payload["profile"]["providerCleanupReceiptAvailable"])
        receipt = payload["profile"]["providerEffectReceipt"]
        self.assertEqual(receipt["state"], "accepted")
        self.assertFalse(payload["profile"]["providerEffectReceipt"]["providerReceiptPresent"])
        for key in (
            "effectId",
            "operationId",
            "outboxEventId",
            "jobId",
            "providerEffectKey",
            "receiptHash",
        ):
            self.assertTrue(receipt.get(key))
        self.assertNotIn("providerSpeakerId", receipt)
        self.assertNotIn("speaker-delete", str(receipt))
        self.assertEqual(replayed.status_code, 200)
        replayed_profile = replayed.json()["profile"]
        self.assertEqual(replayed.json()["status"], "deletionPending")
        self.assertEqual(replayed_profile["lifecycleState"], "deleting")
        self.assertEqual(
            replayed_profile["deletionRequestedAt"],
            payload["profile"]["deletionRequestedAt"],
        )
        self.assertEqual(
            replayed_profile["providerEffectReceipt"],
            payload["profile"]["providerEffectReceipt"],
        )
        stored = store.get_voice_profile(user_id, voice_profile_id)
        self.assertIsNotNone(stored)
        self.assertEqual(stored["providerEffectReceipt"]["state"], "accepted")
        self.assertFalse(stored["providerEffectReceipt"]["providerReceiptPresent"])
        self.assertEqual(store.effect_kernel_repository().record_count(), 1)
        self.assertEqual(synthesis.status_code, 409)
        self.assertEqual(provider.synthesize_count, 0)

    def test_delete_serializes_with_inflight_synthesis_and_fences_later_requests(self) -> None:
        class BlockingTTSProvider:
            is_configured = True
            provider_mode = "testTTS"

            def __init__(self) -> None:
                self.synthesize_count = 0
                self.started = Event()
                self.release = Event()

            def synthesize(self, **_kwargs):
                self.synthesize_count += 1
                self.started.set()
                if not self.release.wait(timeout=3):
                    raise ValueError("test provider was not released")
                return {
                    "audioBase64": "U09VTkQ=",
                    "audioFormat": "mp3",
                    "byteCount": 5,
                    "providerMode": self.provider_mode,
                    "voiceProfileId": "speaker-race",
                    "visemeTimeline": None,
                }

        user_id = "voice-race-owner"
        voice_profile_id = "voice-race-profile"
        now = datetime.now(timezone.utc)
        profile = apply_voice_profile_lifecycle(
            {
                "userId": user_id,
                "voiceProfileId": voice_profile_id,
                "providerSpeakerId": "speaker-race",
                "realCloneProviderReady": True,
                "personaScope": "personal",
                "digitalHumanId": user_id,
            },
            state=VoiceProfileLifecycleState.ACCEPTED,
            consent=make_voice_profile_consent(
                purpose="private_synthesis",
                version="voice-clone-consent-v1",
                now=now,
            ),
            eligibility_decision=eligible_self(),
            eligibility_provenance="syntheticTest",
            now=now,
        )
        store = InMemoryStore()
        store.save_voice_profile(user_id, profile)
        provider = BlockingTTSProvider()
        responses = {}

        def synthesize() -> None:
            with TestClient(app) as client:
                responses["synthesis"] = client.post(
                    "/voice/synthesis",
                    json={
                        "userId": user_id,
                        "voiceProfileId": voice_profile_id,
                        "text": "inflight synthesis must finish before revoke",
                    },
                )

        def delete() -> None:
            with TestClient(app) as client:
                responses["deletion"] = client.delete(
                    f"/voice/profiles/{user_id}/{voice_profile_id}"
                )

        with patch("app.main.store", store), patch(
            "app.main.VoiceCloneTTSProviderFactory"
        ) as factory:
            factory.return_value.make.return_value = provider
            synthesis_thread = Thread(target=synthesize)
            synthesis_thread.start()
            self.assertTrue(provider.started.wait(timeout=1))

            deletion_thread = Thread(target=delete)
            deletion_thread.start()
            time.sleep(0.05)
            self.assertTrue(deletion_thread.is_alive())

            provider.release.set()
            synthesis_thread.join(timeout=3)
            deletion_thread.join(timeout=3)

            after_delete = TestClient(app).post(
                "/voice/synthesis",
                json={
                    "userId": user_id,
                    "voiceProfileId": voice_profile_id,
                    "text": "post-revoke synthesis must be denied",
                },
            )

        self.assertFalse(synthesis_thread.is_alive())
        self.assertFalse(deletion_thread.is_alive())
        self.assertEqual(responses["synthesis"].status_code, 200)
        self.assertEqual(responses["deletion"].status_code, 200)
        self.assertEqual(after_delete.status_code, 409)
        self.assertEqual(provider.synthesize_count, 1)


if __name__ == "__main__":
    unittest.main()
