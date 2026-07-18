import json
import unittest

from fastapi.testclient import TestClient

from app import main as main_module
from app.main import app
from app.services.in_memory_store import InMemoryStore


class AccountDeletionRightsAdapterAPITests(unittest.TestCase):
    def setUp(self):
        self.previous_store = main_module.store
        self.previous_legacy_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        main_module.store = InMemoryStore()
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        self.client = TestClient(app)

    def tearDown(self):
        main_module.store = self.previous_store
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = self.previous_legacy_login

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


if __name__ == "__main__":
    unittest.main()
