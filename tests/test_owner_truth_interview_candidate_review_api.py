from __future__ import annotations

from hashlib import sha256
import json
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main_module
from app.domain.owner_truth.candidate_decisions import OwnerTruthCandidateSnapshot
from app.domain.owner_truth.candidate_extraction import (
    CandidateEvidenceSpan,
    CandidateProposal,
    CandidateReviewMode,
)
from app.domain.owner_truth.contracts import (
    CandidateDecision,
    EpistemicStatus,
    MemoryKind,
    PerspectiveType,
    SensitivityLevel,
    SourceRef,
)
from app.domain.owner_truth.ontology import OWNER_TRUTH_SCHEMA_VERSION
from app.main import app
from app.services.in_memory_store import InMemoryStore


client = TestClient(app)


def _content_hash(content: dict[str, object]) -> str:
    return sha256(
        json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


class OwnerTruthInterviewCandidateReviewAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = main_module.store
        self.previous_backend_token = main_module.BACKEND_API_TOKEN
        self.previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        self.previous_route_mode = main_module.AUTH_ROUTE_MODE
        self.previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
        self.previous_qa_enabled = main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED
        self.store = InMemoryStore()
        main_module.store = self.store
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
            json={"phone": phone, "nickname": "访谈候选审核测试", "password": "password123"},
        )
        if response.status_code != 200:
            raise AssertionError(response.text)
        payload = response.json()
        return payload["user"]["id"], {
            "Authorization": f"Bearer {payload['auth']['accessToken']}",
            "X-DreamJourney-QA-Owner-Truth": "1",
        }

    @staticmethod
    def _candidate(
        *,
        vault_id: str,
        owner_subject_id: str,
        source_id: str,
        extraction_id: str,
        summary: str,
        sensitivity: SensitivityLevel,
        review_mode: CandidateReviewMode,
    ) -> OwnerTruthCandidateSnapshot:
        proposal = CandidateProposal(
            memory_kind=MemoryKind.EXPERIENCE,
            perspective_type=PerspectiveType.FIRST_PERSON,
            epistemic_status=EpistemicStatus.RECALLED,
            sensitivity=sensitivity,
            content={"summary": summary},
            evidence_span=CandidateEvidenceSpan(start=0, end=1),
            confidence=0.74,
            review_mode=review_mode,
        )
        record = proposal.write_record(
            extraction_id=extraction_id,
            source_ref=SourceRef(vault_id=vault_id, source_id=source_id, source_version=1),
        )
        return OwnerTruthCandidateSnapshot(
            candidate_id=record.candidate_id,
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            source_id=source_id,
            memory_kind=record.candidate_kind,
            perspective_type=record.perspective_type,
            epistemic_status=record.epistemic_status,
            sensitivity=record.sensitivity,
            decision=CandidateDecision.PENDING,
            policy_version=OWNER_TRUTH_SCHEMA_VERSION,
            authority_epoch=0,
            row_version=1,
            content_hash=_content_hash({"summary": summary}),
            content_schema_version=record.payload_schema_version,
            payload=record.payload,
        )

    def _seed_review_batch(
        self,
        *,
        vault_id: str,
        owner_subject_id: str,
    ) -> tuple[str, OwnerTruthCandidateSnapshot, OwnerTruthCandidateSnapshot]:
        review_batch_id = str(uuid4())
        admission_id = str(uuid4())
        source_id = str(uuid4())
        extraction_id = str(uuid4())
        standard = self._candidate(
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            source_id=source_id,
            extraction_id=extraction_id,
            summary="小时候常在院子里听外公讲故事。",
            sensitivity=SensitivityLevel.STANDARD,
            review_mode=CandidateReviewMode.BATCH,
        )
        sensitive = self._candidate(
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            source_id=source_id,
            extraction_id=extraction_id,
            summary="需要由本人逐条决定的敏感经历。",
            sensitivity=SensitivityLevel.SENSITIVE,
            review_mode=CandidateReviewMode.SINGLE,
        )
        generic = self.store.owner_truth_candidate_review_repository()
        composition = self.store.owner_truth_interview_candidate_review_repository()
        for candidate in (standard, sensitive):
            generic.seed(candidate)
        composition.seed_vault(vault_id=vault_id, owner_subject_id=owner_subject_id)
        composition.seed_admission(
            admission_id=admission_id,
            review_batch_id=review_batch_id,
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            source_id=source_id,
        )
        composition.seed_extraction(
            extraction_id=extraction_id,
            vault_id=vault_id,
            source_id=source_id,
            source_version=1,
            status="succeeded",
        )
        for candidate in (standard, sensitive):
            composition.seed_candidate(
                candidate=candidate,
                extraction_id=extraction_id,
                source_version=1,
            )
        return review_batch_id, standard, sensitive

    @staticmethod
    def _read_path(vault_id: str, review_batch_id: str) -> str:
        return (
            f"/v2/vaults/{vault_id}/interview-review-batches/"
            f"{review_batch_id}/candidate-review"
        )

    def test_contract_is_default_hidden(self) -> None:
        owner_id, headers = self._login("13800139401")
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = False

        response = client.get(
            self._read_path("vault-hidden-interview", str(uuid4())),
            headers=headers,
        )

        self.assertTrue(owner_id.startswith("user_"))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json()["detail"]["code"],
            "ownerTruthCandidateReviewUnavailable",
        )

    def test_owner_can_partially_accept_standard_and_individually_reject_sensitive_without_memory_activation(self) -> None:
        owner_id, headers = self._login("13800139402")
        vault_id = "vault-interview-review-api"
        review_batch_id, standard, sensitive = self._seed_review_batch(
            vault_id=vault_id,
            owner_subject_id=owner_id,
        )
        read_path = self._read_path(vault_id, review_batch_id)

        initial = client.get(read_path, headers=headers)
        self.assertEqual(initial.status_code, 200)
        self.assertEqual(initial.headers["cache-control"], "no-store")
        self.assertEqual(
            initial.json()["schemaVersion"],
            "owner-truth-interview-candidate-review-read-v1",
        )
        self.assertEqual(initial.json()["review"]["readiness"], "reviewReady")
        self.assertEqual(
            initial.json()["batchCandidates"][0]["candidateId"], standard.candidate_id
        )
        self.assertEqual(initial.json()["batchCandidates"][0]["reviewPath"], "batch")
        self.assertEqual(
            initial.json()["singleCandidates"][0]["candidateId"], sensitive.candidate_id
        )
        self.assertEqual(initial.json()["singleCandidates"][0]["reviewPath"], "single")
        self.assertIn("summary", initial.json()["batchCandidates"][0]["content"])

        accepted = client.post(
            f"{read_path}/batch-accept",
            headers=headers,
            json={
                "commandId": "interview-api-batch-accept-001",
                "selections": [
                    {
                        "candidateId": standard.candidate_id,
                        "expectedCandidateVersion": 1,
                    }
                ],
                "reasonCode": "ownerReviewed",
            },
        )
        self.assertEqual(accepted.status_code, 201)
        accepted_body = accepted.json()
        self.assertEqual(
            accepted_body["schemaVersion"],
            "owner-truth-interview-candidate-batch-decision-response-v1",
        )
        self.assertEqual(accepted_body["acceptedCandidateCount"], 1)
        self.assertEqual(accepted_body["receipts"][0]["candidateId"], standard.candidate_id)
        self.assertEqual(accepted_body["memoryActivation"]["status"], "notApplicable")
        self.assertFalse(accepted_body["memoryActivation"]["memoryVersionCreated"])
        self.assertEqual(
            self.store.owner_truth_candidate_review_repository().snapshot()["memoryActivations"],
            {},
        )

        after_batch = client.get(read_path, headers=headers)
        self.assertEqual(after_batch.status_code, 200)
        self.assertEqual(after_batch.json()["batchCandidates"], [])
        self.assertEqual(
            after_batch.json()["singleCandidates"][0]["candidateId"], sensitive.candidate_id
        )

        rejected = client.post(
            f"{read_path}/candidates/{sensitive.candidate_id}/decision",
            headers=headers,
            json={
                "commandId": "interview-api-single-reject-001",
                "expectedCandidateVersion": 1,
                "action": "reject",
                "reasonCode": "ownerReviewed",
            },
        )
        self.assertEqual(rejected.status_code, 201)
        self.assertEqual(
            rejected.json()["schemaVersion"],
            "owner-truth-interview-candidate-single-review-response-v1",
        )
        self.assertEqual(rejected.json()["receipt"]["decision"], "rejected")
        self.assertFalse(rejected.json()["memoryActivation"]["memoryVersionCreated"])

        replay = client.post(
            f"{read_path}/candidates/{sensitive.candidate_id}/decision",
            headers=headers,
            json={
                "commandId": "interview-api-single-reject-001",
                "expectedCandidateVersion": 1,
                "action": "reject",
                "reasonCode": "ownerReviewed",
            },
        )
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()["status"], "deduplicated")

        final = client.get(read_path, headers=headers)
        self.assertEqual(final.status_code, 200)
        self.assertEqual(final.json()["review"]["readiness"], "noCandidates")
        self.assertEqual(final.json()["batchCandidates"], [])
        self.assertEqual(final.json()["singleCandidates"], [])
        self.assertEqual(
            self.store.owner_truth_candidate_review_repository().snapshot()["memoryActivations"],
            {},
        )

    def test_batch_route_rejects_sensitive_selection_and_other_owner(self) -> None:
        owner_id, headers = self._login("13800139403")
        vault_id = "vault-interview-review-boundary"
        review_batch_id, _, sensitive = self._seed_review_batch(
            vault_id=vault_id,
            owner_subject_id=owner_id,
        )
        read_path = self._read_path(vault_id, review_batch_id)

        invalid_batch = client.post(
            f"{read_path}/batch-accept",
            headers=headers,
            json={
                "commandId": "interview-api-invalid-batch-001",
                "selections": [
                    {
                        "candidateId": sensitive.candidate_id,
                        "expectedCandidateVersion": 1,
                    }
                ],
                "reasonCode": "ownerReviewed",
            },
        )
        self.assertEqual(invalid_batch.status_code, 409)
        self.assertEqual(
            invalid_batch.json()["detail"]["code"],
            "ownerTruthInterviewCandidateSingleReviewRequired",
        )

        _, other_headers = self._login("13800139404")
        denied = client.get(read_path, headers=other_headers)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(
            denied.json()["detail"]["code"],
            "ownerTruthInterviewCandidateReviewDenied",
        )


if __name__ == "__main__":
    unittest.main()
