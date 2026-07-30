"""Read-only M0-B thread summaries and conservative cross-thread associations.

The product permits a knowledge map to associate related interview threads, but
that projection must never turn a model guess into a fact or rewrite historical
Owner Truth records.  This module therefore uses only one narrow association
rule in its first version: two current threads may be associated when each has
an explicit Owner saved-continuation cue bound to the same current, confirmed
``MemoryVersion``.

The result is a rebuildable read model.  It does not merge or archive threads,
and it does not expose memory content, transcript text, generated labels, or
provider output.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Iterable

from .contracts import OwnerTruthContractError, require_nonblank
from .conversation import OwnerTruthConversationThreadAuthoritySnapshot
from .knowledge_dimension_read import (
    OwnerTruthKnowledgeDimensionReadResult,
    OwnerTruthKnowledgeDimensionReadState,
)
from .knowledge_recommendations import (
    KnowledgeDimension,
    ServerPlannedContinuationCue,
    knowledge_dimension_facets,
)


OWNER_TRUTH_THREAD_SUMMARY_PROJECTION_SCHEMA_VERSION = "owner-truth-thread-summary-projection-v1"
OWNER_TRUTH_THREAD_SUMMARY_CHECKPOINT_SCHEMA_VERSION = (
    "owner-truth-thread-summary-checkpoint-v1"
)
THREAD_ASSOCIATION_REASON_SHARED_CONFIRMED_MEMORY_VERSION = "sharedConfirmedMemoryVersion"


class OwnerTruthThreadSummaryError(OwnerTruthContractError):
    """A thread summary projection cannot be derived safely."""


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise OwnerTruthThreadSummaryError("thread summary values must be JSON serializable") from error


def _digest(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _require_digest(value: object, *, field: str) -> str:
    normalized = require_nonblank(str(value or ""), field=field)
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise OwnerTruthThreadSummaryError(f"{field} must be a SHA-256 digest")
    return normalized


@dataclass(frozen=True)
class ThreadSummaryAnchor:
    """A content-free current MemoryVersion pointer for one Thread."""

    memory_version_id: str
    target_dimension: KnowledgeDimension
    missing_facet: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "memory_version_id",
            require_nonblank(self.memory_version_id, field="memory_version_id"),
        )
        try:
            object.__setattr__(self, "target_dimension", KnowledgeDimension(self.target_dimension))
        except (TypeError, ValueError) as error:
            raise OwnerTruthThreadSummaryError("thread summary dimension is not supported") from error
        object.__setattr__(
            self,
            "missing_facet",
            require_nonblank(self.missing_facet, field="missing_facet"),
        )
        if self.missing_facet not in knowledge_dimension_facets(self.target_dimension):
            raise OwnerTruthThreadSummaryError(
                "thread summary missing_facet is not valid for target_dimension"
            )

    def value_free_summary(self) -> dict[str, str]:
        return {
            "memoryVersionId": self.memory_version_id,
            "targetDimension": self.target_dimension.value,
            "missingFacet": self.missing_facet,
        }


@dataclass(frozen=True)
class ThreadSummary:
    """A current interview Thread without transcript or title content."""

    thread_id: str
    session_id: str
    thread_state: str
    session_state: str
    session_boundary: str
    anchors: tuple[ThreadSummaryAnchor, ...]

    def __post_init__(self) -> None:
        for field in ("thread_id", "session_id", "thread_state", "session_state", "session_boundary"):
            object.__setattr__(self, field, require_nonblank(getattr(self, field), field=field))
        normalized = tuple(self.anchors)
        if any(not isinstance(item, ThreadSummaryAnchor) for item in normalized):
            raise OwnerTruthThreadSummaryError("thread summary anchors must be typed")
        keys = tuple(
            (item.memory_version_id, item.target_dimension.value, item.missing_facet)
            for item in normalized
        )
        if len(keys) != len(set(keys)):
            raise OwnerTruthThreadSummaryError("thread summary anchors must not duplicate a cue target")
        object.__setattr__(
            self,
            "anchors",
            tuple(sorted(normalized, key=lambda item: (item.memory_version_id, item.target_dimension.value, item.missing_facet))),
        )

    def value_free_summary(self) -> dict[str, object]:
        return {
            "threadId": self.thread_id,
            "sessionId": self.session_id,
            "threadState": self.thread_state,
            "sessionState": self.session_state,
            "sessionBoundary": self.session_boundary,
            "anchorCount": len(self.anchors),
            "anchors": [item.value_free_summary() for item in self.anchors],
        }


@dataclass(frozen=True)
class ThreadAssociation:
    """A reversible association, never a destructive Thread merge."""

    association_id: str
    anchor_memory_version_id: str
    thread_ids: tuple[str, ...]
    reason_code: str = THREAD_ASSOCIATION_REASON_SHARED_CONFIRMED_MEMORY_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "association_id", require_nonblank(self.association_id, field="association_id"))
        object.__setattr__(
            self,
            "anchor_memory_version_id",
            require_nonblank(self.anchor_memory_version_id, field="anchor_memory_version_id"),
        )
        thread_ids = tuple(sorted(require_nonblank(item, field="thread_id") for item in self.thread_ids))
        if len(thread_ids) < 2 or len(thread_ids) != len(set(thread_ids)):
            raise OwnerTruthThreadSummaryError("thread association requires two distinct threads")
        object.__setattr__(self, "thread_ids", thread_ids)
        if self.reason_code != THREAD_ASSOCIATION_REASON_SHARED_CONFIRMED_MEMORY_VERSION:
            raise OwnerTruthThreadSummaryError("thread association reason is not supported")

    def value_free_summary(self) -> dict[str, object]:
        return {
            "associationId": self.association_id,
            "anchorMemoryVersionId": self.anchor_memory_version_id,
            "threadIds": list(self.thread_ids),
            "reasonCode": self.reason_code,
            "mergeState": "associatedOnly",
        }


@dataclass(frozen=True)
class OwnerTruthThreadSummaryProjection:
    """Rebuildable Phase 4A view over current eligible Threads."""

    owner_subject_id: str
    vault_id: str
    authority_epoch: int
    checkpoint: str
    policy_version: str
    source_dimension_checkpoint: str
    input_digest: str
    summaries: tuple[ThreadSummary, ...]
    associations: tuple[ThreadAssociation, ...]
    filtered_stale_cue_count: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "owner_subject_id",
            require_nonblank(self.owner_subject_id, field="owner_subject_id"),
        )
        object.__setattr__(self, "vault_id", require_nonblank(self.vault_id, field="vault_id"))
        if (
            not isinstance(self.authority_epoch, int)
            or isinstance(self.authority_epoch, bool)
            or self.authority_epoch < 0
        ):
            raise OwnerTruthThreadSummaryError("authority_epoch must be a non-negative integer")
        object.__setattr__(self, "checkpoint", _require_digest(self.checkpoint, field="checkpoint"))
        object.__setattr__(self, "policy_version", require_nonblank(self.policy_version, field="policy_version"))
        object.__setattr__(
            self,
            "source_dimension_checkpoint",
            _require_digest(
                self.source_dimension_checkpoint,
                field="source_dimension_checkpoint",
            ),
        )
        object.__setattr__(self, "input_digest", _require_digest(self.input_digest, field="input_digest"))
        summaries = tuple(self.summaries)
        associations = tuple(self.associations)
        if any(not isinstance(item, ThreadSummary) for item in summaries):
            raise OwnerTruthThreadSummaryError("thread summaries must be typed")
        if any(not isinstance(item, ThreadAssociation) for item in associations):
            raise OwnerTruthThreadSummaryError("thread associations must be typed")
        thread_ids = tuple(item.thread_id for item in summaries)
        if len(thread_ids) != len(set(thread_ids)):
            raise OwnerTruthThreadSummaryError("thread summaries must be unique")
        summarized_thread_ids = set(thread_ids)
        for association in associations:
            if not set(association.thread_ids).issubset(summarized_thread_ids):
                raise OwnerTruthThreadSummaryError("thread association references an unknown Thread")
        if (
            not isinstance(self.filtered_stale_cue_count, int)
            or isinstance(self.filtered_stale_cue_count, bool)
            or self.filtered_stale_cue_count < 0
        ):
            raise OwnerTruthThreadSummaryError("filtered_stale_cue_count must be a non-negative integer")
        object.__setattr__(self, "summaries", tuple(sorted(summaries, key=lambda item: (item.thread_id, item.session_id))))
        object.__setattr__(
            self,
            "associations",
            tuple(sorted(associations, key=lambda item: (item.anchor_memory_version_id, item.association_id))),
        )

    def value_free_summary(self) -> dict[str, object]:
        associated_thread_ids = {
            thread_id for association in self.associations for thread_id in association.thread_ids
        }
        return {
            "schemaVersion": OWNER_TRUTH_THREAD_SUMMARY_PROJECTION_SCHEMA_VERSION,
            "vaultId": self.vault_id,
            "authorityEpoch": self.authority_epoch,
            "checkpoint": self.checkpoint,
            "policyVersion": self.policy_version,
            "sourceDimensionCheckpoint": self.source_dimension_checkpoint,
            "inputDigest": self.input_digest,
            "threadCount": len(self.summaries),
            "associatedThreadCount": len(associated_thread_ids),
            "unanchoredThreadCount": sum(1 for item in self.summaries if not item.anchors),
            "associationCount": len(self.associations),
            "filteredStaleCueCount": self.filtered_stale_cue_count,
            "threads": [item.value_free_summary() for item in self.summaries],
            "associations": [item.value_free_summary() for item in self.associations],
        }


def build_owner_truth_thread_summary_associations(
    *,
    vault_id: str,
    authority_epoch: int,
    summaries: Iterable[ThreadSummary],
) -> tuple[ThreadAssociation, ...]:
    """Derive reversible associations from persisted, content-free anchors.

    The checkpoint never stores a semantic topic label or inferred fact.  It
    preserves the same conservative rule as the live builder: only two or
    more Threads with the exact same current confirmed ``MemoryVersion`` are
    associated.
    """

    vault = require_nonblank(vault_id, field="vault_id")
    if (
        not isinstance(authority_epoch, int)
        or isinstance(authority_epoch, bool)
        or authority_epoch < 0
    ):
        raise OwnerTruthThreadSummaryError("authority_epoch must be a non-negative integer")
    normalized_summaries = tuple(summaries)
    if any(not isinstance(item, ThreadSummary) for item in normalized_summaries):
        raise OwnerTruthThreadSummaryError("thread summaries must be typed")
    associated_threads_by_memory: dict[str, set[str]] = {}
    for summary in normalized_summaries:
        for anchor in summary.anchors:
            associated_threads_by_memory.setdefault(anchor.memory_version_id, set()).add(summary.thread_id)
    return tuple(
        ThreadAssociation(
            association_id=_digest(
                {
                    "schemaVersion": OWNER_TRUTH_THREAD_SUMMARY_PROJECTION_SCHEMA_VERSION,
                    "vaultId": vault,
                    "authorityEpoch": authority_epoch,
                    "anchorMemoryVersionId": memory_version_id,
                    "reasonCode": THREAD_ASSOCIATION_REASON_SHARED_CONFIRMED_MEMORY_VERSION,
                }
            ),
            anchor_memory_version_id=memory_version_id,
            thread_ids=tuple(thread_ids),
        )
        for memory_version_id, thread_ids in associated_threads_by_memory.items()
        if len(thread_ids) >= 2
    )


def build_owner_truth_thread_summary_projection_from_summaries(
    *,
    owner_subject_id: str,
    vault_id: str,
    authority_epoch: int,
    source_dimension_checkpoint: str,
    policy_version: str,
    summaries: Iterable[ThreadSummary],
    filtered_stale_cue_count: int,
) -> OwnerTruthThreadSummaryProjection:
    """Materialize a checkpoint from typed, content-free Thread summaries.

    Both the live builder and persistence reader call this exact function. A
    changed stored anchor set therefore cannot retain a hash for a current
    projection: its recomputed input digest and checkpoint will differ.
    """

    owner = require_nonblank(owner_subject_id, field="owner_subject_id")
    vault = require_nonblank(vault_id, field="vault_id")
    source_checkpoint = _require_digest(
        source_dimension_checkpoint,
        field="source_dimension_checkpoint",
    )
    policy = require_nonblank(policy_version, field="policy_version")
    normalized_summaries = tuple(summaries)
    if any(not isinstance(item, ThreadSummary) for item in normalized_summaries):
        raise OwnerTruthThreadSummaryError("thread summaries must be typed")
    if (
        not isinstance(filtered_stale_cue_count, int)
        or isinstance(filtered_stale_cue_count, bool)
        or filtered_stale_cue_count < 0
    ):
        raise OwnerTruthThreadSummaryError("filtered_stale_cue_count must be a non-negative integer")
    associations = build_owner_truth_thread_summary_associations(
        vault_id=vault,
        authority_epoch=authority_epoch,
        summaries=normalized_summaries,
    )
    sorted_summaries = tuple(sorted(normalized_summaries, key=lambda item: item.thread_id))
    input_digest = _digest(
        {
            "schemaVersion": OWNER_TRUTH_THREAD_SUMMARY_CHECKPOINT_SCHEMA_VERSION,
            "sourceDimensionCheckpoint": source_checkpoint,
            "policyVersion": policy,
            "threads": [item.value_free_summary() for item in sorted_summaries],
            "filteredStaleCueCount": filtered_stale_cue_count,
        }
    )
    checkpoint = _digest(
        {
            "schemaVersion": OWNER_TRUTH_THREAD_SUMMARY_PROJECTION_SCHEMA_VERSION,
            "dimensionCheckpoint": source_checkpoint,
            "inputDigest": input_digest,
            "threads": [item.value_free_summary() for item in sorted_summaries],
            "associations": [
                item.value_free_summary()
                for item in sorted(associations, key=lambda item: item.association_id)
            ],
            "filteredStaleCueCount": filtered_stale_cue_count,
        }
    )
    return OwnerTruthThreadSummaryProjection(
        owner_subject_id=owner,
        vault_id=vault,
        authority_epoch=authority_epoch,
        checkpoint=checkpoint,
        policy_version=policy,
        source_dimension_checkpoint=source_checkpoint,
        input_digest=input_digest,
        summaries=normalized_summaries,
        associations=associations,
        filtered_stale_cue_count=filtered_stale_cue_count,
    )


@dataclass(frozen=True)
class OwnerTruthThreadSummaryReadResult:
    """A fail-closed wrapper around the current dimension-read availability."""

    state: OwnerTruthKnowledgeDimensionReadState
    projection: OwnerTruthThreadSummaryProjection | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", OwnerTruthKnowledgeDimensionReadState(self.state))
        if self.state is OwnerTruthKnowledgeDimensionReadState.READY:
            if self.projection is None:
                raise OwnerTruthThreadSummaryError("ready thread summary read requires a projection")
        elif self.projection is not None:
            raise OwnerTruthThreadSummaryError("non-ready thread summary read must not retain a projection")

    def value_free_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {
            "schemaVersion": OWNER_TRUTH_THREAD_SUMMARY_PROJECTION_SCHEMA_VERSION,
            "state": self.state.value,
        }
        if self.projection is not None:
            summary["projection"] = self.projection.value_free_summary()
        return summary


def build_owner_truth_thread_summary_projection(
    *,
    dimension_read: OwnerTruthKnowledgeDimensionReadResult,
    thread_authorities: Iterable[OwnerTruthConversationThreadAuthoritySnapshot],
    continuation_cues: Iterable[ServerPlannedContinuationCue],
) -> OwnerTruthThreadSummaryProjection:
    """Build a conservative association view from existing private Authority.

    A cue whose MemoryVersion is no longer included in the current explicit
    dimension read is omitted.  A scope or epoch mismatch is considered a
    dependency breach and fails closed instead of silently grouping data from a
    different Owner or historical authority epoch.
    """

    if not isinstance(dimension_read, OwnerTruthKnowledgeDimensionReadResult):
        raise TypeError("dimension_read must be an OwnerTruthKnowledgeDimensionReadResult")
    if dimension_read.state is not OwnerTruthKnowledgeDimensionReadState.READY:
        raise OwnerTruthThreadSummaryError("thread summaries require a ready dimension projection")
    coverage = dimension_read.coverage
    if coverage is None:
        raise OwnerTruthThreadSummaryError("ready dimension read is missing coverage")

    threads = tuple(thread_authorities)
    thread_index: dict[str, OwnerTruthConversationThreadAuthoritySnapshot] = {}
    for thread in threads:
        if not isinstance(thread, OwnerTruthConversationThreadAuthoritySnapshot):
            raise TypeError("thread_authorities must contain OwnerTruthConversationThreadAuthoritySnapshot")
        if (
            thread.owner_subject_id != dimension_read.owner_subject_id
            or thread.vault_id != dimension_read.vault_id
            or thread.authority_epoch != dimension_read.authority_epoch
        ):
            raise OwnerTruthThreadSummaryError("thread authority does not match current Owner dimension scope")
        if thread.thread_id in thread_index:
            raise OwnerTruthThreadSummaryError("thread authorities must not duplicate a Thread")
        thread_index[thread.thread_id] = thread

    current_memory_version_ids = frozenset(dimension_read.included_memory_version_ids)
    anchors_by_thread: dict[str, list[ThreadSummaryAnchor]] = {
        thread_id: [] for thread_id in thread_index
    }
    stale_cue_count = 0
    for cue in continuation_cues:
        if not isinstance(cue, ServerPlannedContinuationCue):
            raise TypeError("continuation_cues must contain ServerPlannedContinuationCue")
        if (
            cue.owner_subject_id != dimension_read.owner_subject_id
            or cue.vault_id != dimension_read.vault_id
            or cue.authority_epoch != dimension_read.authority_epoch
        ):
            raise OwnerTruthThreadSummaryError("continuation cue does not match current Owner dimension scope")
        if cue.thread_id not in thread_index or cue.memory_version_id not in current_memory_version_ids:
            stale_cue_count += 1
            continue
        anchors_by_thread[cue.thread_id].append(
            ThreadSummaryAnchor(
                memory_version_id=cue.memory_version_id,
                target_dimension=cue.target_dimension,
                missing_facet=cue.missing_facet,
            )
        )

    summaries = tuple(
        ThreadSummary(
            thread_id=thread.thread_id,
            session_id=thread.session_id,
            thread_state=thread.state.value,
            session_state=thread.session_state.value,
            session_boundary=thread.session_boundary.value,
            anchors=tuple(anchors_by_thread[thread.thread_id]),
        )
        for thread in thread_index.values()
    )
    return build_owner_truth_thread_summary_projection_from_summaries(
        owner_subject_id=dimension_read.owner_subject_id,
        vault_id=dimension_read.vault_id,
        authority_epoch=dimension_read.authority_epoch,
        source_dimension_checkpoint=dimension_read.checkpoint,
        policy_version=coverage.policy_version,
        summaries=summaries,
        filtered_stale_cue_count=stale_cue_count,
    )


__all__ = [
    "OWNER_TRUTH_THREAD_SUMMARY_PROJECTION_SCHEMA_VERSION",
    "OWNER_TRUTH_THREAD_SUMMARY_CHECKPOINT_SCHEMA_VERSION",
    "THREAD_ASSOCIATION_REASON_SHARED_CONFIRMED_MEMORY_VERSION",
    "OwnerTruthThreadSummaryError",
    "OwnerTruthThreadSummaryProjection",
    "OwnerTruthThreadSummaryReadResult",
    "ThreadAssociation",
    "ThreadSummary",
    "ThreadSummaryAnchor",
    "build_owner_truth_thread_summary_associations",
    "build_owner_truth_thread_summary_projection_from_summaries",
    "build_owner_truth_thread_summary_projection",
]
