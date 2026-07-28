"""Owner-scoped SearchDocument read service for Phase 4C QA.

The service intentionally leaves embedding/vector/provider work disabled.  It
proves the first safe retrieval step: current, confirmed MemoryVersion data is
bound to the requesting Owner and converted to an ephemeral SearchDocument set
before a deterministic text fallback runs.
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
    build_owner_truth_search_document_projection,
    search_owner_truth_documents,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext


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

    def owner_truth_memory_projection_repository(self) -> Any:
        ...


class OwnerTruthMemorySearchReadService:
    """Build and query ephemeral SearchDocuments from current Owner authority."""

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
                memory_projection = self._store.owner_truth_memory_projection_repository().read(
                    context=context
                )
                projection = build_owner_truth_search_document_projection(
                    memory_projection=memory_projection
                )
                if projection is None:
                    return OwnerTruthMemorySearchReadResult(
                        state=str(memory_projection.get("state") or "rebuilding"),
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
        ) as error:
            raise OwnerTruthMemorySearchReadAccessDenied(str(error)) from error
        except OwnerTruthSearchDocumentProjectionError as error:
            raise OwnerTruthMemorySearchReadError(str(error)) from error


__all__ = [
    "OwnerTruthMemorySearchReadAccessDenied",
    "OwnerTruthMemorySearchReadService",
    "OwnerTruthMemorySearchReadStore",
]
