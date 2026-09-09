from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import unittest
from uuid import uuid4

from app.domain.owner_truth.candidate_decisions import (
    CandidateReviewAction,
    OwnerTruthCandidateReviewCommand,
    OwnerTruthCandidateReviewConflict,
    OwnerTruthCandidateSnapshot,
)
from app.domain.owner_truth.contracts import (
    CandidateDecision,
    EpistemicStatus,
    MemoryKind,
    PerspectiveType,
    SensitivityLevel,
)
from app.domain.owner_truth.ontology import (
    OWNER_TRUTH_SCHEMA_VERSION_V5,
    enrich_memory_payload_v5,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_candidate_review import (
    InMemoryOwnerTruthCandidateReviewRepository,
    OwnerTruthCandidateReviewService,
)


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class _Store:
    def __init__(self) -> None:
        self.repository = InMemoryOwnerTruthCandidateReviewRepository()

    @contextmanager
    def request_unit_of_work(self, *, correlation_id: str, command_id: str):
        yield

    def owner_truth_candidate_review_repository(self):
        return self.repository


class OwnerTruthMemoryChangeSetReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vault_id = "vault-memory-changeset-review"
        self.owner_id = "subject-memory-changeset-review"
        self.context = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            actor_subject_id=self.owner_id,
        )
        self.store = _Store()
        self.service = OwnerTruthCandidateReviewService(self.store)

    def _content(self) -> dict[str, object]:
        return enrich_memory_payload_v5(
            kind=MemoryKind.KNOWLEDGE,
            payload={
                "statement": "我在杭州时喜欢吃东坡肉",
                "knowledgeType": "personal_preference",
                "domains": ["饮食"],
                "factType": "preference",
                "predicate": "prefers",
                "object": {"label": "东坡肉", "category": "dish"},
                "qualifiers": {
                    "polarity": "positive",
                    "currentApplicability": "historical",
                    "validTime": {"precision": "year", "expression": "2016年"},
                },
            },
            provenance={"mode": "selfReport"},
            memory_subject_id="person-owner",
            claim_subject_id="person-owner",
        )

    def _candidate(self, *, source_id: str | None = None) -> OwnerTruthCandidateSnapshot:
        source_id = source_id or str(uuid4())
        content = self._content()
        return OwnerTruthCandidateSnapshot(
            candidate_id=str(uuid4()),
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            source_id=source_id,
            memory_kind=MemoryKind.KNOWLEDGE,
            perspective_type=PerspectiveType.FIRST_PERSON,
            epistemic_status=EpistemicStatus.RECALLED,
            sensitivity=SensitivityLevel.STANDARD,
            decision=CandidateDecision.PENDING,
            # Policy version is an authorization/write-policy contract, not the
            # content schema version.  The candidate payload is V5 while this
            # fixture retains the command context's current policy contract.
            policy_version=self.context.policy_version,
            authority_epoch=0,
            row_version=1,
            content_hash=_hash(content),
            content_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V5,
            payload={
                "content": content,
                "contentSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION_V5,
                "evidenceRefs": [
                    {"sourceId": source_id, "sourceVersion": 1, "span": {"start": 0, "end": 9}}
                ],
                "reviewMode": "single",
                "schemaVersion": "owner-truth-candidate-proposal-v1",
            },
        )

    def _accept(
        self,
        candidate: OwnerTruthCandidateSnapshot,
        *,
        command_id: str,
        expected_memory_revision: int | None = None,
    ):
        proposal = self.service.preview_changeset(
            candidate_id=candidate.candidate_id,
            context=self.context,
        )
        self.assertIsNotNone(proposal)
        assert proposal is not None
        return self.service.decide_and_activate(
            command=OwnerTruthCandidateReviewCommand(
                command_id=command_id,
                candidate_id=candidate.candidate_id,
                expected_candidate_version=candidate.row_version,
                expected_memory_revision=(
                    proposal.change_set.base_memory_revision
                    if expected_memory_revision is None
                    else expected_memory_revision
                ),
                expected_change_set_id=proposal.change_set.change_set_id,
                expected_proposal_hash=proposal.proposal_hash,
                action=CandidateReviewAction.ACCEPT,
                corrected_value=None,
                corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V5,
                reason_code="ownerReviewed",
            ),
            context=self.context,
        )

    def test_new_evidence_supersedes_the_current_version_instead_of_adding_a_record(self) -> None:
        first = self._candidate()
        second = self._candidate()
        self.store.repository.seed(first)
        self.store.repository.seed(second)

        first_result = self._accept(first, command_id="changeset-review-first-001", expected_memory_revision=0)
        second_result = self._accept(second, command_id="changeset-review-second-001", expected_memory_revision=1)

        self.assertEqual(first_result.memory_activation.outcome, "created")
        self.assertEqual(second_result.memory_activation.outcome, "revised")
        self.assertEqual(second_result.memory_activation.memory_id, first_result.memory_activation.memory_id)
        self.assertEqual(second_result.memory_activation.memory_version, 2)
        self.assertEqual(first_result.memory_revision, 1)
        self.assertEqual(second_result.memory_revision, 2)
        self.assertEqual(self.store.repository.memory_revision(context=self.context), 2)
        snapshot = self.store.repository.snapshot()
        self.assertEqual(len(snapshot["memoryActivations"]), 2)
        self.assertEqual(len(snapshot["memoryChangesets"]), 2)
        history = self.service.list_memory_version_history(
            memory_id=str(first_result.memory_activation.memory_id),
            context=self.context,
        )
        self.assertEqual([item.version_number for item in history.versions], [2, 1])
        self.assertEqual([item.status for item in history.versions], ["current", "superseded"])

    def test_stale_revision_rolls_back_the_candidate_decision_and_receipt(self) -> None:
        first = self._candidate()
        self.store.repository.seed(first)
        self._accept(first, command_id="changeset-review-prime-001", expected_memory_revision=0)
        stale = self._candidate()
        self.store.repository.seed(stale)

        with self.assertRaises(OwnerTruthCandidateReviewConflict):
            self._accept(stale, command_id="changeset-review-stale-001", expected_memory_revision=0)

        snapshot = self.store.repository.snapshot()
        self.assertEqual(snapshot["candidates"][stale.candidate_id]["decision"], "pending")
        self.assertNotIn("changeset-review-stale-001", str(snapshot["receipts"]))
        self.assertEqual(self.store.repository.memory_revision(context=self.context), 1)

    def test_duplicate_confirmation_has_a_receipt_but_does_not_advance_revision(self) -> None:
        source_id = str(uuid4())
        first = self._candidate(source_id=source_id)
        duplicate = self._candidate(source_id=source_id)
        self.store.repository.seed(first)
        self.store.repository.seed(duplicate)
        self._accept(first, command_id="changeset-review-duplicate-first-001", expected_memory_revision=0)

        result = self._accept(
            duplicate,
            command_id="changeset-review-duplicate-second-001",
            expected_memory_revision=1,
        )

        self.assertEqual(result.memory_activation.outcome, "duplicate")
        self.assertEqual(result.memory_revision, 1)
        self.assertEqual(self.store.repository.memory_revision(context=self.context), 1)
        self.assertEqual(len(self.store.repository.snapshot()["memoryChangesets"]), 2)
        history = self.service.list_review_history(context=self.context)
        duplicate_history = next(
            item for item in history if item.candidate.candidate_id == duplicate.candidate_id
        )
        self.assertEqual(duplicate_history.memory_activation_status, "deduplicated")
        self.assertEqual(
            duplicate_history.memory_id,
            self._acceptance_memory_id(history, first.candidate_id),
        )
        versions = self.service.list_memory_version_history(
            memory_id=str(duplicate_history.memory_id),
            context=self.context,
        )
        self.assertEqual([item.version_number for item in versions.versions], [1])

    def test_v5_review_requires_a_fresh_owner_visible_changeset_proposal(self) -> None:
        candidate = self._candidate()
        self.store.repository.seed(candidate)

        with self.assertRaises(OwnerTruthCandidateReviewConflict):
            self.service.decide_and_activate(
                command=OwnerTruthCandidateReviewCommand(
                    command_id="changeset-review-missing-preview-001",
                    candidate_id=candidate.candidate_id,
                    expected_candidate_version=candidate.row_version,
                    expected_memory_revision=0,
                    action=CandidateReviewAction.ACCEPT,
                    corrected_value=None,
                    corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V5,
                    reason_code="ownerReviewed",
                ),
                context=self.context,
            )

        proposal = self.service.preview_changeset(
            candidate_id=candidate.candidate_id,
            context=self.context,
        )
        self.assertIsNotNone(proposal)
        assert proposal is not None
        result = self.service.decide_and_activate(
            command=OwnerTruthCandidateReviewCommand(
                command_id="changeset-review-bound-preview-001",
                candidate_id=candidate.candidate_id,
                expected_candidate_version=candidate.row_version,
                expected_memory_revision=proposal.change_set.base_memory_revision,
                expected_change_set_id=proposal.change_set.change_set_id,
                expected_proposal_hash=proposal.proposal_hash,
                action=CandidateReviewAction.ACCEPT,
                corrected_value=None,
                corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V5,
                reason_code="ownerReviewed",
            ),
            context=self.context,
        )
        self.assertEqual(result.memory_activation.outcome, "created")

    def test_v5_preview_is_rejected_after_another_review_advances_revision(self) -> None:
        first = self._candidate()
        stale = self._candidate()
        self.store.repository.seed(first)
        self.store.repository.seed(stale)
        stale_proposal = self.service.preview_changeset(
            candidate_id=stale.candidate_id,
            context=self.context,
        )
        self.assertIsNotNone(stale_proposal)
        assert stale_proposal is not None
        self._accept(first, command_id="changeset-review-preview-prime-001")

        with self.assertRaises(OwnerTruthCandidateReviewConflict):
            self.service.decide_and_activate(
                command=OwnerTruthCandidateReviewCommand(
                    command_id="changeset-review-stale-preview-001",
                    candidate_id=stale.candidate_id,
                    expected_candidate_version=stale.row_version,
                    expected_memory_revision=stale_proposal.change_set.base_memory_revision,
                    expected_change_set_id=stale_proposal.change_set.change_set_id,
                    expected_proposal_hash=stale_proposal.proposal_hash,
                    action=CandidateReviewAction.ACCEPT,
                    corrected_value=None,
                    corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V5,
                    reason_code="ownerReviewed",
                ),
                context=self.context,
            )

        snapshot = self.store.repository.snapshot()
        self.assertEqual(snapshot["candidates"][stale.candidate_id]["decision"], "pending")
        self.assertEqual(self.store.repository.memory_revision(context=self.context), 1)

    @staticmethod
    def _acceptance_memory_id(history, candidate_id: str) -> str:
        accepted = next(item for item in history if item.candidate.candidate_id == candidate_id)
        if accepted.memory_id is None:
            raise AssertionError("initial accepted Candidate must activate a formal memory")
        return accepted.memory_id


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
