import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main as main_module
from app.main import app
from app.services.auth_sessions import AuthSessionError
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


class AccountDeletionRightsAdapterAPITests(unittest.TestCase):
    def setUp(self):
        self.previous_store = main_module.store
        self.previous_legacy_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        self.previous_release_policy_service = main_module.RELEASE_POLICY_SERVICE
        self.previous_release_policy_gate = main_module.RELEASE_POLICY_COMMAND_GATE
        main_module.store = InMemoryStore()
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        service = ReleasePolicyService(
            shadow_mode=True,
            enforce_default_closed_stages=False,
        )
        main_module.RELEASE_POLICY_SERVICE = service
        main_module.RELEASE_POLICY_COMMAND_GATE = ReleasePolicyCommandGate(service)
        self.client = TestClient(app)

    def tearDown(self):
        main_module.store = self.previous_store
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = self.previous_legacy_login
        main_module.RELEASE_POLICY_SERVICE = self.previous_release_policy_service
        main_module.RELEASE_POLICY_COMMAND_GATE = self.previous_release_policy_gate

    def test_explicit_command_is_idempotent_and_returns_redacted_rights_summary(self):
        phone = "13900009991"
        created = self.client.post("/auth/login", json={"phone": phone, "nickname": "rights owner"})
        user_id = created.json()["user"]["id"]
        payload = {
            "userId": user_id,
            "phone": phone,
            "commandId": "delete-command-1",
            "firstConfirmation": True,
            "secondConfirmation": True,
        }

        first = self.client.post("/auth/delete", json=payload)
        second = self.client.post("/auth/delete", json=payload)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["rights"]["status"], "completed")
        self.assertEqual(second.json()["rights"]["outcome"], "deduplicated")
        self.assertEqual(
            first.json()["rights"]["requestId"],
            second.json()["rights"]["requestId"],
        )
        self.assertEqual(first.json()["deletion"]["deletedAt"], second.json()["deletion"]["deletedAt"])

        summary = main_module.store.summarize_rights_request(
            first.json()["rights"]["requestId"]
        )
        serialized = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        self.assertEqual(len(summary["executions"]), 1)
        self.assertEqual(len(summary["receipts"]), 1)
        external_effects = main_module.store.list_rights_external_effect_receipts(
            first.json()["rights"]["requestId"]
        )
        external_by_domain = {item["domain"]: item for item in external_effects}
        self.assertEqual(len(external_effects), 5)
        self.assertEqual(external_by_domain["objectStorage"]["state"], "unsupported")
        self.assertEqual(external_by_domain["providerVoice"]["state"], "unsupported")
        self.assertEqual(external_by_domain["providerDigitalHuman"]["state"], "unsupported")
        self.assertEqual(external_by_domain["notificationDelivery"]["state"], "unsupported")
        self.assertEqual(external_by_domain["backupRetention"]["state"], "pending")
        self.assertNotIn("effectIdentityHash", external_by_domain["providerVoice"])
        self.assertNotIn(phone, serialized)
        self.assertNotIn("delete-command-1", serialized)

    def test_reusing_command_with_different_scope_returns_conflict(self):
        phone = "13900009992"
        created = self.client.post("/auth/login", json={"phone": phone})
        user_id = created.json()["user"]["id"]
        base = {
            "userId": user_id,
            "phone": phone,
            "commandId": "delete-command-conflict",
            "firstConfirmation": True,
            "secondConfirmation": True,
        }

        first = self.client.post(
            "/auth/delete",
            json={**base, "rightsScope": ["account", "archive"]},
        )
        conflict = self.client.post(
            "/auth/delete",
            json={**base, "rightsScope": ["account", "voice"]},
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["detail"]["code"], "rightsCommandConflict")

    def test_access_first_delete_suspends_account_and_revokes_all_session_access(self):
        phone = "13900009993"
        created = self.client.post("/auth/login", json={"phone": phone, "nickname": "suspend owner"})
        self.assertEqual(created.status_code, 200)
        user_id = created.json()["user"]["id"]
        first_auth = created.json()["auth"]
        second_auth = main_module._auth_session_service().issue(user_id)

        deleted = self.client.post(
            "/auth/delete",
            headers={"Authorization": f"Bearer {first_auth['accessToken']}"},
            json={
                "userId": user_id,
                "phone": phone,
                "commandId": "access-first-delete-command",
                "firstConfirmation": True,
                "secondConfirmation": True,
            },
        )

        self.assertEqual(deleted.status_code, 200)
        payload = deleted.json()
        self.assertEqual(payload["deletion"]["deletionState"], "softDeleted")
        self.assertEqual(payload["deletion"]["accessState"], "suspended_restorable")
        self.assertEqual(payload["deletion"]["authEpoch"], 1)
        self.assertEqual(payload["deletion"]["providerCapabilityState"], "revoked")
        self.assertEqual(payload["sessionRevocation"]["scope"], "allDevices")
        self.assertEqual(payload["accessRevocation"]["eventType"], "RightsAccessRevoked")
        self.assertEqual(payload["accessRevocation"]["authEpoch"], 1)
        self.assertEqual(payload["accessRevocation"]["status"], "pending")

        for auth in (first_auth, second_auth):
            logout = self.client.post(
                "/auth/logout",
                headers={"Authorization": f"Bearer {auth['accessToken']}"},
                json={"scope": "session"},
            )
            refresh = self.client.post("/auth/refresh", json={"refreshToken": auth["refreshToken"]})
            self.assertEqual(logout.status_code, 401)
            self.assertEqual(refresh.status_code, 401)

        with self.assertRaises(AuthSessionError) as context:
            main_module._auth_session_service().issue(user_id)
        self.assertEqual(context.exception.code, "account_session_issuance_blocked")

        outbox = main_module.store.list_rights_access_revocation_outbox(
            payload["rights"]["requestId"]
        )
        self.assertEqual(len(outbox), 1)
        self.assertEqual(outbox[0]["eventType"], "RightsAccessRevoked")
        self.assertEqual(outbox[0]["authEpoch"], 1)
        self.assertEqual(outbox[0]["status"], "pending")

    def test_account_delete_revokes_profile_authority_before_new_synthesis(self):
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
                    "voiceProfileId": "account-delete-speaker",
                    "visemeTimeline": None,
                }

        phone = "13900009994"
        created = self.client.post("/auth/login", json={"phone": phone, "nickname": "voice owner"})
        self.assertEqual(created.status_code, 200)
        user_id = created.json()["user"]["id"]
        now = datetime.now(timezone.utc)
        profile = apply_voice_profile_lifecycle(
            {
                "userId": user_id,
                "voiceProfileId": "account-delete-profile",
                "providerSpeakerId": "account-delete-speaker",
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
            eligibility_decision=SubjectEligibilityDecision(
                capability=HighRiskCapability.CLONED_VOICE,
                allowed=True,
                decision="allow",
                reason=SubjectEligibilityReason.ELIGIBLE_LIVING_ADULT_SELF,
            ),
            eligibility_provenance="serverVerified",
            now=now,
        )
        main_module.store.save_voice_profile(user_id, profile)
        provider = RecordingTTSProvider()

        with patch("app.main.VoiceCloneTTSProviderFactory") as factory:
            factory.return_value.make.return_value = provider
            before_delete = self.client.post(
                "/voice/synthesis",
                json={
                    "userId": user_id,
                    "voiceProfileId": "account-delete-profile",
                    "text": "before deletion",
                },
            )
            deleted = self.client.post(
                "/auth/delete",
                json={
                    "userId": user_id,
                    "phone": phone,
                    "commandId": "account-delete-voice-authority",
                    "firstConfirmation": True,
                    "secondConfirmation": True,
                },
            )
            after_delete = self.client.post(
                "/voice/synthesis",
                json={
                    "userId": user_id,
                    "voiceProfileId": "account-delete-profile",
                    "text": "after deletion",
                },
            )

        self.assertEqual(before_delete.status_code, 200)
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json()["voiceProfileRevocation"]["status"], "revoked")
        self.assertEqual(deleted.json()["voiceProfileRevocation"]["revokedProfileCount"], 1)
        stored = main_module.store.get_voice_profile(user_id, "account-delete-profile")
        self.assertIsNotNone(stored)
        self.assertEqual(stored["lifecycleState"], "paused")
        self.assertFalse(stored["isEnabled"])
        self.assertEqual(after_delete.status_code, 409)
        self.assertEqual(provider.synthesize_count, 1)

        restored = self.client.post("/auth/restore", json={"phone": phone})
        self.assertEqual(restored.status_code, 200)
        restored_profile = main_module.store.get_voice_profile(
            user_id,
            "account-delete-profile",
        )
        self.assertIsNotNone(restored_profile)
        self.assertEqual(restored_profile["lifecycleState"], "paused")
        self.assertFalse(restored_profile["isEnabled"])


if __name__ == "__main__":
    unittest.main()
