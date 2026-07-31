"""Value-minimized QA read for one private interview session outcome.

Phase 4C needs a way to explain whether an Owner's interview resulted in
confirmed additions and whether a safe continuation remains available.  This
adapter deliberately stays read-only and default-off: it does not read message
text, Candidate payloads, MemoryVersion content, or raw provider data.

``confirmedMemoryVersionCount`` is especially narrow.  It counts only current
MemoryVersions which both have an explicit Owner knowledge-dimension
confirmation and can be traced to a current admitted Source for an
acknowledged review batch from the requested session.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Protocol

from app.domain.owner_truth.contracts import OwnerTruthContractError, require_uuid
from app.domain.owner_truth.conversation import (
    InterviewBoundary,
    InterviewReviewBatchState,
    InterviewSessionState,
    OwnerTruthConversationAccessDenied,
    OwnerTruthInterviewReviewBatchSnapshot,
    OwnerTruthInterviewSessionSnapshot,
)
from app.domain.owner_truth.interview_candidate_review import (
    OwnerTruthInterviewCandidateReviewAccessDenied,
    OwnerTruthInterviewCandidateReviewComposition,
    OwnerTruthInterviewCandidateReviewConflict,
    OwnerTruthInterviewCandidateReviewSourceInactive,
)
from app.domain.owner_truth.knowledge_dimension_read import (
    OwnerTruthKnowledgeDimensionReadResult,
    OwnerTruthKnowledgeDimensionReadService,
    OwnerTruthKnowledgeDimensionReadState,
)
from app.domain.owner_truth.knowledge_recommendations import (
    DimensionProjection,
    ServerPlannedContinuationCue,
)
from app.domain.owner_truth.memory_projection import OwnerTruthMemoryProjectionAccessDenied
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext


OWNER_TRUTH_INTERVIEW_SESSION_OUTCOME_READ_SCHEMA_VERSION = (
    "owner-truth-interview-session-outcome-read-v1"
)
OWNER_TRUTH_INTERVIEW_SESSION_OUTCOME_PRESENTATION_SCHEMA_VERSION = (
    "owner-truth-interview-session-outcome-presentation-v1"
)


class OwnerTruthInterviewSessionOutcomeReadError(OwnerTruthContractError):
    """The value-free session outcome cannot be derived safely."""


class OwnerTruthInterviewSessionOutcomeReadAccessDenied(
    OwnerTruthInterviewSessionOutcomeReadError
):
    """The requested session is not readable by the active Vault Owner."""


def _nonnegative(value: int, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise OwnerTruthInterviewSessionOutcomeReadError(
            f"{field} must be a non-negative integer"
        )
    return value


@dataclass(frozen=True)
class OwnerTruthInterviewSessionOutcomeReadResult:
    """A content-free, current explanation of one interview session outcome."""

    session_id: str
    thread_id: str
    session_state: InterviewSessionState
    session_boundary: InterviewBoundary
    presentation_state: str
    can_continue: bool
    can_continue_later: bool
    review_batch_count: int
    pending_review_batch_count: int
    acknowledged_review_batch_count: int
    admitted_review_batch_count: int
    confirmation_state: OwnerTruthKnowledgeDimensionReadState
    confirmed_memory_version_count: int | None
    eligible_saved_continuation_cue_count: int | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", require_uuid(self.session_id, field="session_id"))
        object.__setattr__(self, "thread_id", require_uuid(self.thread_id, field="thread_id"))
        try:
            object.__setattr__(self, "session_state", InterviewSessionState(self.session_state))
            object.__setattr__(self, "session_boundary", InterviewBoundary(self.session_boundary))
            object.__setattr__(
                self,
                "confirmation_state",
                OwnerTruthKnowledgeDimensionReadState(self.confirmation_state),
            )
        except (TypeError, ValueError) as error:
            raise OwnerTruthInterviewSessionOutcomeReadError(
                "session outcome contains an unsupported state"
            ) from error
        if self.presentation_state not in {
            "reviewPending",
            "readyForNarrative",
            "narrativeRecorded",
            "ended",
            "paused",
        }:
            raise OwnerTruthInterviewSessionOutcomeReadError(
                "session outcome presentation_state is not supported"
            )
        if not isinstance(self.can_continue, bool) or not isinstance(self.can_continue_later, bool):
            raise OwnerTruthInterviewSessionOutcomeReadError(
                "session outcome continuation flags must be booleans"
            )
        for field in (
            "review_batch_count",
            "pending_review_batch_count",
            "acknowledged_review_batch_count",
            "admitted_review_batch_count",
        ):
            object.__setattr__(self, field, _nonnegative(getattr(self, field), field=field))
        if self.pending_review_batch_count + self.acknowledged_review_batch_count != self.review_batch_count:
            raise OwnerTruthInterviewSessionOutcomeReadError(
                "session outcome review batch counts do not reconcile"
            )
        if self.admitted_review_batch_count > self.acknowledged_review_batch_count:
            raise OwnerTruthInterviewSessionOutcomeReadError(
                "session outcome admitted batch count exceeds acknowledged batches"
            )
        if self.confirmation_state is OwnerTruthKnowledgeDimensionReadState.READY:
            object.__setattr__(
                self,
                "confirmed_memory_version_count",
                _nonnegative(
                    self.confirmed_memory_version_count,
                    field="confirmed_memory_version_count",
                ),
            )
            object.__setattr__(
                self,
                "eligible_saved_continuation_cue_count",
                _nonnegative(
                    self.eligible_saved_continuation_cue_count,
                    field="eligible_saved_continuation_cue_count",
                ),
            )
        elif (
            self.confirmed_memory_version_count is not None
            or self.eligible_saved_continuation_cue_count is not None
        ):
            raise OwnerTruthInterviewSessionOutcomeReadError(
                "non-ready session outcome must not report confirmation-derived counts"
            )

    def value_free_summary(self) -> dict[str, object]:
        """Return only durable status/counts, never private interview content."""

        return {
            "schemaVersion": OWNER_TRUTH_INTERVIEW_SESSION_OUTCOME_READ_SCHEMA_VERSION,
            "sessionId": self.session_id,
            "threadId": self.thread_id,
            "presentation": {
                "state": self.presentation_state,
                "canContinue": self.can_continue,
                "canContinueLater": self.can_continue_later,
            },
            "thisSession": {
                "sessionState": self.session_state.value,
                "sessionBoundary": self.session_boundary.value,
                "reviewBatchCount": self.review_batch_count,
                "pendingReviewBatchCount": self.pending_review_batch_count,
                "acknowledgedReviewBatchCount": self.acknowledged_review_batch_count,
                "admittedReviewBatchCount": self.admitted_review_batch_count,
                "confirmationState": self.confirmation_state.value,
                "confirmedMemoryVersionCount": self.confirmed_memory_version_count,
            },
            "laterContinue": {
                "eligibleSavedContinuationCueCount": self.eligible_saved_continuation_cue_count,
            },
        }


def interview_session_outcome_presentation(
    result: OwnerTruthInterviewSessionOutcomeReadResult,
) -> dict[str, object]:
    """Build the default-off product summary for one completed interview.

    The QA-only read above intentionally retains session/thread identifiers so
    test operators can correlate a private session.  This presentation is a
    separate boundary: it is limited to current counts and continuation
    availability.  It never returns raw conversation, Candidate, Source,
    MemoryVersion, review-batch, or provider identifiers.
    """

    if not isinstance(result, OwnerTruthInterviewSessionOutcomeReadResult):
        raise OwnerTruthInterviewSessionOutcomeReadError(
            "session outcome presentation requires a valid read result"
        )

    if result.confirmation_state is OwnerTruthKnowledgeDimensionReadState.READY:
        assert result.confirmed_memory_version_count is not None
        assert result.eligible_saved_continuation_cue_count is not None
        state = "ready"
        confirmed_memory_count = result.confirmed_memory_version_count
        pending_review_batch_count = result.pending_review_batch_count
        eligible_cue_count = result.eligible_saved_continuation_cue_count
    else:
        # A rebuilding projection must never leak a prior confirmed count as
        # though it were current. The pending-review count comes from the
        # scoped, current interview session read, so it remains safe while
        # confirmed coverage is absent or rebuilding.
        state = "rebuilding"
        confirmed_memory_count = 0
        pending_review_batch_count = result.pending_review_batch_count
        eligible_cue_count = 0

    return {
        "state": state,
        "thisSession": {
            "confirmedMemoryCount": confirmed_memory_count,
            "pendingReviewBatchCount": pending_review_batch_count,
        },
        "laterContinue": {
            "canContinueLater": result.can_continue_later,
            "eligibleCueCount": eligible_cue_count,
        },
    }


class OwnerTruthInterviewSessionOutcomeReadStore(Protocol):
    def request_unit_of_work(
        self,
        *,
        correlation_id: str,
        command_id: str,
    ) -> AbstractContextManager[Any]:
        ...

    def owner_truth_conversation_repository(self) -> Any:
        ...

    def owner_truth_interview_candidate_review_repository(self) -> Any:
        ...

    def owner_truth_memory_projection_repository(self) -> Any:
        ...

    def owner_truth_knowledge_dimension_confirmation_repository(self) -> Any:
        ...

    def owner_truth_saved_continuation_cue_repository(self) -> Any:
        ...

    def get_owner_truth_vault(self, vault_id: str) -> Mapping[str, Any] | None:
        ...


class OwnerTruthInterviewSessionOutcomeReadService:
    """Build a safe Phase 4C session outcome from existing Owner Truth reads."""

    def __init__(self, store: OwnerTruthInterviewSessionOutcomeReadStore) -> None:
        self._store = store

    def read(
        self,
        *,
        session_id: str,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthInterviewSessionOutcomeReadResult:
        normalized_session_id = require_uuid(session_id, field="session_id")
        if not isinstance(context, OwnerTruthCommandContext):
            raise OwnerTruthInterviewSessionOutcomeReadError(
                "owner truth command context is required"
            )
        if context.actor_subject_id != context.owner_subject_id:
            raise OwnerTruthInterviewSessionOutcomeReadAccessDenied(
                "only the Vault Owner may read an interview session outcome"
            )
        with self._store.request_unit_of_work(
            correlation_id=(
                "owner-truth-interview-session-outcome-read-"
                f"{context.vault_id}:{normalized_session_id}"
            ),
            command_id="ownerTruthInterviewSessionOutcomeRead",
        ):
            conversation = self._store.owner_truth_conversation_repository()
            try:
                session = conversation.get_interview_session(
                    session_id=normalized_session_id,
                    context=context,
                )
                review_batches = tuple(
                    conversation.list_interview_review_batches(
                        session_id=normalized_session_id,
                        context=context,
                    )
                )
            except OwnerTruthConversationAccessDenied as error:
                raise OwnerTruthInterviewSessionOutcomeReadAccessDenied(str(error)) from error
            self._assert_session_scope(session=session, context=context)
            self._assert_review_batches(
                review_batches=review_batches,
                session=session,
                context=context,
            )
            dimension_read = self._read_dimension_or_unmaterialized(context=context)
            admitted_source_versions = self._current_admitted_source_versions(
                review_batches=review_batches,
                context=context,
            )
            if (
                dimension_read is not None
                and dimension_read.state is OwnerTruthKnowledgeDimensionReadState.READY
            ):
                assert dimension_read.coverage is not None
                confirmed_memory_version_count = self._confirmed_memory_version_count(
                    admitted_source_versions=admitted_source_versions,
                    included_memory_version_ids=dimension_read.included_memory_version_ids,
                    context=context,
                )
                eligible_cue_count = self._eligible_saved_continuation_cue_count(
                    session=session,
                    context=context,
                    authority_epoch=dimension_read.authority_epoch,
                    confirmed_memory_version_ids=dimension_read.included_memory_version_ids,
                    coverage=dimension_read.coverage,
                )
            else:
                confirmed_memory_version_count = None
                eligible_cue_count = None
            presentation_state, can_continue, can_continue_later = self._presentation(session=session)
            pending_count = sum(
                1
                for item in review_batches
                if item.state is InterviewReviewBatchState.PENDING_ACKNOWLEDGEMENT
            )
            acknowledged_count = sum(
                1
                for item in review_batches
                if item.state is InterviewReviewBatchState.ACKNOWLEDGED
            )
            return OwnerTruthInterviewSessionOutcomeReadResult(
                session_id=session.session_id,
                thread_id=session.thread_id,
                session_state=session.state,
                session_boundary=session.boundary,
                presentation_state=presentation_state,
                can_continue=can_continue,
                can_continue_later=can_continue_later,
                review_batch_count=len(review_batches),
                pending_review_batch_count=pending_count,
                acknowledged_review_batch_count=acknowledged_count,
                admitted_review_batch_count=len(admitted_source_versions),
                confirmation_state=(
                    dimension_read.state
                    if dimension_read is not None
                    else OwnerTruthKnowledgeDimensionReadState.REBUILDING
                ),
                confirmed_memory_version_count=confirmed_memory_version_count,
                eligible_saved_continuation_cue_count=eligible_cue_count,
            )

    def _read_dimension_or_unmaterialized(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthKnowledgeDimensionReadResult | None:
        """Allow a new, verified Owner session to report a neutral outcome.

        The conversation and review-batch scopes are checked before this
        helper runs. If no Owner Truth vault exists yet, no Source or
        MemoryVersion has been materialized, which is distinct from a revoked
        or mismatched vault. Existing vaults preserve the fail-closed
        projection access behaviour.
        """

        try:
            return OwnerTruthKnowledgeDimensionReadService(
                self._store.owner_truth_memory_projection_repository(),
                self._store.owner_truth_knowledge_dimension_confirmation_repository(),
            ).read(context=context)
        except OwnerTruthMemoryProjectionAccessDenied as error:
            if self._store.get_owner_truth_vault(context.vault_id) is None:
                return None
            raise OwnerTruthInterviewSessionOutcomeReadAccessDenied(str(error)) from error

    @staticmethod
    def _assert_session_scope(
        *,
        session: OwnerTruthInterviewSessionSnapshot,
        context: OwnerTruthCommandContext,
    ) -> None:
        if (
            not isinstance(session, OwnerTruthInterviewSessionSnapshot)
            or session.vault_id != context.vault_id
            or session.owner_subject_id != context.owner_subject_id
        ):
            raise OwnerTruthInterviewSessionOutcomeReadAccessDenied(
                "interview session does not belong to this active Owner Vault"
            )

    @staticmethod
    def _assert_review_batches(
        *,
        review_batches: Iterable[OwnerTruthInterviewReviewBatchSnapshot],
        session: OwnerTruthInterviewSessionSnapshot,
        context: OwnerTruthCommandContext,
    ) -> None:
        for batch in review_batches:
            if (
                not isinstance(batch, OwnerTruthInterviewReviewBatchSnapshot)
                or batch.vault_id != context.vault_id
                or batch.owner_subject_id != context.owner_subject_id
                or batch.session_id != session.session_id
                or batch.thread_id != session.thread_id
                or batch.authority_epoch != session.authority_epoch
            ):
                raise OwnerTruthInterviewSessionOutcomeReadError(
                    "interview review batch scope does not match session outcome scope"
                )

    def _current_admitted_source_versions(
        self,
        *,
        review_batches: Iterable[OwnerTruthInterviewReviewBatchSnapshot],
        context: OwnerTruthCommandContext,
    ) -> frozenset[tuple[str, int]]:
        """Use only live admitted Sources; no raw candidate payload crosses here."""

        repository = self._store.owner_truth_interview_candidate_review_repository()
        source_versions: set[tuple[str, int]] = set()
        for batch in review_batches:
            if batch.state is not InterviewReviewBatchState.ACKNOWLEDGED:
                continue
            try:
                composition = repository.compose(
                    review_batch_id=batch.review_batch_id,
                    context=context,
                )
            except OwnerTruthInterviewCandidateReviewAccessDenied:
                # An acknowledged review batch can legitimately have produced no
                # admitted Candidate Source. It therefore contributes no
                # confirmed MemoryVersion count rather than a guessed value.
                continue
            except (
                OwnerTruthInterviewCandidateReviewConflict,
                OwnerTruthInterviewCandidateReviewSourceInactive,
            ):
                # Stale/inactive provenance must never be counted as a current
                # session addition. Omit it from this rebuildable projection.
                continue
            if not isinstance(composition, OwnerTruthInterviewCandidateReviewComposition):
                raise OwnerTruthInterviewSessionOutcomeReadError(
                    "interview candidate review repository returned an invalid composition"
                )
            if (
                composition.review_batch_id != batch.review_batch_id
                or composition.authority_epoch != batch.authority_epoch
            ):
                raise OwnerTruthInterviewSessionOutcomeReadError(
                    "interview candidate review composition provenance does not match batch"
                )
            source_versions.add((composition.source_id, composition.source_version))
        return frozenset(source_versions)

    def _confirmed_memory_version_count(
        self,
        *,
        admitted_source_versions: frozenset[tuple[str, int]],
        included_memory_version_ids: Iterable[str],
        context: OwnerTruthCommandContext,
    ) -> int:
        if not admitted_source_versions:
            return 0
        current_confirmed_ids = frozenset(included_memory_version_ids)
        snapshot = self._store.owner_truth_memory_projection_repository().read(context=context)
        if not isinstance(snapshot, Mapping) or not isinstance(snapshot.get("entries"), list):
            raise OwnerTruthInterviewSessionOutcomeReadError(
                "current memory projection is unavailable for session outcome"
            )
        matches: set[str] = set()
        for entry in snapshot["entries"]:
            if not isinstance(entry, Mapping):
                raise OwnerTruthInterviewSessionOutcomeReadError(
                    "current memory projection contains an invalid entry"
                )
            citation = entry.get("citation")
            if not isinstance(citation, Mapping):
                raise OwnerTruthInterviewSessionOutcomeReadError(
                    "current memory projection contains an invalid citation"
                )
            memory_version_id = str(citation.get("memoryVersionId") or "").strip()
            source_id = str(citation.get("sourceId") or "").strip()
            source_version = citation.get("sourceVersion")
            if not memory_version_id or not source_id or not isinstance(source_version, int):
                raise OwnerTruthInterviewSessionOutcomeReadError(
                    "current memory projection citation is incomplete"
                )
            if (
                memory_version_id in current_confirmed_ids
                and (source_id, source_version) in admitted_source_versions
            ):
                matches.add(memory_version_id)
        return len(matches)

    def _eligible_saved_continuation_cue_count(
        self,
        *,
        session: OwnerTruthInterviewSessionSnapshot,
        context: OwnerTruthCommandContext,
        authority_epoch: int,
        confirmed_memory_version_ids: Iterable[str],
        coverage: DimensionProjection,
    ) -> int:
        """Mirror the active/open slice of continuation eligibility, no revival."""

        if (
            session.state is not InterviewSessionState.ACTIVE
            or session.boundary is not InterviewBoundary.OPEN
        ):
            return 0
        confirmed_ids = frozenset(confirmed_memory_version_ids)
        eligible = 0
        for cue in self._store.owner_truth_saved_continuation_cue_repository().list_for_recommendation(
            context=context
        ):
            if not isinstance(cue, ServerPlannedContinuationCue):
                raise OwnerTruthInterviewSessionOutcomeReadError(
                    "saved continuation cue repository returned an invalid cue"
                )
            if (
                cue.owner_subject_id == context.owner_subject_id
                and cue.vault_id == context.vault_id
                and cue.authority_epoch == authority_epoch
                and cue.thread_id == session.thread_id
                and cue.session_id == session.session_id
                and cue.expected_session_version == session.row_version
                and cue.memory_version_id in confirmed_ids
            ):
                dimension_coverage = coverage.for_dimension(cue.target_dimension)
                if (
                    cue.memory_version_id in dimension_coverage.memory_version_ids
                    and cue.missing_facet in dimension_coverage.missing_facets
                ):
                    eligible += 1
        return eligible

    @staticmethod
    def _presentation(
        *,
        session: OwnerTruthInterviewSessionSnapshot,
    ) -> tuple[str, bool, bool]:
        """Stay consistent with the existing private session presentation read."""

        if session.pending_review_batch_id is not None:
            return "reviewPending", False, True
        if session.state is InterviewSessionState.ACTIVE and session.boundary is InterviewBoundary.OPEN:
            return (
                "readyForNarrative" if session.turn_count == 0 else "narrativeRecorded",
                True,
                True,
            )
        if session.state is InterviewSessionState.ENDED:
            return "ended", False, True
        return "paused", False, session.boundary is not InterviewBoundary.DO_NOT_ASK


__all__ = [
    "OWNER_TRUTH_INTERVIEW_SESSION_OUTCOME_PRESENTATION_SCHEMA_VERSION",
    "OWNER_TRUTH_INTERVIEW_SESSION_OUTCOME_READ_SCHEMA_VERSION",
    "OwnerTruthInterviewSessionOutcomeReadAccessDenied",
    "OwnerTruthInterviewSessionOutcomeReadError",
    "OwnerTruthInterviewSessionOutcomeReadResult",
    "OwnerTruthInterviewSessionOutcomeReadService",
    "OwnerTruthInterviewSessionOutcomeReadStore",
    "interview_session_outcome_presentation",
]
