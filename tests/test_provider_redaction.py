import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.main as main_module
from app.core.config import Settings
from app.main import app
from app.services.release_policy import ReleasePolicyCommandGate, ReleasePolicyService


class ProviderRedactionBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.previous_release_policy_service = main_module.RELEASE_POLICY_SERVICE
        self.previous_release_policy_gate = main_module.RELEASE_POLICY_COMMAND_GATE
        release_policy = ReleasePolicyService(
            shadow_mode=True,
            enforce_default_closed_stages=False,
        )
        main_module.RELEASE_POLICY_SERVICE = release_policy
        main_module.RELEASE_POLICY_COMMAND_GATE = ReleasePolicyCommandGate(release_policy)

    def tearDown(self):
        main_module.RELEASE_POLICY_SERVICE = self.previous_release_policy_service
        main_module.RELEASE_POLICY_COMMAND_GATE = self.previous_release_policy_gate

    def _assert_absent(self, payload, *markers):
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        for marker in markers:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, serialized)

    def _provider_settings(self):
        return Settings(
            deepseek_api_key="dry-run-deepseek-key",
            volcengine_api_key="dry-run-volc-key",
            volcengine_voice_type="dry-run-voice-type",
            amap_web_service_key="dry-run-amap-key",
        )

    def test_dry_runs_return_allowlisted_metadata_without_private_input(self):
        client = TestClient(app)
        settings = self._provider_settings()
        markers = (
            "KB_TRANSCRIPT_CANARY",
            "KB_SUMMARY_CANARY",
            "KB_SOURCE_TITLE_CANARY",
            "IMAGE_BASE64_CANARY",
            "TTS_TEXT_CANARY",
            "MAP_QUERY_CANARY",
            "dry-run-user-id",
            "dry-run-archive-id",
            "dry-run-deepseek-key",
            "dry-run-volc-key",
            "dry-run-voice-type",
            "dry-run-amap-key",
        )

        with patch.object(main_module, "settings", settings):
            kb = client.post(
                "/kb/extract?dryRun=true",
                json={
                    "userId": "dry-run-user-id",
                    "transcript": "KB_TRANSCRIPT_CANARY",
                    "existingSummary": "KB_SUMMARY_CANARY",
                    "privacyMetadata": {
                        "scope": "generationAllowed",
                        "sourceRefs": [
                            {
                                "kind": "conversationTurn",
                                "id": "source-ref-canary",
                                "title": "KB_SOURCE_TITLE_CANARY",
                            }
                        ],
                    },
                },
            )
            image = client.post(
                "/archive/image-analysis?dryRun=true",
                json={
                    "userId": "dry-run-user-id",
                    "archiveItemId": "dry-run-archive-id",
                    "imageBase64": "IMAGE_BASE64_CANARY",
                    "privacyMetadata": {"scope": "generationAllowed"},
                },
            )
            tts = client.post(
                "/tts?dryRun=true",
                json={
                    "userId": "dry-run-user-id",
                    "text": "TTS_TEXT_CANARY",
                    "voiceType": "dry-run-voice-type",
                },
            )
            amap = client.get("/maps/district?dryRun=true&keyword=MAP_QUERY_CANARY")

        for response in (kb, image, tts, amap):
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertEqual(payload["dryRun"]["schemaVersion"], 1)
            self.assertEqual(payload["dryRun"]["redactionPolicyVersion"], "providerDryRun-v2")
            self.assertNotIn("request", payload)
            self._assert_absent(payload, *markers)

    def test_provider_dry_run_error_is_value_free(self):
        client = TestClient(app)
        marker = "PROVIDER_ERROR_CANARY"
        with patch.object(
            main_module.DeepSeekKnowledgeExtractionProxy,
            "dry_run_report",
            side_effect=RuntimeError(marker),
        ):
            response = client.post(
                "/kb/extract?dryRun=true",
                json={
                    "userId": "dry-run-user-id",
                    "transcript": "KB_TRANSCRIPT_CANARY",
                    "privacyMetadata": {"scope": "generationAllowed"},
                },
            )

        self.assertEqual(response.status_code, 502)
        payload = response.json()
        self.assertEqual(payload["detail"]["code"], "knowledgeExtractionProviderFailed")
        self.assertEqual(payload["detail"]["redactionPolicyVersion"], "providerDryRun-v2")
        self._assert_absent(payload, marker, "KB_TRANSCRIPT_CANARY", "dry-run-user-id")


if __name__ == "__main__":
    unittest.main()
