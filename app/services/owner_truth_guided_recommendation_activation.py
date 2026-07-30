"""Formal activation for a display-safe guided recommendation.

The public route may name only an opaque recommendation-set binding and slot.
It never receives or persists the rendered assistant question as Owner input.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, ContextManager

from app.domain.owner_truth.contracts import OwnerTruthContractError, require_nonblank
from app.domain.owner_truth.conversation import OwnerTruthConversationAccessDenied
from app.domain.owner_truth.interview_orchestration import InterviewAction
from app.domain.owner_truth.knowledge_dimension_read import OwnerTruthKnowledgeDimensionReadState
from app.domain.owner_truth.knowledge_recommendations import RecommendationSlot
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_knowledge_recommendation_activation import (
    OwnerTruthKnowledgeRecommendationActivationAccessDenied,
    OwnerTruthKnowledgeRecommendationActivationCommand,
    OwnerTruthKnowledgeRecommendationActivationConflict,
    OwnerTruthKnowledgeRecommendationActivationError,
    OwnerTruthKnowledgeRecommendationActivationResult,
    OwnerTruthKnowledgeRecommendationActivationService,
    OwnerTruthKnowledgeRecommendationActivationStale,
    OwnerTruthKnowledgeRecommendationActivationUnavailable,
)
from app.services.owner_truth_knowledge_recommendation_feedback import (
    guided_recommendation_set_id,
)
from app.services.owner_truth_knowledge_recommendation_read import (
    OwnerTruthKnowledgeRecommendationReadService,
)


OWNER_TRUTH_GUIDED_RECOMMENDATION_ACTIVATION_SCHEMA_VERSION = (
    "owner-truth-guided-recommendation-activation-v1"
)
_SHA256_LENGTH = 64


def _sha256(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _require_sha256(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if len(normalized) != _SHA256_LENGTH or any(char not in "0123456789abcdef" for char in normalized):
        raise OwnerTruthKnowledgeRecommendationActivationError(
            f"{field} must be a SHA-256 digest"
        )
    return normalized


@dataclass(frozen=True)
class OwnerTruthGuidedRecommendationActivationCommand:
    """No planner identifiers, question text, or conversation handles cross UI."""

    command_id: str
    recommendation_set_id: str
    slot: RecommendationSlot | str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "command_id",
            require_nonblank(self.command_id, field="command_id"),
        )
        object.__setattr__(
            self,
            "recommendation_set_id",
            _require_sha256(self.recommendation_set_id, field="recommendation_set_id"),
        )
        try:
            object.__setattr__(self, "slot", RecommendationSlot(self.slot))
        except (TypeError, ValueError) as error:
            raise OwnerTruthKnowledgeRecommendationActivationError(
                "guided recommendation slot is not supported"
            ) from error

    @property
    def command_id_hash(self) -> str:
        return _sha256(self.command_id)


@dataclass(frozen=True)
class OwnerTruthGuidedRecommendationActivationResult:
    """Display-safe result proving the UI may await an Owner narrative."""

    outcome: str
    slot: RecommendationSlot | str
    next_action: InterviewAction | str

    def __post_init__(self) -> None:
        if self.outcome not in {"created", "deduplicated"}:
            raise OwnerTruthKnowledgeRecommendationActivationError(
                "guided recommendation activation outcome is not supported"
            )
        try:
            object.__setattr__(self, "slot", RecommendationSlot(self.slot))
            object.__setattr__(self, "next_action", InterviewAction(self.next_action))
        except (TypeError, ValueError) as error:
            raise OwnerTruthKnowledgeRecommendationActivationError(
                "guided recommendation activation contains an unsupported enum value"
            ) from error

    def value_free_summary(self) -> dict[str, str]:
        return {
            "status": self.outcome,
            "slot": self.slot.value,
            "nextAction": self.next_action.value,
            "inputState": "awaitingOwnerNarrative",
        }


class OwnerTruthGuidedRecommendationActivationService:
    """Revalidate the current set and reuse the durable activation receipt."""

    def __init__(self, store: object) -> None:
        self._store = store

    def activate(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: OwnerTruthGuidedRecommendationActivationCommand,
    ) -> OwnerTruthGuidedRecommendationActivationResult:
        if not isinstance(context, OwnerTruthCommandContext):
            raise OwnerTruthKnowledgeRecommendationActivationAccessDenied(
                "owner truth command context is required"
            )
        if context.actor_subject_id != context.owner_subject_id:
            raise OwnerTruthKnowledgeRecommendationActivationAccessDenied(
                "only the Vault Owner may activate a recommendation"
            )
        if not isinstance(command, OwnerTruthGuidedRecommendationActivationCommand):
            raise OwnerTruthKnowledgeRecommendationActivationError(
                "guided recommendation activation command is required"
            )

        with self._request_unit_of_work(
            correlation_id=(
                "owner-truth-guided-recommendation-activation-"
                f"{context.vault_id}:{command.recommendation_set_id}"
            ),
            command_id=command.command_id_hash,
        ):
            return self._activate_within_unit_of_work(
                context=context,
                command=command,
            )

    def _activate_within_unit_of_work(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: OwnerTruthGuidedRecommendationActivationCommand,
    ) -> OwnerTruthGuidedRecommendationActivationResult:
        repository = self._store.owner_truth_knowledge_recommendation_activation_repository()
        replayed = repository.replay_guided(
            context=context,
            command_id_hash=command.command_id_hash,
            recommendation_set_id=command.recommendation_set_id,
            slot=command.slot,
        )
        if replayed is not None:
            return self._public_result(replayed)

        try:
            plan = OwnerTruthKnowledgeRecommendationReadService(self._store).plan(
                context=context
            )
        except OwnerTruthContractError as error:
            raise OwnerTruthKnowledgeRecommendationActivationUnavailable(
                "current Owner-confirmed recommendation plan is unavailable"
            ) from error
        if (
            plan.state is not OwnerTruthKnowledgeDimensionReadState.READY
            or plan.selection is None
        ):
            raise OwnerTruthKnowledgeRecommendationActivationUnavailable(
                "current Owner-confirmed recommendation plan is unavailable"
            )

        current_set_id = guided_recommendation_set_id(
            context=context,
            authority_epoch=plan.dimension_read.authority_epoch,
            decisions=plan.selection.selected,
        )
        if current_set_id != command.recommendation_set_id:
            raise OwnerTruthKnowledgeRecommendationActivationStale(
                "guided recommendation set is no longer current"
            )
        selected = tuple(
            decision
            for decision in plan.selection.selected
            if decision.slot is command.slot
        )
        if len(selected) != 1:
            raise OwnerTruthKnowledgeRecommendationActivationStale(
                "guided recommendation slot is no longer selected"
            )
        decision = selected[0]
        conversation = self._store.owner_truth_conversation_repository()
        try:
            thread = conversation.get_interview_thread_authority(
                thread_id=decision.thread_id,
                context=context,
            )
            if thread.session_id is None:
                raise OwnerTruthKnowledgeRecommendationActivationStale(
                    "guided recommendation has no current interview session"
                )
            session = conversation.get_interview_session(
                session_id=thread.session_id,
                context=context,
            )
        except OwnerTruthConversationAccessDenied as error:
            raise OwnerTruthKnowledgeRecommendationActivationAccessDenied(str(error)) from error
        except OwnerTruthContractError as error:
            raise OwnerTruthKnowledgeRecommendationActivationStale(
                "guided recommendation no longer has current conversation authority"
            ) from error

        internal = OwnerTruthKnowledgeRecommendationActivationCommand(
            command_id=command.command_id,
            expected_candidate_id=decision.candidate_id,
            slot=command.slot,
            expected_session_version=session.row_version,
            guided_recommendation_set_id=command.recommendation_set_id,
        )
        result = OwnerTruthKnowledgeRecommendationActivationService(
            self._store,
            enabled=True,
        ).accept(context=context, command=internal)
        return self._public_result(result)

    def _request_unit_of_work(
        self,
        *,
        correlation_id: str,
        command_id: str,
    ) -> ContextManager[Any]:
        factory = getattr(self._store, "request_unit_of_work", None)
        if callable(factory):
            return factory(correlation_id=correlation_id, command_id=command_id)
        return nullcontext()

    @staticmethod
    def _public_result(
        result: OwnerTruthKnowledgeRecommendationActivationResult,
    ) -> OwnerTruthGuidedRecommendationActivationResult:
        return OwnerTruthGuidedRecommendationActivationResult(
            outcome=result.outcome,
            slot=result.slot,
            next_action=result.next_action,
        )


__all__ = [
    "OWNER_TRUTH_GUIDED_RECOMMENDATION_ACTIVATION_SCHEMA_VERSION",
    "OwnerTruthGuidedRecommendationActivationCommand",
    "OwnerTruthGuidedRecommendationActivationResult",
    "OwnerTruthGuidedRecommendationActivationService",
]
