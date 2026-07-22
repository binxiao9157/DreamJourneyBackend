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
    def _login_release_policy(phone: str) -> tuple[str, dict[str, str], str]:
        response = client.post(
            "/auth/login",
            json={"phone": phone, "nickname": "访谈确认测试", "password": "password123"},
        )
        if response.status_code != 200:
            raise AssertionError(response.text)
        payload = response.json()
        return (
            payload["user"]["id"],
            {"Authorization": f"Bearer {payload['auth']['accessToken']}"},
            payload["auth"]["sessionId"],
        )

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

    @staticmethod
    def _confirmation_path(vault_id: str, review_batch_id: str) -> str:
        return (
            f"/v2/vaults/{vault_id}/interview-review-batches/"
            f"{review_batch_id}/confirmation"
        )

    @classmethod
    def _confirmation_batch_accept_path(cls, vault_id: str, review_batch_id: str) -> str:
        return f"{cls._confirmation_path(vault_id, review_batch_id)}/batch-accept"

    @staticmethod
    def _confirmation_policy_headers(
        headers: dict[str, str],
        *,
        session_id: str,
        decision_id: str,
    ) -> dict[str, str]:
        return {
            **headers,
            "X-DreamJourney-Feature": "ownerTruthCandidateReview",
            "X-DreamJourney-Feature-Decision-Id": decision_id,
            "X-DreamJourney-Feature-Allowed": "true",
            "X-DreamJourney-Policy-Version": "release-policy-v1",
            "X-DreamJourney-Policy-Revision": "1",
            "X-DreamJourney-Account-Generation": sha256(
                session_id.encode("utf-8")
            ).hexdigest()[:24],
        }

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

    def test_product_confirmation_requires_its_own_policy_and_keeps_qa_separate(self) -> None:
        owner_id, owner_headers, owner_session_id = self._login_release_policy("13800139411")
        vault_id = "vault-interview-confirmation-policy"
        review_batch_id, standard, sensitive = self._seed_review_batch(
            vault_id=vault_id,
            owner_subject_id=owner_id,
        )
        path = self._confirmation_path(vault_id, review_batch_id)

        qa_header_only = client.get(
            path,
            headers={**owner_headers, "X-DreamJourney-QA-Owner-Truth": "1"},
        )
        self.assertEqual(qa_header_only.status_code, 403)
        self.assertEqual(
            qa_header_only.json()["detail"]["code"],
            "release_policy_denied",
        )
        self.assertEqual(
            qa_header_only.json()["detail"]["feature"],
            "ownerTruthCandidateReview",
        )

        policy_service = main_module.RELEASE_POLICY_SERVICE
        previous_visible = set(policy_service._CLOSED_PILOT_OWNER_VISIBLE)
        policy_service._CLOSED_PILOT_OWNER_VISIBLE = previous_visible | {
            "ownerTruthCandidateReview"
        }
        try:
            response = client.get(
                path,
                headers=self._confirmation_policy_headers(
                    owner_headers,
                    session_id=owner_session_id,
                    decision_id="candidate-confirmation-owner",
                ),
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
            payload = response.json()
            self.assertEqual(
                payload["schemaVersion"],
                "owner-truth-interview-candidate-confirmation-read-v1",
            )
            self.assertEqual(payload["confirmation"]["readiness"], "reviewReady")
            self.assertEqual(payload["batchCandidates"][0]["candidateId"], standard.candidate_id)
            self.assertEqual(payload["singleCandidates"][0]["candidateId"], sensitive.candidate_id)
            self.assertIn("summary", payload["batchCandidates"][0]["content"])
            self.assertNotIn("review", payload)

            _, other_headers, other_session_id = self._login_release_policy("13800139412")
            denied = client.get(
                path,
                headers=self._confirmation_policy_headers(
                    other_headers,
                    session_id=other_session_id,
                    decision_id="candidate-confirmation-other-owner",
                ),
            )
            self.assertEqual(denied.status_code, 403)
            self.assertEqual(
                denied.json()["detail"]["code"],
                "ownerTruthInterviewCandidateReviewDenied",
            )
        finally:
            policy_service._CLOSED_PILOT_OWNER_VISIBLE = previous_visible

    def test_product_confirmation_batch_accept_requires_policy_and_keeps_qa_separate(self) -> None:
        owner_id, owner_headers, owner_session_id = self._login_release_policy("13800139413")
        vault_id = "vault-interview-confirmation-batch-accept"
        review_batch_id, standard, sensitive = self._seed_review_batch(
            vault_id=vault_id,
            owner_subject_id=owner_id,
        )
        path = self._confirmation_batch_accept_path(vault_id, review_batch_id)
        payload = {
            "commandId": "candidate-confirmation-batch-accept-owner",
            "selections": [
                {
                    "candidateId": standard.candidate_id,
                    "expectedCandidateVersion": 1,
                }
            ],
        }

        qa_header_only = client.post(
            path,
            headers={**owner_headers, "X-DreamJourney-QA-Owner-Truth": "1"},
            json=payload,
        )
        self.assertEqual(qa_header_only.status_code, 403)
        self.assertEqual(
            qa_header_only.json()["detail"]["code"],
            "release_policy_denied",
        )
        self.assertEqual(
            qa_header_only.json()["detail"]["feature"],
            "ownerTruthCandidateReview",
        )

        policy_service = main_module.RELEASE_POLICY_SERVICE
        previous_visible = set(policy_service._CLOSED_PILOT_OWNER_VISIBLE)
        policy_service._CLOSED_PILOT_OWNER_VISIBLE = previous_visible | {
            "ownerTruthCandidateReview"
        }
        try:
            accepted = client.post(
                path,
                headers=self._confirmation_policy_headers(
                    owner_headers,
                    session_id=owner_session_id,
                    decision_id="candidate-confirmation-batch-accept-owner",
                ),
                json=payload,
            )
            self.assertEqual(accepted.status_code, 201)
            self.assertEqual(accepted.headers["cache-control"], "no-store")
            accepted_body = accepted.json()
            self.assertEqual(
                accepted_body["schemaVersion"],
                "owner-truth-interview-candidate-confirmation-batch-decision-response-v1",
            )
            self.assertEqual(accepted_body["acceptedCandidateCount"], 1)
            self.assertEqual(accepted_body["acceptedCandidateIds"], [standard.candidate_id])
            self.assertNotIn("receipts", accepted_body)
            self.assertNotIn("content", accepted_body)
            self.assertNotIn("review", accepted_body)
            self.assertFalse(accepted_body["memoryActivation"]["memoryVersionCreated"])
            self.assertEqual(
                self.store.owner_truth_candidate_review_repository().snapshot()["memoryActivations"],
                {},
            )

            replay = client.post(
                path,
                headers=self._confirmation_policy_headers(
                    owner_headers,
                    session_id=owner_session_id,
                    decision_id="candidate-confirmation-batch-accept-owner-replay",
                ),
                json=payload,
            )
            self.assertEqual(replay.status_code, 200)
            self.assertEqual(replay.json()["status"], "deduplicated")

            confirmation = client.get(
                self._confirmation_path(vault_id, review_batch_id),
                headers=self._confirmation_policy_headers(
                    owner_headers,
                    session_id=owner_session_id,
                    decision_id="candidate-confirmation-after-batch-accept",
                ),
            )
            self.assertEqual(confirmation.status_code, 200)
            self.assertEqual(confirmation.json()["batchCandidates"], [])
            self.assertEqual(
                confirmation.json()["singleCandidates"][0]["candidateId"],
                sensitive.candidate_id,
            )

            _, other_headers, other_session_id = self._login_release_policy("13800139414")
            denied = client.post(
                path,
                headers=self._confirmation_policy_headers(
                    other_headers,
                    session_id=other_session_id,
                    decision_id="candidate-confirmation-batch-accept-other-owner",
                ),
                json={
                    **payload,
                    "commandId": "candidate-confirmation-batch-accept-other-owner",
                },
            )
            self.assertEqual(denied.status_code, 403)
            self.assertEqual(
                denied.json()["detail"]["code"],
                "ownerTruthInterviewCandidateReviewDenied",
            )

            sensitive_batch = client.post(
                self._confirmation_batch_accept_path(vault_id, review_batch_id),
                headers=self._confirmation_policy_headers(
                    owner_headers,
                    session_id=owner_session_id,
                    decision_id="candidate-confirmation-batch-accept-sensitive",
                ),
                json={
                    "commandId": "candidate-confirmation-batch-accept-sensitive",
                    "selections": [
                        {
                            "candidateId": sensitive.candidate_id,
                            "expectedCandidateVersion": 1,
                        }
                    ],
                },
            )
            self.assertEqual(sensitive_batch.status_code, 409)
            self.assertEqual(
                sensitive_batch.json()["detail"]["code"],
                "ownerTruthInterviewCandidateSingleReviewRequired",
            )
        finally:
            policy_service._CLOSED_PILOT_OWNER_VISIBLE = previous_visible

    def test_product_confirmation_records_value_minimized_authority_capture(self) -> None:
        owner_id, owner_headers, owner_session_id = self._login_release_policy("13800139415")
        vault_id = "vault-interview-confirmation-authority-capture"
        review_batch_id, standard, _ = self._seed_review_batch(
            vault_id=vault_id,
            owner_subject_id=owner_id,
        )
        decision_id = "candidate-confirmation-authority-capture-owner"
        policy_service = main_module.RELEASE_POLICY_SERVICE
        previous_visible = set(policy_service._CLOSED_PILOT_OWNER_VISIBLE)
        policy_service._CLOSED_PILOT_OWNER_VISIBLE = previous_visible | {
            "ownerTruthCandidateReview"
        }
        try:
            response = client.post(
                self._confirmation_batch_accept_path(vault_id, review_batch_id),
                headers=self._confirmation_policy_headers(
                    owner_headers,
                    session_id=owner_session_id,
                    decision_id=decision_id,
                ),
                json={
                    "commandId": "candidate-confirmation-authority-capture-command",
                    "selections": [
                        {
                            "candidateId": standard.candidate_id,
                            "expectedCandidateVersion": 1,
                        }
                    ],
                },
            )
            self.assertEqual(response.status_code, 201)

            records = self.store.owner_truth_interview_candidate_batch_decision_repository().snapshot()
            self.assertEqual(len(records), 1)
            record = next(iter(records.values()))
            capture = record.authorization_capture
            self.assertIsNotNone(capture)
            self.assertEqual(capture.policy_version, "release-policy-v1")
            self.assertEqual(capture.policy_revision, 1)
            self.assertEqual(capture.feature, "ownerTruthCandidateReview")
            self.assertEqual(
                capture.account_generation_hash,
                sha256(owner_session_id.encode("utf-8")).hexdigest()[:24],
            )
            self.assertEqual(
                capture.decision_id_hash,
                sha256(decision_id.encode("utf-8")).hexdigest(),
            )
            receipts = self.store.owner_truth_candidate_review_repository().snapshot()["receipts"]
            self.assertEqual(len(receipts), 1)
            self.assertEqual(
                next(iter(receipts.values()))["policyVersion"],
                OWNER_TRUTH_SCHEMA_VERSION,
            )
            serialized = str(capture.value_minimized_payload())
            self.assertNotIn(owner_session_id, serialized)
            self.assertNotIn(decision_id, serialized)
        finally:
            policy_service._CLOSED_PILOT_OWNER_VISIBLE = previous_visible

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
