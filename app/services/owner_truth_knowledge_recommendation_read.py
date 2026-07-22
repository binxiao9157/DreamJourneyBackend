"""Fail-closed, value-free QA reads for the M0-B recommendation selector.

The selector is deliberately not connected to Echo or a public recommendation
surface here.  This adapter only proves that QA candidate references are bound
to the current Owner-confirmed MemoryVersion set before the deterministic
policy selects at most one continuity and one breadth recommendation.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional, Protocol

from app.domain.owner_truth.contracts import OwnerTruthContractError
from app.domain.owner_truth.conversation import (
    InterviewBoundary,
    InterviewSessionState,
    OwnerTruthConversationAccessDenied,
    OwnerTruthConversationThreadAuthoritySnapshot,
)
from app.domain.owner_truth.knowledge_dimension_read import (
    OWNER_TRUTH_KNOWLEDGE_DIMENSION_READ_SCHEMA_VERSION,
    OwnerTruthKnowledgeDimensionReadResult,
    OwnerTruthKnowledgeDimensionReadService,
    OwnerTruthKnowledgeDimensionReadState,
)
from app.domain.owner_truth.knowledge_recommendations import (
    KNOWLEDGE_DIMENSION_POLICY_VERSION,
    RECOMMENDATION_SELECTION_SCHEMA_VERSION,
    RecommendationCandidate,
    RecommendationEvidenceKind,
    RecommendationSelection,
    RecommendationSelector,
    RecommendationSlot,
    ServerPlannedContinuationCue,
    ServerPlannedRecommendationCandidateProjector,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_thread_preferences import (
    OwnerTruthThreadPreferenceSnapshot,
    ThreadPreferenceState,
)


OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_READ_SCHEMA_VERSION = (
    "owner-truth-knowledge-recommendation-read-v1"
)
OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_PLAN_SCHEMA_VERSION = (
    "owner-truth-knowledge-recommendation-plan-v1"
)


class OwnerTruthKnowledgeRecommendationReadError(OwnerTruthContractError):
    """A QA-only recommendation read is malformed or not safely bound."""


class OwnerTruthKnowledgeRecommendationReadStore(Protocol):
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

    def owner_truth_thread_preference_repository(self) -> Any:
        ...


@dataclass(frozen=True)
class OwnerTruthKnowledgeRecommendationReadResult:
    """A value-free composition of receipt-backed coverage and selection."""

    dimension_read: OwnerTruthKnowledgeDimensionReadResult
    selection: Optional[RecommendationSelection]

    def __post_init__(self) -> None:
        if not isinstance(self.dimension_read, OwnerTruthKnowledgeDimensionReadResult):
            raise TypeError("dimension_read must be an OwnerTruthKnowledgeDimensionReadResult")
        if self.dimension_read.state is OwnerTruthKnowledgeDimensionReadState.READY:
            if self.selection is None:
                raise OwnerTruthKnowledgeRecommendationReadError(
                    "ready knowledge dimension read requires a recommendation selection"
                )
            if (
                self.selection.owner_subject_id != self.dimension_read.owner_subject_id
                or self.selection.vault_id != self.dimension_read.vault_id
            ):
                raise OwnerTruthKnowledgeRecommendationReadError(
                    "recommendation selection scope does not match dimension read"
                )
        elif self.selection is not None:
            raise OwnerTruthKnowledgeRecommendationReadError(
                "non-ready knowledge dimension reads must not retain a recommendation selection"
            )

    @property
    def state(self) -> OwnerTruthKnowledgeDimensionReadState:
        return self.dimension_read.state

    def value_free_summary(self) -> dict[str, object]:
        """Return no raw memory, candidate, message, or template text."""

        summary: dict[str, object] = {
            "schemaVersion": OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_READ_SCHEMA_VERSION,
            "selectionState": self.dimension_read.state.value,
            "dimensionReadSchemaVersion": OWNER_TRUTH_KNOWLEDGE_DIMENSION_READ_SCHEMA_VERSION,
            "dimensionRead": self.dimension_read.value_free_summary(),
            "selected": [],
            "filtered": [],
            "policyVersion": KNOWLEDGE_DIMENSION_POLICY_VERSION,
            "selectionSchemaVersion": RECOMMENDATION_SELECTION_SCHEMA_VERSION,
        }
        if self.selection is not None:
            selection_summary = self.selection.value_free_summary()
            summary["selected"] = selection_summary["selected"]
            summary["filtered"] = selection_summary["filtered"]
            summary["policyVersion"] = selection_summary["policyVersion"]
            summary["selectionSchemaVersion"] = selection_summary["schemaVersion"]
        return summary


class OwnerTruthKnowledgeRecommendationReadService:
    """Compose existing receipt-backed coverage with the pure selector.

    The caller may supply only typed, value-free QA candidates.  A candidate is
    accepted only when every evidence reference is a current explicit Owner
    confirmation in the same dimension read.  No candidate or selection is
    persisted by this adapter.
    """

    def __init__(
        self,
        store: OwnerTruthKnowledgeRecommendationReadStore,
        *,
        selector: RecommendationSelector | None = None,
        planner: ServerPlannedRecommendationCandidateProjector | None = None,
    ) -> None:
        self._store = store
        self._selector = selector or RecommendationSelector()
        self._planner = planner or ServerPlannedRecommendationCandidateProjector()

    def read(
        self,
        *,
        context: OwnerTruthCommandContext,
        candidates: Iterable[RecommendationCandidate],
        now: Optional[datetime] = None,
        crisis_active: bool = False,
    ) -> OwnerTruthKnowledgeRecommendationReadResult:
        if not isinstance(context, OwnerTruthCommandContext):
            raise OwnerTruthKnowledgeRecommendationReadError(
                "owner truth command context is required"
            )
        if not isinstance(crisis_active, bool):
            raise OwnerTruthKnowledgeRecommendationReadError("crisis_active must be a boolean")
        candidate_rows = self._candidate_rows(candidates)
        current_time = self._current_time(now)
        with self._store.request_unit_of_work(
            correlation_id=(
                "owner-truth-knowledge-recommendation-read-"
                f"{context.vault_id}:{context.owner_subject_id}"
            ),
            command_id="ownerTruthKnowledgeRecommendationRead",
        ):
            dimension_read = OwnerTruthKnowledgeDimensionReadService(
                self._store.owner_truth_memory_projection_repository(),
                self._store.owner_truth_knowledge_dimension_confirmation_repository(),
            ).read(context=context)
            if dimension_read.state is not OwnerTruthKnowledgeDimensionReadState.READY:
                return OwnerTruthKnowledgeRecommendationReadResult(
                    dimension_read=dimension_read,
                    selection=None,
                )
            assert dimension_read.coverage is not None
            self._assert_current_owner_thread_authority(
                candidates=candidate_rows,
                context=context,
                authority_epoch=dimension_read.authority_epoch,
                now=current_time,
            )
            self._assert_current_owner_confirmed_evidence(
                candidates=candidate_rows,
                dimension_read=dimension_read,
            )
            selection = self._selector.select(
                owner_subject_id=context.owner_subject_id,
                vault_id=context.vault_id,
                coverage=dimension_read.coverage,
                candidates=candidate_rows,
                now=current_time,
                crisis_active=crisis_active,
            )
            return OwnerTruthKnowledgeRecommendationReadResult(
                dimension_read=dimension_read,
                selection=selection,
            )

    def plan(
        self,
        *,
        context: OwnerTruthCommandContext,
        now: Optional[datetime] = None,
        crisis_active: bool = False,
    ) -> OwnerTruthKnowledgeRecommendationReadResult:
        """Plan candidates only from current server-side authority.

        This is a QA-only read path. It deliberately accepts no candidate,
        thread, evidence, ranking, or user-boundary fields from the caller.
        The planner may return zero candidates when no eligible current
        interview authority or no confirmed coverage gap is available. An
        elapsed ``cooldown`` is eligible only after a separate server-clock
        preference check; the read never resumes the paused session.
        """

        if not isinstance(context, OwnerTruthCommandContext):
            raise OwnerTruthKnowledgeRecommendationReadError(
                "owner truth command context is required"
            )
        if not isinstance(crisis_active, bool):
            raise OwnerTruthKnowledgeRecommendationReadError("crisis_active must be a boolean")
        current_time = self._current_time(now)
        with self._store.request_unit_of_work(
            correlation_id=(
                "owner-truth-knowledge-recommendation-plan-"
                f"{context.vault_id}:{context.owner_subject_id}"
            ),
            command_id="ownerTruthKnowledgeRecommendationPlan",
        ):
            dimension_read = OwnerTruthKnowledgeDimensionReadService(
                self._store.owner_truth_memory_projection_repository(),
                self._store.owner_truth_knowledge_dimension_confirmation_repository(),
            ).read(context=context)
            if dimension_read.state is not OwnerTruthKnowledgeDimensionReadState.READY:
                return OwnerTruthKnowledgeRecommendationReadResult(
                    dimension_read=dimension_read,
                    selection=None,
                )
            assert dimension_read.coverage is not None
            repository = self._store.owner_truth_conversation_repository()
            try:
                potential_thread_authorities = repository.list_recommendation_candidate_thread_authorities(
                    context=context,
                )
            except OwnerTruthConversationAccessDenied as error:
                raise OwnerTruthKnowledgeRecommendationReadError(
                    "current Owner Truth interview authority is unavailable for recommendation planning"
                ) from error
            thread_authorities, elapsed_cooldown_thread_ids = self._plan_thread_authorities(
                context=context,
                potential_thread_authorities=potential_thread_authorities,
                now=current_time,
            )
            continuity_cues = self._current_saved_continuation_cues(
                cues=self._store.owner_truth_saved_continuation_cue_repository().list_for_recommendation(
                    context=context,
                ),
                thread_authorities=thread_authorities,
                elapsed_cooldown_thread_ids=elapsed_cooldown_thread_ids,
                dimension_read=dimension_read,
                context=context,
                conversation_repository=repository,
            )
            candidates = self._planner.project(
                owner_subject_id=context.owner_subject_id,
                vault_id=context.vault_id,
                authority_epoch=dimension_read.authority_epoch,
                checkpoint=dimension_read.checkpoint or "",
                coverage=dimension_read.coverage,
                thread_authorities=thread_authorities,
                continuity_cues=continuity_cues,
                elapsed_cooldown_thread_ids=elapsed_cooldown_thread_ids,
            )
            self._assert_current_owner_thread_authority(
                candidates=candidates,
                context=context,
                authority_epoch=dimension_read.authority_epoch,
                now=current_time,
            )
            self._assert_current_owner_confirmed_evidence(
                candidates=candidates,
                dimension_read=dimension_read,
                allow_saved_continuation=True,
            )
            selection = self._selector.select(
                owner_subject_id=context.owner_subject_id,
                vault_id=context.vault_id,
                coverage=dimension_read.coverage,
                candidates=candidates,
                now=current_time,
                crisis_active=crisis_active,
            )
            return OwnerTruthKnowledgeRecommendationReadResult(
                dimension_read=dimension_read,
                selection=selection,
            )

    @staticmethod
    def _candidate_rows(
        candidates: Iterable[RecommendationCandidate],
    ) -> tuple[RecommendationCandidate, ...]:
        try:
            rows = tuple(candidates)
        except TypeError as exc:
            raise OwnerTruthKnowledgeRecommendationReadError("candidates must be iterable") from exc
        if any(not isinstance(candidate, RecommendationCandidate) for candidate in rows):
            raise OwnerTruthKnowledgeRecommendationReadError(
                "candidates must contain RecommendationCandidate"
            )
        return rows

    @staticmethod
    def _current_time(now: Optional[datetime]) -> datetime:
        if now is None:
            return datetime.now(timezone.utc)
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise OwnerTruthKnowledgeRecommendationReadError("now must be timezone-aware")
        return now.astimezone(timezone.utc)

    @staticmethod
    def _assert_current_owner_confirmed_evidence(
        *,
        candidates: Iterable[RecommendationCandidate],
        dimension_read: OwnerTruthKnowledgeDimensionReadResult,
        allow_saved_continuation: bool = False,
    ) -> None:
        assert dimension_read.coverage is not None
        allowed = frozenset(dimension_read.included_memory_version_ids)
        for candidate in candidates:
            if candidate.evidence_kind is RecommendationEvidenceKind.CONFIRMED_MEMORY:
                pass
            elif (
                allow_saved_continuation
                and candidate.evidence_kind is RecommendationEvidenceKind.SAVED_CONTINUATION
                and candidate.slot is RecommendationSlot.CONTINUITY
            ):
                pass
            else:
                raise OwnerTruthKnowledgeRecommendationReadError(
                    "QA recommendation candidates must use confirmedMemory evidence"
                )
            if not set(candidate.evidence_refs).issubset(allowed):
                raise OwnerTruthKnowledgeRecommendationReadError(
                    "candidate evidence_refs must reference current owner-confirmed MemoryVersion records"
                )
            dimension_refs = frozenset(
                dimension_read.coverage.for_dimension(candidate.target_dimension).memory_version_ids
            )
            if not set(candidate.evidence_refs).issubset(dimension_refs):
                raise OwnerTruthKnowledgeRecommendationReadError(
                    "candidate evidence_refs must confirm the target knowledge dimension"
                )

    @staticmethod
    def _current_saved_continuation_cues(
        *,
        cues: Iterable[ServerPlannedContinuationCue],
        thread_authorities: Iterable[OwnerTruthConversationThreadAuthoritySnapshot],
        elapsed_cooldown_thread_ids: frozenset[str],
        dimension_read: OwnerTruthKnowledgeDimensionReadResult,
        context: OwnerTruthCommandContext,
        conversation_repository: Any,
    ) -> tuple[ServerPlannedContinuationCue, ...]:
        """Keep only explicit cues still bound to the current private state.

        Historical cue receipts are intentionally append-only. A stale session,
        authority epoch, replaced MemoryVersion, or newly covered facet simply
        yields no continuity candidate; it is never silently revived. The one
        narrow exception is an immediately following, server-verified
        ``cooldown`` transition: version ``N`` may remain tied to version
        ``N + 1`` while the Owner's cooldown has elapsed. This preserves the
        explicit cue without resuming the session or accepting a caller clock.
        """

        assert dimension_read.coverage is not None
        try:
            cue_rows = tuple(cues)
            authority_rows = tuple(thread_authorities)
        except TypeError as exc:
            raise OwnerTruthKnowledgeRecommendationReadError(
                "saved continuation cue repository returned a non-iterable value"
            ) from exc
        active_sessions = {
            (item.thread_id, item.session_id)
            for item in authority_rows
            if isinstance(item, OwnerTruthConversationThreadAuthoritySnapshot)
            and item.is_recommendation_eligible
        }
        elapsed_cooldown_sessions = {
            (item.thread_id, item.session_id)
            for item in authority_rows
            if isinstance(item, OwnerTruthConversationThreadAuthoritySnapshot)
            and item.thread_id in elapsed_cooldown_thread_ids
            and item.is_elapsed_cooldown_candidate
        }
        current: list[ServerPlannedContinuationCue] = []
        for cue in cue_rows:
            if not isinstance(cue, ServerPlannedContinuationCue):
                raise OwnerTruthKnowledgeRecommendationReadError(
                    "saved continuation cue repository returned an invalid cue"
                )
            if (
                cue.owner_subject_id != dimension_read.owner_subject_id
                or cue.vault_id != dimension_read.vault_id
                or cue.authority_epoch != dimension_read.authority_epoch
                or (
                    (cue.thread_id, cue.session_id) not in active_sessions
                    and (cue.thread_id, cue.session_id) not in elapsed_cooldown_sessions
                )
            ):
                continue
            try:
                session = conversation_repository.get_interview_session(
                    session_id=cue.session_id,
                    context=context,
                )
            except OwnerTruthConversationAccessDenied:
                continue
            if (
                session.thread_id != cue.thread_id
            ):
                continue
            is_active_open = (
                (cue.thread_id, cue.session_id) in active_sessions
                and session.row_version == cue.expected_session_version
                and session.state is InterviewSessionState.ACTIVE
                and session.boundary is InterviewBoundary.OPEN
            )
            is_elapsed_cooldown = (
                (cue.thread_id, cue.session_id) in elapsed_cooldown_sessions
                and session.row_version == cue.expected_session_version + 1
                and session.state is InterviewSessionState.PAUSED
                and session.boundary is InterviewBoundary.COOLDOWN
            )
            if not is_active_open and not is_elapsed_cooldown:
                continue
            coverage = dimension_read.coverage.for_dimension(cue.target_dimension)
            if (
                cue.memory_version_id not in coverage.memory_version_ids
                or cue.missing_facet not in coverage.missing_facets
            ):
                continue
            current.append(cue)
        return tuple(sorted(current, key=lambda item: item.cue_id))

    def _assert_current_owner_thread_authority(
        self,
        *,
        candidates: Iterable[RecommendationCandidate],
        context: OwnerTruthCommandContext,
        authority_epoch: int,
        now: datetime,
    ) -> None:
        """Reject caller-supplied thread IDs unless a current private Thread owns them."""

        repository = self._store.owner_truth_conversation_repository()
        seen_thread_ids: set[str] = set()
        for candidate in candidates:
            if candidate.thread_id in seen_thread_ids:
                continue
            seen_thread_ids.add(candidate.thread_id)
            try:
                snapshot = repository.get_interview_thread_authority(
                    thread_id=candidate.thread_id,
                    context=context,
                )
            except OwnerTruthConversationAccessDenied as error:
                raise OwnerTruthKnowledgeRecommendationReadError(
                    "candidate thread_id must reference a current Owner Truth conversation thread"
                ) from error
            if (
                not isinstance(snapshot, OwnerTruthConversationThreadAuthoritySnapshot)
                or snapshot.thread_id != candidate.thread_id
                or snapshot.vault_id != context.vault_id
                or snapshot.owner_subject_id != context.owner_subject_id
                or snapshot.authority_epoch != authority_epoch
            ):
                raise OwnerTruthKnowledgeRecommendationReadError(
                    "candidate thread_id must reference a current Owner Truth conversation thread"
                )
            preference = self._store.owner_truth_thread_preference_repository().read(
                context=context,
                thread_id=candidate.thread_id,
            )
            structurally_due = snapshot.is_elapsed_cooldown_candidate
            elapsed_due = (
                structurally_due
                and preference is not None
                and preference.is_cooldown_elapsed_at(now=now)
            )
            if not snapshot.is_recommendation_eligible and not elapsed_due:
                raise OwnerTruthKnowledgeRecommendationReadError(
                    "candidate thread_id must reference a current Owner Truth conversation thread in active state"
                )
            if not self._thread_authority_permits_recommendation(
                snapshot=snapshot,
                preference=preference,
                now=now,
            ):
                raise OwnerTruthKnowledgeRecommendationReadError(
                    "candidate thread_id is protected by an Owner thread preference"
                )

    def _plan_thread_authorities(
        self,
        *,
        context: OwnerTruthCommandContext,
        potential_thread_authorities: Iterable[OwnerTruthConversationThreadAuthoritySnapshot],
        now: datetime,
    ) -> tuple[tuple[OwnerTruthConversationThreadAuthoritySnapshot, ...], frozenset[str]]:
        """Choose one effective authority without writing lifecycle state.

        ``cooldown`` is a higher-priority explicit Owner continuation signal
        once it has elapsed.  It is selected deterministically by its original
        expiry, while active/open threads retain the older planner behavior
        when no elapsed cooldown exists.  ``doNotAsk`` is never returned.
        """

        try:
            rows = tuple(potential_thread_authorities)
        except TypeError as exc:
            raise OwnerTruthKnowledgeRecommendationReadError(
                "conversation repository returned non-iterable recommendation authorities"
            ) from exc
        active_open: list[OwnerTruthConversationThreadAuthoritySnapshot] = []
        elapsed_cooldown: list[tuple[datetime, str, OwnerTruthConversationThreadAuthoritySnapshot]] = []
        preferences = self._store.owner_truth_thread_preference_repository()
        for snapshot in rows:
            if not isinstance(snapshot, OwnerTruthConversationThreadAuthoritySnapshot):
                raise OwnerTruthKnowledgeRecommendationReadError(
                    "conversation repository returned invalid recommendation authority"
                )
            preference = preferences.read(context=context, thread_id=snapshot.thread_id)
            if snapshot.is_recommendation_eligible:
                if preference is None or preference.preference is ThreadPreferenceState.OPEN:
                    active_open.append(snapshot)
                continue
            if (
                snapshot.is_elapsed_cooldown_candidate
                and preference is not None
                and preference.is_cooldown_elapsed_at(now=now)
            ):
                assert preference.cooldown_until is not None
                elapsed_cooldown.append((preference.cooldown_until, snapshot.thread_id, snapshot))

        if elapsed_cooldown:
            _, thread_id, selected = min(elapsed_cooldown, key=lambda item: (item[0], item[1]))
            return (selected,), frozenset((thread_id,))
        return tuple(active_open), frozenset()

    @staticmethod
    def _thread_authority_permits_recommendation(
        *,
        snapshot: OwnerTruthConversationThreadAuthoritySnapshot,
        preference: Optional[OwnerTruthThreadPreferenceSnapshot],
        now: datetime,
    ) -> bool:
        if snapshot.is_recommendation_eligible:
            return preference is None or preference.preference is ThreadPreferenceState.OPEN
        return (
            snapshot.is_elapsed_cooldown_candidate
            and preference is not None
            and preference.is_cooldown_elapsed_at(now=now)
        )


__all__ = [
    "OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_PLAN_SCHEMA_VERSION",
    "OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_READ_SCHEMA_VERSION",
    "OwnerTruthKnowledgeRecommendationReadError",
    "OwnerTruthKnowledgeRecommendationReadResult",
    "OwnerTruthKnowledgeRecommendationReadService",
    "OwnerTruthKnowledgeRecommendationReadStore",
]
