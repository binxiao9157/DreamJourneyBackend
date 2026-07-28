"""Owner-scoped read service for the conservative M0-B Thread summary projection.

This service intentionally has no HTTP route or writer in this slice.  It is a
typed composition seam for later QA/map presentation and keeps the association
rule server-side: only explicit, current Owner continuation cues may associate
Threads, and they remain separate records.
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
            dimension_read = OwnerTruthKnowledgeDimensionReadService(
                self._store.owner_truth_memory_projection_repository(),
                self._store.owner_truth_knowledge_dimension_confirmation_repository(),
            ).read(context=context)
            if dimension_read.state is not OwnerTruthKnowledgeDimensionReadState.READY:
                return OwnerTruthThreadSummaryReadResult(
                    state=dimension_read.state,
                    projection=None,
                )
            projection = build_owner_truth_thread_summary_projection(
                dimension_read=dimension_read,
                thread_authorities=(
                    self._store.owner_truth_conversation_repository().list_recommendation_candidate_thread_authorities(
                        context=context
                    )
                ),
                continuation_cues=(
                    self._store.owner_truth_saved_continuation_cue_repository().list_for_recommendation(
                        context=context
                    )
                ),
            )
            return OwnerTruthThreadSummaryReadResult(
                state=OwnerTruthKnowledgeDimensionReadState.READY,
                projection=projection,
            )


__all__ = [
    "OwnerTruthThreadSummaryReadService",
    "OwnerTruthThreadSummaryReadStore",
]
