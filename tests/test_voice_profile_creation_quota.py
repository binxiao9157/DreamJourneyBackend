from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main as main_module
from app.core.config import Settings
from app.main import app
from app.db.migrator import load_migrations
from app.services.in_memory_store import InMemoryStore
from app.services.release_policy import ReleasePolicyCommandGate, ReleasePolicyService
from app.services.voice_clone import VoiceCloneProviderUnavailable
from app.services.voice_profile_creation_quota import (
    VOICE_PROFILE_CREATION_LIMIT,
    VoiceProfileCreationLimitReached,
)
from app.services.voice_profile_eligibility import server_verified_test_resolution
from tests.test_voice_profile_lifecycle import (
    eligible_self,
    issue_sample_authorization_receipt,
    valid_voice_sample_base64,
)


class VoiceProfileCreationQuotaStoreTests(unittest.TestCase):
    def test_first_five_commands_are_accepted_and_sixth_is_rejected(self) -> None:
        store = InMemoryStore()
        subject_id = "voice-quota-owner"

        for ordinal in range(1, VOICE_PROFILE_CREATION_LIMIT + 1):
            reservation = store.reserve_voice_profile_creation(
                subject_id,
                command_id=f"create-command-{ordinal}",
                voice_profile_id=f"voice-profile-{ordinal}",
            )
            self.assertEqual(reservation["creationCount"], ordinal)
            self.assertEqual(
                reservation["remainingCount"],
                VOICE_PROFILE_CREATION_LIMIT - ordinal,
            )
            self.assertFalse(reservation["idempotent"])

        with self.assertRaises(VoiceProfileCreationLimitReached) as raised:
            store.reserve_voice_profile_creation(
                subject_id,
                command_id="create-command-6",
                voice_profile_id="voice-profile-6",
            )

        self.assertEqual(raised.exception.quota["creationCount"], 5)
        self.assertEqual(raised.exception.quota["remainingCount"], 0)

    def test_duplicate_command_is_idempotent_and_cannot_change_profile(self) -> None:
        store = InMemoryStore()
        first = store.reserve_voice_profile_creation(
            "voice-quota-owner",
            command_id="stable-command",
            voice_profile_id="voice-profile-a",
        )
        duplicate = store.reserve_voice_profile_creation(
            "voice-quota-owner",
            command_id="stable-command",
            voice_profile_id="voice-profile-a",
        )

        self.assertFalse(first["idempotent"])
        self.assertTrue(duplicate["idempotent"])
        self.assertEqual(duplicate["creationCount"], 1)
        self.assertEqual(
            store.get_voice_profile_creation_quota("voice-quota-owner")["creationCount"],
            1,
        )

        with self.assertRaises(ValueError):
            store.reserve_voice_profile_creation(
                "voice-quota-owner",
                command_id="stable-command",
                voice_profile_id="voice-profile-b",
            )

    def test_concurrent_reservations_never_exceed_limit(self) -> None:
        store = InMemoryStore()

        def reserve(index: int) -> str:
            try:
                store.reserve_voice_profile_creation(
                    "concurrent-owner",
                    command_id=f"concurrent-command-{index}",
                    voice_profile_id=f"concurrent-profile-{index}",
                )
                return "accepted"
            except VoiceProfileCreationLimitReached:
                return "limited"

        with ThreadPoolExecutor(max_workers=10) as executor:
            outcomes = list(executor.map(reserve, range(10)))

        self.assertEqual(outcomes.count("accepted"), VOICE_PROFILE_CREATION_LIMIT)
        self.assertEqual(outcomes.count("limited"), 5)
        self.assertEqual(
            store.get_voice_profile_creation_quota("concurrent-owner")["creationCount"],
            VOICE_PROFILE_CREATION_LIMIT,
        )

    def test_profile_deletion_does_not_refund_creation_count(self) -> None:
        store = InMemoryStore()
        subject_id = "no-refund-owner"
        store.reserve_voice_profile_creation(
            subject_id,
            command_id="create-once",
            voice_profile_id="voice-profile-once",
        )
        store.save_voice_profile(
            subject_id,
            {
                "voiceProfileId": "voice-profile-once",
                "sampleStatus": "deleted",
            },
        )

        quota = store.get_voice_profile_creation_quota(subject_id)
        self.assertEqual(quota["creationCount"], 1)
        self.assertEqual(quota["remainingCount"], 4)

    def test_0097_migration_is_additive_and_backfills_existing_profiles(self) -> None:
        root = Path(__file__).resolve().parents[1]
        sql = (root / "db/migrations/0097_voice_profile_creation_quota.sql").read_text(
            encoding="utf-8"
        )
        manifest = json.loads(
            (root / "db/migrations/0097_voice_profile_creation_quota.json").read_text(
                encoding="utf-8"
            )
        )
        migrations = load_migrations(root / "db/migrations")

        self.assertIn("0097", {migration.version for migration in migrations})
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertIn("CREATE TABLE voice_profile_creation_quotas", sql)
        self.assertIn("CREATE TABLE voice_profile_creation_commands", sql)
        self.assertIn("FROM voice_profiles", sql)
        self.assertIn("UNIQUE (subject_id, voice_profile_id)", sql)
        self.assertNotIn("provider_speaker_id", sql)


