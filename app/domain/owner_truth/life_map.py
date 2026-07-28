"""Read-only Phase 4C life-map projection over current Owner-confirmed state.

The life map is deliberately a rebuildable navigation projection, not a second
memory store.  It composes the six stable knowledge dimensions with the
conservative Thread summary projection.  No memory text, transcript, Candidate
payload, provider output, source identifier, or inferred title is retained in
the result.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Iterable

from .contracts import OwnerTruthContractError, require_nonblank
from .knowledge_dimension_read import (
    OwnerTruthKnowledgeDimensionReadResult,
    OwnerTruthKnowledgeDimensionReadState,
)
from .knowledge_recommendations import KnowledgeDimension
from .thread_summary import (
    OwnerTruthThreadSummaryProjection,
    OwnerTruthThreadSummaryReadResult,
)


OWNER_TRUTH_LIFE_MAP_PROJECTION_SCHEMA_VERSION = "owner-truth-life-map-projection-v1"


class OwnerTruthLifeMapError(OwnerTruthContractError):
    """The current Owner life-map projection cannot be derived safely."""


@dataclass(frozen=True)
class LifeMapDimensionNode:
    """One stable knowledge dimension without memory content or a completion rate."""

    dimension: KnowledgeDimension | str
    evidence_count: int
    covered_facet_count: int
    missing_facet_count: int
    anchored_thread_count: int

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "dimension", KnowledgeDimension(self.dimension))
        except (TypeError, ValueError) as error:
            raise OwnerTruthLifeMapError("life-map dimension is not supported") from error
        for field_name in (
            "evidence_count",
            "covered_facet_count",
            "missing_facet_count",
            "anchored_thread_count",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise OwnerTruthLifeMapError(f"{field_name} must be a non-negative integer")

    def value_free_summary(self) -> dict[str, object]:
        return {
            "dimension": self.dimension.value,
            "evidenceCount": self.evidence_count,
            "coveredFacetCount": self.covered_facet_count,
            "missingFacetCount": self.missing_facet_count,
            "anchoredThreadCount": self.anchored_thread_count,
        }


@dataclass(frozen=True)
class LifeMapThreadNode:
    """A thread navigation node without title, transcript, or anchor IDs."""

    thread_id: str
    thread_state: str
    session_state: str
    session_boundary: str
    dimensions: tuple[KnowledgeDimension | str, ...]
    association_count: int

    def __post_init__(self) -> None:
        for field_name in ("thread_id", "thread_state", "session_state", "session_boundary"):
            object.__setattr__(
                self,
                field_name,
                require_nonblank(getattr(self, field_name), field=field_name),
            )
        try:
            dimensions = tuple(KnowledgeDimension(item) for item in self.dimensions)
        except (TypeError, ValueError) as error:
            raise OwnerTruthLifeMapError("life-map thread dimension is not supported") from error
        if len(dimensions) != len(set(dimensions)):
            raise OwnerTruthLifeMapError("life-map thread dimensions must not duplicate")
        stable_order = {dimension: index for index, dimension in enumerate(KnowledgeDimension)}
        object.__setattr__(self, "dimensions", tuple(sorted(dimensions, key=stable_order.__getitem__)))
        if (
            not isinstance(self.association_count, int)
            or isinstance(self.association_count, bool)
            or self.association_count < 0
        ):
            raise OwnerTruthLifeMapError("association_count must be a non-negative integer")

    def value_free_summary(self) -> dict[str, object]:
        return {
            "threadId": self.thread_id,
            "threadState": self.thread_state,
            "sessionState": self.session_state,
            "sessionBoundary": self.session_boundary,
            "dimensionKeys": [item.value for item in self.dimensions],
            "associationCount": self.association_count,
        }


@dataclass(frozen=True)
class LifeMapAssociation:
    """A reversible Thread association stripped of its memory-version anchor."""

    association_id: str
    thread_ids: tuple[str, ...]
    reason_code: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "association_id",
            require_nonblank(self.association_id, field="association_id"),
        )
        thread_ids = tuple(sorted(require_nonblank(item, field="thread_id") for item in self.thread_ids))
        if len(thread_ids) < 2 or len(thread_ids) != len(set(thread_ids)):
            raise OwnerTruthLifeMapError("life-map association requires two distinct threads")
        object.__setattr__(self, "thread_ids", thread_ids)
        object.__setattr__(self, "reason_code", require_nonblank(self.reason_code, field="reason_code"))

    def value_free_summary(self) -> dict[str, object]:
        return {
            "associationId": self.association_id,
            "threadIds": list(self.thread_ids),
            "reasonCode": self.reason_code,
            "mergeState": "associatedOnly",
        }


@dataclass(frozen=True)
class OwnerTruthLifeMapProjection:
    """Current, rebuildable Phase 4C map for one Owner/Vault/authority epoch."""

    owner_subject_id: str
    vault_id: str
    authority_epoch: int
    checkpoint: str
    policy_version: str
    dimensions: tuple[LifeMapDimensionNode, ...]
    threads: tuple[LifeMapThreadNode, ...]
    associations: tuple[LifeMapAssociation, ...]
    unmapped_thread_count: int
    filtered_stale_cue_count: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "owner_subject_id",
            require_nonblank(self.owner_subject_id, field="owner_subject_id"),
        )
        object.__setattr__(self, "vault_id", require_nonblank(self.vault_id, field="vault_id"))
        object.__setattr__(self, "checkpoint", require_nonblank(self.checkpoint, field="checkpoint"))
        object.__setattr__(self, "policy_version", require_nonblank(self.policy_version, field="policy_version"))
        if (
            not isinstance(self.authority_epoch, int)
            or isinstance(self.authority_epoch, bool)
            or self.authority_epoch < 0
        ):
            raise OwnerTruthLifeMapError("authority_epoch must be a non-negative integer")
        dimensions = tuple(self.dimensions)
        threads = tuple(self.threads)
        associations = tuple(self.associations)
        if any(not isinstance(item, LifeMapDimensionNode) for item in dimensions):
            raise OwnerTruthLifeMapError("life-map dimensions must be typed")
        if tuple(item.dimension for item in dimensions) != tuple(KnowledgeDimension):
            raise OwnerTruthLifeMapError("life-map dimensions must use stable policy order")
        if any(not isinstance(item, LifeMapThreadNode) for item in threads):
            raise OwnerTruthLifeMapError("life-map threads must be typed")
        if any(not isinstance(item, LifeMapAssociation) for item in associations):
            raise OwnerTruthLifeMapError("life-map associations must be typed")
        thread_ids = tuple(item.thread_id for item in threads)
        if len(thread_ids) != len(set(thread_ids)):
            raise OwnerTruthLifeMapError("life-map threads must be unique")
        known_thread_ids = set(thread_ids)
        for association in associations:
            if not set(association.thread_ids).issubset(known_thread_ids):
                raise OwnerTruthLifeMapError("life-map association references an unknown thread")
        for field_name in ("unmapped_thread_count", "filtered_stale_cue_count"):
            value = getattr(self, field_name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise OwnerTruthLifeMapError(f"{field_name} must be a non-negative integer")
        if self.unmapped_thread_count != sum(1 for item in threads if not item.dimensions):
            raise OwnerTruthLifeMapError("unmapped_thread_count must match life-map thread nodes")
        object.__setattr__(self, "dimensions", dimensions)
        object.__setattr__(self, "threads", tuple(sorted(threads, key=lambda item: item.thread_id)))
        object.__setattr__(
            self,
            "associations",
            tuple(sorted(associations, key=lambda item: item.association_id)),
        )

    def value_free_summary(self) -> dict[str, object]:
        return {
            "schemaVersion": OWNER_TRUTH_LIFE_MAP_PROJECTION_SCHEMA_VERSION,
            "vaultId": self.vault_id,
            "authorityEpoch": self.authority_epoch,
            "checkpoint": self.checkpoint,
            "policyVersion": self.policy_version,
            "dimensionCount": len(self.dimensions),
            "threadCount": len(self.threads),
            "associationCount": len(self.associations),
            "unmappedThreadCount": self.unmapped_thread_count,
            "filteredStaleCueCount": self.filtered_stale_cue_count,
            "dimensions": [item.value_free_summary() for item in self.dimensions],
            "threads": [item.value_free_summary() for item in self.threads],
            "associations": [item.value_free_summary() for item in self.associations],
        }


@dataclass(frozen=True)
class OwnerTruthLifeMapReadResult:
    """Fail-closed read wrapper that never retains a stale projection."""

    state: OwnerTruthKnowledgeDimensionReadState | str
    projection: OwnerTruthLifeMapProjection | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", OwnerTruthKnowledgeDimensionReadState(self.state))
        if self.state is OwnerTruthKnowledgeDimensionReadState.READY:
            if self.projection is None:
                raise OwnerTruthLifeMapError("ready life-map read requires a projection")
        elif self.projection is not None:
            raise OwnerTruthLifeMapError("non-ready life-map read must not retain a projection")

    def value_free_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {
            "schemaVersion": OWNER_TRUTH_LIFE_MAP_PROJECTION_SCHEMA_VERSION,
            "state": self.state.value,
        }
        if self.projection is not None:
            summary["projection"] = self.projection.value_free_summary()
        return summary


def build_owner_truth_life_map_projection(
    *,
    dimension_read: OwnerTruthKnowledgeDimensionReadResult,
    thread_summary: OwnerTruthThreadSummaryProjection,
) -> OwnerTruthLifeMapProjection:
    """Compose current confirmed dimensions and conservative Thread associations.

    The caller supplies two projections read from the same Owner/Vault authority
    scope.  Any mismatch is an authority failure, not an opportunity to merge
    a stale map with a fresh dimension result.
    """

    if not isinstance(dimension_read, OwnerTruthKnowledgeDimensionReadResult):
        raise TypeError("dimension_read must be an OwnerTruthKnowledgeDimensionReadResult")
    if not isinstance(thread_summary, OwnerTruthThreadSummaryProjection):
        raise TypeError("thread_summary must be an OwnerTruthThreadSummaryProjection")
    if dimension_read.state is not OwnerTruthKnowledgeDimensionReadState.READY:
        raise OwnerTruthLifeMapError("life-map projection requires ready dimensions")
    coverage = dimension_read.coverage
    if coverage is None or dimension_read.checkpoint is None:
        raise OwnerTruthLifeMapError("ready dimensions are missing coverage or checkpoint")
    if (
        thread_summary.owner_subject_id != dimension_read.owner_subject_id
        or thread_summary.vault_id != dimension_read.vault_id
        or thread_summary.authority_epoch != dimension_read.authority_epoch
        or thread_summary.policy_version != coverage.policy_version
    ):
        raise OwnerTruthLifeMapError("thread summary does not match current life-map scope")

    association_count_by_thread: dict[str, int] = {
        item.thread_id: 0 for item in thread_summary.summaries
    }
    associations = tuple(
        LifeMapAssociation(
            association_id=item.association_id,
            thread_ids=item.thread_ids,
            reason_code=item.reason_code,
        )
        for item in thread_summary.associations
    )
    for association in associations:
        for thread_id in association.thread_ids:
            association_count_by_thread[thread_id] += 1

    threads = tuple(
        LifeMapThreadNode(
            thread_id=item.thread_id,
            thread_state=item.thread_state,
            session_state=item.session_state,
            session_boundary=item.session_boundary,
            dimensions=tuple({anchor.target_dimension for anchor in item.anchors}),
            association_count=association_count_by_thread[item.thread_id],
        )
        for item in thread_summary.summaries
    )
    anchored_threads_by_dimension = {
        dimension: sum(1 for thread in threads if dimension in thread.dimensions)
        for dimension in KnowledgeDimension
    }
    dimensions = tuple(
        LifeMapDimensionNode(
            dimension=item.dimension,
            evidence_count=len(item.memory_version_ids),
            covered_facet_count=len(item.covered_facets),
            missing_facet_count=item.missing_facet_count,
            anchored_thread_count=anchored_threads_by_dimension[item.dimension],
        )
        for item in coverage.coverage
    )
    checkpoint = _digest(
        {
            "schemaVersion": OWNER_TRUTH_LIFE_MAP_PROJECTION_SCHEMA_VERSION,
            "dimensionCheckpoint": dimension_read.checkpoint,
            "threadSummaryCheckpoint": thread_summary.checkpoint,
            "dimensions": [item.value_free_summary() for item in dimensions],
            "threads": [item.value_free_summary() for item in sorted(threads, key=lambda item: item.thread_id)],
            "associations": [
                item.value_free_summary()
                for item in sorted(associations, key=lambda item: item.association_id)
            ],
        }
    )
    return OwnerTruthLifeMapProjection(
        owner_subject_id=dimension_read.owner_subject_id,
        vault_id=dimension_read.vault_id,
        authority_epoch=dimension_read.authority_epoch,
        checkpoint=checkpoint,
        policy_version=coverage.policy_version,
        dimensions=dimensions,
        threads=threads,
        associations=associations,
        unmapped_thread_count=sum(1 for item in threads if not item.dimensions),
        filtered_stale_cue_count=thread_summary.filtered_stale_cue_count,
    )


def _digest(value: object) -> str:
    try:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise OwnerTruthLifeMapError("life-map values must be JSON serializable") from error
    return sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "LifeMapAssociation",
    "LifeMapDimensionNode",
    "LifeMapThreadNode",
    "OWNER_TRUTH_LIFE_MAP_PROJECTION_SCHEMA_VERSION",
    "OwnerTruthLifeMapError",
    "OwnerTruthLifeMapProjection",
    "OwnerTruthLifeMapReadResult",
    "build_owner_truth_life_map_projection",
]
