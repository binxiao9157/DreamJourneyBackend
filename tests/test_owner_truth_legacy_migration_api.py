from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.services.in_memory_store import InMemoryStore


client = TestClient(app)


class OwnerTruthLegacyMigrationAPITests(unittest.TestCase):
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
            json={"phone": phone, "nickname": "旧数据盘点 QA", "password": "password123"},
        )
        if response.status_code != 200:
            raise AssertionError(response.text)
        payload = response.json()
        return payload["user"]["id"], {
            "Authorization": f"Bearer {payload['auth']['accessToken']}",
            "X-DreamJourney-QA-Owner-Truth": "1",
        }

    def test_contract_is_default_hidden(self) -> None:
        _owner_id, headers = self._login("13800139321")
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = False

        response = client.post(
            "/v2/vaults/vault-legacy-hidden/legacy-migration/inventory",
            headers=headers,
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json()["detail"]["code"],
            "ownerTruthLegacyMigrationUnavailable",
        )

    def test_owner_can_inventory_legacy_rows_without_creating_authority_targets(self) -> None:
        owner_id, headers = self._login("13800139322")
        vault_id = "vault-legacy-owner"
        raw_archive_note = "这段私密档案正文不得出现在 inventory 响应"
        raw_memory_summary = "这段旧记忆正文也不得泄漏"
        main_module.store.add_archive_item(
            owner_id,
            {"id": "archive-legacy-api-001", "kind": "text", "note": raw_archive_note},
        )
        main_module.store.add_memory(
            owner_id,
            {"id": "memory-legacy-api-001", "summary": raw_memory_summary},
        )

        created = client.post(
            f"/v2/vaults/{vault_id}/legacy-migration/inventory",
            headers=headers,
        )
        replay = client.post(
            f"/v2/vaults/{vault_id}/legacy-migration/inventory",
            headers=headers,
        )

        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.headers["cache-control"], "no-store")
        body = created.json()
        self.assertEqual(body["schemaVersion"], "owner-truth-legacy-migration-inventory-v1")
        self.assertEqual(body["outcome"], "created")
        self.assertEqual(body["inventory"]["entryCount"], 2)
        self.assertEqual(
            body["inventory"]["classificationCounts"],
            {"needs_review": 1, "observed_candidate": 1},
        )
        self.assertNotIn(raw_archive_note, str(body))
        self.assertNotIn(raw_memory_summary, str(body))
        self.assertNotIn("archive-legacy-api-001", str(body))
        self.assertNotIn("memory-legacy-api-001", str(body))
        self.assertEqual(main_module.store.owner_truth_source_count(vault_id), 0)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()["outcome"], "deduplicated")
        self.assertEqual(replay.json()["runId"], body["runId"])

    def test_authenticated_qa_header_is_not_anonymous_authorization(self) -> None:
        response = client.post(
            "/v2/vaults/vault-legacy-anonymous/legacy-migration/inventory",
            headers={"X-DreamJourney-QA-Owner-Truth": "1"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.json()["detail"]["code"],
            "route_authentication_denied",
        )


if __name__ == "__main__":
    unittest.main()
