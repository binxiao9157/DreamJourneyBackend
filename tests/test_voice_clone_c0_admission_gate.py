"""C0 admission coverage for production voice cloning.

These tests intentionally keep the identity verifier and clone provider
separate.  A configured voice provider must never compensate for a missing,
expired, cross-account, or non-adult/liveness identity receipt.
"""

from __future__ import annotations

from array import array
import base64
from datetime import datetime, timedelta, timezone
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
from app.services.runtime_config import RuntimeConfigService
from app.services.safety_policy import SubjectEligibilityReason
from app.services.voice_identity_eligibility import (
    UnavailableVoiceIdentityEligibilityProvider,
    VoiceIdentityEligibilityReceipt,
)
from app.services.voice_profile_eligibility import VoiceProfileEligibilityResolver
from app.services.voice_profile_lifecycle import (
    VoiceProfileLifecycleState,
    apply_voice_profile_lifecycle,
    make_voice_profile_consent,
)


def _voice_sample_base64() -> str:
    sample_rate = 16_000
    samples = array("h")
    for index in range(sample_rate * 12):
        if index < sample_rate * 2:
            samples.append(0)
        else:
            samples.append(int(7_000 * math.sin(2 * math.pi * 220 * index / sample_rate)))
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(samples.tobytes())
    return base64.b64encode(output.getvalue()).decode("ascii")


class _StaticIdentityProvider:
    provider_kind = "testStrongIdentity"
    is_configured = True

    def __init__(
        self,
        *,
        age_status: str = "adult",
        living_status: str = "living",
        liveness_verified: bool = True,
        actor_user_id: str | None = None,
        subject_user_id: str | None = None,
        expires_at: datetime | None = None,
    ) -> None:
        self.age_status = age_status
        self.living_status = living_status
        self.liveness_verified = liveness_verified
        self.actor_user_id = actor_user_id
        self.subject_user_id = subject_user_id
        self.expires_at = expires_at

    def resolve(self, *, actor_user_id: str, subject_user_id: str, now: datetime) -> VoiceIdentityEligibilityReceipt:
        issued_at = now - timedelta(minutes=1)
        return VoiceIdentityEligibilityReceipt(
            provider_kind=self.provider_kind,
            receipt_id_hash="sha256:" + "1" * 64,
            actor_user_id=self.actor_user_id or actor_user_id,
            subject_user_id=self.subject_user_id or subject_user_id,
            age_status=self.age_status,
            living_status=self.living_status,
            liveness_verified=self.liveness_verified,
            issued_at=issued_at,
            expires_at=self.expires_at or now + timedelta(hours=1),
        )


class _RecordingCloneProvider:
    is_configured = True
    provider_mode = "testVoiceClone"

    def __init__(self) -> None:
        self.submit_count = 0

    def submit_training(self, **_kwargs):
        self.submit_count += 1
        return {"providerStatus": "pending", "sampleStatus": "pending"}


