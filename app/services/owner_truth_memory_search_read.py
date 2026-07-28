"""Owner-scoped persisted SearchDocument read service for Phase 4C QA.

The service intentionally leaves embedding/vector/provider work disabled. It
reads only a current, checkpoint-bound SearchDocument projection and runs the
deterministic text fallback over that private derived index.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Protocol

from app.domain.owner_truth.candidate_decisions import OwnerTruthCandidateReviewAccessDenied
from app.domain.owner_truth.memory_projection import OwnerTruthMemoryProjectionAccessDenied
from app.domain.owner_truth.search_documents import (
    OWNER_TRUTH_MEMORY_SEARCH_MAX_LIMIT,
    OwnerTruthMemorySearchReadError,
    OwnerTruthMemorySearchReadResult,
    OwnerTruthSearchDocumentProjectionError,
    build_owner_truth_memory_search_query_plan,
    search_owner_truth_documents,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_memory_search_projection import (
    OwnerTruthMemorySearchProjectionAccessDenied,
)


class OwnerTruthMemorySearchReadAccessDenied(OwnerTruthMemorySearchReadError):
    """The actor cannot read private SearchDocuments for this Owner Vault."""


class OwnerTruthMemorySearchReadStore(Protocol):
    def request_unit_of_work(
        self,
        *,
        correlation_id: str,
        command_id: str,
    ) -> AbstractContextManager[Any]:
        ...

    def owner_truth_memory_search_document_projection_repository(self) -> Any:
        ...


class OwnerTruthMemorySearchReadService:
    """Query the current Owner's persisted, rebuildable SearchDocuments."""

    def __init__(self, store: OwnerTruthMemorySearchReadStore) -> None:
        self._store = store

    def read(
        self,
        *,
        context: OwnerTruthCommandContext,
        query: str,
        limit: int = OWNER_TRUTH_MEMORY_SEARCH_MAX_LIMIT,
    ) -> OwnerTruthMemorySearchReadResult:
        if not isinstance(context, OwnerTruthCommandContext):
            raise OwnerTruthMemorySearchReadAccessDenied("owner truth command context is required")
        if context.actor_subject_id != context.owner_subject_id:
            raise OwnerTruthMemorySearchReadAccessDenied(
                "only the Vault Owner may search confirmed memory"
            )
        try:
            with self._store.request_unit_of_work(
                correlation_id=f"owner-truth-memory-search-read-{context.vault_id}",
                command_id="ownerTruthMemorySearchRead",
            ):
                projection = (
                    self._store.owner_truth_memory_search_document_projection_repository().read(
                        context=context
                    )
                )
                if projection is None:
                    return OwnerTruthMemorySearchReadResult(
                        state="rebuilding",
                        projection=None,
                        query_plan=None,
                        hits=(),
                    )
                query_plan = build_owner_truth_memory_search_query_plan(
                    projection=projection,
                    query=query,
                    limit=limit,
                )
                return OwnerTruthMemorySearchReadResult(
                    state="ready",
                    projection=projection,
                    query_plan=query_plan,
                    hits=search_owner_truth_documents(
                        projection=projection,
                        query_plan=query_plan,
                    ),
                )
        except (
            OwnerTruthMemoryProjectionAccessDenied,
            OwnerTruthCandidateReviewAccessDenied,
            OwnerTruthMemorySearchProjectionAccessDenied,
        ) as error:
            raise OwnerTruthMemorySearchReadAccessDenied(str(error)) from error
        except OwnerTruthSearchDocumentProjectionError as error:
            raise OwnerTruthMemorySearchReadError(str(error)) from error


__all__ = [
    "OwnerTruthMemorySearchReadAccessDenied",
    "OwnerTruthMemorySearchReadService",
    "OwnerTruthMemorySearchReadStore",
]
