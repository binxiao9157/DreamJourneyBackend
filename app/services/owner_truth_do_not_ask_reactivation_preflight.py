"""Write-free preflight for an explicit do-not-ask topic reactivation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.domain.owner_truth.do_not_ask_reactivation_detection import (
    DO_NOT_ASK_REACTIVATION_DETECTION_SCHEMA_VERSION,
    DoNotAskReactivationDetector,
)
from app.domain.owner_truth.interview_orchestration import InterviewAction
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_conversation import OwnerTruthConversationService
from app.services.owner_truth_interview_session_orchestration import (
    InterviewSessionOrchestrationSignals,
    OwnerTruthInterviewSessionOrchestrationService,
)


OWNER_TRUTH_DO_NOT_ASK_REACTIVATION_PREFLIGHT_SCHEMA_VERSION = (
    "owner-truth-do-not-ask-reactivation-preflight-v1"
)
_SERVER_OWNED_TOPIC_ID = "naturalInputDoNotAskReactivation"


class OwnerTruthDoNotAskReactivationPreflightStatus(str, Enum):
    NOT_REQUIRED = "notRequired"
    CONFIRMATION_REQUIRED = "confirmationRequired"


@dataclass(frozen=True)
class OwnerTruthDoNotAskReactivationPreflightResult:
    """A value-free result that never exposes input text or session internals."""

    status: OwnerTruthDoNotAskReactivationPreflightStatus
    reason_code: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "status",
            OwnerTruthDoNotAskReactivationPreflightStatus(self.status),
        )
        if self.reason_code not in {
            "doNotAskRestoreConfirmationRequired",
            "noExplicitDoNotAskReactivationCue",
            "sessionDoesNotRequireDoNotAskRestore",
        }:
            raise ValueError("do-not-ask reactivation preflight reason is unsupported")
        if (
            self.status is OwnerTruthDoNotAskReactivationPreflightStatus.CONFIRMATION_REQUIRED
            and self.reason_code != "doNotAskRestoreConfirmationRequired"
        ):
            raise ValueError("confirmation-required preflight has an invalid reason")

    @property
    def requires_confirmation(self) -> bool:
        return self.status is OwnerTruthDoNotAskReactivationPreflightStatus.CONFIRMATION_REQUIRED

    def value_free_summary(self) -> dict[str, object]:
        return {
            "reasonCode": self.reason_code,
            "requiresConfirmation": self.requires_confirmation,
            "schemaVersion": OWNER_TRUTH_DO_NOT_ASK_REACTIVATION_PREFLIGHT_SCHEMA_VERSION,
            "status": self.status.value,
        }


class OwnerTruthDoNotAskReactivationPreflightService:
    """Request confirmation before a natural-input write could bypass a boundary.

    The service intentionally does not restore a boundary, append a message,
    start a thread, or record a receipt.  It only invokes the existing
    orchestrator's value-free clarification decision.
    """

    def __init__(
        self,
        *,
        conversation_service: OwnerTruthConversationService,
        detector: DoNotAskReactivationDetector | None = None,
    ) -> None:
        if not isinstance(conversation_service, OwnerTruthConversationService):
            raise TypeError("OwnerTruthConversationService is required")
        self._conversation_service = conversation_service
        self._detector = detector or DoNotAskReactivationDetector()
        self._orchestration = OwnerTruthInterviewSessionOrchestrationService(
            conversation_service=conversation_service
        )

    def preflight(
        self,
        *,
        session_id: str,
        context: OwnerTruthCommandContext,
        user_text: str,
    ) -> OwnerTruthDoNotAskReactivationPreflightResult:
        detection = self._detector.detect(user_text)
        if not detection.user_reopened_do_not_ask_topic:
            return OwnerTruthDoNotAskReactivationPreflightResult(
                status=OwnerTruthDoNotAskReactivationPreflightStatus.NOT_REQUIRED,
                reason_code="noExplicitDoNotAskReactivationCue",
            )

        result = self._orchestration.decide(
            session_id=session_id,
            context=context,
            signals=InterviewSessionOrchestrationSignals(
                topic_id=_SERVER_OWNED_TOPIC_ID,
                user_reopened_do_not_ask_topic=True,
            ),
        )
        if (
            result.decision.action is InterviewAction.CLARIFY
            and result.decision.reason_code == "doNotAskRestoreConfirmationRequired"
        ):
            return OwnerTruthDoNotAskReactivationPreflightResult(
                status=OwnerTruthDoNotAskReactivationPreflightStatus.CONFIRMATION_REQUIRED,
                reason_code=result.decision.reason_code,
            )
        return OwnerTruthDoNotAskReactivationPreflightResult(
            status=OwnerTruthDoNotAskReactivationPreflightStatus.NOT_REQUIRED,
            reason_code="sessionDoesNotRequireDoNotAskRestore",
        )


__all__ = [
    "OWNER_TRUTH_DO_NOT_ASK_REACTIVATION_PREFLIGHT_SCHEMA_VERSION",
    "OwnerTruthDoNotAskReactivationPreflightResult",
    "OwnerTruthDoNotAskReactivationPreflightService",
    "OwnerTruthDoNotAskReactivationPreflightStatus",
]
