"""Pure state transitions for the narrative writing domain."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Generic, Mapping, TypeVar

from app.domain.narrative.contracts import (
    BookProjectState,
    NarrativeArtifactState,
    NarrativeJobState,
)


class NarrativeTransitionCode(str, Enum):
    INVALID_PROJECT_TRANSITION = "invalid_project_transition"
    INVALID_ARTIFACT_TRANSITION = "invalid_artifact_transition"
    INVALID_JOB_TRANSITION = "invalid_job_transition"


class NarrativeTransitionError(ValueError):
    def __init__(self, code: NarrativeTransitionCode, source: str, target: str) -> None:
        super().__init__(f"{source} cannot transition to {target}")
        self.code = code
        self.source = source
        self.target = target


StateT = TypeVar("StateT", bound=Enum)


@dataclass(frozen=True)
class TransitionTable(Generic[StateT]):
    transitions: Mapping[StateT, frozenset[StateT]]
    error_code: NarrativeTransitionCode

    def allows(self, source: StateT, target: StateT) -> bool:
        return source == target or target in self.transitions.get(source, frozenset())

    def require(self, source: StateT, target: StateT) -> None:
        if not self.allows(source, target):
            raise NarrativeTransitionError(self.error_code, source.value, target.value)


def _states(*values: StateT) -> frozenset[StateT]:
    return frozenset(values)


PROJECT_TRANSITIONS = TransitionTable(
    transitions={
        BookProjectState.NOT_STARTED: _states(
            BookProjectState.CHECKING_READINESS,
            BookProjectState.PAUSED,
            BookProjectState.ARCHIVED,
            BookProjectState.DELETED,
        ),
        BookProjectState.CHECKING_READINESS: _states(
            BookProjectState.NEEDS_MORE_MEMORY,
            BookProjectState.READY_FOR_CONFIRMATION,
            BookProjectState.PAUSED,
            BookProjectState.DISPUTED,
            BookProjectState.SUSPENDED,
        ),
        BookProjectState.NEEDS_MORE_MEMORY: _states(
            BookProjectState.CHECKING_READINESS,
            BookProjectState.READY_FOR_CONFIRMATION,
            BookProjectState.PAUSED,
        ),
        BookProjectState.READY_FOR_CONFIRMATION: _states(
            BookProjectState.GENERATING_AUDITIONS,
            BookProjectState.NEEDS_MORE_MEMORY,
            BookProjectState.PAUSED,
        ),
        BookProjectState.GENERATING_AUDITIONS: _states(
            BookProjectState.AUDITIONS_READY,
            BookProjectState.NEEDS_MORE_MEMORY,
            BookProjectState.READY_FOR_CONFIRMATION,
        ),
        BookProjectState.AUDITIONS_READY: _states(
            BookProjectState.GENERATING_GOLDEN_SAMPLE,
            BookProjectState.GENERATING_AUDITIONS,
            BookProjectState.PAUSED,
        ),
        BookProjectState.GENERATING_GOLDEN_SAMPLE: _states(
            BookProjectState.GOLDEN_SAMPLE_REVIEW,
            BookProjectState.AUDITIONS_READY,
        ),
        BookProjectState.GOLDEN_SAMPLE_REVIEW: _states(
            BookProjectState.GENERATING_GOLDEN_SAMPLE,
            BookProjectState.AUDITIONS_READY,
            BookProjectState.TONE_CONFIRMED,
            BookProjectState.PAUSED,
        ),
        BookProjectState.TONE_CONFIRMED: _states(
            BookProjectState.OUTLINE_REVIEW,
            BookProjectState.PAUSED,
        ),
        BookProjectState.OUTLINE_REVIEW: _states(
            BookProjectState.OUTLINE_REVIEW,
            BookProjectState.WRITING,
            BookProjectState.PAUSED,
        ),
        BookProjectState.WRITING: _states(
            BookProjectState.UPDATE_AVAILABLE,
            BookProjectState.PAUSED,
            BookProjectState.ARCHIVED,
        ),
        BookProjectState.UPDATE_AVAILABLE: _states(
            BookProjectState.WRITING,
            BookProjectState.PAUSED,
            BookProjectState.ARCHIVED,
        ),
        BookProjectState.PAUSED: _states(
            BookProjectState.CHECKING_READINESS,
            BookProjectState.NEEDS_MORE_MEMORY,
            BookProjectState.READY_FOR_CONFIRMATION,
            BookProjectState.AUDITIONS_READY,
            BookProjectState.GOLDEN_SAMPLE_REVIEW,
            BookProjectState.TONE_CONFIRMED,
            BookProjectState.OUTLINE_REVIEW,
            BookProjectState.WRITING,
            BookProjectState.UPDATE_AVAILABLE,
            BookProjectState.ARCHIVED,
            BookProjectState.DELETED,
        ),
        BookProjectState.DISPUTED: _states(
            BookProjectState.SUSPENDED,
            BookProjectState.PAUSED,
            BookProjectState.DELETED,
        ),
        BookProjectState.SUSPENDED: _states(
            BookProjectState.PAUSED,
            BookProjectState.DELETED,
        ),
        BookProjectState.ARCHIVED: _states(BookProjectState.DELETED),
        BookProjectState.DELETED: frozenset(),
    },
    error_code=NarrativeTransitionCode.INVALID_PROJECT_TRANSITION,
)


ARTIFACT_TRANSITIONS = TransitionTable(
    transitions={
        NarrativeArtifactState.DRAFT: _states(
            NarrativeArtifactState.READY_FOR_REVIEW,
            NarrativeArtifactState.SUPERSEDED,
        ),
        NarrativeArtifactState.READY_FOR_REVIEW: _states(
            NarrativeArtifactState.CONFIRMED,
            NarrativeArtifactState.FINAL,
            NarrativeArtifactState.SUPERSEDED,
            NarrativeArtifactState.STALE,
        ),
        NarrativeArtifactState.CONFIRMED: _states(
            NarrativeArtifactState.STALE,
            NarrativeArtifactState.SUPERSEDED,
        ),
        NarrativeArtifactState.FINAL: _states(NarrativeArtifactState.STALE),
        NarrativeArtifactState.STALE: _states(NarrativeArtifactState.SUPERSEDED),
        NarrativeArtifactState.SUPERSEDED: frozenset(),
    },
    error_code=NarrativeTransitionCode.INVALID_ARTIFACT_TRANSITION,
)


JOB_TRANSITIONS = TransitionTable(
    transitions={
        NarrativeJobState.QUEUED: _states(
            NarrativeJobState.SNAPSHOTTING,
            NarrativeJobState.CANCELLED,
        ),
        NarrativeJobState.SNAPSHOTTING: _states(
            NarrativeJobState.RETRIEVING,
            NarrativeJobState.NEEDS_ECHO,
            NarrativeJobState.FAILED,
            NarrativeJobState.CANCELLED,
        ),
        NarrativeJobState.RETRIEVING: _states(
            NarrativeJobState.PLANNING,
            NarrativeJobState.NEEDS_ECHO,
            NarrativeJobState.FAILED,
            NarrativeJobState.CANCELLED,
        ),
        NarrativeJobState.PLANNING: _states(
            NarrativeJobState.DRAFTING,
            NarrativeJobState.NEEDS_ECHO,
            NarrativeJobState.FAILED,
            NarrativeJobState.CANCELLED,
        ),
        NarrativeJobState.DRAFTING: _states(
            NarrativeJobState.VALIDATING_FACTS,
            NarrativeJobState.FAILED,
            NarrativeJobState.CANCELLED,
        ),
        NarrativeJobState.VALIDATING_FACTS: _states(
            NarrativeJobState.EDITING_STYLE,
            NarrativeJobState.FAILED,
            NarrativeJobState.CANCELLED,
        ),
        NarrativeJobState.EDITING_STYLE: _states(
            NarrativeJobState.FINAL_VALIDATION,
            NarrativeJobState.FAILED,
            NarrativeJobState.CANCELLED,
        ),
        NarrativeJobState.FINAL_VALIDATION: _states(
            NarrativeJobState.READY_FOR_REVIEW,
            NarrativeJobState.FAILED,
            NarrativeJobState.CANCELLED,
        ),
        NarrativeJobState.READY_FOR_REVIEW: _states(NarrativeJobState.SUPERSEDED),
        NarrativeJobState.NEEDS_ECHO: _states(
            NarrativeJobState.QUEUED,
            NarrativeJobState.CANCELLED,
            NarrativeJobState.SUPERSEDED,
        ),
        NarrativeJobState.FAILED: _states(
            NarrativeJobState.QUEUED,
            NarrativeJobState.CANCELLED,
            NarrativeJobState.SUPERSEDED,
        ),
        NarrativeJobState.CANCELLED: frozenset(),
        NarrativeJobState.SUPERSEDED: frozenset(),
    },
    error_code=NarrativeTransitionCode.INVALID_JOB_TRANSITION,
)


__all__ = [
    "ARTIFACT_TRANSITIONS",
    "JOB_TRANSITIONS",
    "PROJECT_TRANSITIONS",
    "NarrativeTransitionCode",
    "NarrativeTransitionError",
    "TransitionTable",
]
