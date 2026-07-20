from __future__ import annotations

from contextlib import contextmanager
from unittest import TestCase
from uuid import uuid4

from app.domain.owner_truth.candidate_decisions import (
    CandidateReviewAction,
    OwnerTruthCandidateSnapshot,
    OwnerTruthCandidateVersionConflict,
)
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
from app.domain.owner_truth.interview_candidate_review import (
    InterviewCandidateReviewPath,
    InterviewCandidateReviewReadiness,
    OwnerTruthInterviewCandidateReviewComposition,
    OwnerTruthInterviewReviewCandidateItem,
)
from app.domain.owner_truth.interview_candidate_single_review import (
    OwnerTruthInterviewCandidateSingleReviewBatchRequired,
    OwnerTruthInterviewCandidateSingleReviewCommand,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_candidate_review import (
    InMemoryOwnerTruthCandidateReviewRepository,
)
from app.services.owner_truth_interview_candidate_batch_decision import (
    InMemoryOwnerTruthInterviewCandidateBatchDecisionRepository,
)
from app.services.owner_truth_interview_candidate_single_review import (
    OwnerTruthInterviewCandidateSingleReviewService,
)


class _CompositionRepository:
    def __init__(self, composition: OwnerTruthInterviewCandidateReviewComposition) -> None:
        self.composition = composition
        self.calls = 0

    def compose(
        self,
        *,
        review_batch_id: str,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthInterviewCandidateReviewComposition:
        del context
        self.calls += 1
        if review_batch_id != self.composition.review_batch_id:
            raise AssertionError("unexpected review batch")
        return self.composition


class _Store:
    def __init__(
        self,
        *,
        composition: OwnerTruthInterviewCandidateReviewComposition,
        review_repository: InMemoryOwnerTruthCandidateReviewRepository,
    ) -> None:
        self.composition_repository = _CompositionRepository(composition)
        self.review_repository = review_repository
        self.ledger_repository = InMemoryOwnerTruthInterviewCandidateBatchDecisionRepository()

    @contextmanager
    def request_unit_of_work(self, *, correlation_id: str, command_id: str):
        del correlation_id, command_id
        with self.ledger_repository.transaction(), self.review_repository.transaction():
            yield

    def owner_truth_interview_candidate_review_repository(self):
        return self.composition_repository

    def owner_truth_candidate_review_repository(self):
        return self.review_repository

    def owner_truth_interview_candidate_batch_decision_repository(self):
        return self.ledger_repository


class OwnerTruthInterviewCandidateSingleReviewTests(TestCase):
    def setUp(self) -> None:
        self.vault_id = "interview-single-review-vault"
        self.owner_subject_id = "interview-single-review-owner"
        self.context = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            actor_subject_id=self.owner_subject_id,
            policy_version="owner-truth-v1",
        )
        self.review_batch_id = str(uuid4())
        self.admission_id = str(uuid4())
        self.source_id = str(uuid4())
        self.extraction_id = str(uuid4())

    def _candidate(
        self,
        *,
        summary: str,
        sensitivity: SensitivityLevel = SensitivityLevel.SENSITIVE,
        review_mode: CandidateReviewMode = CandidateReviewMode.SINGLE,
    ) -> OwnerTruthCandidateSnapshot:
        proposal = CandidateProposal(
            memory_kind=MemoryKind.EXPERIENCE,
            perspective_type=PerspectiveType.FIRST_PERSON,
            epistemic_status=EpistemicStatus.RECALLED,
            sensitivity=sensitivity,
            content={"summary": summary},
            evidence_span=CandidateEvidenceSpan(start=0, end=1),
            confidence=0.71,
            review_mode=review_mode,
        )
        record = proposal.write_record(
            extraction_id=self.extraction_id,
            source_ref=SourceRef(
                vault_id=self.vault_id,
                source_id=self.source_id,
                source_version=1,
            ),
        )
        return OwnerTruthCandidateSnapshot(
            candidate_id=record.candidate_id,
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            source_id=self.source_id,
            memory_kind=record.candidate_kind,
            perspective_type=record.perspective_type,
            epistemic_status=record.epistemic_status,
            sensitivity=record.sensitivity,
            decision=CandidateDecision.PENDING,
            policy_version="owner-truth-v1",
            authority_epoch=0,
            row_version=1,
            content_hash=record.content_hash,
            content_schema_version=record.payload_schema_version,
            payload=record.payload,
        )

    def _composition(
        self,
        *candidates: OwnerTruthCandidateSnapshot,
    ) -> OwnerTruthInterviewCandidateReviewComposition:
        items: list[OwnerTruthInterviewReviewCandidateItem] = []
        for candidate in candidates:
            review_mode = str(candidate.payload.get("reviewMode") or "single")
            path = (
                InterviewCandidateReviewPath.BATCH
                if candidate.sensitivity is SensitivityLevel.STANDARD
                and review_mode == CandidateReviewMode.BATCH.value
                else InterviewCandidateReviewPath.SINGLE
            )
            items.append(
                OwnerTruthInterviewReviewCandidateItem(
                    candidate_id=candidate.candidate_id,
                    extraction_id=self.extraction_id,
                    candidate_row_version=candidate.row_version,
                    candidate_kind=candidate.memory_kind.value,
                    sensitivity=candidate.sensitivity.value,
                    review_mode=review_mode,
                    review_path=path,
                )
            )
        return OwnerTruthInterviewCandidateReviewComposition(
            review_batch_id=self.review_batch_id,
            admission_id=self.admission_id,
            source_id=self.source_id,
            source_version=1,
            authority_epoch=0,
            readiness=InterviewCandidateReviewReadiness.REVIEW_READY,
            latest_extraction_status="succeeded",
            batch_candidates=tuple(
                item for item in items if item.review_path is InterviewCandidateReviewPath.BATCH
            ),
            single_candidates=tuple(
                item for item in items if item.review_path is InterviewCandidateReviewPath.SINGLE
            ),
        )

    def _service(
        self,
        *candidates: OwnerTruthCandidateSnapshot,
    ) -> tuple[OwnerTruthInterviewCandidateSingleReviewService, _Store]:
        repository = InMemoryOwnerTruthCandidateReviewRepository()
        for candidate in candidates:
            repository.seed(candidate)
        store = _Store(
            composition=self._composition(*candidates),
            review_repository=repository,
        )
        return OwnerTruthInterviewCandidateSingleReviewService(store), store

    def _command(
        self,
        candidate: OwnerTruthCandidateSnapshot,
        *,
        action: CandidateReviewAction = CandidateReviewAction.ACCEPT,
        command_id: str = "interview-single-review-001",
        corrected_value: dict[str, str] | None = None,
    ) -> OwnerTruthInterviewCandidateSingleReviewCommand:
        return OwnerTruthInterviewCandidateSingleReviewCommand(
            command_id=command_id,
            review_batch_id=self.review_batch_id,
            candidate_id=candidate.candidate_id,
            expected_candidate_version=candidate.row_version,
            action=action,
            corrected_value=corrected_value,
            corrected_value_schema_version=candidate.content_schema_version,
            reason_code="ownerReviewed",
        )

    def test_sensitive_candidate_is_terminally_accepted_without_memory_activation(self) -> None:
        sensitive = self._candidate(summary="敏感候选必须单条确认。")
        service, store = self._service(sensitive)

        result = service.review_single(
            command=self._command(sensitive),
            context=self.context,
        )

        self.assertEqual(result.outcome, "created")
        self.assertEqual(result.review.decision, CandidateDecision.ACCEPTED)
        snapshot = store.review_repository.snapshot()
        self.assertEqual(snapshot["candidates"][sensitive.candidate_id]["decision"], "accepted")
        self.assertEqual(snapshot["memoryActivations"], {})
        self.assertEqual(len(snapshot["receipts"]), 1)
        self.assertEqual(len(store.ledger_repository.snapshot()), 1)
        self.assertEqual(result.public_summary()["receiptCount"], 1)

    def test_explicit_single_standard_candidate_uses_the_same_private_lane(self) -> None:
        explicit_single = self._candidate(
            summary="普通敏感度但显式单条审核的候选。",
            sensitivity=SensitivityLevel.STANDARD,
            review_mode=CandidateReviewMode.SINGLE,
        )
        service, store = self._service(explicit_single)

        result = service.review_single(
            command=self._command(
                explicit_single,
                command_id="interview-single-review-explicit-001",
            ),
            context=self.context,
        )

        self.assertEqual(result.outcome, "created")
        snapshot = store.review_repository.snapshot()
        self.assertEqual(snapshot["candidates"][explicit_single.candidate_id]["decision"], "accepted")
        self.assertEqual(snapshot["memoryActivations"], {})

    def test_replay_deduplicates_after_candidate_leaves_pending_composition(self) -> None:
        sensitive = self._candidate(summary="可重放的敏感候选。")
        service, store = self._service(sensitive)
        command = self._command(sensitive)

        created = service.review_single(command=command, context=self.context)
        replayed = service.review_single(command=command, context=self.context)

        self.assertEqual(created.outcome, "created")
        self.assertEqual(replayed.outcome, "deduplicated")
        self.assertEqual(replayed.review.outcome, "deduplicated")
        self.assertEqual(store.composition_repository.calls, 1)
        self.assertEqual(len(store.review_repository.snapshot()["receipts"]), 1)

    def test_batch_candidate_cannot_bypass_partial_batch_boundary(self) -> None:
        batch_candidate = self._candidate(
            summary="普通候选必须走批量确认。",
            sensitivity=SensitivityLevel.STANDARD,
            review_mode=CandidateReviewMode.BATCH,
        )
        service, store = self._service(batch_candidate)

        with self.assertRaises(OwnerTruthInterviewCandidateSingleReviewBatchRequired):
            service.review_single(
                command=self._command(batch_candidate),
                context=self.context,
            )

        snapshot = store.review_repository.snapshot()
        self.assertEqual(snapshot["candidates"][batch_candidate.candidate_id]["decision"], "pending")
        self.assertEqual(snapshot["receipts"], {})
        self.assertEqual(store.ledger_repository.snapshot(), {})

    def test_stale_sensitive_candidate_version_is_rejected_before_any_write(self) -> None:
        sensitive = self._candidate(summary="版本冲突敏感候选。")
        service, store = self._service(sensitive)
        stale = OwnerTruthInterviewCandidateSingleReviewCommand(
            command_id="interview-single-review-stale-001",
            review_batch_id=self.review_batch_id,
            candidate_id=sensitive.candidate_id,
            expected_candidate_version=2,
            action=CandidateReviewAction.REJECT,
            corrected_value=None,
            corrected_value_schema_version=sensitive.content_schema_version,
            reason_code="ownerReviewed",
        )

        with self.assertRaises(OwnerTruthCandidateVersionConflict):
            service.review_single(command=stale, context=self.context)

        self.assertEqual(store.review_repository.snapshot()["receipts"], {})
        self.assertEqual(store.ledger_repository.snapshot(), {})

    def test_single_review_supports_correct_without_memory_activation(self) -> None:
        sensitive = self._candidate(summary="需要更正的敏感候选。")
        service, store = self._service(sensitive)

        result = service.review_single(
            command=self._command(
                sensitive,
                action=CandidateReviewAction.CORRECT,
                command_id="interview-single-review-correct-001",
                corrected_value={"summary": "Owner 修正后的敏感候选。"},
            ),
            context=self.context,
        )

        self.assertEqual(result.review.decision, CandidateDecision.CORRECTED)
        snapshot = store.review_repository.snapshot()
        self.assertEqual(snapshot["memoryActivations"], {})
        self.assertEqual(len(snapshot["receipts"]), 1)


if __name__ == "__main__":
    import unittest

    unittest.main()
