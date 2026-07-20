"""Private individual review for sensitive/single Candidates from M0-A batches."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ContextManager, Protocol

from app.domain.owner_truth.candidate_decisions import (
    OwnerTruthCandidateReviewAccessDenied,
    OwnerTruthCandidateVersionConflict,
)
from app.domain.owner_truth.interview_candidate_single_review import (
    OwnerTruthInterviewCandidateSingleReviewBatchRequired,
    OwnerTruthInterviewCandidateSingleReviewCommand,
    OwnerTruthInterviewCandidateSingleReviewConflict,
    OwnerTruthInterviewCandidateSingleReviewNotReady,
)
from app.domain.owner_truth.interview_candidate_review import (
    InterviewCandidateReviewPath,
    InterviewCandidateReviewReadiness,
    OwnerTruthInterviewCandidateReviewComposition,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_candidate_review import OwnerTruthCandidateReviewResult


@dataclass(frozen=True)
class OwnerTruthInterviewCandidateSingleReviewResult:
    outcome: str
    batch_decision_id: str
    review_batch_id: str
    review: OwnerTruthCandidateReviewResult

    def public_summary(self) -> dict[str, object]:
        return {
            "schemaVersion": "owner-truth-interview-candidate-single-review-result-v1",
            "outcome": self.outcome,
            "reviewBatchId": self.review_batch_id,
            "decision": self.review.decision.value,
            "receiptCount": 1,
        }


class OwnerTruthInterviewCandidateSingleReviewStore(Protocol):
    def request_unit_of_work(
        self,
        *,
        correlation_id: str,
        command_id: str,
    ) -> ContextManager[Any]:
        ...

    def owner_truth_interview_candidate_review_repository(self) -> Any:
        ...

    def owner_truth_candidate_review_repository(self) -> Any:
        ...

    def owner_truth_interview_candidate_batch_decision_repository(self) -> Any:
        ...


def _assert_owner_context(context: OwnerTruthCommandContext) -> None:
    if not isinstance(context, OwnerTruthCommandContext):
        raise OwnerTruthInterviewCandidateSingleReviewConflict(
            "owner truth command context is required"
        )
    if context.actor_subject_id != context.owner_subject_id:
        raise OwnerTruthCandidateReviewAccessDenied(
            "only the Vault Owner may review an interview Candidate"
        )


class OwnerTruthInterviewCandidateSingleReviewService:
    """Apply one terminal decision without promoting a MemoryVersion."""

    def __init__(self, store: OwnerTruthInterviewCandidateSingleReviewStore):
        self._store = store

    def review_single(
        self,
        *,
        command: OwnerTruthInterviewCandidateSingleReviewCommand,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthInterviewCandidateSingleReviewResult:
        _assert_owner_context(context)
        with self._store.request_unit_of_work(
            correlation_id=(
                "owner-truth-interview-candidate-single-review-"
                f"{context.vault_id}:{command.command_id_hash}"
            ),
            command_id=command.command_id_hash,
        ):
            ledger = self._store.owner_truth_interview_candidate_batch_decision_repository()
            existing = self._lookup_existing(
                ledger=ledger,
                command=command,
                context=context,
            )
            if existing is None:
                composition = self._store.owner_truth_interview_candidate_review_repository().compose(
                    review_batch_id=command.review_batch_id,
                    context=context,
                )
                self._assert_single_candidate(
                    composition=composition,
                    command=command,
                )
                authority_epoch = composition.authority_epoch
            else:
                authority_epoch = existing.authority_epoch
            outcome, record = ledger.claim(
                command=command,
                context=context,
                authority_epoch=authority_epoch,
            )
            review = self._store.owner_truth_candidate_review_repository().decide(
                command=command.child_command(),
                context=context,
            )
        return OwnerTruthInterviewCandidateSingleReviewResult(
            outcome=outcome,
            batch_decision_id=record.batch_decision_id,
            review_batch_id=record.review_batch_id,
            review=review,
        )

    @staticmethod
    def _lookup_existing(*, ledger: Any, command: Any, context: OwnerTruthCommandContext):
        lookup = getattr(ledger, "lookup", None)
        if not callable(lookup):
            return None
        return lookup(command=command, context=context)

    @staticmethod
    def _assert_single_candidate(
        *,
        composition: OwnerTruthInterviewCandidateReviewComposition,
        command: OwnerTruthInterviewCandidateSingleReviewCommand,
    ) -> None:
        if composition.readiness is not InterviewCandidateReviewReadiness.REVIEW_READY:
            raise OwnerTruthInterviewCandidateSingleReviewNotReady(
                "interview Candidate extraction is not ready for individual review"
            )
        single_candidates = {
            item.candidate_id: item for item in composition.single_candidates
        }
        item = single_candidates.get(command.candidate_id)
        if item is None:
            if any(
                candidate.candidate_id == command.candidate_id
                for candidate in composition.batch_candidates
            ):
                raise OwnerTruthInterviewCandidateSingleReviewBatchRequired(
                    "batch-eligible Candidate must use the partial batch path"
                )
            raise OwnerTruthInterviewCandidateSingleReviewConflict(
                "Candidate is not pending in this single-review composition"
            )
        if item.review_path is not InterviewCandidateReviewPath.SINGLE:
            raise OwnerTruthInterviewCandidateSingleReviewBatchRequired(
                "batch-eligible Candidate must use the partial batch path"
            )
        if item.candidate_row_version != command.expected_candidate_version:
            raise OwnerTruthCandidateVersionConflict(
                expected_version=command.expected_candidate_version,
                current_version=item.candidate_row_version,
            )


__all__ = [
    "OwnerTruthInterviewCandidateSingleReviewResult",
    "OwnerTruthInterviewCandidateSingleReviewService",
    "OwnerTruthInterviewCandidateSingleReviewStore",
]
