"""Default-off review-batch automation for persisted M0-A interview turns.

The conversation repository already owns ReviewBatch persistence and validates
whether a threshold or session-exit batch is due. This service only composes
that existing command after a durable interview transition. It never reads
message text, extracts a Candidate, writes a MemoryVersion, or calls a
Provider.
"""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from typing import Any, Protocol

from app.domain.owner_truth.contracts import require_nonblank, require_uuid
from app.domain.owner_truth.conversation import (
    CreateInterviewReviewBatchCommand,
    InterviewReviewBatchState,
    InterviewReviewBatchTrigger,
    InterviewSessionState,
    OwnerTruthConversationConflict,
    OwnerTruthInterviewReviewBatchSnapshot,
)
from app.domain.owner_truth.interview_orchestration import MIN_TURNS_BEFORE_CANDIDATE_BATCH
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_conversation import OwnerTruthConversationService


OWNER_TRUTH_INTERVIEW_REVIEW_BATCH_AUTOMATION_SCHEMA_VERSION = (
    "owner-truth-interview-review-batch-automation-v1"
)


class OwnerTruthInterviewReviewBatchAutomationError(OwnerTruthConversationConflict):
    """The private ReviewBatch automation boundary cannot proceed safely."""


class OwnerTruthInterviewReviewBatchAutomationUnavailable(
    OwnerTruthInterviewReviewBatchAutomationError
):
    """The default-off automation lane is not enabled for this call."""


class OwnerTruthInterviewReviewBatchAutomationStore(Protocol):
    def request_unit_of_work(
        self,
        *,
        correlation_id: str,
        command_id: str,
    ) -> AbstractContextManager[Any]:
        ...

    def owner_truth_conversation_repository(self) -> Any:
        ...


@dataclass(frozen=True)
class OwnerTruthInterviewReviewBatchAutomationResult:
    """A content-free result for one automatic review-boundary attempt."""

    state: str
    review_batch: OwnerTruthInterviewReviewBatchSnapshot | None
    session_version: int

    def __post_init__(self) -> None:
        if self.state not in {"created", "alreadyPending", "notDue"}:
            raise OwnerTruthInterviewReviewBatchAutomationError(
                "review batch automation state is unsupported"
            )
        if self.state == "notDue" and self.review_batch is not None:
            raise OwnerTruthInterviewReviewBatchAutomationError(
                "not-due automation must not carry a review batch"
            )
        if self.state != "notDue" and self.review_batch is None:
            raise OwnerTruthInterviewReviewBatchAutomationError(
                "review batch automation state requires a review batch"
            )
        if (
            not isinstance(self.session_version, int)
            or isinstance(self.session_version, bool)
            or self.session_version < 1
        ):
            raise OwnerTruthInterviewReviewBatchAutomationError(
                "review batch automation session version is invalid"
            )

    @property
    def review_batch_created(self) -> bool:
        return self.state == "created"


