"""Conservative, transient detection of explicit do-not-ask reactivation.

The result never contains owner input.  It exists solely to let the natural
input write path request explicit confirmation before a paused ``doNotAsk``
session could be restored through its already-existing command.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re


DO_NOT_ASK_REACTIVATION_DETECTION_SCHEMA_VERSION = (
    "owner-truth-do-not-ask-reactivation-detection-v1"
)


class DoNotAskReactivationReasonCode(str, Enum):
    EXPLICIT_REACTIVATION_CUE = "explicitDoNotAskReactivationCue"
    NO_EXPLICIT_REACTIVATION_CUE = "noExplicitDoNotAskReactivationCue"


@dataclass(frozen=True)
class DoNotAskReactivationDetection:
    """Content-free result from inspecting one transient natural-input value."""

    user_reopened_do_not_ask_topic: bool
    reason_code: DoNotAskReactivationReasonCode

    def __post_init__(self) -> None:
        if not isinstance(self.user_reopened_do_not_ask_topic, bool):
            raise TypeError("user_reopened_do_not_ask_topic must be a boolean")
        object.__setattr__(
            self,
            "reason_code",
            DoNotAskReactivationReasonCode(self.reason_code),
        )
        if (
            self.user_reopened_do_not_ask_topic
            != (
                self.reason_code
                is DoNotAskReactivationReasonCode.EXPLICIT_REACTIVATION_CUE
            )
        ):
            raise ValueError("do-not-ask reactivation reason does not match its boolean result")

    def value_free_summary(self) -> dict[str, object]:
        return {
            "reasonCode": self.reason_code.value,
            "schemaVersion": DO_NOT_ASK_REACTIVATION_DETECTION_SCHEMA_VERSION,
            "userReopenedDoNotAskTopic": self.user_reopened_do_not_ask_topic,
        }


class DoNotAskReactivationDetector:
    """Detect only unambiguous owner consent to resume a stopped topic."""

    _NEGATIVE_CUES = (
        re.compile(r"(?:不想|不要|不愿意|别)(?:再|重新|继续|恢复)(?:聊|谈|说|问)"),
        re.compile(r"\b(?:do not|don't|dont)\s+(?:ask|talk)\b", re.IGNORECASE),
    )
    _EXPLICIT_REACTIVATION_CUES = (
        re.compile(r"(?:我(?:愿意|想|可以)|我们(?:可以)?)(?:再|重新|继续|恢复)(?:聊|谈|说)(?:这个|这件事|这个话题)"),
        re.compile(r"(?:你|系统)(?:可以|能)(?:再|重新|继续)(?:问|聊)(?:我)?(?:这个|这件事|这个话题)"),
        re.compile(r"(?:我(?:愿意|同意))(?:让你)?(?:再|重新|继续)(?:问|聊)"),
        re.compile(r"\b(?:i(?:'m| am)\s+ready\s+to\s+talk\s+about\s+this\s+again|you\s+can\s+ask\s+me\s+about\s+this\s+again)\b", re.IGNORECASE),
    )

    def detect(self, user_text: str) -> DoNotAskReactivationDetection:
        if not isinstance(user_text, str):
            raise TypeError("user_text must be a string")

        normalized = " ".join(user_text.split())
        if any(pattern.search(normalized) for pattern in self._NEGATIVE_CUES):
            return DoNotAskReactivationDetection(
                user_reopened_do_not_ask_topic=False,
                reason_code=DoNotAskReactivationReasonCode.NO_EXPLICIT_REACTIVATION_CUE,
            )
        if any(pattern.search(normalized) for pattern in self._EXPLICIT_REACTIVATION_CUES):
            return DoNotAskReactivationDetection(
                user_reopened_do_not_ask_topic=True,
                reason_code=DoNotAskReactivationReasonCode.EXPLICIT_REACTIVATION_CUE,
            )
        return DoNotAskReactivationDetection(
            user_reopened_do_not_ask_topic=False,
            reason_code=DoNotAskReactivationReasonCode.NO_EXPLICIT_REACTIVATION_CUE,
        )


__all__ = [
    "DO_NOT_ASK_REACTIVATION_DETECTION_SCHEMA_VERSION",
    "DoNotAskReactivationDetection",
    "DoNotAskReactivationDetector",
    "DoNotAskReactivationReasonCode",
]
