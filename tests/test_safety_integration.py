import json
import unittest

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.services.in_memory_store import InMemoryStore
from app.services.release_policy import ReleasePolicyCommandGate, ReleasePolicyService


class SafetyIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.previous_store = main_module.store
        self.previous_release_policy_service = main_module.RELEASE_POLICY_SERVICE
        self.previous_release_policy_gate = main_module.RELEASE_POLICY_COMMAND_GATE
        main_module.store = InMemoryStore()
        service = ReleasePolicyService(
            shadow_mode=True,
            enforce_default_closed_stages=False,
        )
        main_module.RELEASE_POLICY_SERVICE = service
        main_module.RELEASE_POLICY_COMMAND_GATE = ReleasePolicyCommandGate(service)
        self.client = TestClient(app)

    def tearDown(self):
        main_module.store = self.previous_store
        main_module.RELEASE_POLICY_SERVICE = self.previous_release_policy_service
        main_module.RELEASE_POLICY_COMMAND_GATE = self.previous_release_policy_gate

    @staticmethod
    def _eligibility(*, capability: str, age_status: str = "adult") -> dict:
        return {
            "capability": capability,
            "subjectKind": "self",
            "ageStatus": age_status,
            "livingStatus": "living",
            "ageVerified": True,
            "livenessVerified": True,
            "subjectMatchesActor": True,
            "consentVerified": True,
            "consentPurpose": capability,
        }

    def test_context_build_crisis_returns_value_free_neutral_packet_without_persona_or_provider_effects(self):
        raw_expression = "我真的撑不住了。"
        main_module.store.save_voice_profile(
            "safety_owner",
            {
                "userId": "safety_owner",
                "voiceProfileId": "S_must_not_be_selected",
                "sampleStatus": "ready",
                "isEnabled": True,
                "realCloneProviderReady": True,
                "qualityAcceptanceRequired": False,
                "personaScope": "personal",
                "digitalHumanId": "safety_owner",
            },
        )
        main_module.store.add_archive_item(
            "safety_owner",
            {
                "id": "archive_must_not_be_read",
                "userId": "safety_owner",
                "kind": "text",
                "title": "private sentinel",
                "note": "PRIVATE_MEMORY_SENTINEL",
                "personaScope": "personal",
                "digitalHumanId": "safety_owner",
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )

        response = self.client.post(
            "/context/build",
            json={
                "userId": "safety_owner",
                "intent": "echo_chat",
                "query": raw_expression,
                "personaScope": "personal",
                "digitalHumanId": "safety_owner",
            },
        )

        self.assertEqual(response.status_code, 200)
        packet = response.json()["contextPacket"]
        self.assertEqual(packet["query"], "")
        self.assertFalse(packet["containsRawExpression"])
        self.assertEqual(packet["safetyPolicy"]["riskClass"], "highDistress")
        self.assertEqual(packet["safetyPolicy"]["action"], "respondWithNeutralSafetyText")
        self.assertEqual(packet["policy"]["safetyMode"], "neutralSafetyText")
        self.assertEqual(packet["memory"]["archiveItems"], [])
        self.assertEqual(packet["selectedContext"], [])
        self.assertEqual(packet["generationContext"]["text"], "")
        self.assertEqual(packet["persona"]["personaScope"], "neutralSafety")
        self.assertIsNone(packet["persona"]["digitalHumanId"])
        self.assertEqual(packet["persona"]["authority"], "deniedBySafetyPolicy")
        self.assertFalse(packet["policy"]["canUsePersona"])
        self.assertEqual(packet["policy"]["privacyScope"]["scope"], "neutralSafety")
        self.assertEqual(packet["policy"]["privacyScope"]["allowedDigitalHumanIds"], [])
        self.assertFalse(packet["voice"]["cloneReady"])
        self.assertFalse(packet["digitalHuman"]["sessionReady"])
        serialized = json.dumps(packet, ensure_ascii=False, sort_keys=True)
        self.assertNotIn(raw_expression, serialized)
        self.assertNotIn("PRIVATE_MEMORY_SENTINEL", serialized)
        self.assertNotIn("S_must_not_be_selected", serialized)

    def test_context_and_runtime_expose_persistent_ai_identity_contract(self):
        context = self.client.post(
            "/context/build",
            json={
                "userId": "disclosure_owner",
                "query": "今天过得怎么样？",
                "personaScope": "personal",
                "digitalHumanId": "disclosure_owner",
            },
        )
        runtime = self.client.get("/config/runtime")

        self.assertEqual(context.status_code, 200)
        disclosure = context.json()["contextPacket"]["aiDisclosure"]
        self.assertTrue(disclosure["required"])
        self.assertTrue(disclosure["persistent"])
        self.assertEqual(disclosure["visibleLabel"], "AI 生成")
        self.assertEqual(disclosure["assistantLabel"], "AI 助手")
        self.assertEqual(runtime.status_code, 200)
        self.assertEqual(runtime.json()["safety"]["aiDisclosure"], disclosure)

    def test_delayed_reply_requires_transient_text_and_blocks_crisis_without_persisting_it(self):
        missing = self.client.post(
            "/echo/delayed-replies",
            json={
                "userId": "delayed_owner",
                "delayedReplyId": "missing_safety_input",
                "deliverAt": "2026-07-18T12:05:00Z",
                "minutes": 7,
                "trigger": "contentSignal",
            },
        )
        raw_expression = "我想去陪去世的妈妈。"
        crisis = self.client.post(
            "/echo/delayed-replies",
            json={
                "userId": "delayed_owner",
                "delayedReplyId": "blocked_crisis",
                "deliverAt": "2026-07-18T12:05:00Z",
                "minutes": 7,
                "trigger": "contentSignal",
                "rawTranscript": raw_expression,
            },
        )
        normal = self.client.post(
            "/echo/delayed-replies",
            json={
                "userId": "delayed_owner",
                "delayedReplyId": "allowed_normal",
                "deliverAt": "2026-07-18T12:05:00Z",
                "minutes": 7,
                "trigger": "contentSignal",
                "rawTranscript": "今天整理了小时候的照片。",
            },
        )
        listed = self.client.get("/echo/delayed-replies/delayed_owner")

        self.assertEqual(missing.status_code, 400)
        self.assertEqual(missing.json()["detail"]["code"], "echo_delayed_reply_safety_input_required")
        self.assertEqual(crisis.status_code, 409)
        self.assertEqual(crisis.json()["detail"]["code"], "echo_delayed_reply_blocked_by_safety_policy")
        self.assertNotIn(raw_expression, crisis.text)
        self.assertEqual(normal.status_code, 200)
        self.assertEqual([item["id"] for item in listed.json()["items"]], ["allowed_normal"])
        self.assertNotIn("rawTranscript", normal.json()["item"])
        self.assertNotIn("今天整理了小时候的照片", listed.text)

    def test_minor_and_family_voice_or_digital_human_requests_hard_deny_before_provider(self):
        minor_voice = self.client.post(
            "/voice/profiles",
            json={
                "userId": "minor_owner",
                "voiceProfileId": "minor_voice",
                "authorizationConfirmed": True,
                "personaScope": "personal",
                "digitalHumanId": "minor_owner",
                "privacyMetadata": {"scope": "generationAllowed"},
                "subjectEligibility": self._eligibility(
                    capability="clonedVoice",
                    age_status="minor",
                ),
            },
        )
        family_voice = self.client.post(
            "/voice/profiles",
            json={
                "userId": "adult_owner",
                "voiceProfileId": "family_voice",
                "authorizationConfirmed": True,
                "personaScope": "family",
                "digitalHumanId": "family_relative",
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )
        minor_digital_human = self.client.post(
            "/digital-human/sessions",
            json={
                "userId": "minor_owner",
                "personaId": "minor_persona",
                "scene": "echo",
                "lifecycleMode": "sunlight",
                "subjectEligibility": self._eligibility(
                    capability="digitalHuman",
                    age_status="minor",
                ),
            },
        )

        for response in (minor_voice, family_voice, minor_digital_human):
            with self.subTest(response=response.json()):
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["detail"]["code"], "subject_eligibility_hard_denied")
                self.assertFalse(response.json()["detail"]["eligibilityDecision"]["allowed"])
                self.assertFalse(response.json()["detail"]["retryable"])


if __name__ == "__main__":
    unittest.main()
