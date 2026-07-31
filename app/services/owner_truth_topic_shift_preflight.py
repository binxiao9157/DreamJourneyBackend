"""Write-free QA preflight for an explicit natural-language topic switch."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.domain.owner_truth.interview_orchestration import InterviewAction
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.domain.owner_truth.topic_shift_detection import TopicShiftDetector
from app.services.owner_truth_conversation import OwnerTruthConversationService
from app.services.owner_truth_interview_session_orchestration import (
    InterviewSessionOrchestrationSignals,
    OwnerTruthInterviewSessionOrchestrationService,
)


OWNER_TRUTH_TOPIC_SHIFT_PREFLIGHT_SCHEMA_VERSION = (
    "owner-truth-topic-shift-preflight-v1"
)
_SERVER_OWNED_TOPIC_ID = "naturalInputTopicShift"


class OwnerTruthTopicShiftPreflightStatus(str, Enum):
    NOT_REQUIRED = "notRequired"
    TOPIC_SWITCH_REQUIRED = "topicSwitchRequired"


@dataclass(frozen=True)
class OwnerTruthTopicShiftPreflightResult:
    """Value-free result for the existing explicit pause-and-restart flow."""

    status: OwnerTruthTopicShiftPreflightStatus
    reason_code: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", OwnerTruthTopicShiftPreflightStatus(self.status))
        if self.reason_code not in {
            "explicitTopicSwitchRequired",
            "noExplicitTopicChangeCue",
            "sessionDoesNotRequireTopicSwitch",
        }:
            raise ValueError("topic-shift preflight reason is unsupported")
        if (
            self.status is OwnerTruthTopicShiftPreflightStatus.TOPIC_SWITCH_REQUIRED
            and self.reason_code != "explicitTopicSwitchRequired"
        ):
            raise ValueError("topic-switch-required preflight has an invalid reason")

    @property
    def requires_topic_switch(self) -> bool:
        return self.status is OwnerTruthTopicShiftPreflightStatus.TOPIC_SWITCH_REQUIRED

    def value_free_summary(self) -> dict[str, object]:
        return {
            "reasonCode": self.reason_code,
            "requiresTopicSwitch": self.requires_topic_switch,
            "schemaVersion": OWNER_TRUTH_TOPIC_SHIFT_PREFLIGHT_SCHEMA_VERSION,
            "status": self.status.value,
        }


class OwnerTruthTopicShiftPreflightService:
    """Stop a matching write before it can land in the prior interview thread.

    The service only checks a transient explicit cue and reads the current
    Owner session through the existing value-free orchestration bridge. It
    never appends a message, pauses a session, creates a new thread, or writes
    Source/Candidate/Memory/Provider state.
    """

    def __init__(
        self,
        *,
        conversation_service: OwnerTruthConversationService,
        detector: TopicShiftDetector | None = None,
    ) -> None:
        if not isinstance(conversation_service, OwnerTruthConversationService):
            raise TypeError("OwnerTruthConversationService is required")
        self._detector = detector or TopicShiftDetector()
        self._orchestration = OwnerTruthInterviewSessionOrchestrationService(
            conversation_service=conversation_service
        )

    def preflight(
        self,
        *,
        session_id: str,
        context: OwnerTruthCommandContext,
        user_text: str,
    ) -> OwnerTruthTopicShiftPreflightResult:
        detection = self._detector.detect(user_text)
        if not detection.user_changed_topic:
            return OwnerTruthTopicShiftPreflightResult(
                status=OwnerTruthTopicShiftPreflightStatus.NOT_REQUIRED,
                reason_code="noExplicitTopicChangeCue",
            )

        result = self._orchestration.decide(
            session_id=session_id,
            context=context,
            signals=InterviewSessionOrchestrationSignals(
                topic_id=_SERVER_OWNED_TOPIC_ID,
                user_changed_topic=True,
            ),
        )
        if (
            result.decision.action is InterviewAction.PAUSE
            and result.decision.reason_code == "topicChanged"
        ):
            return OwnerTruthTopicShiftPreflightResult(
                status=OwnerTruthTopicShiftPreflightStatus.TOPIC_SWITCH_REQUIRED,
                reason_code="explicitTopicSwitchRequired",
            )
        return OwnerTruthTopicShiftPreflightResult(
            status=OwnerTruthTopicShiftPreflightStatus.NOT_REQUIRED,
            reason_code="sessionDoesNotRequireTopicSwitch",
        )


__all__ = [
    "OWNER_TRUTH_TOPIC_SHIFT_PREFLIGHT_SCHEMA_VERSION",
    "OwnerTruthTopicShiftPreflightResult",
    "OwnerTruthTopicShiftPreflightService",
    "OwnerTruthTopicShiftPreflightStatus",
]
