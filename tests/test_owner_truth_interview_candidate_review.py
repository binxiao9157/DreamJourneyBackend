from __future__ import annotations

from contextlib import contextmanager
from unittest import TestCase
from uuid import uuid4

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
from app.domain.owner_truth.interview_candidate_review import (
    InterviewCandidateReviewReadiness,
    OwnerTruthInterviewCandidateReviewAccessDenied,
    OwnerTruthInterviewCandidateReviewConflict,
    OwnerTruthInterviewCandidateReviewSourceInactive,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_interview_candidate_review import (
    InMemoryOwnerTruthInterviewCandidateReviewRepository,
    OwnerTruthInterviewCandidateReviewCompositionService,
)


class _CompositionStore:
    def __init__(self, repository: InMemoryOwnerTruthInterviewCandidateReviewRepository) -> None:
        self.repository = repository

    @contextmanager
    def request_unit_of_work(self, *, correlation_id: str, command_id: str):
        del correlation_id, command_id
        with self.repository.transaction():
            yield

    def owner_truth_interview_candidate_review_repository(self):
        return self.repository


class OwnerTruthInterviewCandidateReviewCompositionTests(TestCase):
    def setUp(self) -> None:
        self.vault_id = "interview-review-composition-vault"
        self.owner_subject_id = "interview-review-composition-owner"
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
        self.repository = InMemoryOwnerTruthInterviewCandidateReviewRepository()
        self.repository.seed_vault(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
        )
        self.repository.seed_admission(
            admission_id=self.admission_id,
            review_batch_id=self.review_batch_id,
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            source_id=self.source_id,
        )
        self.repository.seed_extraction(
            extraction_id=self.extraction_id,
            vault_id=self.vault_id,
            source_id=self.source_id,
            source_version=1,
            status="succeeded",
        )
        self.service = OwnerTruthInterviewCandidateReviewCompositionService(
            _CompositionStore(self.repository)
        )

    def _candidate(
        self,
        *,
        sensitivity: SensitivityLevel = SensitivityLevel.STANDARD,
        review_mode: CandidateReviewMode = CandidateReviewMode.BATCH,
        decision: CandidateDecision = CandidateDecision.PENDING,
        summary: str = "童年常在河边听外公讲故事。",
    ) -> OwnerTruthCandidateSnapshot:
        proposal = CandidateProposal(
            memory_kind=MemoryKind.EXPERIENCE,
            perspective_type=PerspectiveType.FIRST_PERSON,
            epistemic_status=EpistemicStatus.RECALLED,
            sensitivity=sensitivity,
            content={"summary": summary},
            evidence_span=CandidateEvidenceSpan(start=0, end=1),
            confidence=0.72,
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
            decision=decision,
            policy_version="owner-truth-v1",
            authority_epoch=0,
            row_version=1,
            content_hash=record.content_hash,
            content_schema_version=record.payload_schema_version,
            payload=record.payload,
        )

    def _seed_candidate(self, candidate: OwnerTruthCandidateSnapshot) -> None:
        self.repository.seed_candidate(
            candidate=candidate,
            extraction_id=self.extraction_id,
            source_version=1,
        )

    def test_composition_keeps_partial_pending_and_sensitive_single_review_separate(self) -> None:
        batch_candidate = self._candidate(summary="可批量确认的普通候选。")
        sensitive_candidate = self._candidate(
            sensitivity=SensitivityLevel.SENSITIVE,
            review_mode=CandidateReviewMode.SINGLE,
            summary="需要逐条确认的敏感候选。",
        )
        explicit_single_candidate = self._candidate(
            review_mode=CandidateReviewMode.SINGLE,
            summary="普通但要求逐条确认的候选。",
        )
        terminal_candidate = self._candidate(
            decision=CandidateDecision.ACCEPTED,
            summary="已经被部分接受的候选。",
        )
        for candidate in (
            batch_candidate,
            sensitive_candidate,
            explicit_single_candidate,
            terminal_candidate,
        ):
            self._seed_candidate(candidate)

        composition = self.service.compose(
            review_batch_id=self.review_batch_id,
            context=self.context,
        )

        self.assertEqual(composition.readiness, InterviewCandidateReviewReadiness.REVIEW_READY)
        self.assertEqual(
            tuple(item.candidate_id for item in composition.batch_candidates),
            (batch_candidate.candidate_id,),
        )
        self.assertEqual(
            {item.candidate_id for item in composition.single_candidates},
            {sensitive_candidate.candidate_id, explicit_single_candidate.candidate_id},
        )
        self.assertNotIn(
            terminal_candidate.candidate_id,
            {item.candidate_id for item in composition.batch_candidates + composition.single_candidates},
        )
        summary = composition.public_summary()
        self.assertEqual(summary["batchCandidateCount"], 1)
        self.assertEqual(summary["singleCandidateCount"], 2)
        self.assertNotIn("需要逐条确认的敏感候选。", str(summary))

    def test_failed_or_pending_extraction_exposes_no_reviewable_candidate(self) -> None:
        failed_repository = InMemoryOwnerTruthInterviewCandidateReviewRepository()
        failed_repository.seed_vault(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
        )
        failed_repository.seed_admission(
            admission_id=self.admission_id,
            review_batch_id=self.review_batch_id,
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            source_id=self.source_id,
        )
        failed_repository.seed_extraction(
            extraction_id=self.extraction_id,
            vault_id=self.vault_id,
            source_id=self.source_id,
            source_version=1,
            status="failed",
        )
        failed_service = OwnerTruthInterviewCandidateReviewCompositionService(
            _CompositionStore(failed_repository)
        )

        failed = failed_service.compose(
            review_batch_id=self.review_batch_id,
            context=self.context,
        )

        self.assertEqual(
            failed.readiness,
            InterviewCandidateReviewReadiness.EXTRACTION_FAILED,
        )
        self.assertEqual(failed.candidate_count, 0)

        awaiting_repository = InMemoryOwnerTruthInterviewCandidateReviewRepository()
        awaiting_repository.seed_vault(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
        )
        awaiting_repository.seed_admission(
            admission_id=self.admission_id,
            review_batch_id=self.review_batch_id,
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            source_id=self.source_id,
        )
        awaiting = OwnerTruthInterviewCandidateReviewCompositionService(
            _CompositionStore(awaiting_repository)
        ).compose(review_batch_id=self.review_batch_id, context=self.context)
        self.assertEqual(
            awaiting.readiness,
            InterviewCandidateReviewReadiness.AWAITING_EXTRACTION,
        )

    def test_composition_fails_closed_for_other_owner_or_invalid_provenance(self) -> None:
        with self.assertRaises(OwnerTruthInterviewCandidateReviewAccessDenied):
            self.service.compose(
                review_batch_id=self.review_batch_id,
                context=OwnerTruthCommandContext(
                    vault_id=self.vault_id,
                    owner_subject_id="another-owner",
                    actor_subject_id="another-owner",
                ),
            )

        invalid_repository = InMemoryOwnerTruthInterviewCandidateReviewRepository()
        invalid_repository.seed_vault(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
        )
        invalid_repository.seed_admission(
            admission_id=self.admission_id,
            review_batch_id=self.review_batch_id,
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            source_id=self.source_id,
            source_metadata={"origin": "unexpected", "reviewBatchId": self.review_batch_id},
        )
        with self.assertRaises(OwnerTruthInterviewCandidateReviewConflict):
            OwnerTruthInterviewCandidateReviewCompositionService(
                _CompositionStore(invalid_repository)
            ).compose(review_batch_id=self.review_batch_id, context=self.context)

        inactive_repository = InMemoryOwnerTruthInterviewCandidateReviewRepository()
        inactive_repository.seed_vault(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
        )
        inactive_repository.seed_admission(
            admission_id=self.admission_id,
            review_batch_id=self.review_batch_id,
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            source_id=self.source_id,
            source_state="redacted",
        )
        with self.assertRaises(OwnerTruthInterviewCandidateReviewSourceInactive):
            OwnerTruthInterviewCandidateReviewCompositionService(
                _CompositionStore(inactive_repository)
            ).compose(review_batch_id=self.review_batch_id, context=self.context)
