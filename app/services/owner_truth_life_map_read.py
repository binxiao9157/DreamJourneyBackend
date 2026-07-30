"""Owner-scoped read service for the default-off Phase 4C life-map projection."""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Protocol

from app.domain.owner_truth.knowledge_dimension_read import (
    OwnerTruthKnowledgeDimensionReadService,
    OwnerTruthKnowledgeDimensionReadState,
)
from app.domain.owner_truth.memory_projection import OwnerTruthMemoryProjectionAccessDenied
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.domain.owner_truth.thread_summary import (
    OwnerTruthThreadSummaryError,
    build_owner_truth_thread_summary_projection,
)
from app.domain.owner_truth.life_map import (
    OwnerTruthLifeMapError,
    OwnerTruthLifeMapReadResult,
    build_owner_truth_life_map_projection,
)


OWNER_TRUTH_LIFE_MAP_PRESENTATION_SCHEMA_VERSION = (
    "owner-truth-life-map-presentation-response-v1"
)


class OwnerTruthLifeMapReadAccessDenied(OwnerTruthLifeMapError):
    """The caller cannot read this Owner/Vault life-map projection."""


class OwnerTruthLifeMapReadStore(Protocol):
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


class OwnerTruthLifeMapReadService:
    """Build a current map from confirmed knowledge and explicit Thread anchors."""

    def __init__(self, store: OwnerTruthLifeMapReadStore) -> None:
        self._store = store

    def read(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthLifeMapReadResult:
        if not isinstance(context, OwnerTruthCommandContext):
            raise OwnerTruthLifeMapReadAccessDenied("owner truth command context is required")
        if context.actor_subject_id != context.owner_subject_id:
            raise OwnerTruthLifeMapReadAccessDenied("only the Vault Owner may read a life map")
        try:
            with self._store.request_unit_of_work(
                correlation_id=f"owner-truth-life-map-read-{context.vault_id}",
                command_id="ownerTruthLifeMapRead",
            ):
                dimension_read = OwnerTruthKnowledgeDimensionReadService(
                    self._store.owner_truth_memory_projection_repository(),
                    self._store.owner_truth_knowledge_dimension_confirmation_repository(),
                ).read(context=context)
                if dimension_read.state is not OwnerTruthKnowledgeDimensionReadState.READY:
                    return OwnerTruthLifeMapReadResult(
                        state=dimension_read.state,
                        projection=None,
                    )
                thread_summary = build_owner_truth_thread_summary_projection(
                    dimension_read=dimension_read,
                    thread_authorities=(
                        self._store.owner_truth_conversation_repository()
                        .list_recommendation_candidate_thread_authorities(context=context)
                    ),
                    continuation_cues=(
                        self._store.owner_truth_saved_continuation_cue_repository()
                        .list_for_recommendation(context=context)
                    ),
                )
                return OwnerTruthLifeMapReadResult(
                    state=OwnerTruthKnowledgeDimensionReadState.READY,
                    projection=build_owner_truth_life_map_projection(
                        dimension_read=dimension_read,
                        thread_summary=thread_summary,
                    ),
                )
        except OwnerTruthMemoryProjectionAccessDenied as error:
            raise OwnerTruthLifeMapReadAccessDenied(str(error)) from error
        except OwnerTruthThreadSummaryError as error:
            raise OwnerTruthLifeMapError(str(error)) from error


def life_map_presentation(result: OwnerTruthLifeMapReadResult) -> dict[str, object]:
    """Return the display-safe subset used by the default-off product route.

    The QA read exposes a value-free diagnostic projection with opaque internal
    thread/association identifiers. The product surface needs neither those
    identifiers nor checkpoints/policy metadata, so it receives only stable
    dimension counts and aggregate navigation counts. This remains a read-only
    projection, not a second memory store or a completion score.
    """

    if not isinstance(result, OwnerTruthLifeMapReadResult):
        raise OwnerTruthLifeMapError("life-map presentation requires a typed result")

    presentation: dict[str, object] = {
        "state": result.state.value,
        "storyCount": 0,
        "associatedStoryCount": 0,
        "dimensions": [],
    }
    projection = result.projection
    if projection is None:
        return presentation

    presentation["storyCount"] = len(projection.threads)
    presentation["associatedStoryCount"] = len(projection.associations)
    presentation["dimensions"] = [
        {
            "dimension": item.dimension.value,
            "confirmedEvidenceCount": item.evidence_count,
            "coveredFacetCount": item.covered_facet_count,
            "unfilledFacetCount": item.missing_facet_count,
            "relatedStoryCount": item.anchored_thread_count,
        }
        for item in projection.dimensions
    ]
    return presentation


__all__ = [
    "OWNER_TRUTH_LIFE_MAP_PRESENTATION_SCHEMA_VERSION",
    "OwnerTruthLifeMapReadAccessDenied",
    "OwnerTruthLifeMapReadService",
    "OwnerTruthLifeMapReadStore",
    "life_map_presentation",
]
