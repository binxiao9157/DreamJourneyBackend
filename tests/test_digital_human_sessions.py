import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import app.main as main_module
from app.core.config import settings
from app.main import app
from app.services.in_memory_store import InMemoryStore
from app.services.release_policy import ReleasePolicyCommandGate, ReleasePolicyService


client = TestClient(app)


def verified_digital_human_eligibility() -> dict:
    return {
        "capability": "digitalHuman",
        "subjectKind": "self",
        "ageStatus": "adult",
        "livingStatus": "living",
        "ageVerified": True,
        "livenessVerified": True,
        "subjectMatchesActor": True,
        "consentVerified": True,
        "consentPurpose": "digitalHuman",
    }


class DigitalHumanSessionAPITests(unittest.TestCase):
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

    def tearDown(self):
        main_module.store = self.previous_store
        main_module.RELEASE_POLICY_SERVICE = self.previous_release_policy_service
        main_module.RELEASE_POLICY_COMMAND_GATE = self.previous_release_policy_gate

    def assert_product_closed(self, response):
        self.assertEqual(response.status_code, 403, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "release_policy_denied")
        self.assertEqual(detail["feature"], "digitalHumanLivePanel")
        self.assertEqual(detail["reason"], "productClosed")

    def test_create_digital_human_session_is_blocked_by_confirmed_product_scope(self):
        response = client.post(
            "/digital-human/sessions",
            json={
                "userId": "user_qa",
                "personaId": "persona_mother_001",
                "scene": "echo",
                "deviceId": "ios-simulator",
                "lifecycleMode": "star",
                "subjectEligibility": verified_digital_human_eligibility(),
            },
        )

        self.assert_product_closed(response)
        self.assertEqual(main_module.store._digital_human_sessions, {})

    def test_blocked_session_requests_never_allocate_or_reuse_a_lease(self):
        payload = {
            "userId": "user_qa",
            "personaId": "persona_mother_001",
            "scene": "echo",
            "deviceId": "ios-device-1",
            "lifecycleMode": "star",
            "subjectEligibility": verified_digital_human_eligibility(),
        }
        first = client.post("/digital-human/sessions", json=payload)
        repeated = client.post("/digital-human/sessions", json=payload)
        conflict = client.post(
            "/digital-human/sessions",
            json={**payload, "userId": "user_other", "deviceId": "ios-device-2"},
        )

        self.assert_product_closed(first)
        self.assert_product_closed(repeated)
        self.assert_product_closed(conflict)
        self.assertEqual(main_module.store._digital_human_sessions, {})

    def test_confirmed_product_scope_denies_create_and_heartbeat_but_allows_release(self):
        service = ReleasePolicyService(
            authenticated_owner_v4_enabled=True,
            capability_resolver=lambda capability: capability == "digitalHumanLivePanel",
            enforce_default_closed_stages=False,
        )
        main_module.RELEASE_POLICY_SERVICE = service
        main_module.RELEASE_POLICY_COMMAND_GATE = ReleasePolicyCommandGate(service)
        session_id = "dh_session_product_closed_001"
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
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
                "expiresAt": (now + timedelta(seconds=120)).isoformat(),
            },
            max_concurrent_sessions=1,
            now_iso=now_iso,
        )

        created = client.post(
            "/digital-human/sessions",
            json={
                "userId": "user_qa",
                "personaId": "persona_mother_001",
                "scene": "echo",
                "deviceId": "ios-device-2",
                "lifecycleMode": "star",
                "subjectEligibility": verified_digital_human_eligibility(),
            },
        )
        heartbeat = client.post(
            f"/digital-human/sessions/{session_id}/heartbeat",
            json={"userId": "user_qa", "deviceId": "ios-device-1"},
        )
        released = client.post(
            f"/digital-human/sessions/{session_id}/release",
            json={"userId": "user_qa", "deviceId": "ios-device-1", "reason": "productClosed"},
        )

        for response in (created, heartbeat):
            self.assert_product_closed(response)
        self.assertEqual(released.status_code, 200, released.text)
        self.assertEqual(released.json()["status"], "released")
        self.assertEqual(released.json()["lease"]["releaseReason"], "productClosed")

    def test_legacy_session_release_is_owner_scoped_and_idempotent(self):
        session_id = "dh_session_legacy_001"
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
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
                "expiresAt": (now + timedelta(seconds=120)).isoformat(),
            },
            max_concurrent_sessions=1,
            now_iso=now_iso,
        )

        heartbeat = client.post(
            f"/digital-human/sessions/{session_id}/heartbeat",
            json={"userId": "user_qa", "deviceId": "ios-device-1"},
        )
        wrong_owner = client.post(
            f"/digital-human/sessions/{session_id}/release",
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

        self.assert_product_closed(heartbeat)
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
                "subjectEligibility": verified_digital_human_eligibility(),
            },
        )
        self.assert_product_closed(next_device)

    def test_product_closure_precedes_silent_mode_validation(self):
        response = client.post(
            "/digital-human/sessions",
            json={
                "userId": "user_qa",
                "personaId": "persona_silent",
                "scene": "echo",
                "deviceId": "ios-simulator",
                "lifecycleMode": "silent",
                "subjectEligibility": verified_digital_human_eligibility(),
            },
        )

        self.assert_product_closed(response)

    def test_product_closure_precedes_persona_validation(self):
        response = client.post(
            "/digital-human/sessions",
            json={
                "userId": "user_qa",
                "scene": "echo",
                "deviceId": "ios-simulator",
                "lifecycleMode": "star",
            },
        )

        self.assert_product_closed(response)

    def test_runtime_config_blocks_digital_human_without_scoped_broker(self):
        response = client.get("/config/runtime")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["capabilities"]["digitalHumanSession"])
        digital_human = body["digitalHuman"]
        self.assertEqual(digital_human["reason"], "productClosed")
        self.assertEqual(digital_human["productState"], "closed")
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
                    "subjectEligibility": verified_digital_human_eligibility(),
                },
            )

            self.assert_product_closed(response)
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
            self.assertEqual(digital_human["reason"], "productClosed")
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
