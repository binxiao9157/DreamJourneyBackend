import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.main as main_module
from app.core.config import settings
from app.main import app
from app.services.in_memory_store import InMemoryStore
from app.services.release_policy import ReleasePolicyCommandGate, ReleasePolicyService
from app.services.tokens import TokenService


client = TestClient(app)


class CredentialResponseBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.previous_store = main_module.store
        self.previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        self.previous_release_policy_service = main_module.RELEASE_POLICY_SERVICE
        self.previous_release_policy_gate = main_module.RELEASE_POLICY_COMMAND_GATE
        main_module.store = InMemoryStore()
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        release_policy = ReleasePolicyService(
            shadow_mode=True,
            enforce_default_closed_stages=False,
        )
        main_module.RELEASE_POLICY_SERVICE = release_policy
        main_module.RELEASE_POLICY_COMMAND_GATE = ReleasePolicyCommandGate(release_policy)
        self.setting_names = (
            "backend_api_token",
            "tencent_digital_human_app_key",
            "tencent_digital_human_access_token",
            "tencent_digital_human_asset_virtualman_key",
            "volcengine_app_id",
            "volcengine_app_key",
            "volcengine_app_token",
            "volcengine_api_key",
        )
        self.previous_settings = {
            name: getattr(settings, name, None) for name in self.setting_names
        }
        self.sentinels = {
            "backend_api_token": "test-system-token-boundary",
            "tencent_digital_human_app_key": "test-dh-app-key-boundary",
            "tencent_digital_human_access_token": "test-dh-access-token-boundary",
            "tencent_digital_human_asset_virtualman_key": "test-public-asset-id",
            "volcengine_app_id": "test-public-app-id",
            "volcengine_app_key": "test-voice-app-key-boundary",
            "volcengine_app_token": "test-voice-app-token-boundary",
            "volcengine_api_key": "test-voice-api-key-boundary",
        }
        for name, value in self.sentinels.items():
            object.__setattr__(settings, name, value)
        main_module.BACKEND_API_TOKEN = self.sentinels["backend_api_token"]

    def tearDown(self):
        main_module.store = self.previous_store
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = self.previous_legacy_phone_login
        main_module.RELEASE_POLICY_SERVICE = self.previous_release_policy_service
        main_module.RELEASE_POLICY_COMMAND_GATE = self.previous_release_policy_gate
        for name, value in self.previous_settings.items():
            object.__setattr__(settings, name, value)
        main_module.BACKEND_API_TOKEN = str(settings.backend_api_token or "")

    def assert_no_store(self, response):
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        self.assertEqual(response.headers.get("pragma"), "no-cache")

    def assert_value_free(self, payload):
        serialized = json.dumps(payload, sort_keys=True).lower()
        for value in self.sentinels.values():
            self.assertNotIn(value.lower(), serialized)
        for forbidden_field in (
            "appkey",
            "accesstoken",
            "apptoken",
            "apikey",
            "secretkey",
        ):
            self.assertNotIn(forbidden_field, serialized)

    def user_headers(self, phone: str = "13800139902"):
        login = client.post(
            "/auth/login",
            json={"phone": phone, "nickname": "Boundary User", "password": "password123"},
        )
        self.assertEqual(login.status_code, 200)
        return {"Authorization": f"Bearer {login.json()['auth']['accessToken']}"}, login.json()["user"]["id"]

    def test_auth_and_runtime_responses_are_no_store(self):
        login = client.post(
            "/auth/login",
            json={"phone": "13800139901", "nickname": "Boundary User"},
        )
        self.assertEqual(login.status_code, 200)
        self.assert_no_store(login)

        runtime = client.get("/config/runtime")
        self.assertEqual(runtime.status_code, 200)
        self.assert_no_store(runtime)
        body = runtime.json()
        self.assertFalse(body["capabilities"]["realtimeToken"])
        self.assertFalse(body["capabilities"]["digitalHumanSession"])
        self.assertFalse(body["auth"]["legacyBackendTokenCompatible"])
        self.assertEqual(body["voice"]["credentialMode"], "blockedStaticCredential")
        self.assertEqual(body["voice"]["accessPath"], "backendProxyOrText")
        self.assertFalse(body["voice"]["mobileDirectAllowed"])
        self.assertEqual(
            body["voice"]["decisionReceipt"]["reasonCode"],
            "scopedSessionCredentialContractNotVerified",
        )
        self.assertEqual(body["digitalHuman"]["credentialMode"], "blockedStaticCredential")
        self.assertFalse(body["digitalHuman"]["releaseVisible"])
        self.assert_value_free(body)

    def test_digital_human_session_is_blocked_without_true_broker(self):
        headers, user_id = self.user_headers("13800139903")
        response = client.post(
            "/digital-human/sessions",
            headers=headers,
            json={
                "userId": user_id,
                "personaId": "persona_boundary",
                "scene": "echo",
                "deviceId": "ios-boundary",
                "lifecycleMode": "sunlight",
                "subjectEligibility": {
                    "capability": "digitalHuman",
                    "subjectKind": "self",
                    "ageStatus": "adult",
                    "livingStatus": "living",
                    "ageVerified": True,
                    "livenessVerified": True,
                    "subjectMatchesActor": True,
                    "consentVerified": True,
                    "consentPurpose": "digitalHuman",
                },
            },
        )

        self.assertEqual(response.status_code, 503)
        self.assert_no_store(response)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "digital_human_credential_broker_unavailable")
        self.assertFalse(detail["providerReady"])
        self.assertFalse(detail["releaseVisible"])
        self.assertFalse(detail["retryable"])
        self.assertEqual(detail["fallbackMode"], "text")
        self.assert_value_free(response.json())

    def test_realtime_voice_returns_blocked_value_free_capability(self):
        headers, user_id = self.user_headers("13800139904")
        response = client.post(
            "/voice/realtime-token",
            headers=headers,
            json={"userId": user_id},
        )

        self.assertEqual(response.status_code, 200)
        self.assert_no_store(response)
        body = response.json()
        self.assertEqual(body["status"], "blocked")
        self.assertEqual(body["credentialMode"], "blockedStaticCredential")
        self.assertFalse(body["providerReady"])
        self.assertFalse(body["releaseVisible"])
        self.assertFalse(body["retryable"])
        self.assertEqual(body["accessPath"], "backendProxyOrText")
        self.assertFalse(body["mobileDirectAllowed"])
        self.assertEqual(body["brokerStatus"], "providerContractNotVerified")
        receipt = body["decisionReceipt"]
        self.assertEqual(receipt["decision"], "keepDirectMobileClosed")
        self.assertEqual(
            receipt["requiredProperties"],
            ["scope", "ttl", "audience", "revocation"],
        )
        self.assertEqual(receipt["verifiedProperties"], [])
        self.assertEqual(receipt["missingProperties"], receipt["requiredProperties"])
        self.assertEqual(body["fallback"]["mode"], "backendProxyOrText")
        self.assertNotIn("expiresAt", body)
        self.assertNotIn("expiresInSeconds", body)
        self.assert_value_free(body)

        service_payload = TokenService(settings).realtime_config(user_id=user_id)
        self.assertEqual(service_payload, body)

    def test_legacy_tts_response_is_no_store_and_redacts_provider_references(self):
        headers, user_id = self.user_headers("13800139905")
        with patch("app.main.VolcTTSProxy.request_tts") as request_tts:
            request_tts.return_value = {
                "code": 3000,
                "message": "Success from Provider",
                "reqid": "raw-provider-request-id",
                "logid": "raw-provider-log-id",
                "data": "U09VTkQ=",
                "appkey": "raw-provider-app-key",
            }
            response = client.post(
                "/tts",
                json={"text": "边界测试", "userId": user_id},
                headers=headers,
            )

        self.assertEqual(response.status_code, 200)
        self.assert_no_store(response)
        body = response.json()
        self.assertEqual(body["code"], 3000)
        self.assertEqual(body["data"], "U09VTkQ=")
        self.assertTrue(body["providerRequestIdHash"].startswith("sha256:"))
        self.assertTrue(body["providerLogIdHash"].startswith("sha256:"))
        self.assertNotIn("providerMessageHash", body)
        self.assertNotIn("raw-provider", response.text)
        self.assertNotIn("appkey", response.text.lower())


if __name__ == "__main__":
    unittest.main()
