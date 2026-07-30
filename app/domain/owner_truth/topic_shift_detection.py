"""Conservative, transient topic-shift detection for M0 interview shadowing.

This module is intentionally outside ``interview_orchestration``: the
orchestrator accepts only opaque identifiers and boolean policy signals, while
this detector may briefly inspect the current owner message.  It retains no
message text, calls no provider, and produces only a boolean plus a fixed
reason code suitable for the existing value-free audit lane.

The first version detects only explicit, user-authored topic-change cues.  It
does not infer a semantic topic from a narrative and therefore deliberately
prefers missed detections to accidentally pausing an active interview.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re


TOPIC_SHIFT_DETECTION_SCHEMA_VERSION = "owner-truth-topic-shift-detection-v1"


class TopicShiftReasonCode(str, Enum):
    EXPLICIT_TOPIC_CHANGE_CUE = "explicitTopicChangeCue"
    NO_EXPLICIT_TOPIC_CHANGE_CUE = "noExplicitTopicChangeCue"


@dataclass(frozen=True)
class TopicShiftDetection:
    """Content-free result of inspecting one transient owner message."""

    user_changed_topic: bool
    reason_code: TopicShiftReasonCode

    def __post_init__(self) -> None:
        if not isinstance(self.user_changed_topic, bool):
            raise TypeError("user_changed_topic must be a boolean")
        object.__setattr__(self, "reason_code", TopicShiftReasonCode(self.reason_code))
        if (
            self.user_changed_topic
            != (self.reason_code is TopicShiftReasonCode.EXPLICIT_TOPIC_CHANGE_CUE)
        ):
            raise ValueError("topic-shift detection reason does not match its boolean result")

    def value_free_summary(self) -> dict[str, object]:
        return {
            "reasonCode": self.reason_code.value,
            "schemaVersion": TOPIC_SHIFT_DETECTION_SCHEMA_VERSION,
            "userChangedTopic": self.user_changed_topic,
        }


class TopicShiftDetector:
    """Recognize only explicit topic-change language, without persistence."""

    _EXPLICIT_TOPIC_CHANGE_CUES = (
        re.compile(r"(?:^|[，。！？；;、\s])(?:我(?:们)?(?:想|要)?|那(?:我(?:们)?)?)?(?:换(?:个|一)(?:话题|主题|事情|事))(?:吧|呀|啊|。|，|！|？|$)"),
        re.compile(r"(?:^|[，。！？；;、\s])(?:先|还是)?(?:别|不|先不)(?:再)?聊(?:这个|这件事|这个话题)(?:了|吧|。|，|！|？|$)"),
        re.compile(r"(?:^|[，。！？；;、\s])(?:我(?:们)?(?:想|要)?)(?:聊|说)(?:点|些)?(?:别的|其他的)(?:吧|呀|啊|。|，|！|？|$)"),
        re.compile(r"\b(?:let(?:'s| us)\s+(?:change|switch)\s+(?:the\s+)?topic|switch\s+topics|talk\s+about\s+something\s+else)\b", re.IGNORECASE),
    )

    def detect(self, user_text: str) -> TopicShiftDetection:
        if not isinstance(user_text, str):
            raise TypeError("user_text must be a string")

        normalized = " ".join(user_text.split())
        if any(pattern.search(normalized) for pattern in self._EXPLICIT_TOPIC_CHANGE_CUES):
            return TopicShiftDetection(
                user_changed_topic=True,
                reason_code=TopicShiftReasonCode.EXPLICIT_TOPIC_CHANGE_CUE,
            )
        return TopicShiftDetection(
            user_changed_topic=False,
            reason_code=TopicShiftReasonCode.NO_EXPLICIT_TOPIC_CHANGE_CUE,
        )


__all__ = [
    "TOPIC_SHIFT_DETECTION_SCHEMA_VERSION",
    "TopicShiftDetection",
    "TopicShiftDetector",
    "TopicShiftReasonCode",
]