class VoiceIdentityEligibilityResolverTests(unittest.TestCase):
    def test_unconfigured_verifier_fails_closed(self) -> None:
        resolution = VoiceProfileEligibilityResolver().resolve(
            actor_user_id="owner-a",
            profile_user_id="owner-a",
        )

        self.assertFalse(resolution.decision.allowed)
        self.assertEqual(resolution.availability, "providerUnavailable")
        self.assertEqual(
            resolution.decision.reason,
            SubjectEligibilityReason.AGE_VERIFICATION_MISSING,
        )

    def test_current_living_adult_self_receipt_is_the_only_allow_case(self) -> None:
        resolution = VoiceProfileEligibilityResolver(_StaticIdentityProvider()).resolve(
            actor_user_id="owner-a",
            profile_user_id="owner-a",
        )

        self.assertTrue(resolution.decision.allowed)
        self.assertEqual(resolution.provenance, "serverVerified")
        self.assertEqual(resolution.receipt_summary["providerKind"], "testStrongIdentity")
        self.assertNotIn("owner-a", str(resolution.receipt_summary))

    def test_expired_and_cross_account_receipts_are_hard_denied(self) -> None:
        now = datetime.now(timezone.utc)
        fixtures = (
            (
                _StaticIdentityProvider(expires_at=now - timedelta(seconds=1)),
                SubjectEligibilityReason.AGE_VERIFICATION_MISSING,
            ),
            (
                _StaticIdentityProvider(subject_user_id="other-owner"),
                SubjectEligibilityReason.SUBJECT_MISMATCH,
            ),
        )
        for provider, reason in fixtures:
            with self.subTest(reason=reason.value):
                resolution = VoiceProfileEligibilityResolver(provider).resolve(
                    actor_user_id="owner-a",
                    profile_user_id="owner-a",
                )
                self.assertFalse(resolution.decision.allowed)
                self.assertEqual(resolution.decision.reason, reason)

    def test_minor_deceased_unknown_and_liveness_failures_are_hard_denied(self) -> None:
        fixtures = (
            (_StaticIdentityProvider(age_status="minor"), SubjectEligibilityReason.MINOR),
            (_StaticIdentityProvider(age_status="unknown"), SubjectEligibilityReason.AGE_UNKNOWN),
            (_StaticIdentityProvider(living_status="deceased"), SubjectEligibilityReason.DECEASED_SUBJECT),
            (_StaticIdentityProvider(living_status="unknown"), SubjectEligibilityReason.LIVING_STATUS_UNKNOWN),
            (_StaticIdentityProvider(liveness_verified=False), SubjectEligibilityReason.LIVENESS_MISSING),
        )
        for provider, reason in fixtures:
            with self.subTest(reason=reason.value):
                resolution = VoiceProfileEligibilityResolver(provider).resolve(
                    actor_user_id="owner-a",
                    profile_user_id="owner-a",
                )
                self.assertFalse(resolution.decision.allowed)
                self.assertEqual(resolution.decision.reason, reason)


