"""Read-only bridge from persisted M0-A sessions to interview policy.

The conversation repository is authoritative for session state, boundary,
owner turn count, pacing counters, fatigue, and authority epoch. Callers may
supply only bounded, value-free policy signals such as an opaque topic
identifier. This bridge cannot read message text, call a provider, or create an
Owner Truth Source, Candidate, DecisionReceipt, or MemoryVersion.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from app.domain.owner_truth.conversation import (
    InterviewBoundary as PersistedInterviewBoundary,
    InterviewSessionState as PersistedInterviewSessionState,
    OwnerTruthInterviewSessionSnapshot,
)
from app.domain.owner_truth.interview_orchestration import (
    INTERVIEW_ORCHESTRATION_SCHEMA_VERSION,
    InterviewBoundary as PolicyInterviewBoundary,
    InterviewDecision,
    InterviewFatigue,
    InterviewOrchestrationInput,
    InterviewOrchestrator,
    InterviewSessionState as PolicyInterviewSessionState,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_conversation import OwnerTruthConversationService


INTERVIEW_SESSION_ORCHESTRATION_SCHEMA_VERSION = (
    "owner-truth-interview-session-orchestration-v1"
)


class OwnerTruthInterviewSessionOrchestrationError(ValueError):
    """The bounded, value-free policy signal envelope is malformed."""


@dataclass(frozen=True)
class InterviewSessionOrchestrationSignals:
    """Transient policy signals that never contain raw conversation content.

    Pacing counters, fatigue, session state, user boundary, owner, Vault,
    thread, and authority epoch deliberately are not caller supplied. The
    bridge reads those values from the persisted private session instead.
    """

    topic_id: str
    topic_incomplete: bool = False
    needs_clarification: bool = False
    user_changed_topic: bool = False
    is_sensitive: bool = False

    def __post_init__(self) -> None:
        for field in (
            "topic_incomplete",
            "needs_clarification",
            "user_changed_topic",
            "is_sensitive",
        ):
            if not isinstance(getattr(self, field), bool):
                raise OwnerTruthInterviewSessionOrchestrationError(
                    f"{field} must be a boolean"
                )


@dataclass(frozen=True)
class OwnerTruthInterviewSessionOrchestrationResult:
    """Private result with a trace-safe, value-free summary projection."""

    persisted_session: OwnerTruthInterviewSessionSnapshot
    decision: InterviewDecision
    persisted_owner_turn_count: int
    persisted_boundary: PersistedInterviewBoundary
    persisted_deepening_turn_count: int
    persisted_candidate_batch_turn_count: int
    persisted_fatigue: InterviewFatigue

    def value_free_summary(self) -> Mapping[str, object]:
        """Expose no identifiers, topic names, or message content to QA traces."""

        return {
            "schemaVersion": INTERVIEW_SESSION_ORCHESTRATION_SCHEMA_VERSION,
            "policySchemaVersion": INTERVIEW_ORCHESTRATION_SCHEMA_VERSION,
            "decision": self.decision.value_free_summary(),
            "persistedSession": {
                "boundary": self.persisted_boundary.value,
                "candidateBatchTurnCount": self.persisted_candidate_batch_turn_count,
                "deepeningTurnCount": self.persisted_deepening_turn_count,
                "fatigue": self.persisted_fatigue.value,
                "ownerTurnCount": self.persisted_owner_turn_count,
                "state": self.persisted_session.state.value,
            },
            "transientSignals": "opaqueTopicAndBooleanPolicySignalsOnly",
        }


_POLICY_SESSION_STATE_BY_PERSISTED_STATE = {
    PersistedInterviewSessionState.ACTIVE: PolicyInterviewSessionState.ACTIVE,
    PersistedInterviewSessionState.PAUSED: PolicyInterviewSessionState.PAUSED,
    PersistedInterviewSessionState.ENDED: PolicyInterviewSessionState.ENDED,
}

_POLICY_BOUNDARY_BY_PERSISTED_BOUNDARY = {
    PersistedInterviewBoundary.OPEN: PolicyInterviewBoundary.NONE,
    PersistedInterviewBoundary.SKIP_ONCE: PolicyInterviewBoundary.SKIP_ONCE,
    PersistedInterviewBoundary.COOLDOWN: PolicyInterviewBoundary.COOLDOWN,
    PersistedInterviewBoundary.DO_NOT_ASK: PolicyInterviewBoundary.DO_NOT_ASK,
}


class OwnerTruthInterviewSessionOrchestrationService:
    """Decide the next safe interview action from one persisted session.

    This service is read-only. Pacing changes are recorded only by an explicit
    private command after their real interaction milestone completes; deciding
    an action is not itself an effect.
    """

    def __init__(
        self,
        *,
        conversation_service: OwnerTruthConversationService,
        orchestrator: InterviewOrchestrator | None = None,
    ) -> None:
        if not isinstance(conversation_service, OwnerTruthConversationService):
            raise TypeError("OwnerTruthConversationService is required")
        self._conversation_service = conversation_service
        self._orchestrator = orchestrator or InterviewOrchestrator()

    def decide(
        self,
        *,
        session_id: str,
        context: OwnerTruthCommandContext,
        signals: InterviewSessionOrchestrationSignals,
    ) -> OwnerTruthInterviewSessionOrchestrationResult:
        if not isinstance(signals, InterviewSessionOrchestrationSignals):
            raise TypeError("InterviewSessionOrchestrationSignals is required")
        persisted = self._conversation_service.read_session(
            session_id=session_id,
            context=context,
        )
        try:
            policy_state = _POLICY_SESSION_STATE_BY_PERSISTED_STATE[persisted.state]
            policy_boundary = _POLICY_BOUNDARY_BY_PERSISTED_BOUNDARY[persisted.boundary]
        except KeyError as exc:
            raise OwnerTruthInterviewSessionOrchestrationError(
                "persisted interview session contains an unsupported policy state"
            ) from exc

        decision = self._orchestrator.decide(
            InterviewOrchestrationInput(
                thread_id=persisted.thread_id,
                vault_id=persisted.vault_id,
                owner_subject_id=persisted.owner_subject_id,
                topic_id=signals.topic_id,
                authority_epoch=persisted.authority_epoch,
                session_state=policy_state,
                deepening_turn_count=persisted.deepening_turn_count,
                candidate_batch_turn_count=persisted.candidate_batch_turn_count,
                topic_incomplete=signals.topic_incomplete,
                needs_clarification=signals.needs_clarification,
                user_changed_topic=signals.user_changed_topic,
                user_boundary=policy_boundary,
                is_sensitive=signals.is_sensitive,
                fatigue=persisted.fatigue,
            )
        )
        return OwnerTruthInterviewSessionOrchestrationResult(
            persisted_session=persisted,
            decision=decision,
            persisted_owner_turn_count=persisted.turn_count,
            persisted_boundary=persisted.boundary,
            persisted_deepening_turn_count=persisted.deepening_turn_count,
            persisted_candidate_batch_turn_count=persisted.candidate_batch_turn_count,
            persisted_fatigue=persisted.fatigue,
        )


__all__ = [
    "INTERVIEW_SESSION_ORCHESTRATION_SCHEMA_VERSION",
    "InterviewSessionOrchestrationSignals",
    "OwnerTruthInterviewSessionOrchestrationError",
    "OwnerTruthInterviewSessionOrchestrationResult",
    "OwnerTruthInterviewSessionOrchestrationService",
]
