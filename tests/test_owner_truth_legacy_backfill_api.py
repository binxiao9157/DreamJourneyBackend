from __future__ import annotations

import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main_module
from app.domain.owner_truth.source_commands import (
    CreateTextSourceCommand,
    OwnerTruthCommandContext,
)
from app.main import app
from app.services.in_memory_store import InMemoryStore
from app.services.owner_truth_source import OwnerTruthSourceCommandService


client = TestClient(app)


class OwnerTruthLegacyBackfillAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = main_module.store
        self.previous_backend_token = main_module.BACKEND_API_TOKEN
        self.previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        self.previous_route_mode = main_module.AUTH_ROUTE_MODE
        self.previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
        self.previous_qa_enabled = main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED
        main_module.store = InMemoryStore()
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = True

    def tearDown(self) -> None:
        main_module.store = self.previous_store
        main_module.BACKEND_API_TOKEN = self.previous_backend_token
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = self.previous_legacy_phone_login
        main_module.AUTH_ROUTE_MODE = self.previous_route_mode
        main_module.AUTH_OWNERSHIP_MODE = self.previous_ownership_mode
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = self.previous_qa_enabled

    @staticmethod
    def _login(phone: str) -> tuple[str, dict[str, str]]:
        response = client.post(
            "/auth/login",
            json={"phone": phone, "nickname": "C03 QA", "password": "password123"},
        )
        if response.status_code != 200:
            raise AssertionError(response.text)
        payload = response.json()
        return payload["user"]["id"], {
            "Authorization": f"Bearer {payload['auth']['accessToken']}",
            "X-DreamJourney-QA-Owner-Truth": "1",
        }

    @staticmethod
    def _seed_active_vault(owner_id: str, vault_id: str) -> None:
        context = OwnerTruthCommandContext(
            vault_id=vault_id,
            owner_subject_id=owner_id,
            actor_subject_id=owner_id,
        )
        OwnerTruthSourceCommandService(main_module.store).create_text_source(
            command=CreateTextSourceCommand(
                command_id=f"legacy-backfill-api-seed:{vault_id}",
                source_id=str(uuid4()),
                expected_version=0,
                text="seed active owner truth vault",
                metadata={},
            ),
            context=context,
        )

    def test_contract_is_default_hidden(self) -> None:
        owner_id, headers = self._login("13800139441")
        self._seed_active_vault(owner_id, "vault-backfill-hidden")
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = False

        response = client.post(
            "/v2/vaults/vault-backfill-hidden/legacy-migration/backfill-plan",
            headers=headers,
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json()["detail"]["code"],
            "ownerTruthLegacyMigrationUnavailable",
        )

    def test_owner_can_create_value_free_plan_without_authority_promotion(self) -> None:
        owner_id, headers = self._login("13800139442")
        vault_id = "vault-backfill-owner"
        self._seed_active_vault(owner_id, vault_id)
        private_body = "这段旧档案正文不得出现在 C03 计划响应"
        main_module.store.add_archive_item(
            owner_id,
            {"id": "legacy-backfill-api-private-id", "kind": "text", "note": private_body},
        )

        created = client.post(
            f"/v2/vaults/{vault_id}/legacy-migration/backfill-plan",
            headers=headers,
        )
        replay = client.post(
            f"/v2/vaults/{vault_id}/legacy-migration/backfill-plan",
            headers=headers,
        )

        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.headers["cache-control"], "no-store")
        payload = created.json()
        self.assertEqual(
            payload["schemaVersion"],
            "owner-truth-legacy-backfill-plan-service-v1",
        )
        self.assertEqual(
            payload["planSchemaVersion"],
            "owner-truth-legacy-backfill-admission-plan-v1",
        )
        self.assertEqual(payload["entryCount"], 1)
        self.assertEqual(payload["targetState"], "notCreated")
        self.assertFalse(payload["cutoverAllowed"])
        self.assertFalse(payload["legacyWriterRetired"])
        self.assertNotIn(private_body, str(payload))
        self.assertNotIn("legacy-backfill-api-private-id", str(payload))
        self.assertEqual(main_module.store.owner_truth_source_count(vault_id), 1)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()["planId"], payload["planId"])

    def test_cross_owner_is_rejected_before_inventory_or_plan_is_written(self) -> None:
        owner_id, _owner_headers = self._login("13800139443")
        vault_id = "vault-backfill-cross-owner"
        self._seed_active_vault(owner_id, vault_id)
        _attacker_id, attacker_headers = self._login("13800139444")

        response = client.post(
            f"/v2/vaults/{vault_id}/legacy-migration/backfill-plan",
            headers=attacker_headers,
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json()["detail"]["code"],
            "ownerTruthLegacyMigrationDenied",
        )
        self.assertEqual(main_module.store.owner_truth_legacy_migration_repository().snapshot()["runCount"], 0)
        self.assertEqual(main_module.store.owner_truth_legacy_backfill_repository().snapshot()["planCount"], 0)


if __name__ == "__main__":
    unittest.main()
