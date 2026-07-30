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
    OWNER_TRUTH_MEMORY_SEARCH_RETRIEVAL_MODE,
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


OWNER_TRUTH_MEMORY_SEARCH_PRESENTATION_SCHEMA_VERSION = (
    "owner-truth-memory-search-presentation-response-v1"
)
OWNER_TRUTH_MEMORY_SEARCH_PRESENTATION_MAX_RESULTS = 8
OWNER_TRUTH_MEMORY_SEARCH_PRESENTATION_MAX_PREVIEW_CHARACTERS = 180


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


def memory_search_presentation(
    result: OwnerTruthMemorySearchReadResult,
) -> dict[str, object]:
    """Return the default-off product read without diagnostic identifiers.

    This deliberately remains a deterministic-text fallback until a separate
    semantic-ranker capability is approved.  The Owner can read a bounded
    preview of their own confirmed MemoryVersion, but never a Source, Candidate,
    internal citation ID, checkpoint, authority epoch, query plan, or policy.
    """

    if not isinstance(result, OwnerTruthMemorySearchReadResult):
        raise TypeError("result must be an OwnerTruthMemorySearchReadResult")

    presentation: dict[str, object] = {
        "state": result.state,
        "retrievalMode": OWNER_TRUTH_MEMORY_SEARCH_RETRIEVAL_MODE,
        "resultCount": 0,
        "results": [],
    }
    if result.state != "ready":
        return presentation

    if result.query_plan is None:
        raise OwnerTruthMemorySearchReadError("ready search presentation requires a QueryPlan")

    rendered_results = [
        _presentation_result(
            hit=hit,
            normalized_query=result.query_plan.normalized_query,
        )
        for hit in result.hits[:OWNER_TRUTH_MEMORY_SEARCH_PRESENTATION_MAX_RESULTS]
    ]
    presentation["resultCount"] = len(rendered_results)
    presentation["results"] = rendered_results
    return presentation


def _presentation_result(
    *,
    hit: OwnerTruthMemorySearchHit,
    normalized_query: str,
) -> dict[str, object]:
    preview = _presentation_preview(
        hit.document.search_text,
        normalized_query=normalized_query,
    )
    if not preview:
        raise OwnerTruthMemorySearchReadError("search presentation preview is empty")
    return {
        "rank": hit.rank,
        "preview": preview,
        "memoryKind": hit.document.memory_kind,
        "perspectiveType": hit.document.perspective_type,
        "sensitivity": hit.document.sensitivity,
        "matchKind": hit.match_kind,
    }


def _presentation_preview(text: str, *, normalized_query: str) -> str:
    normalized_text = " ".join(str(text).split())
    if not normalized_text:
        return ""
    match_index = normalized_text.find(normalized_query)
    if match_index < 0:
        match_index = 0
    start = max(0, match_index - 56)
    end = min(
        len(normalized_text),
        max(
            start + OWNER_TRUTH_MEMORY_SEARCH_PRESENTATION_MAX_PREVIEW_CHARACTERS,
            match_index + len(normalized_query) + 96,
        ),
    )
    preview = normalized_text[start:end].strip()
    if start > 0:
        preview = f"…{preview}"
    if end < len(normalized_text):
        preview = f"{preview}…"
    return preview


__all__ = [
    "OWNER_TRUTH_MEMORY_SEARCH_PRESENTATION_SCHEMA_VERSION",
    "OwnerTruthMemorySearchReadAccessDenied",
    "OwnerTruthMemorySearchReadService",
    "OwnerTruthMemorySearchReadStore",
    "memory_search_presentation",
]
