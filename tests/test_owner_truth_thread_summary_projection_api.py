from __future__ import annotations

from hashlib import sha256
import json
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main_module
from app.domain.owner_truth.candidate_decisions import (
    CandidateReviewAction,
    OwnerTruthCandidateReviewCommand,
    OwnerTruthCandidateSnapshot,
)
from app.domain.owner_truth.contracts import (
    CandidateDecision,
    EpistemicStatus,
    MemoryKind,
    PerspectiveType,
    SensitivityLevel,
)
from app.domain.owner_truth.ontology import OWNER_TRUTH_SCHEMA_VERSION
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.main import app
from app.services.in_memory_store import InMemoryStore
from app.services.owner_truth_candidate_review import OwnerTruthCandidateReviewService
from app.services.owner_truth_memory_projection import OwnerTruthMemoryProjectionService


client = TestClient(app)


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class OwnerTruthThreadSummaryProjectionAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = main_module.store
        self.previous_backend_token = main_module.BACKEND_API_TOKEN
        self.previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        self.previous_route_mode = main_module.AUTH_ROUTE_MODE
        self.previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
        self.previous_candidate_qa = main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED
        self.previous_confirmation_qa = (
            main_module.OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_QA_ENABLED
        )
        self.previous_thread_summary_qa = main_module.OWNER_TRUTH_THREAD_SUMMARY_READ_QA_ENABLED
        self.previous_thread_summary_projection_qa = (
            main_module.OWNER_TRUTH_THREAD_SUMMARY_PROJECTION_QA_ENABLED
        )
        self.store = InMemoryStore()
        main_module.store = self.store
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = True
        main_module.OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_QA_ENABLED = True
        main_module.OWNER_TRUTH_THREAD_SUMMARY_READ_QA_ENABLED = True
        main_module.OWNER_TRUTH_THREAD_SUMMARY_PROJECTION_QA_ENABLED = True

    def tearDown(self) -> None:
        main_module.store = self.previous_store
        main_module.BACKEND_API_TOKEN = self.previous_backend_token
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = self.previous_legacy_phone_login
        main_module.AUTH_ROUTE_MODE = self.previous_route_mode
        main_module.AUTH_OWNERSHIP_MODE = self.previous_ownership_mode
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = self.previous_candidate_qa
        main_module.OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_QA_ENABLED = (
            self.previous_confirmation_qa
        )
        main_module.OWNER_TRUTH_THREAD_SUMMARY_READ_QA_ENABLED = self.previous_thread_summary_qa
        main_module.OWNER_TRUTH_THREAD_SUMMARY_PROJECTION_QA_ENABLED = (
            self.previous_thread_summary_projection_qa
        )

    @staticmethod
    def _login(phone: str) -> tuple[str, dict[str, str]]:
        response = client.post(
            "/auth/login",
            json={"phone": phone, "nickname": "Thread Summary Checkpoint QA", "password": "password123"},
        )
        if response.status_code != 200:
            raise AssertionError(response.text)
        body = response.json()
        return str(body["user"]["id"]), {
            "Authorization": f"Bearer {body['auth']['accessToken']}",
            "X-DreamJourney-QA-Owner-Truth": "1",
        }

    def _activate_memory(self, *, vault_id: str, owner_id: str) -> tuple[str, str]:
        source_id = str(uuid4())
        content = {"claim": "private checkpoint API content must not leak"}
        candidate = OwnerTruthCandidateSnapshot(
            candidate_id=str(uuid4()),
            vault_id=vault_id,
            owner_subject_id=owner_id,
            source_id=source_id,
            memory_kind=MemoryKind.KNOWLEDGE,
            perspective_type=PerspectiveType.FIRST_PERSON,
            epistemic_status=EpistemicStatus.RECALLED,
            sensitivity=SensitivityLevel.STANDARD,
            decision=CandidateDecision.PENDING,
            policy_version=OWNER_TRUTH_SCHEMA_VERSION,
            authority_epoch=0,
            row_version=1,
            content_hash=_hash(content),
            content_schema_version=OWNER_TRUTH_SCHEMA_VERSION,
            payload={
                "content": content,
                "contentSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION,
                "evidenceRefs": [{"sourceId": source_id, "sourceVersion": 1}],
                "reviewMode": "single",
                "schemaVersion": "owner-truth-candidate-proposal-v1",
            },
        )
        self.store.owner_truth_candidate_review_repository().seed(candidate)
        context = OwnerTruthCommandContext(
            vault_id=vault_id,
            owner_subject_id=owner_id,
            actor_subject_id=owner_id,
        )
        OwnerTruthCandidateReviewService(self.store).decide_and_activate(
            command=OwnerTruthCandidateReviewCommand(
                command_id="thread-summary-projection-api-memory-accept-001",
                candidate_id=candidate.candidate_id,
                expected_candidate_version=1,
                action=CandidateReviewAction.ACCEPT,
                corrected_value=None,
                corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION,
                reason_code="ownerReviewed",
            ),
            context=context,
        )
        OwnerTruthMemoryProjectionService(self.store).rebuild(context=context)
        entry = self.store.owner_truth_memory_projection_repository().read(context=context)["entries"][0]
        return str(entry["citation"]["memoryVersionId"]), str(entry["citation"]["contentHash"])

    def _confirm_memory(
        self,
        *,
        vault_id: str,
        memory_version_id: str,
        content_hash: str,
        headers: dict[str, str],
    ) -> None:
        response = client.post(
            f"/v2/vaults/{vault_id}/memory-versions/{memory_version_id}"
            "/knowledge-dimension-confirmations",
            headers=headers,
            json={
                "commandId": "thread-summary-projection-api-confirm-001",
                "expectedContentHash": content_hash,
                "dimension": "keyDecisions",
                "coveredFacets": ["choice"],
            },
        )
        self.assertEqual(response.status_code, 201, response.text)

    def test_contract_is_hidden_when_checkpoint_flag_is_disabled(self) -> None:
        owner_id, headers = self._login("13800139431")
        main_module.OWNER_TRUTH_THREAD_SUMMARY_PROJECTION_QA_ENABLED = False

        response = client.post(
            "/v2/vaults/vault-thread-summary-projection-hidden/"
            "thread-summary-projections/rebuild",
            headers=headers,
            json={},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json()["detail"]["code"],
            "ownerTruthThreadSummaryProjectionUnavailable",
        )

        vault_id = "vault-thread-summary-projection-existing-read"
        memory_version_id, content_hash = self._activate_memory(
            vault_id=vault_id,
            owner_id=owner_id,
        )
        self._confirm_memory(
            vault_id=vault_id,
            memory_version_id=memory_version_id,
            content_hash=content_hash,
            headers=headers,
        )
        existing_read = client.post(
            f"/v2/vaults/{vault_id}/thread-summaries/read",
            headers=headers,
            json={},
        )
        self.assertEqual(existing_read.status_code, 200, existing_read.text)

    def test_rebuild_read_idempotency_and_value_free_response(self) -> None:
        owner_id, headers = self._login("13800139432")
        vault_id = "vault-thread-summary-projection-api"
        memory_version_id, content_hash = self._activate_memory(
            vault_id=vault_id,
            owner_id=owner_id,
        )
        self._confirm_memory(
            vault_id=vault_id,
            memory_version_id=memory_version_id,
            content_hash=content_hash,
            headers=headers,
        )

        before = client.post(
            f"/v2/vaults/{vault_id}/thread-summary-projections/read",
            headers=headers,
            json={},
        )
        self.assertEqual(before.status_code, 200, before.text)
        self.assertEqual(before.json()["threadSummaryProjection"]["state"], "rebuilding")

        rebuilt = client.post(
            f"/v2/vaults/{vault_id}/thread-summary-projections/rebuild",
            headers=headers,
            json={},
        )
        self.assertEqual(rebuilt.status_code, 200, rebuilt.text)
        self.assertEqual(rebuilt.headers["cache-control"], "no-store")
        rebuild_summary = rebuilt.json()["threadSummaryProjection"]
        self.assertEqual(rebuild_summary["status"], "rebuilt")
        self.assertEqual(rebuild_summary["threadCount"], 0)
        self.assertNotIn("private checkpoint API content", rebuilt.text)
        self.assertNotIn('"claim"', rebuilt.text)

        unchanged = client.post(
            f"/v2/vaults/{vault_id}/thread-summary-projections/rebuild",
            headers=headers,
            json={},
        )
        self.assertEqual(unchanged.status_code, 200, unchanged.text)
        self.assertEqual(
            unchanged.json()["threadSummaryProjection"]["status"],
            "unchanged",
        )

        ready = client.post(
            f"/v2/vaults/{vault_id}/thread-summary-projections/read",
            headers=headers,
            json={},
        )
        self.assertEqual(ready.status_code, 200, ready.text)
        body = ready.json()
        self.assertEqual(
            body["schemaVersion"],
            "owner-truth-thread-summary-projection-read-response-v1",
        )
        self.assertEqual(body["threadSummaryProjection"]["state"], "ready")
        self.assertEqual(
            body["threadSummaryProjection"]["projection"]["threadCount"],
            0,
        )
        self.assertNotIn("private checkpoint API content", ready.text)

    def test_invalid_payload_and_cross_owner_access_fail_closed(self) -> None:
        owner_id, headers = self._login("13800139433")
        vault_id = "vault-thread-summary-projection-access"
        memory_version_id, content_hash = self._activate_memory(
            vault_id=vault_id,
            owner_id=owner_id,
        )
        self._confirm_memory(
            vault_id=vault_id,
            memory_version_id=memory_version_id,
            content_hash=content_hash,
            headers=headers,
        )
        rebuilt = client.post(
            f"/v2/vaults/{vault_id}/thread-summary-projections/rebuild",
            headers=headers,
            json={},
        )
        self.assertEqual(rebuilt.status_code, 200, rebuilt.text)

        invalid = client.post(
            f"/v2/vaults/{vault_id}/thread-summary-projections/read",
            headers=headers,
            json={"topic": "must-not-be-accepted"},
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(
            invalid.json()["detail"]["code"],
            "ownerTruthThreadSummaryProjectionInvalid",
        )

        _other_id, other_headers = self._login("13800139434")
        denied = client.post(
            f"/v2/vaults/{vault_id}/thread-summary-projections/read",
            headers=other_headers,
            json={},
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(
            denied.json()["detail"]["code"],
            "ownerTruthThreadSummaryProjectionDenied",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
