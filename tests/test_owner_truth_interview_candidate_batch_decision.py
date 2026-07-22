from __future__ import annotations

from contextlib import contextmanager
from unittest import TestCase
from uuid import uuid4

from app.domain.owner_truth.candidate_decisions import (
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
from app.domain.owner_truth.interview_candidate_batch_decision import (
    OwnerTruthInterviewCandidateBatchAcceptCommand,
    OwnerTruthInterviewCandidateBatchDecisionConflict,
    OwnerTruthInterviewCandidateBatchDecisionSingleReviewRequired,
    OwnerTruthInterviewCandidateBatchSelection,
)
from app.domain.owner_truth.interview_candidate_review import (
    InterviewCandidateReviewPath,
    InterviewCandidateReviewReadiness,
    OwnerTruthInterviewCandidateReviewComposition,
    OwnerTruthInterviewReviewCandidateItem,
)
from app.domain.owner_truth.source_commands import (
    OwnerTruthCommandAuthorizationCapture,
    OwnerTruthCommandContext,
)
from app.services.owner_truth_candidate_review import (
    InMemoryOwnerTruthCandidateReviewRepository,
)
from app.services.owner_truth_interview_candidate_batch_decision import (
    InMemoryOwnerTruthInterviewCandidateBatchDecisionRepository,
    OwnerTruthInterviewCandidateBatchDecisionService,
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


class OwnerTruthInterviewCandidateBatchDecisionTests(TestCase):
    def setUp(self) -> None:
        self.vault_id = "interview-batch-decision-vault"
        self.owner_subject_id = "interview-batch-decision-owner"
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
        sensitivity: SensitivityLevel = SensitivityLevel.STANDARD,
        review_mode: CandidateReviewMode = CandidateReviewMode.BATCH,
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
            selected_extraction_id=self.extraction_id,
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
    ) -> tuple[OwnerTruthInterviewCandidateBatchDecisionService, _Store]:
        repository = InMemoryOwnerTruthCandidateReviewRepository()
        for candidate in candidates:
            repository.seed(candidate)
        store = _Store(
            composition=self._composition(*candidates),
            review_repository=repository,
        )
        return OwnerTruthInterviewCandidateBatchDecisionService(store), store

    def _command(
        self,
        *candidates: OwnerTruthCandidateSnapshot,
        command_id: str = "interview-batch-accept-001",
    ) -> OwnerTruthInterviewCandidateBatchAcceptCommand:
        return OwnerTruthInterviewCandidateBatchAcceptCommand(
            command_id=command_id,
            review_batch_id=self.review_batch_id,
            selections=tuple(
                OwnerTruthInterviewCandidateBatchSelection(
                    candidate_id=candidate.candidate_id,
                    expected_candidate_version=candidate.row_version,
                )
                for candidate in candidates
            ),
            reason_code="ownerReviewed",
        )

    def _formal_context(
        self,
        *,
        decision_hash_character: str,
        feature: str = "ownerTruthCandidateReview",
    ) -> OwnerTruthCommandContext:
        return OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            actor_subject_id=self.owner_subject_id,
            policy_version="owner-truth-v1",
            authorization_capture=OwnerTruthCommandAuthorizationCapture(
                feature=feature,
                policy_version="release-policy-v1",
                policy_revision=1,
                emergency_revision=0,
                account_generation_hash="a" * 24,
                decision_id_hash=decision_hash_character * 64,
                audience="owner",
                cohort="closedPilotAdultSelf",
                client_build=1,
                expires_at="2026-07-22T00:00:00+00:00",
            ),
        )

    def test_partially_accepts_selected_standard_candidates_without_memory_activation(self) -> None:
        first = self._candidate(summary="第一条普通候选。")
        second = self._candidate(summary="第二条普通候选。")
        sensitive = self._candidate(
            summary="敏感候选，必须逐条审核。",
            sensitivity=SensitivityLevel.SENSITIVE,
            review_mode=CandidateReviewMode.SINGLE,
        )
        service, store = self._service(first, second, sensitive)

        result = service.accept_selected(
            command=self._command(first),
            context=self.context,
        )

        self.assertEqual(result.outcome, "created")
        self.assertEqual(result.accepted_candidate_count, 1)
        self.assertEqual(result.candidate_results[0].candidate_id, first.candidate_id)
        snapshot = store.review_repository.snapshot()
        self.assertEqual(snapshot["candidates"][first.candidate_id]["decision"], "accepted")
        self.assertEqual(snapshot["candidates"][second.candidate_id]["decision"], "pending")
        self.assertEqual(snapshot["candidates"][sensitive.candidate_id]["decision"], "pending")
        self.assertEqual(snapshot["memoryActivations"], {})
        self.assertEqual(len(snapshot["receipts"]), 1)
        self.assertEqual(len(store.ledger_repository.snapshot()), 1)
        links = store.ledger_repository.receipt_links_snapshot()
        self.assertEqual(len(links), 1)
        link = next(iter(links.values()))
        self.assertEqual(link.candidate_id, first.candidate_id)
        self.assertEqual(link.receipt_id, result.candidate_results[0].receipt_id)
        self.assertEqual(
            link.candidate_command_id_hash,
            self._command(first).child_command(
                selection=OwnerTruthInterviewCandidateBatchSelection(
                    candidate_id=first.candidate_id,
                    expected_candidate_version=first.row_version,
                )
            ).command_id_hash,
        )
        self.assertEqual(result.public_summary()["acceptedCandidateCount"], 1)

    def test_replay_is_idempotent_without_recomposing_terminal_candidate(self) -> None:
        candidate = self._candidate(summary="可重放的普通候选。")
        service, store = self._service(candidate)
        command = self._command(candidate)

        created = service.accept_selected(command=command, context=self.context)
        replayed = service.accept_selected(command=command, context=self.context)

        self.assertEqual(created.outcome, "created")
        self.assertEqual(replayed.outcome, "deduplicated")
        self.assertEqual(replayed.candidate_results[0].outcome, "deduplicated")
        self.assertEqual(store.composition_repository.calls, 1)
        self.assertEqual(len(store.review_repository.snapshot()["receipts"]), 1)
        self.assertEqual(len(store.ledger_repository.receipt_links_snapshot()), 1)

    def test_formal_confirmation_cannot_replay_a_qa_only_root(self) -> None:
        candidate = self._candidate(summary="QA 根命令不能被正式确认复用。")
        service, store = self._service(candidate)
        command = self._command(candidate)

        service.accept_selected(command=command, context=self.context)

        with self.assertRaisesRegex(
            OwnerTruthInterviewCandidateBatchDecisionConflict,
            "QA-only and formally authorized",
        ):
            service.accept_selected(
                command=command,
                context=self._formal_context(decision_hash_character="b"),
            )

        self.assertEqual(len(store.ledger_repository.snapshot()), 1)
        self.assertEqual(len(store.review_repository.snapshot()["receipts"]), 1)

    def test_formal_confirmation_rejects_capture_from_another_feature_before_write(self) -> None:
        candidate = self._candidate(summary="不允许借用其他功能的正式授权。")
        service, store = self._service(candidate)

        with self.assertRaisesRegex(
            OwnerTruthInterviewCandidateBatchDecisionConflict,
            "requires ownerTruthCandidateReview authorization",
        ):
            service.accept_selected(
                command=self._command(candidate),
                context=self._formal_context(
                    decision_hash_character="e",
                    feature="echoTextInput",
                ),
            )

        self.assertEqual(store.ledger_repository.snapshot(), {})
        self.assertEqual(store.ledger_repository.receipt_links_snapshot(), {})
        snapshot = store.review_repository.snapshot()
        self.assertEqual(snapshot["receipts"], {})
        self.assertEqual(snapshot["candidates"][candidate.candidate_id]["decision"], "pending")

    def test_formal_confirmation_replay_allows_a_fresh_policy_decision(self) -> None:
        candidate = self._candidate(summary="正式确认可使用新策略凭据重试。")
        service, store = self._service(candidate)
        command = self._command(candidate)

        created = service.accept_selected(
            command=command,
            context=self._formal_context(decision_hash_character="c"),
        )
        replayed = service.accept_selected(
            command=command,
            context=self._formal_context(decision_hash_character="d"),
        )

        self.assertEqual(created.outcome, "created")
        self.assertEqual(replayed.outcome, "deduplicated")
        self.assertEqual(len(store.ledger_repository.snapshot()), 1)
        self.assertEqual(len(store.review_repository.snapshot()["receipts"]), 1)

    def test_sensitive_or_single_candidate_cannot_enter_batch_acceptance(self) -> None:
        standard = self._candidate(summary="普通候选。")
        sensitive = self._candidate(
            summary="敏感候选。",
            sensitivity=SensitivityLevel.SENSITIVE,
            review_mode=CandidateReviewMode.SINGLE,
        )
        service, store = self._service(standard, sensitive)

        with self.assertRaises(OwnerTruthInterviewCandidateBatchDecisionSingleReviewRequired):
            service.accept_selected(
                command=self._command(sensitive),
                context=self.context,
            )

        snapshot = store.review_repository.snapshot()
        self.assertEqual(snapshot["candidates"][standard.candidate_id]["decision"], "pending")
        self.assertEqual(snapshot["candidates"][sensitive.candidate_id]["decision"], "pending")
        self.assertEqual(store.ledger_repository.snapshot(), {})

    def test_reused_root_command_cannot_change_selected_subset(self) -> None:
        first = self._candidate(summary="第一条候选。")
        second = self._candidate(summary="第二条候选。")
        service, store = self._service(first, second)

        service.accept_selected(command=self._command(first), context=self.context)
        with self.assertRaises(OwnerTruthInterviewCandidateBatchDecisionConflict):
            service.accept_selected(
                command=self._command(second),
                context=self.context,
            )

        snapshot = store.review_repository.snapshot()
        self.assertEqual(snapshot["candidates"][first.candidate_id]["decision"], "accepted")
        self.assertEqual(snapshot["candidates"][second.candidate_id]["decision"], "pending")

    def test_stale_selected_candidate_version_is_rejected_before_any_write(self) -> None:
        candidate = self._candidate(summary="版本冲突候选。")
        service, store = self._service(candidate)
        stale = OwnerTruthInterviewCandidateBatchAcceptCommand(
            command_id="interview-batch-accept-stale-001",
            review_batch_id=self.review_batch_id,
            selections=(
                OwnerTruthInterviewCandidateBatchSelection(
                    candidate_id=candidate.candidate_id,
                    expected_candidate_version=2,
                ),
            ),
            reason_code="ownerReviewed",
        )

        with self.assertRaises(OwnerTruthCandidateVersionConflict):
            service.accept_selected(command=stale, context=self.context)

        self.assertEqual(store.review_repository.snapshot()["receipts"], {})
        self.assertEqual(store.ledger_repository.snapshot(), {})


if __name__ == "__main__":
    import unittest

    unittest.main()
