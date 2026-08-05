"""P1-S3 coverage for Echo PCM role/profile binding and fail-closed fallback."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from io import BytesIO
import unittest
from unittest.mock import patch
import wave

from fastapi.testclient import TestClient

from app import main as main_module
from app.main import app
from app.services.in_memory_store import InMemoryStore
from app.services.release_policy import ReleasePolicyCommandGate, ReleasePolicyService
from app.services.safety_policy import (
    HighRiskCapability,
    SubjectEligibilityDecision,
    SubjectEligibilityReason,
)
from app.services.voice_profile_lifecycle import (
    VoiceProfileLifecycleState,
    apply_voice_profile_lifecycle,
    make_voice_profile_consent,
)


def _accepted_profile(*, profile_id: str = "self-profile", owner: str = "owner") -> dict:
    return apply_voice_profile_lifecycle(
        {
            "voiceProfileId": profile_id,
            "providerSpeakerId": "S_provider_profile",
            "realCloneProviderReady": True,
            "personaScope": "personal",
            "digitalHumanId": owner,
        },
        state=VoiceProfileLifecycleState.ACCEPTED,
        consent=make_voice_profile_consent(
            purpose="private_synthesis",
            version="voice-clone-consent-v1",
            now=datetime.now(timezone.utc),
        ),
        eligibility_decision=SubjectEligibilityDecision(
            capability=HighRiskCapability.CLONED_VOICE,
            allowed=True,
            decision="allow",
            reason=SubjectEligibilityReason.ELIGIBLE_LIVING_ADULT_SELF,
        ),
        eligibility_provenance="syntheticTest",
        now=datetime.now(timezone.utc),
    )


def _pcm_wav_base64() -> str:
    output = BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16_000)
        writer.writeframes(b"\x00\x10" * 16_000)
    return base64.b64encode(output.getvalue()).decode("ascii")


class _TTSProvider:
    provider_mode = "testVoiceClone"
    is_configured = True

    def __init__(self, *, response_profile_id: str = "S_provider_profile", raises: bool = False) -> None:
        self.response_profile_id = response_profile_id
        self.raises = raises
        self.call_count = 0

    def synthesize(self, **kwargs):
        self.call_count += 1
        if self.raises:
            error = ValueError("provider failed")
            setattr(error, "provider_request_id", "provider-request-id")
            setattr(error, "provider_log_id", "provider-log-id")
            raise error
        return {
            "audioBase64": _pcm_wav_base64(),
            "audioFormat": kwargs["audio_format"],
            "byteCount": 32_000,
            "providerMode": self.provider_mode,
            "voiceProfileId": self.response_profile_id,
            "providerRequestId": "provider-request-id",
            "providerLogId": "provider-log-id",
            "visemeTimeline": None,
        }


class VoiceSynthesisRoleBindingTests(unittest.TestCase):
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

    def _post(self, payload: dict, *, provider: _TTSProvider, store: InMemoryStore):
        with patch("app.main.store", store), patch("app.main.VoiceCloneTTSProviderFactory") as factory:
            factory.return_value.make.return_value = provider
            return TestClient(app).post("/voice/synthesis", json=payload)

    @staticmethod
    def _payload(**overrides: object) -> dict:
        payload = {
            "userId": "owner",
            "voiceProfileId": "self-profile",
            "text": "请把这句话送到数字人。",
            "outputMode": "tencentAudioDrive",
            "roleSubjectId": "owner",
            "roleKey": "personalOwner",
            "personaScope": "personal",
            "digitalHumanId": "owner",
            "requestPurpose": "echo",
        }
        payload.update(overrides)
        return payload

    def test_accepted_self_profile_returns_pcm_with_server_bound_owner_role(self) -> None:
        store = InMemoryStore()
        store.save_voice_profile("owner", _accepted_profile())
        provider = _TTSProvider()

        response = self._post(self._payload(), provider=provider, store=store)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["audio"]["format"], "pcm16kMono")
        self.assertEqual(payload["synthesisBinding"], {
            "schemaVersion": "voice-synthesis-binding-v1",
            "voiceProfileId": "self-profile",
            "profileVersion": 1,
            "ownerUserId": "owner",
            "roleSubjectId": "owner",
            "roleKey": "personalOwner",
            "personaScope": "personal",
            "digitalHumanId": "owner",
            "requestPurpose": "echo",
            "outputMode": "tencentAudioDrive",
            "audioOwner": "tencentDigitalHuman",
        })
        self.assertEqual(provider.call_count, 1)

    def test_pending_profile_returns_explainable_fallback_without_provider_call(self) -> None:
        store = InMemoryStore()
        pending = _accepted_profile()
        pending["lifecycleState"] = VoiceProfileLifecycleState.TRAINING.value
        pending["sampleStatus"] = "pending"
        store.save_voice_profile("owner", pending)
        provider = _TTSProvider()

        response = self._post(self._payload(), provider=provider, store=store)

        self.assertEqual(response.status_code, 409)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "accepted_voice_profile_required")
        self.assertEqual(detail["fallback"]["mode"], "neutralOrText")
        self.assertTrue(detail["fallback"]["clearPreviousProfile"])
        self.assertTrue(detail["fallback"]["forbidDifferentProfile"])
        self.assertEqual(provider.call_count, 0)

    def test_non_owner_role_is_not_substituted_with_the_owner_profile(self) -> None:
        store = InMemoryStore()
        store.save_voice_profile("owner", _accepted_profile())
        provider = _TTSProvider()

        response = self._post(
            self._payload(roleSubjectId="family-member", roleKey="familyMember"),
            provider=provider,
            store=store,
        )

        self.assertEqual(response.status_code, 409)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "role_voice_binding_unavailable")
        self.assertEqual(detail["fallback"]["mode"], "neutralOrText")
        self.assertEqual(provider.call_count, 0)

    def test_profile_owner_mismatch_is_rejected_without_provider_call(self) -> None:
        store = InMemoryStore()
        store.save_voice_profile("owner", _accepted_profile())
        provider = _TTSProvider()

        response = self._post(
            self._payload(userId="other", roleSubjectId="other", digitalHumanId="other"),
            provider=provider,
            store=store,
        )

        self.assertEqual(response.status_code, 404)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "accepted_voice_profile_required")
        self.assertTrue(detail["fallback"]["clearPreviousProfile"])
        self.assertEqual(provider.call_count, 0)

    def test_echo_pcm_requires_the_explicit_personal_owner_role_key(self) -> None:
        store = InMemoryStore()
        store.save_voice_profile("owner", _accepted_profile())
        provider = _TTSProvider()

        response = self._post(self._payload(roleKey=""), provider=provider, store=store)

        self.assertEqual(response.status_code, 409)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "role_voice_binding_unavailable")
        self.assertTrue(detail["fallback"]["forbidDifferentProfile"])
        self.assertEqual(provider.call_count, 0)

    def test_provider_profile_mismatch_is_rejected_without_audio_fallback(self) -> None:
        store = InMemoryStore()
        store.save_voice_profile("owner", _accepted_profile())
        provider = _TTSProvider(response_profile_id="S_other_profile")

        response = self._post(self._payload(), provider=provider, store=store)

        self.assertEqual(response.status_code, 502)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "voice_synthesis_provider_profile_mismatch")
        self.assertEqual(detail["fallback"]["mode"], "textOnly")
        self.assertTrue(detail["fallback"]["forbidDifferentProfile"])
        self.assertNotIn("S_other_profile", response.text)

    def test_provider_failure_never_falls_back_to_any_other_voice(self) -> None:
        store = InMemoryStore()
        store.save_voice_profile("owner", _accepted_profile())
        provider = _TTSProvider(raises=True)

        response = self._post(self._payload(), provider=provider, store=store)

        self.assertEqual(response.status_code, 502)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "voice_synthesis_provider_failed")
        self.assertEqual(detail["fallback"]["mode"], "textOnly")
        self.assertEqual(detail["fallback"]["audioOwner"], "none")
        self.assertNotIn("provider-request-id", response.text)
        self.assertNotIn("provider-log-id", response.text)

    def test_invalid_provider_audio_response_is_a_text_only_failure(self) -> None:
        class InvalidAudioProvider(_TTSProvider):
            def synthesize(self, **kwargs):
                self.call_count += 1
                return {
                    "providerMode": self.provider_mode,
                    "voiceProfileId": "S_provider_profile",
                    "providerRequestId": "provider-request-id",
                }

        store = InMemoryStore()
        store.save_voice_profile("owner", _accepted_profile())
        provider = InvalidAudioProvider()

        response = self._post(self._payload(), provider=provider, store=store)

        self.assertEqual(response.status_code, 502)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "voice_synthesis_provider_invalid_response")
        self.assertEqual(detail["fallback"]["mode"], "textOnly")
        self.assertNotIn("provider-request-id", response.text)


if __name__ == "__main__":
    unittest.main()
