import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.services.in_memory_store import InMemoryStore
from app.services.release_policy import ReleasePolicyCommandGate, ReleasePolicyService


client = TestClient(app)


class AccountPurgeAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = main_module.store
        self.previous_backend_token = main_module.BACKEND_API_TOKEN
        self.previous_route_mode = main_module.AUTH_ROUTE_MODE
        self.previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
        self.previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        self.previous_release_policy_service = main_module.RELEASE_POLICY_SERVICE
        self.previous_release_policy_gate = main_module.RELEASE_POLICY_COMMAND_GATE

        self.store = InMemoryStore()
        main_module.store = self.store
        main_module.BACKEND_API_TOKEN = "account-purge-machine-token"
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        release_policy = ReleasePolicyService(
            shadow_mode=True,
            enforce_default_closed_stages=False,
        )
        main_module.RELEASE_POLICY_SERVICE = release_policy
        main_module.RELEASE_POLICY_COMMAND_GATE = ReleasePolicyCommandGate(release_policy)

    def tearDown(self) -> None:
        main_module.store = self.previous_store
        main_module.BACKEND_API_TOKEN = self.previous_backend_token
        main_module.AUTH_ROUTE_MODE = self.previous_route_mode
        main_module.AUTH_OWNERSHIP_MODE = self.previous_ownership_mode
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = self.previous_legacy_phone_login
        main_module.RELEASE_POLICY_SERVICE = self.previous_release_policy_service
        main_module.RELEASE_POLICY_COMMAND_GATE = self.previous_release_policy_gate

    @staticmethod
    def machine_headers() -> dict:
        return {"Authorization": "Bearer account-purge-machine-token"}

    def test_purge_is_machine_only_uses_server_clock_and_redacts_tombstones(self):
        phone = "13800138113"
        user = self.store.upsert_user(phone, "清除接口测试")
        self.store.soft_delete_user(
            user["id"],
            phone=phone,
            requested_at_iso="2026-01-01T00:00:00+00:00",
            deletion_request_id="rr_purge_api_001",
        )

        anonymous = client.post("/auth/purge-expired-deletions", json={})
        self.assertEqual(anonymous.status_code, 401)

        with patch.object(
            main_module,
            "_account_purge_server_cutoff",
            return_value="2026-01-15T00:00:00+00:00",
        ):
            before_deadline = client.post(
                "/auth/purge-expired-deletions",
                headers=self.machine_headers(),
                json={"cutoff": "2099-01-01T00:00:00+00:00"},
            )

        self.assertEqual(before_deadline.status_code, 200, before_deadline.text)
        self.assertEqual(before_deadline.json()["status"], "purgeScanCompleted")
        self.assertEqual(before_deadline.json()["cutoff"], "2026-01-15T00:00:00+00:00")
        self.assertEqual(before_deadline.json()["cutoffSource"], "serverClock")
        self.assertEqual(before_deadline.json()["purgedCount"], 0)
        self.assertNotIn("items", before_deadline.json())

        with patch.object(
            main_module,
            "_account_purge_server_cutoff",
            return_value="2026-02-01T00:00:00+00:00",
        ):
            due = client.post(
                "/auth/purge-expired-deletions",
                headers=self.machine_headers(),
                json={"cutoff": "2000-01-01T00:00:00+00:00"},
            )

        self.assertEqual(due.status_code, 200, due.text)
        self.assertEqual(due.json()["purgedCount"], 1)
        self.assertNotIn("items", due.json())
        self.assertNotIn(phone, due.text)
        self.assertNotIn(user["id"], due.text)
        self.assertEqual(self.store.get_user(user["id"])["phone"], "")
        self.assertIsNotNone(self.store.get_account_purge_receipt(user["id"]))


if __name__ == "__main__":
    unittest.main()