class OwnerTruthInterviewReviewBatchAutomationService:
    """Ensure a due persisted interview has exactly one pending ReviewBatch.

    The automatic child command is deterministic from the parent durable
    transition command. Retrying the parent transition therefore cannot create
    multiple batches. If a concurrent caller already created the batch, this
    service returns ``alreadyPending`` rather than issuing another one.
    """

    def __init__(
        self,
        store: OwnerTruthInterviewReviewBatchAutomationStore,
        *,
        enabled: bool = False,
    ) -> None:
        self._store = store
        self._enabled = bool(enabled)

    def ensure_after_transition(
        self,
        *,
        session_id: str,
        transition_command_id: str,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthInterviewReviewBatchAutomationResult:
        if not self._enabled:
            raise OwnerTruthInterviewReviewBatchAutomationUnavailable(
                "interview review batch automation is unavailable"
            )
        normalized_session_id = require_uuid(session_id, field="session_id")
        normalized_transition_command_id = require_nonblank(
            transition_command_id,
            field="transition_command_id",
        )
        with self._unit_of_work(
            correlation_id=(
                "owner-truth-interview-review-batch-automation:"
                f"{context.vault_id}:{normalized_session_id}"
            ),
            command_id=f"auto-review-batch:{normalized_transition_command_id}",
        ):
            conversation = OwnerTruthConversationService(
                self._store.owner_truth_conversation_repository()
            )
            session = conversation.read_session(
                session_id=normalized_session_id,
                context=context,
            )
            pending = self._pending_batch(
                conversation=conversation,
                session_id=normalized_session_id,
                pending_review_batch_id=session.pending_review_batch_id,
                context=context,
            )
            if pending is not None:
                return OwnerTruthInterviewReviewBatchAutomationResult(
                    state="alreadyPending",
                    review_batch=pending,
                    session_version=session.row_version,
                )
            if not self._is_due(session_state=session.state, turn_count=session.candidate_batch_turn_count):
                return OwnerTruthInterviewReviewBatchAutomationResult(
                    state="notDue",
                    review_batch=None,
                    session_version=session.row_version,
                )
            try:
                created = conversation.create_review_batch(
                    command=CreateInterviewReviewBatchCommand(
                        command_id=f"auto-review-batch:{normalized_transition_command_id}",
                        thread_id=session.thread_id,
                        session_id=session.session_id,
                        expected_session_version=session.row_version,
                    ),
                    context=context,
                )
            except OwnerTruthConversationConflict:
                return self._reconcile_conflict(
                    conversation=conversation,
                    session_id=normalized_session_id,
                    context=context,
                )
            return OwnerTruthInterviewReviewBatchAutomationResult(
                state="created",
                review_batch=created.review_batch,
                session_version=created.session_version,
            )

    @staticmethod
    def _is_due(*, session_state: InterviewSessionState, turn_count: int) -> bool:
        if turn_count >= MIN_TURNS_BEFORE_CANDIDATE_BATCH:
            return True
        return session_state is not InterviewSessionState.ACTIVE and turn_count > 0

    @staticmethod
    def _pending_batch(
        *,
        conversation: OwnerTruthConversationService,
        session_id: str,
        pending_review_batch_id: str | None,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthInterviewReviewBatchSnapshot | None:
        if pending_review_batch_id is None:
            return None
        for batch in conversation.list_review_batches(session_id=session_id, context=context):
            if batch.review_batch_id == pending_review_batch_id:
                if batch.state is not InterviewReviewBatchState.PENDING_ACKNOWLEDGEMENT:
                    raise OwnerTruthInterviewReviewBatchAutomationError(
                        "session pending review batch is not pending acknowledgement"
                    )
                return batch
        raise OwnerTruthInterviewReviewBatchAutomationError(
            "session pending review batch cannot be resolved"
        )

    def _reconcile_conflict(
        self,
        *,
        conversation: OwnerTruthConversationService,
        session_id: str,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthInterviewReviewBatchAutomationResult:
        """Fail closed on ambiguity, but tolerate a concurrent valid creator."""

        refreshed = conversation.read_session(session_id=session_id, context=context)
        pending = self._pending_batch(
            conversation=conversation,
            session_id=session_id,
            pending_review_batch_id=refreshed.pending_review_batch_id,
            context=context,
        )
        if pending is not None:
            return OwnerTruthInterviewReviewBatchAutomationResult(
                state="alreadyPending",
                review_batch=pending,
                session_version=refreshed.row_version,
            )
        if not self._is_due(
            session_state=refreshed.state,
            turn_count=refreshed.candidate_batch_turn_count,
        ):
            return OwnerTruthInterviewReviewBatchAutomationResult(
                state="notDue",
                review_batch=None,
                session_version=refreshed.row_version,
            )
        raise OwnerTruthInterviewReviewBatchAutomationError(
            "review batch creation conflicted while the session remains due"
        )

    def _unit_of_work(self, *, correlation_id: str, command_id: str) -> AbstractContextManager[Any]:
        factory = getattr(self._store, "request_unit_of_work", None)
        if callable(factory):
            return factory(correlation_id=correlation_id, command_id=command_id)
        return nullcontext()


def review_batch_automation_summary(
    result: OwnerTruthInterviewReviewBatchAutomationResult,
) -> dict[str, object]:
    """Return an owner-scoped operational handle without interview content."""

    if not isinstance(result, OwnerTruthInterviewReviewBatchAutomationResult):
        raise OwnerTruthInterviewReviewBatchAutomationError(
            "review batch automation result is invalid"
        )
    payload: dict[str, object] = {
        "schemaVersion": OWNER_TRUTH_INTERVIEW_REVIEW_BATCH_AUTOMATION_SCHEMA_VERSION,
        "state": result.state,
        "reviewBatchCreated": result.review_batch_created,
        "sessionVersion": result.session_version,
    }
    if result.review_batch is not None:
        payload["reviewBatch"] = {
            "reviewBatchId": result.review_batch.review_batch_id,
            "trigger": result.review_batch.trigger.value,
            "state": result.review_batch.state.value,
            "capturedCandidateBatchTurnCount": result.review_batch.captured_candidate_batch_turn_count,
            "rowVersion": result.review_batch.row_version,
        }
    return payload


__all__ = [
    "OWNER_TRUTH_INTERVIEW_REVIEW_BATCH_AUTOMATION_SCHEMA_VERSION",
    "OwnerTruthInterviewReviewBatchAutomationError",
    "OwnerTruthInterviewReviewBatchAutomationResult",
    "OwnerTruthInterviewReviewBatchAutomationService",
    "OwnerTruthInterviewReviewBatchAutomationStore",
    "OwnerTruthInterviewReviewBatchAutomationUnavailable",
    "review_batch_automation_summary",
]
