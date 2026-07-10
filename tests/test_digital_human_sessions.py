import unittest

from fastapi.testclient import TestClient

import app.main as main_module
from app.core.config import settings
from app.main import app
from app.services.in_memory_store import InMemoryStore


client = TestClient(app)


class DigitalHumanSessionAPITests(unittest.TestCase):
    def setUp(self):
        self.previous_store = main_module.store
        main_module.store = InMemoryStore()

    def tearDown(self):
        main_module.store = self.previous_store

    def test_create_digital_human_session_returns_tencent_mock_contract(self):
        response = client.post(
            "/digital-human/sessions",
            json={
                "userId": "user_qa",
                "personaId": "persona_mother_001",
                "scene": "echo",
                "deviceId": "ios-simulator",
                "lifecycleMode": "star",
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["provider"], "tencent")
        self.assertEqual(body["providerMode"], "mockContract")
        self.assertEqual(body["personaId"], "persona_mother_001")
        self.assertEqual(body["scene"], "echo")
        self.assertEqual(body["driveMode"], "streamText")
        self.assertTrue(body["alphaEnabled"])
        self.assertFalse(body["smartActionEnabled"])
        self.assertTrue(body["sessionPolicy"]["allowInterrupt"])
        self.assertFalse(body["sessionPolicy"]["proactiveSpeechAllowed"])
        self.assertEqual(body["sessionPolicy"]["maxDurationSeconds"], 180)
        self.assertEqual(body["credential"]["mode"], "backend-issued-mock")
        self.assertTrue(body["credential"]["expiresAt"])
        self.assertEqual(body["fallback"]["mode"], "audioOnly")
        self.assertEqual(body["contractVersion"], 2)
        self.assertEqual(body["userId"], "user_qa")
        self.assertEqual(body["lease"]["status"], "active")
        self.assertFalse(body["lease"]["reused"])
        self.assertEqual(body["lease"]["contractVersion"], 1)
        self.assertEqual(body["lease"]["heartbeatEndpoint"], f"/digital-human/sessions/{body['sessionId']}/heartbeat")
        self.assertEqual(body["lease"]["releaseEndpoint"], f"/digital-human/sessions/{body['sessionId']}/release")
        self.assertGreater(body["lease"]["heartbeatIntervalSeconds"], 0)

        persisted = main_module.store.get_digital_human_session_lease(body["sessionId"])
        self.assertNotIn("credential", persisted)
        self.assertNotIn("appkey", str(persisted))
        self.assertNotIn("accesstoken", str(persisted))

    def test_session_lease_reuses_same_context_and_rejects_competing_device(self):
        payload = {
            "userId": "user_qa",
            "personaId": "persona_mother_001",
            "scene": "echo",
            "deviceId": "ios-device-1",
            "lifecycleMode": "star",
        }
        first = client.post("/digital-human/sessions", json=payload)
        repeated = client.post("/digital-human/sessions", json=payload)
        conflict = client.post(
            "/digital-human/sessions",
            json={**payload, "userId": "user_other", "deviceId": "ios-device-2"},
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.json()["sessionId"], first.json()["sessionId"])
        self.assertTrue(repeated.json()["lease"]["reused"])
        self.assertEqual(conflict.status_code, 409)
        detail = conflict.json()["detail"]
        self.assertEqual(detail["code"], "digital_human_session_capacity_exhausted")
        self.assertEqual(detail["activeSessionCount"], 1)
        self.assertGreater(detail["retryAfterSeconds"], 0)

    def test_session_lease_heartbeat_and_release_are_owner_scoped_and_idempotent(self):
        created = client.post(
            "/digital-human/sessions",
            json={
                "userId": "user_qa",
                "personaId": "persona_mother_001",
                "scene": "echo",
                "deviceId": "ios-device-1",
                "lifecycleMode": "star",
            },
        ).json()
        session_id = created["sessionId"]
        original_expiry = created["lease"]["expiresAt"]

        heartbeat = client.post(
            f"/digital-human/sessions/{session_id}/heartbeat",
            json={"userId": "user_qa", "deviceId": "ios-device-1"},
        )
        wrong_owner = client.post(
            f"/digital-human/sessions/{session_id}/heartbeat",
            json={"userId": "user_other", "deviceId": "ios-device-2"},
        )
        released = client.post(
            f"/digital-human/sessions/{session_id}/release",
            json={"userId": "user_qa", "deviceId": "ios-device-1", "reason": "pageExit"},
        )
        repeated_release = client.post(
            f"/digital-human/sessions/{session_id}/release",
            json={"userId": "user_qa", "deviceId": "ios-device-1", "reason": "pageExit"},
        )

        self.assertEqual(heartbeat.status_code, 200)
        self.assertEqual(heartbeat.json()["status"], "active")
        self.assertGreaterEqual(heartbeat.json()["lease"]["expiresAt"], original_expiry)
        self.assertEqual(wrong_owner.status_code, 404)
        self.assertEqual(released.status_code, 200)
        self.assertEqual(released.json()["status"], "released")
        self.assertEqual(released.json()["lease"]["releaseReason"], "pageExit")
        self.assertEqual(repeated_release.status_code, 200)
        self.assertEqual(repeated_release.json()["status"], "alreadyReleased")

        next_device = client.post(
            "/digital-human/sessions",
            json={
                "userId": "user_other",
                "personaId": "persona_mother_001",
                "scene": "echo",
                "deviceId": "ios-device-2",
                "lifecycleMode": "star",
            },
        )
        self.assertEqual(next_device.status_code, 200)

    def test_create_digital_human_session_rejects_silent_mode(self):
        response = client.post(
            "/digital-human/sessions",
            json={
                "userId": "user_qa",
                "personaId": "persona_silent",
                "scene": "echo",
                "deviceId": "ios-simulator",
                "lifecycleMode": "silent",
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("silent mode", response.json()["detail"])

    def test_create_digital_human_session_requires_persona_id(self):
        response = client.post(
            "/digital-human/sessions",
            json={
                "userId": "user_qa",
                "scene": "echo",
                "deviceId": "ios-simulator",
                "lifecycleMode": "star",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "personaId is required")

    def test_runtime_config_exposes_digital_human_session_capability(self):
        response = client.get("/config/runtime")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["capabilities"]["digitalHumanSession"])
        digital_human = body["digitalHuman"]
        self.assertEqual(digital_human["provider"], "tencent")
        self.assertEqual(digital_human["providerMode"], "mockContract")
        self.assertFalse(digital_human["realProviderReady"])
        self.assertEqual(digital_human["sessionEndpoint"], "/digital-human/sessions")
        self.assertTrue(digital_human["sessionLease"]["enabled"])
        self.assertEqual(digital_human["sessionLease"]["contractVersion"], 1)
        self.assertEqual(digital_human["sessionLease"]["maxConcurrentSessions"], 1)
        self.assertGreater(digital_human["sessionLease"]["ttlSeconds"], 0)
        self.assertGreater(digital_human["sessionLease"]["heartbeatIntervalSeconds"], 0)
        self.assertIn("{sessionId}", digital_human["sessionLease"]["heartbeatEndpointTemplate"])
        self.assertIn("{sessionId}", digital_human["sessionLease"]["releaseEndpointTemplate"])
        self.assertEqual(digital_human["fallbackMode"], "audioOnly")
        self.assertFalse(digital_human["defaultReleaseVisible"])
        self.assertFalse(digital_human["sdkAdapterLinked"])
        self.assertEqual(digital_human["sdkProvider"], "tencent-cloud-digital-human")
        self.assertEqual(digital_human["sdkAuthMode"], "appkeyAccessToken")
        self.assertIn("TENCENT_DIGITAL_HUMAN_APP_KEY", digital_human["requiredServerEnv"])
        self.assertIn("TENCENT_DIGITAL_HUMAN_ACCESS_TOKEN", digital_human["requiredServerEnv"])
        self.assertIn("TENCENT_DIGITAL_HUMAN_ASSET_VIRTUALMAN_KEY", digital_human["requiredAssetEnv"])
        self.assertIn("TENCENT_DIGITAL_HUMAN_VIRTUALMAN_PROJECT_ID", digital_human["requiredAssetEnv"])
        self.assertIn("asset_virtualman_key", digital_human["providerFieldAliases"])
        self.assertIn("virtualman_project_id", digital_human["providerFieldAliases"])
        self.assertIn("TENCENT_DIGITAL_HUMAN_SECRET_ID", digital_human["optionalASREnv"])
        self.assertIn("TENCENT_DIGITAL_HUMAN_SECRET_KEY", digital_human["optionalASREnv"])
        self.assertEqual(
            digital_human["sdkReadinessMessage"],
            "Tencent digital human appkey/accesstoken and native adapter are not linked in this build.",
        )

    def test_create_digital_human_session_returns_cloud_render_contract_when_configured(self):
        previous_values = {
            "tencent_digital_human_app_key": getattr(settings, "tencent_digital_human_app_key", None),
            "tencent_digital_human_access_token": getattr(settings, "tencent_digital_human_access_token", None),
            "tencent_digital_human_asset_virtualman_key": getattr(
                settings,
                "tencent_digital_human_asset_virtualman_key",
                None,
            ),
            "tencent_digital_human_virtualman_project_id": getattr(
                settings,
                "tencent_digital_human_virtualman_project_id",
                None,
            ),
        }
        try:
            object.__setattr__(settings, "tencent_digital_human_app_key", "qa_appkey")
            object.__setattr__(settings, "tencent_digital_human_access_token", "qa_accesstoken")
            object.__setattr__(settings, "tencent_digital_human_asset_virtualman_key", "asset_qa")
            object.__setattr__(settings, "tencent_digital_human_virtualman_project_id", None)

            response = client.post(
                "/digital-human/sessions",
                json={
                    "userId": "user_qa",
                    "personaId": "persona_mother_001",
                    "scene": "echo",
                    "deviceId": "ios-simulator",
                    "lifecycleMode": "sunlight",
                },
            )

            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body["provider"], "tencent")
            self.assertEqual(body["providerMode"], "cloudRender")
            self.assertEqual(body["assetKey"], "asset_qa")
            self.assertEqual(body["providerAssetId"], "asset_qa")
            self.assertNotIn("providerProjectId", body)
            self.assertEqual(body["credential"]["mode"], "backend-issued-tencent-cloud")
            self.assertEqual(body["credential"]["appkey"], "qa_appkey")
            self.assertEqual(body["credential"]["accesstoken"], "qa_accesstoken")
            self.assertEqual(body["fallback"]["mode"], "none")
            self.assertEqual(body["contractVersion"], 2)
            self.assertEqual(body["lease"]["status"], "active")
        finally:
            for key, value in previous_values.items():
                object.__setattr__(settings, key, value)

    def test_runtime_config_reports_cloud_render_ready_when_configured(self):
        previous_values = {
            "tencent_digital_human_app_key": getattr(settings, "tencent_digital_human_app_key", None),
            "tencent_digital_human_access_token": getattr(settings, "tencent_digital_human_access_token", None),
            "tencent_digital_human_asset_virtualman_key": getattr(
                settings,
                "tencent_digital_human_asset_virtualman_key",
                None,
            ),
            "tencent_digital_human_virtualman_project_id": getattr(
                settings,
                "tencent_digital_human_virtualman_project_id",
                None,
            ),
        }
        try:
            object.__setattr__(settings, "tencent_digital_human_app_key", "qa_appkey")
            object.__setattr__(settings, "tencent_digital_human_access_token", "qa_accesstoken")
            object.__setattr__(settings, "tencent_digital_human_asset_virtualman_key", None)
            object.__setattr__(settings, "tencent_digital_human_virtualman_project_id", "project_qa")

            response = client.get("/config/runtime")

            self.assertEqual(response.status_code, 200)
            digital_human = response.json()["digitalHuman"]
            self.assertEqual(digital_human["providerMode"], "cloudRender")
            self.assertTrue(digital_human["realProviderReady"])
            self.assertTrue(digital_human["sdkAdapterLinked"])
            self.assertEqual(digital_human["assetMode"], "project")
            self.assertEqual(digital_human["sdkReadinessMessage"], "Tencent cloud-render digital human session is ready.")
        finally:
            for key, value in previous_values.items():
                object.__setattr__(settings, key, value)


if __name__ == "__main__":
    unittest.main()