class VoiceCloneC0AdmissionRouteTests(unittest.TestCase):
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

    def _payload(self) -> dict[str, object]:
        return {
            "userId": "owner-a",
            "voiceProfileId": "voice-c0-owner-a",
            "sampleStatus": "pending",
            "sampleCount": 1,
            "authorizationConfirmed": True,
            # These fields are deliberately malicious/non-canonical. The
            # route may accept them for compatibility but never stores them as
            # the authoritative consent policy or eligibility decision.
            "consentVersion": "client-forged-v999",
            "authorizationText": "client-forged-text",
            "purpose": "training",
            "personaScope": "personal",
            "digitalHumanId": "owner-a",
            "audioBase64": _voice_sample_base64(),
            "audioFormat": "wav",
            "sampleVersion": "voice-sample-v1",
            "privacyMetadata": {"scope": "generationAllowed"},
            "subjectEligibility": {
                "ageStatus": "adult",
                "livenessVerified": True,
                "subjectMatchesActor": True,
            },
        }

    def _post_with(
        self,
        *,
        identity_provider: object,
        clone_provider: _RecordingCloneProvider,
        payload_patch: dict[str, object] | None = None,
    ):
        store = InMemoryStore()
        settings = Settings(identity_binding_hmac_key="c0-sample-authorization-key")
        client = TestClient(app)
        payload = self._payload()
        if payload_patch:
            payload.update(payload_patch)
        with patch("app.main.store", store), patch("app.main.settings", settings), patch(
            "app.main.VoiceCloneProviderFactory"
        ) as clone_factory, patch(
            "app.main.make_voice_identity_eligibility_provider",
            return_value=identity_provider,
        ):
            clone_factory.return_value.make.return_value = clone_provider
            challenge = client.post(
                f"/voice/profiles/{payload['userId']}/sample-authorization",
                json={"voiceProfileId": payload["voiceProfileId"]},
            )
            self.assertEqual(challenge.status_code, 200, challenge.text)
            payload["sampleAuthorizationReceiptId"] = challenge.json()["sampleAuthorization"]["receiptId"]
            response = client.post("/voice/profiles", json=payload)
        return response, store, payload

    def test_training_uses_server_receipts_and_does_not_persist_client_claims(self) -> None:
        clone_provider = _RecordingCloneProvider()
        response, store, payload = self._post_with(
            identity_provider=_StaticIdentityProvider(),
            clone_provider=clone_provider,
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(clone_provider.submit_count, 1)
        public_profile = response.json()["profile"]
        self.assertNotIn("eligibilityReceipt", public_profile)
        self.assertNotIn("trainingConsentReceipt", public_profile)
        self.assertNotIn("client-forged", response.text)
        persisted = store.get_voice_profile(payload["userId"], payload["voiceProfileId"])
        self.assertEqual(persisted["consent"]["version"], "voice-private-training-consent-v2")
        self.assertEqual(persisted["consent"]["source"], "serverReceipt")
        self.assertEqual(persisted["eligibilityReceipt"]["providerKind"], "testStrongIdentity")
        self.assertIn("trainingConsentReceipt", persisted)

    def test_identity_hard_deny_never_calls_clone_provider(self) -> None:
        clone_provider = _RecordingCloneProvider()
        response, store, payload = self._post_with(
            identity_provider=_StaticIdentityProvider(age_status="minor"),
            clone_provider=clone_provider,
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["code"], "subject_eligibility_hard_denied")
        self.assertEqual(clone_provider.submit_count, 0)
        self.assertIsNone(store.get_voice_profile(payload["userId"], payload["voiceProfileId"]))

    def test_unavailable_identity_verifier_never_calls_clone_provider(self) -> None:
        clone_provider = _RecordingCloneProvider()
        response, store, payload = self._post_with(
            identity_provider=UnavailableVoiceIdentityEligibilityProvider(),
            clone_provider=clone_provider,
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "voice_identity_verification_unavailable")
        self.assertEqual(clone_provider.submit_count, 0)
        self.assertIsNone(store.get_voice_profile(payload["userId"], payload["voiceProfileId"]))

    def test_family_profile_is_hard_denied_before_identity_or_provider_calls(self) -> None:
        clone_provider = _RecordingCloneProvider()
        response, store, payload = self._post_with(
            identity_provider=_StaticIdentityProvider(),
            clone_provider=clone_provider,
            payload_patch={
                "personaScope": "family",
                "digitalHumanId": "family-member-a",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["code"], "subject_eligibility_hard_denied")
        self.assertEqual(clone_provider.submit_count, 0)
        self.assertIsNone(store.get_voice_profile(payload["userId"], payload["voiceProfileId"]))

    def test_synthetic_eligibility_cannot_create_a_usable_profile(self) -> None:
        decision = VoiceProfileEligibilityResolver(_StaticIdentityProvider()).resolve(
            actor_user_id="owner-a",
            profile_user_id="owner-a",
        ).decision
        with self.assertRaisesRegex(ValueError, "eligibility provenance"):
            apply_voice_profile_lifecycle(
                {"voiceProfileId": "legacy-c0"},
                state=VoiceProfileLifecycleState.ACCEPTED,
                consent=make_voice_profile_consent(
                    purpose="private_synthesis",
                    version="legacy",
                    now=datetime.now(timezone.utc),
                ),
                eligibility_decision=decision,
                eligibility_provenance="syntheticTest",
                now=datetime.now(timezone.utc),
            )


class VoiceCloneC0RuntimeCapabilityTests(unittest.TestCase):
    def test_voice_provider_without_identity_verifier_keeps_training_closed(self) -> None:
        runtime = RuntimeConfigService(
            Settings(
                volcengine_voice_clone_api_key="test-clone-key",
                volcengine_voice_clone_tts_api_key="test-tts-key",
            )
        ).public_config()["voiceClone"]

        self.assertTrue(runtime["realProviderReady"])
        self.assertFalse(runtime["identityEligibilityProviderReady"])
        self.assertFalse(runtime["trainingAdmissionEnabled"])
        self.assertEqual(runtime["trainingAdmissionReason"], "identityLivenessProviderUnavailable")

    def test_configured_identity_verifier_opens_only_the_runtime_admission_axis(self) -> None:
        runtime = RuntimeConfigService(
            Settings(
                volcengine_voice_clone_api_key="test-clone-key",
                voice_identity_eligibility_provider="httpJson",
                voice_identity_eligibility_http_json_url="https://identity.example.test/voice-eligibility",
                voice_identity_eligibility_http_json_api_key="test-identity-key",
            )
        ).public_config()["voiceClone"]

        self.assertTrue(runtime["identityEligibilityProviderReady"])
        self.assertTrue(runtime["trainingAdmissionEnabled"])
        self.assertEqual(runtime["trainingAdmissionReason"], "ready")


if __name__ == "__main__":
    unittest.main()
