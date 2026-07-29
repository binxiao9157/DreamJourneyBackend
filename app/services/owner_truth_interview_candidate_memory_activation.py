"""Explicit MemoryVersion activation after formal interview confirmation.

Formal interview batch confirmation deliberately ends at an immutable
``DecisionReceipt``.  This service is the next, separate Owner command: it
accepts only a receipt that is linked to the same formally-authorized review
batch, then delegates MemoryVersion creation to the canonical candidate review
repository.  It does not run extraction, alter the original confirmation
receipt, expose candidate content, or make KBLite authoritative.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, ContextManager, Protocol

from app.async_effects.contracts import EffectReceiptSummary
from app.domain.owner_truth.candidate_decisions import (
    OwnerTruthCandidateReviewAccessDenied,
    OwnerTruthCandidateReviewError,
)
from app.domain.owner_truth.contracts import require_uuid
from app.domain.owner_truth.memory_activation import OwnerTruthMemoryActivationResult
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_interview_candidate_batch_decision import (
    FORMAL_INTERVIEW_CANDIDATE_REVIEW_FEATURE,
    OwnerTruthInterviewCandidateFormalActivationAdmission,
)
from app.services.owner_truth_memory_projection_effects import (
    build_memory_projection_rebuild_effect_intent,
)


@dataclass(frozen=True)
class OwnerTruthInterviewCandidateMemoryActivationCommand:
    """One explicit, value-free request to activate a confirmed Candidate."""

    command_id: str
    review_batch_id: str
    candidate_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.command_id, str) or not self.command_id.strip():
            raise OwnerTruthCandidateReviewError("command_id must be non-empty")
        if len(self.command_id.strip()) > 128:
            raise OwnerTruthCandidateReviewError("command_id exceeds maximum length")
        object.__setattr__(self, "command_id", self.command_id.strip())
        object.__setattr__(
            self,
            "review_batch_id",
            require_uuid(self.review_batch_id, field="review_batch_id"),
        )
        object.__setattr__(
            self,
            "candidate_id",
            require_uuid(self.candidate_id, field="candidate_id"),
        )

    @property
    def command_id_hash(self) -> str:
        return sha256(self.command_id.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class OwnerTruthInterviewCandidateMemoryActivationResult:
    """Value-minimized activation outcome for the formal confirmation route."""

    outcome: str
    review_batch_id: str
    candidate_id: str
    memory_activation: OwnerTruthMemoryActivationResult
    projection_effect: EffectReceiptSummary | None = None


class OwnerTruthInterviewCandidateMemoryActivationStore(Protocol):
    def request_unit_of_work(
        self,
        *,
        correlation_id: str,
        command_id: str,
    ) -> ContextManager[Any]:
        ...

    def owner_truth_interview_candidate_batch_decision_repository(self) -> Any:
        ...

    def owner_truth_candidate_review_repository(self) -> Any:
        ...


def _assert_formal_owner_context(context: OwnerTruthCommandContext) -> None:
    if context.actor_subject_id != context.owner_subject_id:
        raise OwnerTruthCandidateReviewAccessDenied(
            "only the Vault Owner may activate an interview Candidate"
        )
    capture = context.authorization_capture
    if capture is None or capture.feature != FORMAL_INTERVIEW_CANDIDATE_REVIEW_FEATURE:
        raise OwnerTruthCandidateReviewAccessDenied(
            "MemoryVersion activation requires ownerTruthCandidateReview authorization"
        )


class OwnerTruthInterviewCandidateMemoryActivationService:
    """Compose formal receipt admission, canonical activation, and projection intent."""

    def __init__(self, store: OwnerTruthInterviewCandidateMemoryActivationStore):
        self._store = store

    def activate(
        self,
        *,
        command: OwnerTruthInterviewCandidateMemoryActivationCommand,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthInterviewCandidateMemoryActivationResult:
        _assert_formal_owner_context(context)
        with self._request_unit_of_work(
            correlation_id=(
                "owner-truth-interview-formal-memory-activation-"
                f"{context.vault_id}:{command.command_id_hash}"
            ),
            command_id=command.command_id_hash,
        ):
            admission = self._formal_admission(command=command, context=context)
            review_repository = self._store.owner_truth_candidate_review_repository()
            transaction = getattr(review_repository, "transaction", None)
            scope = transaction() if callable(transaction) else nullcontext()
            with scope:
                activation = review_repository.activate_memory_version(
                    receipt_id=admission.receipt_id,
                    context=context,
                )
                projection_effect = self._write_projection_rebuild_effect(
                    context=context,
                    activation=activation,
                )
        return OwnerTruthInterviewCandidateMemoryActivationResult(
            outcome=activation.outcome,
            review_batch_id=admission.review_batch_id,
            candidate_id=admission.candidate_id,
            memory_activation=activation,
            projection_effect=projection_effect,
        )

    def _formal_admission(
        self,
        *,
        command: OwnerTruthInterviewCandidateMemoryActivationCommand,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthInterviewCandidateFormalActivationAdmission:
        repository = self._store.owner_truth_interview_candidate_batch_decision_repository()
        reader = getattr(repository, "formal_activation_admission", None)
        if not callable(reader):
            raise OwnerTruthCandidateReviewError(
                "formal interview confirmation activation is not supported by this store"
            )
        return reader(
            review_batch_id=command.review_batch_id,
            candidate_id=command.candidate_id,
            context=context,
        )

    def _write_projection_rebuild_effect(
        self,
        *,
        context: OwnerTruthCommandContext,
        activation: OwnerTruthMemoryActivationResult,
    ) -> EffectReceiptSummary | None:
        if activation.memory_version_id is None:
            return None
        factory = getattr(self._store, "effect_kernel_repository", None)
        if not callable(factory):
            return None
        return factory().accept(
            build_memory_projection_rebuild_effect_intent(
                context=context,
                activation=activation,
            )
        )

    def _request_unit_of_work(
        self,
        *,
        correlation_id: str,
        command_id: str,
    ) -> ContextManager[Any]:
        factory = getattr(self._store, "request_unit_of_work", None)
        if callable(factory):
            return factory(correlation_id=correlation_id, command_id=command_id)
        return nullcontext()


__all__ = [
    "OwnerTruthInterviewCandidateMemoryActivationCommand",
    "OwnerTruthInterviewCandidateMemoryActivationResult",
    "OwnerTruthInterviewCandidateMemoryActivationService",
]
