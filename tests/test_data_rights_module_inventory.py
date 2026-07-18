import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main as main_module
from app.main import app
from app.services.data_rights_module_inventory import build_module_owned_data_export
from app.services.in_memory_store import InMemoryStore
from app.services.release_policy import ReleasePolicyCommandGate, ReleasePolicyService


class DataRightsModuleInventoryTests(unittest.TestCase):
    def setUp(self):
        self.previous_store = main_module.store
        self.previous_backend_token = main_module.BACKEND_API_TOKEN
        self.previous_route_mode = main_module.AUTH_ROUTE_MODE
        self.previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
        self.previous_legacy_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        self.previous_release_policy_service = main_module.RELEASE_POLICY_SERVICE
        self.previous_release_policy_gate = main_module.RELEASE_POLICY_COMMAND_GATE

        self.store = InMemoryStore()
        main_module.store = self.store
        main_module.BACKEND_API_TOKEN = "rights-inventory-machine-token"
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        policy = ReleasePolicyService(
            shadow_mode=True,
            enforce_default_closed_stages=False,
        )
        main_module.RELEASE_POLICY_SERVICE = policy
        main_module.RELEASE_POLICY_COMMAND_GATE = ReleasePolicyCommandGate(policy)
        self.client = TestClient(app)

    def tearDown(self):
        main_module.store = self.previous_store
        main_module.BACKEND_API_TOKEN = self.previous_backend_token
        main_module.AUTH_ROUTE_MODE = self.previous_route_mode
        main_module.AUTH_OWNERSHIP_MODE = self.previous_ownership_mode
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = self.previous_legacy_login
        main_module.RELEASE_POLICY_SERVICE = self.previous_release_policy_service
        main_module.RELEASE_POLICY_COMMAND_GATE = self.previous_release_policy_gate

    def _login(self, phone="13900007771"):
        response = self.client.post(
            "/auth/login",
            json={"phone": phone, "nickname": "导出测试用户"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_owner_export_contains_module_data_but_redacts_credentials_and_media_boundary(self):
        login = self._login()
        user_id = login["user"]["id"]
        self.store.save_profile(
            user_id,
            {"nickname": "导出测试用户", "apiToken": "must-not-leak", "bio": "个人说明"},
        )
        self.store.add_memory(user_id, {"id": "memory_export_1", "content": "可导出的记忆"})
        self.store.add_archive_item(
            user_id,
            {"id": "archive_export_1", "kind": "text", "description": "可导出的档案"},
        )
        self.store.save_kb_snapshot(user_id, {"nodes": [{"id": "node_export_1"}]})
        self.store.save_push_device_token(
            user_id,
            {"id": "device_1", "deviceToken": "raw-device-token-should-not-export"},
        )

        export = build_module_owned_data_export(
            self.store,
            user_id=user_id,
            generated_at="2026-07-18T12:00:00+00:00",
        )
        serialized = json.dumps(export, ensure_ascii=False, sort_keys=True)
        archive = next(
            item
            for item in export["machineReadable"]["objects"]
            if item["moduleId"] == "archive"
        )

        self.assertEqual(export["schemaVersion"], 1)
        self.assertEqual(export["status"], "ready")
        self.assertEqual(export["generatedAt"], "2026-07-18T12:00:00+00:00")
        self.assertEqual(archive["itemCount"], 1)
        relationship_export = next(
            item
            for item in export["machineReadable"]["objects"]
            if item["resourceType"] == "ownerRelationship"
        )
        self.assertEqual(relationship_export["status"], "partial")
        self.assertEqual(
            relationship_export["reasonCode"],
            "ownerScopedRelationshipProjection",
        )
        self.assertIn("可导出的记忆", serialized)
        self.assertIn("可导出的档案", serialized)
        self.assertNotIn("must-not-leak", serialized)
        self.assertNotIn("raw-device-token-should-not-export", serialized)
        self.assertNotIn("13900007771", serialized)
        boundary_by_module = {
            item["moduleId"]: item["status"]
            for item in export["externalBoundaries"]
        }
        self.assertEqual(boundary_by_module["objectStorage"], "unsupported")
        self.assertEqual(boundary_by_module["backupRetention"], "pending")

    def test_export_route_requires_active_owner_session_and_disables_response_caching(self):
        login = self._login("13900007772")
        token = login["auth"]["accessToken"]

        anonymous = self.client.post("/auth/data-export", json={})
        response = self.client.post(
            "/auth/data-export",
            headers={"Authorization": f"Bearer {token}"},
            json={},
        )

        self.assertEqual(anonymous.status_code, 401)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.json()["ownerUserId"], login["user"]["id"])

    def test_terminal_purge_records_module_cleanup_without_claiming_external_completion(self):
        login = self._login("13900007773")
        user_id = login["user"]["id"]
        self.store.add_archive_item(
            user_id,
            {"id": "archive_cleanup_1", "kind": "text", "description": "待清理"},
        )
        self.store.save_voice_profile(
            user_id,
            {"id": "voice_cleanup_1", "voiceProfileId": "voice_cleanup_1", "status": "ready"},
        )
        delete = self.client.post(
            "/auth/delete",
            headers={"Authorization": f"Bearer {login['auth']['accessToken']}"},
            json={
                "userId": user_id,
                "phone": "13900007773",
                "commandId": "rights-module-cleanup",
                "firstConfirmation": True,
                "secondConfirmation": True,
            },
        )
        self.assertEqual(delete.status_code, 200, delete.text)
        request_id = delete.json()["rights"]["requestId"]

        with patch.object(
            main_module,
            "_account_purge_server_cutoff",
            return_value="2099-01-01T00:00:00+00:00",
        ):
            purged = self.client.post(
                "/auth/purge-expired-deletions",
                headers={"Authorization": "Bearer rights-inventory-machine-token"},
                json={},
            )

        self.assertEqual(purged.status_code, 200, purged.text)
        self.assertEqual(purged.json()["purgedCount"], 1)
        summary = self.store.summarize_rights_request(request_id)
        executions = {
            item["moduleId"]: item["outcome"]
            for item in summary["executions"]
        }
        receipts = {
            item["moduleId"]: item["outcome"]
            for item in summary["receipts"]
        }
        serialized = json.dumps(summary, ensure_ascii=False, sort_keys=True)

        self.assertEqual(executions["archive"], "completed")
        self.assertEqual(executions["providerVoice"], "pending")
        self.assertEqual(executions["backupRetention"], "pending")
        self.assertEqual(receipts["objectStorage"], "unsupported")
        self.assertNotIn("13900007773", serialized)
        self.assertNotIn(user_id, serialized)


if __name__ == "__main__":
    unittest.main()
