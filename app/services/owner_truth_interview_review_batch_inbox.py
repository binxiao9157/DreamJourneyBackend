"""Value-minimized formal discovery for pending M0-A ReviewBatch boundaries."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Protocol

from app.domain.owner_truth.contracts import require_uuid
from app.domain.owner_truth.conversation import (
    InterviewReviewBatchState,
    OwnerTruthConversationError,
    OwnerTruthInterviewReviewBatchSnapshot,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_conversation import OwnerTruthConversationService


OWNER_TRUTH_INTERVIEW_PENDING_REVIEW_BATCH_INBOX_SCHEMA_VERSION = (
    "owner-truth-interview-pending-review-batch-inbox-v1"
)


class OwnerTruthInterviewPendingReviewBatchInboxError(OwnerTruthConversationError):
    """The value-minimized formal pending-batch inbox cannot be read safely."""


class OwnerTruthInterviewPendingReviewBatchInboxStore(Protocol):
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
class OwnerTruthInterviewPendingReviewBatchInboxItem:
    """Operational handles for a same-owner acknowledgement, never content."""

    review_batch_id: str
    thread_id: str
    session_id: str
    review_batch_version: int
    session_version: int
    trigger: str
    captured_candidate_batch_turn_count: int

    def __post_init__(self) -> None:
        for field in ("review_batch_id", "thread_id", "session_id"):
            object.__setattr__(self, field, require_uuid(getattr(self, field), field=field))
        for field in ("review_batch_version", "session_version"):
            value = getattr(self, field)
            if type(value) is not int or value < 1:
                raise OwnerTruthInterviewPendingReviewBatchInboxError(
                    f"{field} must be a positive integer"
                )
        if (
            type(self.captured_candidate_batch_turn_count) is not int
            or self.captured_candidate_batch_turn_count < 1
        ):
            raise OwnerTruthInterviewPendingReviewBatchInboxError(
                "captured_candidate_batch_turn_count must be a positive integer"
            )
        if self.trigger not in {"turnThreshold", "sessionExit"}:
            raise OwnerTruthInterviewPendingReviewBatchInboxError(
                "pending review batch trigger is unsupported"
            )

    def public_summary(self) -> dict[str, object]:
        return {
            "reviewBatchId": self.review_batch_id,
            "threadId": self.thread_id,
            "sessionId": self.session_id,
            "reviewBatchVersion": self.review_batch_version,
            "sessionVersion": self.session_version,
            "trigger": self.trigger,
            "capturedCandidateBatchTurnCount": self.captured_candidate_batch_turn_count,
        }


@dataclass(frozen=True)
class OwnerTruthInterviewPendingReviewBatchInbox:
    items: tuple[OwnerTruthInterviewPendingReviewBatchInboxItem, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))
        if any(
            not isinstance(item, OwnerTruthInterviewPendingReviewBatchInboxItem)
            for item in self.items
        ):
            raise OwnerTruthInterviewPendingReviewBatchInboxError(
                "pending review batch inbox items are required"
            )
        identifiers = [item.review_batch_id for item in self.items]
        if len(identifiers) != len(set(identifiers)):
            raise OwnerTruthInterviewPendingReviewBatchInboxError(
                "pending review batch inbox cannot repeat a review batch"
            )


class OwnerTruthInterviewPendingReviewBatchInboxReadService:
    """Find actionable formal ReviewBatch handles without reading narratives."""

    def __init__(self, store: OwnerTruthInterviewPendingReviewBatchInboxStore):
        self._store = store

    def read(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthInterviewPendingReviewBatchInbox:
        conversation = OwnerTruthConversationService(
            self._store.owner_truth_conversation_repository()
        )
        with self._store.request_unit_of_work(
            correlation_id=(
                "owner-truth-interview-pending-review-batch-inbox:"
                f"{context.vault_id}"
            ),
            command_id=f"read:pending-review-batches:{context.vault_id}",
        ):
            items: list[OwnerTruthInterviewPendingReviewBatchInboxItem] = []
            for batch in conversation.list_pending_review_batches(context=context):
                item = self._item_for_current_batch(
                    batch=batch,
                    conversation=conversation,
                    context=context,
                )
                if item is not None:
                    items.append(item)
        return OwnerTruthInterviewPendingReviewBatchInbox(items=tuple(items))

    @staticmethod
    def _item_for_current_batch(
        *,
        batch: OwnerTruthInterviewReviewBatchSnapshot,
        conversation: OwnerTruthConversationService,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthInterviewPendingReviewBatchInboxItem | None:
        session = conversation.read_session(session_id=batch.session_id, context=context)
        if (
            batch.state is not InterviewReviewBatchState.PENDING_ACKNOWLEDGEMENT
            or session.pending_review_batch_id != batch.review_batch_id
            or session.thread_id != batch.thread_id
            or session.authority_epoch != batch.authority_epoch
        ):
            return None
        return OwnerTruthInterviewPendingReviewBatchInboxItem(
            review_batch_id=batch.review_batch_id,
            thread_id=batch.thread_id,
            session_id=batch.session_id,
            review_batch_version=batch.row_version,
            session_version=session.row_version,
            trigger=batch.trigger.value,
            captured_candidate_batch_turn_count=batch.captured_candidate_batch_turn_count,
        )


__all__ = [
    "OWNER_TRUTH_INTERVIEW_PENDING_REVIEW_BATCH_INBOX_SCHEMA_VERSION",
    "OwnerTruthInterviewPendingReviewBatchInbox",
    "OwnerTruthInterviewPendingReviewBatchInboxError",
    "OwnerTruthInterviewPendingReviewBatchInboxItem",
    "OwnerTruthInterviewPendingReviewBatchInboxReadService",
    "OwnerTruthInterviewPendingReviewBatchInboxStore",
]
