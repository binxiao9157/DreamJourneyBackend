import unittest
from datetime import datetime, timedelta, timezone

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

    def test_create_digital_human_session_is_blocked_without_scoped_broker(self):
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

        self.assertEqual(response.status_code, 503)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "digital_human_credential_broker_unavailable")
        self.assertEqual(detail["credentialMode"], "blockedStaticCredential")
        self.assertEqual(detail["accessPath"], "textFallback")
        self.assertFalse(detail["mobileDirectAllowed"])
        self.assertEqual(detail["brokerStatus"], "providerContractNotVerified")
        self.assertFalse(detail["providerReady"])
        self.assertFalse(detail["releaseVisible"])
        self.assertFalse(detail["retryable"])
        self.assertEqual(detail["fallbackMode"], "text")
        receipt = detail["decisionReceipt"]
        required = ["scope", "ttl", "audience", "revocation"]
        self.assertEqual(receipt["decision"], "keepDirectMobileClosed")
        self.assertEqual(receipt["reasonCode"], "scopedSessionCredentialContractNotVerified")
        self.assertEqual(receipt["requiredProperties"], required)
        self.assertEqual(receipt["verifiedProperties"], [])
        self.assertEqual(receipt["missingProperties"], required)
        self.assertNotIn("expiresAt", detail)
        self.assertNotIn("expiresInSeconds", detail)
        self.assertEqual(detail["contractVersion"], 4)
        self.assertEqual(main_module.store._digital_human_sessions, {})

    def test_blocked_session_requests_never_allocate_or_reuse_a_lease(self):
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

        self.assertEqual(first.status_code, 503)
        self.assertEqual(repeated.status_code, 503)
        self.assertEqual(conflict.status_code, 503)
        self.assertEqual(main_module.store._digital_human_sessions, {})

    def test_session_lease_heartbeat_and_release_are_owner_scoped_and_idempotent(self):
        session_id = "dh_session_legacy_001"
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        original_expiry = (now + timedelta(seconds=120)).isoformat()
        main_module.store.acquire_digital_human_session_lease(
            {
                "sessionId": session_id,
                "resourceKey": "legacy_resource",
                "userId": "user_qa",
                "deviceId": "ios-device-1",
                "personaId": "persona_mother_001",
                "scene": "echo",
                "lifecycleMode": "star",
                "providerMode": "legacyCloudRender",
                "status": "active",
                "createdAt": now_iso,
                "heartbeatAt": now_iso,
                "expiresAt": original_expiry,
            },
            max_concurrent_sessions=1,
            now_iso=now_iso,
        )

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
        self.assertEqual(next_device.status_code, 503)

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

    def test_runtime_config_blocks_digital_human_without_scoped_broker(self):
        response = client.get("/config/runtime")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["capabilities"]["digitalHumanSession"])
        digital_human = body["digitalHuman"]
        self.assertEqual(digital_human["provider"], "tencent")
        self.assertEqual(digital_human["providerMode"], "blocked")
        self.assertFalse(digital_human["realProviderReady"])
        self.assertEqual(digital_human["sessionEndpoint"], "/digital-human/sessions")
        self.assertFalse(digital_human["sessionLease"]["enabled"])
        self.assertEqual(digital_human["sessionLease"]["contractVersion"], 1)
        self.assertEqual(digital_human["sessionLease"]["maxConcurrentSessions"], 1)
        self.assertGreater(digital_human["sessionLease"]["ttlSeconds"], 0)
        self.assertGreater(digital_human["sessionLease"]["heartbeatIntervalSeconds"], 0)
        self.assertIn("{sessionId}", digital_human["sessionLease"]["heartbeatEndpointTemplate"])
        self.assertIn("{sessionId}", digital_human["sessionLease"]["releaseEndpointTemplate"])
        self.assertEqual(digital_human["fallbackMode"], "text")
        self.assertFalse(digital_human["defaultReleaseVisible"])
        self.assertFalse(digital_human["sdkAdapterLinked"])
        self.assertEqual(digital_human["sdkProvider"], "tencent-cloud-digital-human")
        self.assertEqual(digital_human["sdkAuthMode"], "staticProjectCredentialUnsupportedOnMobile")
        self.assertEqual(digital_human["credentialMode"], "blockedStaticCredential")
        self.assertEqual(digital_human["accessPath"], "textFallback")
        self.assertFalse(digital_human["mobileDirectAllowed"])
        self.assertEqual(digital_human["brokerStatus"], "providerContractNotVerified")
        self.assertFalse(digital_human["releaseVisible"])
        self.assertEqual(digital_human["credentialBroker"]["status"], "providerContractNotVerified")
        required = ["scope", "ttl", "audience", "revocation"]
        receipt = digital_human["decisionReceipt"]
        self.assertEqual(receipt["decision"], "keepDirectMobileClosed")
        self.assertEqual(receipt["requiredProperties"], required)
        self.assertEqual(receipt["verifiedProperties"], [])
        self.assertEqual(receipt["missingProperties"], required)
        self.assertEqual(digital_human["contractVersion"], 4)
        self.assertEqual(
            digital_human["sdkReadinessMessage"],
            "Tencent mobile SDK only exposes project-level static credentials; digital human rendering is blocked.",
        )

    def test_static_provider_configuration_does_not_reenable_session_response(self):
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

            self.assertEqual(response.status_code, 503)
            detail = response.json()["detail"]
            self.assertEqual(detail["code"], "digital_human_credential_broker_unavailable")
            self.assertNotIn("qa_appkey", response.text)
            self.assertNotIn("qa_accesstoken", response.text)
            self.assertEqual(main_module.store._digital_human_sessions, {})
        finally:
            for key, value in previous_values.items():
                object.__setattr__(settings, key, value)

    def test_runtime_config_stays_blocked_when_only_static_provider_values_exist(self):
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
            self.assertEqual(digital_human["providerMode"], "blocked")
            self.assertFalse(digital_human["realProviderReady"])
            self.assertFalse(digital_human["sdkAdapterLinked"])
            self.assertEqual(digital_human["assetMode"], "project")
            self.assertEqual(digital_human["credentialMode"], "blockedStaticCredential")
            self.assertNotIn("qa_appkey", response.text)
            self.assertNotIn("qa_accesstoken", response.text)
        finally:
            for key, value in previous_values.items():
                object.__setattr__(settings, key, value)


if __name__ == "__main__":
    unittest.main()