class VoiceProfileCreationQuotaAPITests(unittest.TestCase):
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

    @staticmethod
    def _payload(
        client: TestClient,
        *,
        user_id: str,
        voice_profile_id: str,
        command_id: str,
    ) -> dict[str, object]:
        return {
            "userId": user_id,
            "voiceProfileId": voice_profile_id,
            "commandId": command_id,
            "sampleStatus": "pending",
            "sampleCount": 1,
            "authorizationConfirmed": True,
            "purpose": "training",
            "personaScope": "personal",
            "digitalHumanId": user_id,
            "audioBase64": valid_voice_sample_base64(),
            "audioFormat": "wav",
            "sampleVersion": "voice-sample-v1",
            "sampleAuthorizationReceiptId": issue_sample_authorization_receipt(
                client,
                user_id=user_id,
                voice_profile_id=voice_profile_id,
            ),
            "privacyMetadata": {"scope": "generationAllowed"},
        }

    def test_create_response_exposes_quota_and_duplicate_does_not_retrain(self) -> None:
        class ReadyProvider:
            is_configured = True
            provider_mode = "fakeVoiceClone"

            def __init__(self) -> None:
                self.submit_count = 0

            def submit_training(self, **_kwargs):
                self.submit_count += 1
                return {"providerStatus": "4", "sampleStatus": "ready"}

        store = InMemoryStore()
        provider = ReadyProvider()
        client = TestClient(app)

        with patch("app.main.store", store), patch(
            "app.main.settings",
            Settings(
                volcengine_voice_clone_api_key="fake-key",
                identity_binding_hmac_key="test-sample-authorization-key",
            ),
        ), patch("app.main.VoiceCloneProviderFactory") as factory, patch(
            "app.main._resolve_trusted_voice_profile_eligibility",
            return_value=server_verified_test_resolution(eligible_self()),
        ):
            factory.return_value.make.return_value = provider
            payload = self._payload(
                client,
                user_id="quota-api-owner",
                voice_profile_id="quota-api-profile",
                command_id="quota-api-command-0001",
            )
            first = client.post("/voice/profiles", json=payload)
            duplicate = client.post("/voice/profiles", json=payload)
            inventory = client.get("/voice/profiles/quota-api-owner")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["creationQuota"]["creationCount"], 1)
        self.assertEqual(first.json()["creationQuota"]["remainingCount"], 4)
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(duplicate.json()["status"], "deduplicated")
        self.assertEqual(duplicate.json()["creationQuota"]["creationCount"], 1)
        self.assertEqual(provider.submit_count, 1)
        self.assertEqual(inventory.json()["creationQuota"]["remainingCount"], 4)

    def test_provider_failure_still_consumes_one_accepted_creation(self) -> None:
        class FailingProvider:
            is_configured = True
            provider_mode = "fakeVoiceClone"

            def submit_training(self, **_kwargs):
                raise VoiceCloneProviderUnavailable("synthetic provider failure")

        store = InMemoryStore()
        client = TestClient(app)

        with patch("app.main.store", store), patch(
            "app.main.settings",
            Settings(
                volcengine_voice_clone_api_key="fake-key",
                identity_binding_hmac_key="test-sample-authorization-key",
            ),
        ), patch("app.main.VoiceCloneProviderFactory") as factory, patch(
            "app.main._resolve_trusted_voice_profile_eligibility",
            return_value=server_verified_test_resolution(eligible_self()),
        ):
            factory.return_value.make.return_value = FailingProvider()
            payload = self._payload(
                client,
                user_id="provider-failure-owner",
                voice_profile_id="provider-failure-profile",
                command_id="provider-failure-command-0001",
            )
            response = client.post("/voice/profiles", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["profile"]["sampleStatus"], "failed")
        self.assertEqual(response.json()["creationQuota"]["creationCount"], 1)
        self.assertEqual(response.json()["creationQuota"]["remainingCount"], 4)

    def test_invalid_sample_does_not_consume_creation_quota(self) -> None:
        store = InMemoryStore()
        client = TestClient(app)
        payload = {
            "userId": "invalid-sample-owner",
            "voiceProfileId": "invalid-sample-profile",
            "commandId": "invalid-sample-command-0001",
            "sampleStatus": "pending",
            "sampleCount": 1,
            "authorizationConfirmed": True,
            "purpose": "training",
            "personaScope": "personal",
            "digitalHumanId": "invalid-sample-owner",
            "audioBase64": "U09VTkQ=",
            "audioFormat": "wav",
            "sampleVersion": "voice-sample-v1",
            "privacyMetadata": {"scope": "generationAllowed"},
        }

        with patch("app.main.store", store), patch(
            "app.main.settings",
            Settings(identity_binding_hmac_key="test-sample-authorization-key"),
        ):
            response = client.post("/voice/profiles", json=payload)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "invalidWavSample")
        self.assertEqual(
            store.get_voice_profile_creation_quota("invalid-sample-owner")["creationCount"],
            0,
        )

    def test_family_scope_cannot_replace_voice_subject_consent(self) -> None:
        store = InMemoryStore()
        client = TestClient(app)
        payload = {
            "userId": "family-request-owner",
            "voiceProfileId": "family-request-profile",
            "commandId": "family-request-command-0001",
            "sampleStatus": "pending",
            "sampleCount": 1,
            "authorizationConfirmed": True,
            "purpose": "training",
            "personaScope": "family",
            "digitalHumanId": "family-member-subject",
            "audioBase64": valid_voice_sample_base64(),
            "audioFormat": "wav",
            "sampleVersion": "voice-sample-v1",
            "privacyMetadata": {"scope": "familyCircle"},
        }

        with patch("app.main.store", store), patch(
            "app.main.settings",
            Settings(identity_binding_hmac_key="test-sample-authorization-key"),
        ):
            response = client.post("/voice/profiles", json=payload)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            store.get_voice_profile_creation_quota("family-request-owner")["creationCount"],
            0,
        )


if __name__ == "__main__":
    unittest.main()
