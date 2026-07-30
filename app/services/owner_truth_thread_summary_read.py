"""Owner-scoped read service for the conservative M0-B Thread summary projection.

The live read remains a typed composition seam for QA/map presentation. Its
separate QA route never writes; the persisted checkpoint lane reuses this exact
source assembly and remains independently default-off. Only explicit, current
Owner continuation cues may associate Threads, and they remain separate
records.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Protocol

from app.domain.owner_truth.knowledge_dimension_read import (
    OwnerTruthKnowledgeDimensionReadService,
    OwnerTruthKnowledgeDimensionReadState,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.domain.owner_truth.thread_summary import (
    OwnerTruthThreadSummaryReadResult,
    build_owner_truth_thread_summary_projection,
)


class OwnerTruthThreadSummaryReadStore(Protocol):
    def request_unit_of_work(
        self,
        *,
        correlation_id: str,
        command_id: str,
    ) -> AbstractContextManager[Any]:
        ...

    def owner_truth_memory_projection_repository(self) -> Any:
        ...

    def owner_truth_knowledge_dimension_confirmation_repository(self) -> Any:
        ...

    def owner_truth_conversation_repository(self) -> Any:
        ...

    def owner_truth_saved_continuation_cue_repository(self) -> Any:
        ...


def read_owner_truth_thread_summary_source(
    *,
    context: OwnerTruthCommandContext,
    memory_projection_repository: Any,
    confirmation_repository: Any,
    conversation_repository: Any,
    continuation_cue_repository: Any,
) -> OwnerTruthThreadSummaryReadResult:
    """Assemble one current content-free Thread summary projection.

    Keeping the source assembly here makes a persisted checkpoint use the
    exact same eligibility and association rules as the existing QA read.
    Callers own their transaction boundary; this helper never opens a second
    Unit of Work.
    """

    dimension_read = OwnerTruthKnowledgeDimensionReadService(
        memory_projection_repository,
        confirmation_repository,
    ).read(context=context)
    if dimension_read.state is not OwnerTruthKnowledgeDimensionReadState.READY:
        return OwnerTruthThreadSummaryReadResult(
            state=dimension_read.state,
            projection=None,
        )
    projection = build_owner_truth_thread_summary_projection(
        dimension_read=dimension_read,
        thread_authorities=(
            conversation_repository.list_recommendation_candidate_thread_authorities(
                context=context
            )
        ),
        continuation_cues=(
            continuation_cue_repository.list_for_recommendation(context=context)
        ),
    )
    return OwnerTruthThreadSummaryReadResult(
        state=OwnerTruthKnowledgeDimensionReadState.READY,
        projection=projection,
    )


class OwnerTruthThreadSummaryReadService:
    """Compose only current Owner Truth reads into a rebuildable thread map."""

    def __init__(self, store: OwnerTruthThreadSummaryReadStore) -> None:
        self._store = store

    def read(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthThreadSummaryReadResult:
        with self._store.request_unit_of_work(
            correlation_id=f"owner-truth-thread-summary-read-{context.vault_id}",
            command_id="ownerTruthThreadSummaryRead",
        ):
            return read_owner_truth_thread_summary_source(
                context=context,
                memory_projection_repository=self._store.owner_truth_memory_projection_repository(),
                confirmation_repository=(
                    self._store.owner_truth_knowledge_dimension_confirmation_repository()
                ),
                conversation_repository=self._store.owner_truth_conversation_repository(),
                continuation_cue_repository=(
                    self._store.owner_truth_saved_continuation_cue_repository()
                ),
            )


__all__ = [
    "OwnerTruthThreadSummaryReadService",
    "OwnerTruthThreadSummaryReadStore",
    "read_owner_truth_thread_summary_source",
]
