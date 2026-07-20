"""Deterministic, provider-neutral policy for M0 guided interviews.

The interview orchestrator deliberately sits between persisted conversation
messages/Sources and future response generation.  It may decide whether the
next response can contain one primary question, but it cannot generate text,
write a Candidate, mutate a MemoryVersion, or call a provider.

Only opaque identifiers and counters enter this policy.  User text, prompt
content, and model outputs stay outside the decision record so this first
slice can be persisted or audited without duplicating private content.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from uuid import UUID

from .contracts import OwnerTruthContractError, require_nonblank


INTERVIEW_ORCHESTRATION_SCHEMA_VERSION = "owner-truth-interview-orchestration-v1"
MIN_DEEPENING_TURNS_BEFORE_SUMMARY = 2
MAX_DEEPENING_TURNS_BEFORE_SUMMARY = 4
MIN_TURNS_BEFORE_CANDIDATE_BATCH = 5

_OPAQUE_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")


class InterviewOrchestrationError(OwnerTruthContractError):
    """An interview policy request cannot be interpreted safely."""


class InterviewAction(str, Enum):
    LISTEN = "listen"
    DEEPEN = "deepen"
    CLARIFY = "clarify"
    BROADEN = "broaden"
    SUMMARIZE = "summarize"
    PAUSE = "pause"


class InterviewSessionState(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    ENDING = "ending"
    ENDED = "ended"
    INVALID = "invalid"


class InterviewBoundary(str, Enum):
    NONE = "none"
    SKIP_ONCE = "skipOnce"
    COOLDOWN = "cooldown"
    DO_NOT_ASK = "doNotAsk"


class InterviewFatigue(str, Enum):
    NORMAL = "normal"
    GUARDED = "guarded"
    EXHAUSTED = "exhausted"


def _opaque_identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _OPAQUE_IDENTIFIER.fullmatch(normalized):
        raise InterviewOrchestrationError(f"{field} must be an opaque identifier")
    return normalized


@dataclass(frozen=True)
class InterviewOrchestrationInput:
    """Value-free state needed to choose one interview action.

    ``candidate_batch_turn_count`` is advisory only.  A positive batch hint
    never creates a Candidate and never promotes a Candidate to memory.
    """

    thread_id: str
    vault_id: str
    owner_subject_id: str
    topic_id: str
    authority_epoch: int
    session_state: InterviewSessionState
    deepening_turn_count: int
    candidate_batch_turn_count: int
    topic_incomplete: bool
    needs_clarification: bool
    user_changed_topic: bool
    user_boundary: InterviewBoundary
    is_sensitive: bool
    fatigue: InterviewFatigue = InterviewFatigue.NORMAL
    has_pending_review_batch: bool = False

    def __post_init__(self) -> None:
        normalized_thread_id = str(self.thread_id or "").strip()
        try:
            UUID(normalized_thread_id)
        except (TypeError, ValueError) as exc:
            raise InterviewOrchestrationError("thread_id must be a UUID") from exc
        object.__setattr__(self, "thread_id", normalized_thread_id)
        object.__setattr__(self, "vault_id", require_nonblank(self.vault_id, field="vault_id"))
        object.__setattr__(
            self,
            "owner_subject_id",
            require_nonblank(self.owner_subject_id, field="owner_subject_id"),
        )
        object.__setattr__(self, "topic_id", _opaque_identifier(self.topic_id, field="topic_id"))
        try:
            object.__setattr__(self, "session_state", InterviewSessionState(self.session_state))
            object.__setattr__(self, "user_boundary", InterviewBoundary(self.user_boundary))
            object.__setattr__(self, "fatigue", InterviewFatigue(self.fatigue))
        except ValueError as exc:
            raise InterviewOrchestrationError("interview input contains an unsupported enum value") from exc
        if self.authority_epoch < 0:
            raise InterviewOrchestrationError("authority_epoch must not be negative")
        for field in ("deepening_turn_count", "candidate_batch_turn_count"):
            value = getattr(self, field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise InterviewOrchestrationError(f"{field} must be a non-negative integer")
        for field in (
            "topic_incomplete",
            "needs_clarification",
            "user_changed_topic",
            "is_sensitive",
            "has_pending_review_batch",
        ):
            if not isinstance(getattr(self, field), bool):
                raise InterviewOrchestrationError(f"{field} must be a boolean")


@dataclass(frozen=True)
class InterviewDecision:
    """A deterministic, content-free next-step decision.

    The natural-language response layer may use this action, but must still
    respect the one-primary-question limit and cannot elevate any result to
    Owner Truth without the normal Candidate/DecisionReceipt route.
    """

    action: InterviewAction
    reason_code: str
    max_followups_remaining: int
    review_batch_due: bool
    next_session_state: InterviewSessionState
    consumes_one_shot_boundary: bool = False

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "action", InterviewAction(self.action))
            object.__setattr__(
                self,
                "next_session_state",
                InterviewSessionState(self.next_session_state),
            )
        except ValueError as exc:
            raise InterviewOrchestrationError("interview decision contains an unsupported enum value") from exc
        object.__setattr__(self, "reason_code", _opaque_identifier(self.reason_code, field="reason_code"))
        if (
            not isinstance(self.max_followups_remaining, int)
            or isinstance(self.max_followups_remaining, bool)
            or not 0 <= self.max_followups_remaining <= MAX_DEEPENING_TURNS_BEFORE_SUMMARY
        ):
            raise InterviewOrchestrationError("max_followups_remaining is outside the policy bound")
        if not isinstance(self.review_batch_due, bool):
            raise InterviewOrchestrationError("review_batch_due must be a boolean")
        if not isinstance(self.consumes_one_shot_boundary, bool):
            raise InterviewOrchestrationError("consumes_one_shot_boundary must be a boolean")

    def value_free_summary(self) -> dict[str, object]:
        """Return only policy evidence safe for an event or trace envelope."""
        return {
            "action": self.action.value,
            "consumesOneShotBoundary": self.consumes_one_shot_boundary,
            "maxFollowupsRemaining": self.max_followups_remaining,
            "nextSessionState": self.next_session_state.value,
            "reasonCode": self.reason_code,
            "reviewBatchDue": self.review_batch_due,
            "schemaVersion": INTERVIEW_ORCHESTRATION_SCHEMA_VERSION,
        }


class InterviewOrchestrator:
    """Select the next safe M0 interview action without performing effects."""

    def decide(self, state: InterviewOrchestrationInput) -> InterviewDecision:
        if not isinstance(state, InterviewOrchestrationInput):
            raise TypeError("InterviewOrchestrationInput is required")

        batch_due = self._review_batch_due(state)
        remaining = max(0, MAX_DEEPENING_TURNS_BEFORE_SUMMARY - state.deepening_turn_count)

        if state.session_state is InterviewSessionState.ENDING:
            return self._pause("sessionEnding", batch_due=batch_due)
        if state.session_state is not InterviewSessionState.ACTIVE:
            return self._pause("sessionNotActive", batch_due=batch_due)
        if state.user_boundary is InterviewBoundary.DO_NOT_ASK:
            return self._pause("userDoNotAsk", batch_due=batch_due)
        if state.is_sensitive:
            return self._pause("sensitiveOrUnsafe", batch_due=batch_due)
        if state.fatigue is InterviewFatigue.EXHAUSTED:
            return self._pause("fatigueLimitReached", batch_due=batch_due)
        if state.user_changed_topic:
            return self._pause("topicChanged", batch_due=batch_due)
        if state.user_boundary is InterviewBoundary.COOLDOWN:
            return self._pause("userCooldown", batch_due=batch_due)
        if state.user_boundary is InterviewBoundary.SKIP_ONCE:
            return InterviewDecision(
                action=InterviewAction.LISTEN,
                reason_code="userSkippedCurrentOpportunity",
                max_followups_remaining=remaining,
                review_batch_due=batch_due,
                next_session_state=InterviewSessionState.ACTIVE,
                consumes_one_shot_boundary=True,
            )
        if state.deepening_turn_count >= MAX_DEEPENING_TURNS_BEFORE_SUMMARY:
            return InterviewDecision(
                action=InterviewAction.SUMMARIZE,
                reason_code="followupBudgetReached",
                max_followups_remaining=0,
                review_batch_due=batch_due,
                next_session_state=InterviewSessionState.ACTIVE,
            )
        if state.needs_clarification:
            return InterviewDecision(
                action=InterviewAction.CLARIFY,
                reason_code="materialAmbiguity",
                max_followups_remaining=remaining,
                review_batch_due=batch_due,
                next_session_state=InterviewSessionState.ACTIVE,
            )
        if (
            state.fatigue is InterviewFatigue.GUARDED
            and state.deepening_turn_count >= MIN_DEEPENING_TURNS_BEFORE_SUMMARY
        ):
            return InterviewDecision(
                action=InterviewAction.SUMMARIZE,
                reason_code="fatiguePrefersSummary",
                max_followups_remaining=remaining,
                review_batch_due=batch_due,
                next_session_state=InterviewSessionState.ACTIVE,
            )
        if state.topic_incomplete:
            return InterviewDecision(
                action=InterviewAction.DEEPEN,
                reason_code="highValueIncompleteStory",
                max_followups_remaining=remaining,
                review_batch_due=batch_due,
                next_session_state=InterviewSessionState.ACTIVE,
            )
        return InterviewDecision(
            action=InterviewAction.LISTEN,
            reason_code="noSafePrimaryQuestion",
            max_followups_remaining=remaining,
            review_batch_due=batch_due,
            next_session_state=InterviewSessionState.ACTIVE,
        )

    @staticmethod
    def _review_batch_due(state: InterviewOrchestrationInput) -> bool:
        if state.has_pending_review_batch:
            return False
        if state.candidate_batch_turn_count >= MIN_TURNS_BEFORE_CANDIDATE_BATCH:
            return True
        return state.session_state is not InterviewSessionState.ACTIVE and state.candidate_batch_turn_count > 0

    @staticmethod
    def _pause(reason_code: str, *, batch_due: bool) -> InterviewDecision:
        return InterviewDecision(
            action=InterviewAction.PAUSE,
            reason_code=reason_code,
            max_followups_remaining=0,
            review_batch_due=batch_due,
            next_session_state=InterviewSessionState.PAUSED,
        )


__all__ = [
    "INTERVIEW_ORCHESTRATION_SCHEMA_VERSION",
    "MAX_DEEPENING_TURNS_BEFORE_SUMMARY",
    "MIN_DEEPENING_TURNS_BEFORE_SUMMARY",
    "MIN_TURNS_BEFORE_CANDIDATE_BATCH",
    "InterviewAction",
    "InterviewBoundary",
    "InterviewDecision",
    "InterviewFatigue",
    "InterviewOrchestrationError",
    "InterviewOrchestrationInput",
    "InterviewOrchestrator",
    "InterviewSessionState",
]
