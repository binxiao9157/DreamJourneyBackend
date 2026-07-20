import json
import unittest

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.services.data_rights_contract import DataRightsRequestAuthority
from app.services.in_memory_store import InMemoryStore
from app.services.release_policy import ReleasePolicyCommandGate, ReleasePolicyService


class DataRightsEvidenceProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = main_module.store
        self.previous_backend_token = main_module.BACKEND_API_TOKEN
        self.previous_route_mode = main_module.AUTH_ROUTE_MODE
        self.previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
        self.previous_legacy_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        self.previous_release_policy_service = main_module.RELEASE_POLICY_SERVICE
        self.previous_release_policy_gate = main_module.RELEASE_POLICY_COMMAND_GATE

        self.store = InMemoryStore()
        main_module.store = self.store
        main_module.BACKEND_API_TOKEN = "rights-evidence-machine-token"
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
        main_module.BACKEND_API_TOKEN = self.previous_backend_token
        main_module.AUTH_ROUTE_MODE = self.previous_route_mode
        main_module.AUTH_OWNERSHIP_MODE = self.previous_ownership_mode
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = self.previous_legacy_login
        main_module.RELEASE_POLICY_SERVICE = self.previous_release_policy_service
        main_module.RELEASE_POLICY_COMMAND_GATE = self.previous_release_policy_gate

    @staticmethod
    def _request() -> object:
        authority = DataRightsRequestAuthority()
        return authority.create_request(
            command_id="private-command-must-not-leak",
            subject_id="private-subject-must-not-leak",
            identity_proof={"kind": "reauthenticatedSession", "value": "private-proof"},
            payload={
                "action": "account.delete",
                "scope": ["archive", "voice"],
                "privateBody": "private-data-must-not-leak",
            },
            now="2026-07-21T10:00:00+00:00",
        ).request

    def _create_request(self) -> str:
        request = self._request()
        self.store.create_rights_request(request)
        return request.request_id

    def test_projection_separates_access_revocation_from_physical_cleanup(self) -> None:
        from app.services.data_rights_evidence_projection import (
            build_data_rights_evidence_projection,
        )

        request_id = self._create_request()
        self.store.record_rights_execution(
            request_id,
            module_id="archive",
            resource_type="archiveItem",
            execution_id_hash="execution-archive",
            outcome="completed",
            evidence_id_hash="evidence-archive",
            updated_at="2026-07-21T10:01:00+00:00",
        )
        self.store.append_resource_deletion_receipt(
            receipt_id="receipt-archive",
            request_id=request_id,
            execution_id_hash="execution-archive",
            module_id="archive",
            resource_scope_hash="scope-archive",
            outcome="completed",
            receipt_hash="receipt-hash-archive",
            evidence_event_id_hash="evidence-archive",
            created_at="2026-07-21T10:02:00+00:00",
        )
        self.store.record_rights_access_revocation_outbox(
            event_id="revocation-event",
            request_id=request_id,
            user_id="private-subject-must-not-leak",
            auth_epoch=3,
            provider_capability_state="revoked",
            session_revocation={"scope": "allDevices", "revocationReceiptId": "private"},
            delegated_grant_revocation={"revokedGrantCount": 2},
            created_at="2026-07-21T10:01:30+00:00",
        )

        report = build_data_rights_evidence_projection(
            self.store.summarize_rights_request(request_id),
            access_revocation_events=self.store.list_rights_access_revocation_outbox(request_id),
            now="2026-07-21T10:03:00+00:00",
        )
        serialized = json.dumps(report, ensure_ascii=False, sort_keys=True)

        self.assertEqual(report["request"]["requestId"], request_id)
        self.assertEqual(report["accessRevocation"]["status"], "revoked")
        self.assertEqual(report["physicalCleanup"]["status"], "completed")
        self.assertEqual(report["denominatorMode"], "observedEvidenceOnly")
        archive = next(
            item for item in report["resources"] if item["moduleId"] == "archive"
        )
        self.assertEqual(archive["layer"], "module")
        self.assertEqual(archive["status"], "completed")
        self.assertTrue(archive["receiptPresent"])
        self.assertEqual(archive["ageSeconds"], 60)
        self.assertNotIn("private-subject-must-not-leak", serialized)
        self.assertNotIn("private-command-must-not-leak", serialized)
        self.assertNotIn("private-proof", serialized)
        self.assertNotIn("private-data-must-not-leak", serialized)

    def test_terminal_execution_without_matching_receipt_is_unknown_not_completed(self) -> None:
        from app.services.data_rights_evidence_projection import (
            build_data_rights_evidence_projection,
        )

        request_id = self._create_request()
        self.store.record_rights_execution(
            request_id,
            module_id="providerVoice",
            resource_type="voiceCloneTrainingAsset",
            execution_id_hash="execution-provider",
            outcome="completed",
            updated_at="2026-07-21T10:01:00+00:00",
        )

        report = build_data_rights_evidence_projection(
            self.store.summarize_rights_request(request_id),
            now="2026-07-21T10:03:00+00:00",
        )
        provider = next(
            item
            for item in report["resources"]
            if item["moduleId"] == "providerVoice"
        )

        self.assertEqual(provider["layer"], "provider")
        self.assertEqual(provider["status"], "unknown")
        self.assertFalse(provider["receiptPresent"])
        self.assertIn("terminalExecutionMissingReceipt", provider["reasonCodes"])
        self.assertEqual(report["physicalCleanup"]["status"], "unknown")
        self.assertGreaterEqual(report["gapSummary"]["missingReceiptCount"], 1)

    def test_request_without_execution_or_receipt_stays_unknown_not_completed(self) -> None:
        from app.services.data_rights_evidence_projection import (
            build_data_rights_evidence_projection,
        )

        request_id = self._create_request()
        summary = self.store.summarize_rights_request(request_id)
        summary["request"]["status"] = "completed"

        report = build_data_rights_evidence_projection(
            summary,
            now="2026-07-21T10:03:00+00:00",
        )

        self.assertEqual(report["request"]["requestStatus"], "completed")
        self.assertEqual(report["physicalCleanup"]["status"], "unknown")
        self.assertEqual(report["accessRevocation"]["status"], "unknown")
        self.assertEqual(report["gapSummary"]["scopeCoverage"], "unverifiableFromRedactedScopeHash")

    def test_ops_endpoint_is_machine_only_owner_value_free_and_no_store(self) -> None:
        request_id = self._create_request()
        self.store.record_rights_execution(
            request_id,
            module_id="backupRetention",
            resource_type="backupCopy",
            execution_id_hash="execution-backup",
            outcome="pending",
            updated_at="2026-07-21T10:01:00+00:00",
        )

        anonymous = self.client.get(
            f"/ops/data-rights/requests/{request_id}/evidence"
        )
        response = self.client.get(
            f"/ops/data-rights/requests/{request_id}/evidence",
            headers={"Authorization": "Bearer rights-evidence-machine-token"},
        )
        missing = self.client.get(
            "/ops/data-rights/requests/missing/evidence",
            headers={"Authorization": "Bearer rights-evidence-machine-token"},
        )

        self.assertEqual(anonymous.status_code, 401)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        self.assertEqual(response.json()["physicalCleanup"]["status"], "pending")
        self.assertEqual(missing.status_code, 404)
        self.assertNotIn("private-subject-must-not-leak", response.text)


if __name__ == "__main__":
    unittest.main()
