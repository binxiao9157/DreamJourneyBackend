import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main as main_module
from app.main import app
from app.services.in_memory_store import InMemoryStore
from app.services.release_policy import ReleasePolicyCommandGate, ReleasePolicyService


class DataExportJobRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = main_module.store
        self.previous_route_mode = main_module.AUTH_ROUTE_MODE
        self.previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
        self.previous_legacy_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        self.previous_release_policy_service = main_module.RELEASE_POLICY_SERVICE
        self.previous_release_policy_gate = main_module.RELEASE_POLICY_COMMAND_GATE

        self.store = InMemoryStore()
        main_module.store = self.store
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

    def tearDown(self) -> None:
        main_module.store = self.previous_store
        main_module.AUTH_ROUTE_MODE = self.previous_route_mode
        main_module.AUTH_OWNERSHIP_MODE = self.previous_ownership_mode
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = self.previous_legacy_login
        main_module.RELEASE_POLICY_SERVICE = self.previous_release_policy_service
        main_module.RELEASE_POLICY_COMMAND_GATE = self.previous_release_policy_gate

    def _login(self, phone: str) -> dict:
        response = self.client.post(
            "/auth/login",
            json={"phone": phone, "nickname": "数据副本测试用户"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    @staticmethod
    def _headers(login: dict) -> dict:
        return {"Authorization": f"Bearer {login['auth']['accessToken']}"}

    def test_job_is_owner_scoped_idempotent_and_downloads_partial_manifest(self) -> None:
        owner = self._login("13900008801")
        other = self._login("13900008802")
        owner_id = owner["user"]["id"]
        self.store.save_profile(
            owner_id,
            {
                "nickname": "数据副本测试用户",
                "bio": "仅本人可导出的内容",
                "apiToken": "must-not-leak-export-job",
            },
        )

        first = self.client.post(
            "/auth/data-export/jobs",
            headers=self._headers(owner),
            json={"requestKey": "export-request-001"},
        )
        replay = self.client.post(
            "/auth/data-export/jobs",
            headers=self._headers(owner),
            json={"requestKey": "export-request-001"},
        )

        self.assertEqual(first.status_code, 202, first.text)
        self.assertEqual(replay.status_code, 202, replay.text)
        self.assertEqual(first.json()["jobId"], replay.json()["jobId"])
        job_id = first.json()["jobId"]

        status = self.client.get(
            f"/auth/data-export/jobs/{job_id}",
            headers=self._headers(owner),
        )
        cross_owner = self.client.get(
            f"/auth/data-export/jobs/{job_id}",
            headers=self._headers(other),
        )
        download = self.client.get(
            f"/auth/data-export/jobs/{job_id}/download",
            headers=self._headers(owner),
        )

        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(status.json()["status"], "partial")
        self.assertEqual(status.json()["manifest"]["packageStatus"], "partial")
        self.assertEqual(cross_owner.status_code, 404, cross_owner.text)
        self.assertEqual(download.status_code, 200, download.text)
        self.assertIn("no-store", download.headers["cache-control"])
        payload = download.json()
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        self.assertEqual(payload["manifest"]["jobId"], job_id)
        self.assertEqual(payload["manifest"]["packageStatus"], "partial")
        self.assertEqual(payload["dataExport"]["ownerUserId"], owner_id)
        self.assertIn("仅本人可导出的内容", serialized)
        self.assertNotIn("must-not-leak-export-job", serialized)
        self.assertNotIn(other["user"]["id"], serialized)
        self.assertNotIn("requestKey", serialized)

    def test_failed_job_can_retry_without_changing_job_identity(self) -> None:
        owner = self._login("13900008803")
        headers = self._headers(owner)

        with patch.object(
            main_module,
            "build_module_owned_data_export",
            side_effect=RuntimeError("private provider detail must not escape"),
        ):
            created = self.client.post(
                "/auth/data-export/jobs",
                headers=headers,
                json={"requestKey": "export-request-retry-001"},
            )

        self.assertEqual(created.status_code, 202, created.text)
        job_id = created.json()["jobId"]
        failed = self.client.get(f"/auth/data-export/jobs/{job_id}", headers=headers)
        self.assertEqual(failed.status_code, 200, failed.text)
        self.assertEqual(failed.json()["status"], "failed")
        self.assertEqual(failed.json()["failureCode"], "exportMaterializationFailed")
        self.assertNotIn("private provider detail", failed.text)

        retried = self.client.post(
            f"/auth/data-export/jobs/{job_id}/retry",
            headers=headers,
            json={},
        )
        status = self.client.get(f"/auth/data-export/jobs/{job_id}", headers=headers)

        self.assertEqual(retried.status_code, 202, retried.text)
        self.assertEqual(retried.json()["jobId"], job_id)
        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(status.json()["status"], "partial")
        self.assertEqual(status.json()["attempt"], 2)

    def test_account_must_be_active_when_creating_job(self) -> None:
        owner = self._login("13900008804")
        inactive = dict(self.store.get_user(owner["user"]["id"]) or {})
        inactive["deletionState"] = "softDeleted"

        with patch.object(main_module, "_store_get_user", return_value=inactive):
            response = self.client.post(
                "/auth/data-export/jobs",
                headers=self._headers(owner),
                json={"requestKey": "export-after-delete"},
            )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "data_export_unavailable_after_deletion",
        )


if __name__ == "__main__":
    unittest.main()
