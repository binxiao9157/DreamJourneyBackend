from __future__ import annotations

from hashlib import sha256
import json
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main_module
from app.async_effects.contracts import AsyncEffectJobState
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
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
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
        self.previous_closed_pilot_owner_ids = (
            main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS
        )
        self.previous_qa_enabled = main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED
        self.store = InMemoryStore()
        main_module.store = self.store
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = frozenset()
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = True

    def tearDown(self) -> None:
        main_module.store = self.previous_store
        main_module.BACKEND_API_TOKEN = self.previous_backend_token
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = self.previous_legacy_phone_login
        main_module.AUTH_ROUTE_MODE = self.previous_route_mode
        main_module.AUTH_OWNERSHIP_MODE = self.previous_ownership_mode
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = (
            self.previous_closed_pilot_owner_ids
        )
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
        owner_id = str(payload["user"]["id"])
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = frozenset(
            set(main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS) | {owner_id}
        )
        return owner_id, {
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
        owner_id = str(payload["user"]["id"])
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = frozenset(
            set(main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS) | {owner_id}
        )
        return (
            owner_id,
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

    @staticmethod
    def _confirmation_inbox_path(vault_id: str) -> str:
        return f"/v2/vaults/{vault_id}/interview-candidate-confirmations"

    @staticmethod
    def _memory_activation_inbox_path(vault_id: str) -> str:
        return f"/v2/vaults/{vault_id}/interview-memory-activation-inbox"

    @staticmethod
    def _memory_projection_recovery_inbox_path(vault_id: str) -> str:
        return (
            f"/v2/vaults/{vault_id}/"
            "interview-memory-projection-recovery-inbox"
        )

    @classmethod
    def _confirmation_batch_accept_path(cls, vault_id: str, review_batch_id: str) -> str:
        return f"{cls._confirmation_path(vault_id, review_batch_id)}/batch-accept"

    @classmethod
    def _confirmation_memory_activation_path(
        cls,
        vault_id: str,
        review_batch_id: str,
        candidate_id: str,
    ) -> str:
        return (
            f"{cls._confirmation_path(vault_id, review_batch_id)}/candidates/"
            f"{candidate_id}/memory-activation"
        )

    @classmethod
    def _confirmation_single_decision_path(
        cls,
        vault_id: str,
        review_batch_id: str,
        candidate_id: str,
    ) -> str:
        return (
            f"{cls._confirmation_path(vault_id, review_batch_id)}/candidates/"
            f"{candidate_id}/decision"
        )

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

    def test_product_confirmation_inbox_discovers_only_live_owner_batches(self) -> None:
        owner_id, owner_headers, owner_session_id = self._login_release_policy("13800139410")
        vault_id = "vault-interview-confirmation-inbox"
        first_batch_id, _, _ = self._seed_review_batch(
            vault_id=vault_id,
            owner_subject_id=owner_id,
        )
        second_batch_id, _, _ = self._seed_review_batch(
            vault_id=vault_id,
            owner_subject_id=owner_id,
        )
        path = self._confirmation_inbox_path(vault_id)

        qa_header_only = client.get(
            path,
            headers={**owner_headers, "X-DreamJourney-QA-Owner-Truth": "1"},
        )
        self.assertEqual(qa_header_only.status_code, 403)
        self.assertEqual(
            qa_header_only.json()["detail"]["code"],
            "release_policy_denied",
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
                    decision_id="candidate-confirmation-inbox-owner",
                ),
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
            payload = response.json()
            self.assertEqual(
                payload["schemaVersion"],
                "owner-truth-interview-candidate-confirmation-inbox-v1",
            )
            self.assertEqual(payload["vaultId"], vault_id)
            self.assertEqual(
                {item["reviewBatchId"] for item in payload["confirmations"]},
                {first_batch_id, second_batch_id},
            )
            self.assertTrue(
                all(item["readiness"] == "reviewReady" for item in payload["confirmations"])
            )
            self.assertTrue(
                all(item["batchCandidateCount"] == 1 for item in payload["confirmations"])
            )
            self.assertTrue(
                all(item["singleCandidateCount"] == 1 for item in payload["confirmations"])
            )
            rendered = json.dumps(payload, ensure_ascii=False)
            for forbidden in ("candidateId", "sourceId", "content", "receipt", "admissionId"):
                self.assertNotIn(forbidden, rendered)

            _, other_headers, other_session_id = self._login_release_policy("13800139409")
            denied = client.get(
                path,
                headers=self._confirmation_policy_headers(
                    other_headers,
                    session_id=other_session_id,
                    decision_id="candidate-confirmation-inbox-other-owner",
                ),
            )
            self.assertEqual(denied.status_code, 403)
            self.assertEqual(
                denied.json()["detail"]["code"],
                "ownerTruthInterviewCandidateReviewDenied",
            )
        finally:
            policy_service._CLOSED_PILOT_OWNER_VISIBLE = previous_visible

    def test_formal_memory_activation_inbox_is_value_minimized_and_filters_terminal_entries(self) -> None:
        owner_id, owner_headers, owner_session_id = self._login_release_policy("13800139429")
        vault_id = "vault-interview-memory-activation-inbox"
        review_batch_id, standard, sensitive = self._seed_review_batch(
            vault_id=vault_id,
            owner_subject_id=owner_id,
        )
        qa_batch_id, qa_standard, _ = self._seed_review_batch(
            vault_id=vault_id,
            owner_subject_id=owner_id,
        )
        inbox_path = self._memory_activation_inbox_path(vault_id)
        policy_service = main_module.RELEASE_POLICY_SERVICE
        previous_visible = set(policy_service._CLOSED_PILOT_OWNER_VISIBLE)
        policy_service._CLOSED_PILOT_OWNER_VISIBLE = previous_visible | {
            "ownerTruthCandidateReview"
        }
        try:
            qa_header_only = client.get(
                inbox_path,
                headers={**owner_headers, "X-DreamJourney-QA-Owner-Truth": "1"},
            )
            self.assertEqual(qa_header_only.status_code, 403)
            self.assertEqual(
                qa_header_only.json()["detail"]["code"],
                "release_policy_denied",
            )

            accepted = client.post(
                self._confirmation_batch_accept_path(vault_id, review_batch_id),
                headers=self._confirmation_policy_headers(
                    owner_headers,
                    session_id=owner_session_id,
                    decision_id="memory-activation-inbox-formal-accept",
                ),
                json={
                    "commandId": "memory-activation-inbox-formal-accept-command",
                    "selections": [
                        {
                            "candidateId": standard.candidate_id,
                            "expectedCandidateVersion": 1,
                        }
                    ],
                },
            )
            self.assertEqual(accepted.status_code, 201)

            rejected = client.post(
                self._confirmation_single_decision_path(
                    vault_id,
                    review_batch_id,
                    sensitive.candidate_id,
                ),
                headers=self._confirmation_policy_headers(
                    owner_headers,
                    session_id=owner_session_id,
                    decision_id="memory-activation-inbox-formal-reject",
                ),
                json={
                    "commandId": "memory-activation-inbox-formal-reject-command",
                    "expectedCandidateVersion": 1,
                    "action": "reject",
                },
            )
            self.assertEqual(rejected.status_code, 201)

            qa_only = client.post(
                f"{self._read_path(vault_id, qa_batch_id)}/batch-accept",
                headers={**owner_headers, "X-DreamJourney-QA-Owner-Truth": "1"},
                json={
                    "commandId": "memory-activation-inbox-qa-only-command",
                    "selections": [
                        {
                            "candidateId": qa_standard.candidate_id,
                            "expectedCandidateVersion": 1,
                        }
                    ],
                    "reasonCode": "ownerReviewed",
                },
            )
            self.assertEqual(qa_only.status_code, 201)

            response = client.get(
                inbox_path,
                headers=self._confirmation_policy_headers(
                    owner_headers,
                    session_id=owner_session_id,
                    decision_id="memory-activation-inbox-formal-read",
                ),
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
            payload = response.json()
            self.assertEqual(
                payload["schemaVersion"],
                "owner-truth-interview-candidate-memory-activation-inbox-v1",
            )
            self.assertEqual(payload["vaultId"], vault_id)
            self.assertEqual(
                payload["items"],
                [
                    {
                        "reviewBatchId": review_batch_id,
                        "candidateId": standard.candidate_id,
                    }
                ],
            )
            rendered = json.dumps(payload, ensure_ascii=False)
            for forbidden in (
                "content",
                "receipt",
                "memoryId",
                "memoryVersionId",
                "provider",
                "sourceId",
            ):
                self.assertNotIn(forbidden, rendered)

            activated = client.post(
                self._confirmation_memory_activation_path(
                    vault_id,
                    review_batch_id,
                    standard.candidate_id,
                ),
                headers=self._confirmation_policy_headers(
                    owner_headers,
                    session_id=owner_session_id,
                    decision_id="memory-activation-inbox-formal-activate",
                ),
                json={"commandId": "memory-activation-inbox-formal-activate-command"},
            )
            self.assertEqual(activated.status_code, 201)

            after_activation = client.get(
                inbox_path,
                headers=self._confirmation_policy_headers(
                    owner_headers,
                    session_id=owner_session_id,
                    decision_id="memory-activation-inbox-after-activation",
                ),
            )
            self.assertEqual(after_activation.status_code, 200)
            self.assertEqual(after_activation.json()["items"], [])
        finally:
            policy_service._CLOSED_PILOT_OWNER_VISIBLE = previous_visible

    def test_formal_memory_projection_recovery_inbox_is_status_only_until_rebuilt(self) -> None:
        owner_id, owner_headers, owner_session_id = self._login_release_policy("13800139432")
        vault_id = "vault-interview-memory-projection-recovery-inbox"
        review_batch_id, standard, _ = self._seed_review_batch(
            vault_id=vault_id,
            owner_subject_id=owner_id,
        )
        inbox_path = self._memory_projection_recovery_inbox_path(vault_id)
        policy_service = main_module.RELEASE_POLICY_SERVICE
        previous_visible = set(policy_service._CLOSED_PILOT_OWNER_VISIBLE)
        policy_service._CLOSED_PILOT_OWNER_VISIBLE = previous_visible | {
            "ownerTruthCandidateReview"
        }
        try:
            qa_header_only = client.get(
                inbox_path,
                headers={**owner_headers, "X-DreamJourney-QA-Owner-Truth": "1"},
            )
            self.assertEqual(qa_header_only.status_code, 403)
            self.assertEqual(
                qa_header_only.json()["detail"]["code"],
                "release_policy_denied",
            )

            accepted = client.post(
                self._confirmation_batch_accept_path(vault_id, review_batch_id),
                headers=self._confirmation_policy_headers(
                    owner_headers,
                    session_id=owner_session_id,
                    decision_id="memory-projection-recovery-inbox-formal-accept",
                ),
                json={
                    "commandId": "memory-projection-recovery-inbox-formal-accept-command",
                    "selections": [
                        {
                            "candidateId": standard.candidate_id,
                            "expectedCandidateVersion": 1,
                        }
                    ],
                },
            )
            self.assertEqual(accepted.status_code, 201)

            activated = client.post(
                self._confirmation_memory_activation_path(
                    vault_id,
                    review_batch_id,
                    standard.candidate_id,
                ),
                headers=self._confirmation_policy_headers(
                    owner_headers,
                    session_id=owner_session_id,
                    decision_id="memory-projection-recovery-inbox-formal-activate",
                ),
                json={
                    "commandId": "memory-projection-recovery-inbox-formal-activate-command"
                },
            )
            self.assertEqual(activated.status_code, 201)

            rebuilding = client.get(
                inbox_path,
                headers=self._confirmation_policy_headers(
                    owner_headers,
                    session_id=owner_session_id,
                    decision_id="memory-projection-recovery-inbox-rebuilding-read",
                ),
            )
            self.assertEqual(rebuilding.status_code, 200)
            self.assertEqual(rebuilding.headers["cache-control"], "no-store")
            payload = rebuilding.json()
            self.assertEqual(
                payload["schemaVersion"],
                "owner-truth-interview-memory-projection-recovery-inbox-v1",
            )
            self.assertEqual(payload["vaultId"], vault_id)
            self.assertEqual(
                payload["items"],
                [
                    {
                        "reviewBatchId": review_batch_id,
                        "candidateId": standard.candidate_id,
                        "state": "rebuilding",
                    }
                ],
            )
            rendered = json.dumps(payload, ensure_ascii=False)
            for forbidden in (
                "content",
                "receipt",
                "memoryId",
                "memoryVersionId",
                "sourceId",
                "job",
                "checkpoint",
                "rebuildReason",
                "provider",
            ):
                self.assertNotIn(forbidden, rendered)

            effect_records = self.store.effect_kernel_repository().snapshot()
            projection_job_id = next(
                record["intent"].job_id
                for record in effect_records.values()
                if record["intent"].target.vault_id == vault_id
            )
            self.store.effect_kernel_repository().set_job_state_for_test(
                job_id=projection_job_id,
                state=AsyncEffectJobState.CANCELLED,
            )
            terminal = client.get(
                inbox_path,
                headers=self._confirmation_policy_headers(
                    owner_headers,
                    session_id=owner_session_id,
                    decision_id="memory-projection-recovery-inbox-terminal-read",
                ),
            )
            self.assertEqual(terminal.status_code, 200)
            self.assertEqual(terminal.json()["items"], [])

            self.store.owner_truth_memory_projection_repository().rebuild(
                context=OwnerTruthCommandContext(
                    vault_id=vault_id,
                    owner_subject_id=owner_id,
                    actor_subject_id=owner_id,
                )
            )
            ready = client.get(
                inbox_path,
                headers=self._confirmation_policy_headers(
                    owner_headers,
                    session_id=owner_session_id,
                    decision_id="memory-projection-recovery-inbox-ready-read",
                ),
            )
            self.assertEqual(ready.status_code, 200)
            self.assertEqual(ready.json()["items"], [])
        finally:
            policy_service._CLOSED_PILOT_OWNER_VISIBLE = previous_visible

    def test_formal_memory_activation_inbox_rejects_cross_owner_and_vault(self) -> None:
        owner_id, owner_headers, owner_session_id = self._login_release_policy("13800139430")
        vault_id = "vault-interview-memory-activation-inbox-boundary"
        self._seed_review_batch(vault_id=vault_id, owner_subject_id=owner_id)
        policy_service = main_module.RELEASE_POLICY_SERVICE
        previous_visible = set(policy_service._CLOSED_PILOT_OWNER_VISIBLE)
        policy_service._CLOSED_PILOT_OWNER_VISIBLE = previous_visible | {
            "ownerTruthCandidateReview"
        }
        try:
            _, other_headers, other_session_id = self._login_release_policy("13800139431")
            cross_owner = client.get(
                self._memory_activation_inbox_path(vault_id),
                headers=self._confirmation_policy_headers(
                    other_headers,
                    session_id=other_session_id,
                    decision_id="memory-activation-inbox-cross-owner",
                ),
            )
            self.assertEqual(cross_owner.status_code, 403)
            self.assertEqual(
                cross_owner.json()["detail"]["code"],
                "ownerTruthInterviewCandidateReviewDenied",
            )

            cross_vault = client.get(
                self._memory_activation_inbox_path("vault-not-owned-by-session"),
                headers=self._confirmation_policy_headers(
                    owner_headers,
                    session_id=owner_session_id,
                    decision_id="memory-activation-inbox-cross-vault",
                ),
            )
            self.assertEqual(cross_vault.status_code, 403)
            self.assertEqual(
                cross_vault.json()["detail"]["code"],
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

    def test_formal_single_confirmation_requires_policy_and_explicit_activation(self) -> None:
        owner_id, owner_headers, owner_session_id = self._login_release_policy("13800139421")
        vault_id = "vault-interview-confirmation-single-activation"
        review_batch_id, _, sensitive = self._seed_review_batch(
            vault_id=vault_id,
            owner_subject_id=owner_id,
        )
        path = self._confirmation_single_decision_path(
            vault_id,
            review_batch_id,
            sensitive.candidate_id,
        )
        payload = {
            "commandId": "candidate-confirmation-single-accept-command",
            "expectedCandidateVersion": 1,
            "action": "accept",
        }

        qa_header_only = client.post(
            path,
            headers={**owner_headers, "X-DreamJourney-QA-Owner-Truth": "1"},
            json=payload,
        )
        self.assertEqual(qa_header_only.status_code, 403)
        self.assertEqual(
            qa_header_only.json()["detail"]["code"], "release_policy_denied"
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
                    decision_id="candidate-confirmation-single-accept",
                ),
                json=payload,
            )
            self.assertEqual(accepted.status_code, 201)
            self.assertEqual(accepted.headers["cache-control"], "no-store")
            accepted_body = accepted.json()
            self.assertEqual(
                accepted_body["schemaVersion"],
                "owner-truth-interview-candidate-confirmation-single-decision-response-v1",
            )
            self.assertEqual(accepted_body["candidateId"], sensitive.candidate_id)
            self.assertEqual(accepted_body["decision"], "accepted")
            self.assertNotIn("receipt", accepted_body)
            self.assertFalse(accepted_body["memoryActivation"]["memoryVersionCreated"])
            self.assertEqual(
                self.store.owner_truth_candidate_review_repository().snapshot()[
                    "memoryActivations"
                ],
                {},
            )

            replay = client.post(
                path,
                headers=self._confirmation_policy_headers(
                    owner_headers,
                    session_id=owner_session_id,
                    decision_id="candidate-confirmation-single-accept-replay",
                ),
                json=payload,
            )
            self.assertEqual(replay.status_code, 200)
            self.assertEqual(replay.json()["status"], "deduplicated")

            activation = client.post(
                self._confirmation_memory_activation_path(
                    vault_id,
                    review_batch_id,
                    sensitive.candidate_id,
                ),
                headers=self._confirmation_policy_headers(
                    owner_headers,
                    session_id=owner_session_id,
                    decision_id="candidate-confirmation-single-memory-activation",
                ),
                json={"commandId": "candidate-confirmation-single-memory-command"},
            )
            self.assertEqual(activation.status_code, 201)
            self.assertTrue(
                activation.json()["memoryActivation"]["memoryVersionCreated"]
            )
        finally:
            policy_service._CLOSED_PILOT_OWNER_VISIBLE = previous_visible

    def test_formal_memory_activation_rejects_qa_only_single_receipt(self) -> None:
        owner_id, qa_headers = self._login("13800139422")
        vault_id = "vault-interview-confirmation-qa-single-receipt"
        review_batch_id, _, sensitive = self._seed_review_batch(
            vault_id=vault_id,
            owner_subject_id=owner_id,
        )
        qa_decision = client.post(
            f"{self._read_path(vault_id, review_batch_id)}/candidates/"
            f"{sensitive.candidate_id}/decision",
            headers=qa_headers,
            json={
                "commandId": "candidate-qa-only-single-accept-command",
                "expectedCandidateVersion": 1,
                "action": "accept",
                "reasonCode": "ownerReviewed",
            },
        )
        self.assertEqual(qa_decision.status_code, 201)

        _, owner_headers, owner_session_id = self._login_release_policy("13800139422")
        policy_service = main_module.RELEASE_POLICY_SERVICE
        previous_visible = set(policy_service._CLOSED_PILOT_OWNER_VISIBLE)
        policy_service._CLOSED_PILOT_OWNER_VISIBLE = previous_visible | {
            "ownerTruthCandidateReview"
        }
        try:
            denied = client.post(
                self._confirmation_memory_activation_path(
                    vault_id,
                    review_batch_id,
                    sensitive.candidate_id,
                ),
                headers=self._confirmation_policy_headers(
                    owner_headers,
                    session_id=owner_session_id,
                    decision_id="candidate-qa-only-single-activation-attempt",
                ),
                json={"commandId": "candidate-qa-only-single-activation-command"},
            )
            self.assertEqual(denied.status_code, 403)
            self.assertEqual(
                denied.json()["detail"]["code"],
                "ownerTruthInterviewCandidateReviewDenied",
            )
            self.assertEqual(
                self.store.owner_truth_candidate_review_repository().snapshot()[
                    "memoryActivations"
                ],
                {},
            )
        finally:
            policy_service._CLOSED_PILOT_OWNER_VISIBLE = previous_visible

    def test_formal_confirmation_requires_explicit_memory_activation(self) -> None:
        owner_id, owner_headers, owner_session_id = self._login_release_policy("13800139416")
        vault_id = "vault-interview-confirmation-memory-activation"
        review_batch_id, standard, _ = self._seed_review_batch(
            vault_id=vault_id,
            owner_subject_id=owner_id,
        )
        policy_service = main_module.RELEASE_POLICY_SERVICE
        previous_visible = set(policy_service._CLOSED_PILOT_OWNER_VISIBLE)
        policy_service._CLOSED_PILOT_OWNER_VISIBLE = previous_visible | {
            "ownerTruthCandidateReview"
        }
        try:
            accepted = client.post(
                self._confirmation_batch_accept_path(vault_id, review_batch_id),
                headers=self._confirmation_policy_headers(
                    owner_headers,
                    session_id=owner_session_id,
                    decision_id="candidate-confirmation-activation-accept",
                ),
                json={
                    "commandId": "candidate-confirmation-activation-accept-command",
                    "selections": [
                        {
                            "candidateId": standard.candidate_id,
                            "expectedCandidateVersion": 1,
                        }
                    ],
                },
            )
            self.assertEqual(accepted.status_code, 201)
            self.assertEqual(
                self.store.owner_truth_candidate_review_repository().snapshot()[
                    "memoryActivations"
                ],
                {},
            )

            activation_path = self._confirmation_memory_activation_path(
                vault_id,
                review_batch_id,
                standard.candidate_id,
            )
            activation = client.post(
                activation_path,
                headers=self._confirmation_policy_headers(
                    owner_headers,
                    session_id=owner_session_id,
                    decision_id="candidate-confirmation-activation-memory",
                ),
                json={"commandId": "candidate-confirmation-activation-memory-command"},
            )
            self.assertEqual(activation.status_code, 201)
            self.assertEqual(activation.headers["cache-control"], "no-store")
            body = activation.json()
            self.assertEqual(
                body["schemaVersion"],
                "owner-truth-interview-candidate-confirmation-memory-activation-response-v1",
            )
            self.assertEqual(body["status"], "created")
            self.assertEqual(body["candidateId"], standard.candidate_id)
            self.assertTrue(body["memoryActivation"]["memoryVersionCreated"])
            self.assertNotIn("memoryVersionId", str(body))
            self.assertNotIn("receiptId", str(body))
            self.assertEqual(
                len(
                    self.store.owner_truth_candidate_review_repository().snapshot()[
                        "memoryActivations"
                    ]
                ),
                1,
            )

            replay = client.post(
                activation_path,
                headers=self._confirmation_policy_headers(
                    owner_headers,
                    session_id=owner_session_id,
                    decision_id="candidate-confirmation-activation-memory-replay",
                ),
                json={"commandId": "candidate-confirmation-activation-memory-command-replay"},
            )
            self.assertEqual(replay.status_code, 200)
            self.assertEqual(replay.json()["status"], "deduplicated")
        finally:
            policy_service._CLOSED_PILOT_OWNER_VISIBLE = previous_visible

    def test_formal_memory_activation_rejects_qa_only_batch_receipt(self) -> None:
        owner_id, qa_headers = self._login("13800139417")
        vault_id = "vault-interview-confirmation-qa-receipt"
        review_batch_id, standard, _ = self._seed_review_batch(
            vault_id=vault_id,
            owner_subject_id=owner_id,
        )
        qa_accept = client.post(
            f"{self._read_path(vault_id, review_batch_id)}/batch-accept",
            headers=qa_headers,
            json={
                "commandId": "candidate-qa-only-accept-command",
                "selections": [
                    {
                        "candidateId": standard.candidate_id,
                        "expectedCandidateVersion": 1,
                    }
                ],
                "reasonCode": "ownerReviewed",
            },
        )
        self.assertEqual(qa_accept.status_code, 201)

        _, owner_headers, owner_session_id = self._login_release_policy("13800139417")
        policy_service = main_module.RELEASE_POLICY_SERVICE
        previous_visible = set(policy_service._CLOSED_PILOT_OWNER_VISIBLE)
        policy_service._CLOSED_PILOT_OWNER_VISIBLE = previous_visible | {
            "ownerTruthCandidateReview"
        }
        try:
            denied = client.post(
                self._confirmation_memory_activation_path(
                    vault_id,
                    review_batch_id,
                    standard.candidate_id,
                ),
                headers=self._confirmation_policy_headers(
                    owner_headers,
                    session_id=owner_session_id,
                    decision_id="candidate-qa-only-activation-attempt",
                ),
                json={"commandId": "candidate-qa-only-activation-command"},
            )
            self.assertEqual(denied.status_code, 403)
            self.assertEqual(
                denied.json()["detail"]["code"],
                "ownerTruthInterviewCandidateReviewDenied",
            )
            self.assertEqual(
                self.store.owner_truth_candidate_review_repository().snapshot()[
                    "memoryActivations"
                ],
                {},
            )
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
            initial.json()["review"]["selectedExtractionId"],
            initial.json()["batchCandidates"][0]["extractionId"],
        )
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
