"""Regression coverage for the legacy voice synthesis family-scope hard deny."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main as main_module
from app.main import app
from app.services.in_memory_store import InMemoryStore
from app.services.release_policy import ReleasePolicyCommandGate, ReleasePolicyService


def _verified_self_eligibility() -> dict[str, object]:
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


class VoiceSynthesisFamilyScopeTests(unittest.TestCase):
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

    def test_family_scoped_ready_profile_is_hard_denied_before_provider_call(self) -> None:
        profile_store = InMemoryStore()
        profile_store.save_voice_profile(
            "owner",
            {
                "voiceProfileId": "family-ready-profile",
                "providerSpeakerId": "S_family_provider_slot",
                "sampleStatus": "ready",
                "isEnabled": True,
                "realCloneProviderReady": True,
                "qualityAcceptanceRequired": False,
                "qualityAcceptanceState": "accepted",
                "personaScope": "family",
                "digitalHumanId": "family-member",
            },
        )

        with patch("app.main.store", profile_store), patch("app.main.VoiceCloneTTSProviderFactory") as factory:
            response = TestClient(app).post(
                "/voice/synthesis",
                json={
                    "userId": "owner",
                    "voiceProfileId": "family-ready-profile",
                    "text": "这段文字不应触发家人复刻音色。",
                    "subjectEligibility": _verified_self_eligibility(),
                },
            )

        self.assertEqual(response.status_code, 403)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "subject_eligibility_hard_denied")
        self.assertFalse(detail["retryable"])
        self.assertEqual(detail["eligibilityDecision"]["reason"], "familySubjectHardDeny")
        factory.return_value.make.assert_not_called()


if __name__ == "__main__":
    unittest.main()
