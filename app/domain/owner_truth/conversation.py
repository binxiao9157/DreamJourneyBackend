"""Owner Truth M0-A conversation and interview-session contracts.

This module deliberately models only the private conversation lane. A message
is not an Owner Truth Source, Candidate, DecisionReceipt, or MemoryVersion.
Promoting a user statement into those authority records remains an explicit
later command with its own review policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
from typing import Any, Mapping, Optional, Tuple
from uuid import UUID, uuid5

from .contracts import OwnerTruthContractError, require_nonblank, require_uuid
from .interview_orchestration import (
    InterviewFatigue,
    MAX_DEEPENING_TURNS_BEFORE_SUMMARY,
    MIN_TURNS_BEFORE_CANDIDATE_BATCH,
)
from .source_commands import OwnerTruthCommandContext


OWNER_TRUTH_CONVERSATION_SCHEMA_VERSION = "owner-truth-conversation-v1"
_RECEIPT_NAMESPACE = UUID("10c87c90-f96f-4da5-98d2-f1925d472c26")
_REVIEW_BATCH_NAMESPACE = UUID("66e875da-e2ca-40b1-8a68-a9074b8fac27")
_MAX_MESSAGE_CHARACTERS = 20_000
_ENTRY_MODES = frozenset({"naturalInput", "recommendation", "resume"})


class OwnerTruthConversationError(OwnerTruthContractError):
    """Base error for the private M0-A conversation lane."""


class OwnerTruthConversationConflict(OwnerTruthConversationError):
    """A stable command or record identity was reused with different meaning."""


class OwnerTruthConversationAccessDenied(OwnerTruthConversationError):
    """The caller is not the active Owner of the target Vault."""


class OwnerTruthConversationVersionConflict(OwnerTruthConversationError):
    """A command tried to update a stale interview thread or session."""

    def __init__(self, *, resource: str, expected_version: int, current_version: int):
        self.resource = resource
        self.expected_version = expected_version
        self.current_version = current_version
        super().__init__(f"owner truth {resource} version does not match expectedVersion")


class OwnerTruthInterviewSessionStateConflict(OwnerTruthConversationError):
    """A message was attempted while the interview session is not active."""


class ConversationMessageAuthor(str, Enum):
    OWNER = "owner"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class ConversationMessageKind(str, Enum):
    NARRATIVE = "narrative"
    QUESTION = "question"
    SUMMARY = "summary"


class ConversationThreadState(str, Enum):
    """Lifecycle state for a private ConversationThread.

    Recommendation reads may only continue a currently active Thread.  The
    value deliberately carries no message, title, or other private content.
    """

    ACTIVE = "active"
    PAUSED = "paused"
    ENDED = "ended"


class InterviewBoundary(str, Enum):
    OPEN = "open"
    SKIP_ONCE = "skipOnce"
    COOLDOWN = "cooldown"
    DO_NOT_ASK = "doNotAsk"


class InterviewSessionState(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    ENDED = "ended"


class InterviewPacingEvent(str, Enum):
    """Explicit, content-free facts that can advance session pacing state."""

    DEEPENING_COMPLETED = "deepeningCompleted"
    SUMMARY_COMPLETED = "summaryCompleted"
    FATIGUE_GUARDED = "fatigueGuarded"
    FATIGUE_EXHAUSTED = "fatigueExhausted"
    FATIGUE_RESET = "fatigueReset"
    SKIP_ONCE_CONSUMED = "skipOnceConsumed"


class InterviewReviewBatchState(str, Enum):
    """A private interview review batch is not a Candidate decision."""

    PENDING_ACKNOWLEDGEMENT = "pendingAcknowledgement"
    ACKNOWLEDGED = "acknowledged"


class InterviewReviewBatchTrigger(str, Enum):
    """The persisted policy condition that made a batch reviewable."""

    TURN_THRESHOLD = "turnThreshold"
    SESSION_EXIT = "sessionExit"


def _canonical_json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthConversationError("conversation payload must be JSON serializable") from exc


def _sha256(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _positive_version(value: int, *, field: str, allow_zero: bool = False) -> int:
    if not isinstance(value, int) or value < (0 if allow_zero else 1):
        minimum = "non-negative" if allow_zero else "positive"
        raise OwnerTruthConversationError(f"{field} must be a {minimum} integer")
    return value


def _receipt_id(*, context: OwnerTruthCommandContext, command_id_hash: str) -> str:
    return str(uuid5(_RECEIPT_NAMESPACE, f"{context.vault_id}:{command_id_hash}"))


def _review_batch_id(
    *,
    context: OwnerTruthCommandContext,
    session_id: str,
    command_id_hash: str,
) -> str:
    return str(
        uuid5(
            _REVIEW_BATCH_NAMESPACE,
            f"{context.vault_id}:{session_id}:{command_id_hash}",
        )
    )


def _normalise_enum(value: Any, enum_type: Any, *, field: str) -> Any:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise OwnerTruthConversationError(f"{field} is not supported") from exc


@dataclass(frozen=True)
class InterviewPacingState:
    """Private, bounded state used by the provider-neutral interview policy."""

    boundary: InterviewBoundary
    deepening_turn_count: int
    candidate_batch_turn_count: int
    fatigue: InterviewFatigue

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "boundary",
            _normalise_enum(self.boundary, InterviewBoundary, field="boundary"),
        )
        try:
            object.__setattr__(self, "fatigue", InterviewFatigue(self.fatigue))
        except (TypeError, ValueError) as exc:
            raise OwnerTruthConversationError("fatigue is not supported") from exc
        for field in ("deepening_turn_count", "candidate_batch_turn_count"):
            value = getattr(self, field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise OwnerTruthConversationError(f"{field} must be a non-negative integer")

    def apply(self, event: InterviewPacingEvent) -> "InterviewPacingState":
        event = _normalise_enum(event, InterviewPacingEvent, field="event")
        if event is InterviewPacingEvent.DEEPENING_COMPLETED:
            if self.boundary is not InterviewBoundary.OPEN:
                raise OwnerTruthConversationConflict(
                    "deepening requires an open interview boundary"
                )
            if self.fatigue is InterviewFatigue.EXHAUSTED:
                raise OwnerTruthConversationConflict(
                    "deepening is not allowed while interview fatigue is exhausted"
                )
            if self.deepening_turn_count >= MAX_DEEPENING_TURNS_BEFORE_SUMMARY:
                raise OwnerTruthConversationConflict(
                    "deepening cannot exceed the bounded interview follow-up budget"
                )
            return InterviewPacingState(
                boundary=self.boundary,
                deepening_turn_count=self.deepening_turn_count + 1,
                candidate_batch_turn_count=self.candidate_batch_turn_count,
                fatigue=self.fatigue,
            )
        if event is InterviewPacingEvent.SUMMARY_COMPLETED:
            return InterviewPacingState(
                boundary=self.boundary,
                deepening_turn_count=0,
                candidate_batch_turn_count=self.candidate_batch_turn_count,
                fatigue=self.fatigue,
            )
        if event is InterviewPacingEvent.FATIGUE_GUARDED:
            if self.fatigue is InterviewFatigue.EXHAUSTED:
                raise OwnerTruthConversationConflict(
                    "exhausted fatigue requires an explicit reset before guarded"
                )
            return InterviewPacingState(
                boundary=self.boundary,
                deepening_turn_count=self.deepening_turn_count,
                candidate_batch_turn_count=self.candidate_batch_turn_count,
                fatigue=InterviewFatigue.GUARDED,
            )
        if event is InterviewPacingEvent.FATIGUE_EXHAUSTED:
            return InterviewPacingState(
                boundary=self.boundary,
                deepening_turn_count=self.deepening_turn_count,
                candidate_batch_turn_count=self.candidate_batch_turn_count,
                fatigue=InterviewFatigue.EXHAUSTED,
            )
        if event is InterviewPacingEvent.FATIGUE_RESET:
            return InterviewPacingState(
                boundary=self.boundary,
                deepening_turn_count=self.deepening_turn_count,
                candidate_batch_turn_count=self.candidate_batch_turn_count,
                fatigue=InterviewFatigue.NORMAL,
            )
        if event is InterviewPacingEvent.SKIP_ONCE_CONSUMED:
            if self.boundary is not InterviewBoundary.SKIP_ONCE:
                raise OwnerTruthConversationConflict(
                    "skipOnce can only be consumed while that boundary is active"
                )
            return InterviewPacingState(
                boundary=InterviewBoundary.OPEN,
                deepening_turn_count=self.deepening_turn_count,
                candidate_batch_turn_count=self.candidate_batch_turn_count,
                fatigue=self.fatigue,
            )
        raise OwnerTruthConversationError("event is not supported")


@dataclass(frozen=True)
class StartInterviewSessionCommand:
    command_id: str
    thread_id: str
    session_id: str
    expected_thread_version: int
    entry_mode: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", require_nonblank(self.command_id, field="command_id"))
        object.__setattr__(self, "thread_id", require_uuid(self.thread_id, field="thread_id"))
        object.__setattr__(self, "session_id", require_uuid(self.session_id, field="session_id"))
        _positive_version(
            self.expected_thread_version,
            field="expected_thread_version",
            allow_zero=True,
        )
        if self.expected_thread_version != 0:
            raise OwnerTruthConversationVersionConflict(
                resource="thread",
                expected_version=self.expected_thread_version,
                current_version=0,
            )
        normalized_entry_mode = require_nonblank(self.entry_mode, field="entry_mode")
        if normalized_entry_mode not in _ENTRY_MODES:
            raise OwnerTruthConversationError("entry_mode is not supported")
        object.__setattr__(self, "entry_mode", normalized_entry_mode)

    def write_record(self, *, context: OwnerTruthCommandContext) -> "StartInterviewSessionWriteRecord":
        command_id_hash = _sha256(self.command_id)
        payload = {
            "schemaVersion": OWNER_TRUTH_CONVERSATION_SCHEMA_VERSION,
            "commandType": "startInterviewSession",
            "threadId": self.thread_id,
            "sessionId": self.session_id,
            "expectedThreadVersion": self.expected_thread_version,
            "entryMode": self.entry_mode,
        }
        return StartInterviewSessionWriteRecord(
            receipt_id=_receipt_id(context=context, command_id_hash=command_id_hash),
            command_id_hash=command_id_hash,
            payload_hash=_sha256(_canonical_json(payload)),
            thread_id=self.thread_id,
            session_id=self.session_id,
            expected_thread_version=self.expected_thread_version,
            entry_mode=self.entry_mode,
            vault_id=context.vault_id,
            owner_subject_id=context.owner_subject_id,
            actor_subject_id=context.actor_subject_id,
            policy_version=context.policy_version,
        )


@dataclass(frozen=True)
class AppendInterviewMessageCommand:
    command_id: str
    thread_id: str
    session_id: str
    message_id: str
    expected_thread_version: int
    expected_session_version: int
    author: ConversationMessageAuthor
    kind: ConversationMessageKind
    text: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", require_nonblank(self.command_id, field="command_id"))
        object.__setattr__(self, "thread_id", require_uuid(self.thread_id, field="thread_id"))
        object.__setattr__(self, "session_id", require_uuid(self.session_id, field="session_id"))
        object.__setattr__(self, "message_id", require_uuid(self.message_id, field="message_id"))
        _positive_version(self.expected_thread_version, field="expected_thread_version")
        _positive_version(self.expected_session_version, field="expected_session_version")
        object.__setattr__(
            self,
            "author",
            _normalise_enum(self.author, ConversationMessageAuthor, field="author"),
        )
        object.__setattr__(
            self,
            "kind",
            _normalise_enum(self.kind, ConversationMessageKind, field="kind"),
        )
        text = require_nonblank(self.text, field="text")
        if len(text) > _MAX_MESSAGE_CHARACTERS:
            raise OwnerTruthConversationError("text exceeds maximum conversation message length")
        object.__setattr__(self, "text", text)

    def write_record(self, *, context: OwnerTruthCommandContext) -> "AppendInterviewMessageWriteRecord":
        command_id_hash = _sha256(self.command_id)
        content_payload = {
            "schemaVersion": OWNER_TRUTH_CONVERSATION_SCHEMA_VERSION,
            "text": self.text,
        }
        payload = {
            "schemaVersion": OWNER_TRUTH_CONVERSATION_SCHEMA_VERSION,
            "commandType": "appendInterviewMessage",
            "threadId": self.thread_id,
            "sessionId": self.session_id,
            "messageId": self.message_id,
            "expectedThreadVersion": self.expected_thread_version,
            "expectedSessionVersion": self.expected_session_version,
            "author": self.author.value,
            "kind": self.kind.value,
            "content": content_payload,
        }
        return AppendInterviewMessageWriteRecord(
            receipt_id=_receipt_id(context=context, command_id_hash=command_id_hash),
            command_id_hash=command_id_hash,
            payload_hash=_sha256(_canonical_json(payload)),
            thread_id=self.thread_id,
            session_id=self.session_id,
            message_id=self.message_id,
            expected_thread_version=self.expected_thread_version,
            expected_session_version=self.expected_session_version,
            author=self.author,
            kind=self.kind,
            content_hash=_sha256(_canonical_json(content_payload)),
            content_payload=content_payload,
            vault_id=context.vault_id,
            owner_subject_id=context.owner_subject_id,
            actor_subject_id=context.actor_subject_id,
            policy_version=context.policy_version,
        )


@dataclass(frozen=True)
class SetInterviewBoundaryCommand:
    command_id: str
    thread_id: str
    session_id: str
    expected_session_version: int
    boundary: InterviewBoundary

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", require_nonblank(self.command_id, field="command_id"))
        object.__setattr__(self, "thread_id", require_uuid(self.thread_id, field="thread_id"))
        object.__setattr__(self, "session_id", require_uuid(self.session_id, field="session_id"))
        _positive_version(self.expected_session_version, field="expected_session_version")
        object.__setattr__(
            self,
            "boundary",
            _normalise_enum(self.boundary, InterviewBoundary, field="boundary"),
        )

    def write_record(self, *, context: OwnerTruthCommandContext) -> "SetInterviewBoundaryWriteRecord":
        command_id_hash = _sha256(self.command_id)
        state = (
            InterviewSessionState.PAUSED
            if self.boundary in {InterviewBoundary.COOLDOWN, InterviewBoundary.DO_NOT_ASK}
            else InterviewSessionState.ACTIVE
        )
        payload = {
            "schemaVersion": OWNER_TRUTH_CONVERSATION_SCHEMA_VERSION,
            "commandType": "setInterviewBoundary",
            "threadId": self.thread_id,
            "sessionId": self.session_id,
            "expectedSessionVersion": self.expected_session_version,
            "boundary": self.boundary.value,
            "state": state.value,
        }
        return SetInterviewBoundaryWriteRecord(
            receipt_id=_receipt_id(context=context, command_id_hash=command_id_hash),
            command_id_hash=command_id_hash,
            payload_hash=_sha256(_canonical_json(payload)),
            thread_id=self.thread_id,
            session_id=self.session_id,
            expected_session_version=self.expected_session_version,
            boundary=self.boundary,
            state=state,
            vault_id=context.vault_id,
            owner_subject_id=context.owner_subject_id,
            actor_subject_id=context.actor_subject_id,
            policy_version=context.policy_version,
        )


@dataclass(frozen=True)
class RestoreDoNotAskInterviewBoundaryCommand:
    """Explicitly reopen a ``doNotAsk`` session after owner confirmation.

    This is intentionally a separate command from the generic boundary write.
    A client cannot accidentally reopen a paused interview by submitting
    ``boundary=open`` through the regular owner-control route.
    """

    command_id: str
    thread_id: str
    session_id: str
    expected_session_version: int
    confirmed: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", require_nonblank(self.command_id, field="command_id"))
        object.__setattr__(self, "thread_id", require_uuid(self.thread_id, field="thread_id"))
        object.__setattr__(self, "session_id", require_uuid(self.session_id, field="session_id"))
        _positive_version(self.expected_session_version, field="expected_session_version")
        if self.confirmed is not True:
            raise OwnerTruthConversationError("doNotAsk restore requires explicit confirmation")

    def write_record(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> "RestoreDoNotAskInterviewBoundaryWriteRecord":
        command_id_hash = _sha256(self.command_id)
        payload = {
            "schemaVersion": OWNER_TRUTH_CONVERSATION_SCHEMA_VERSION,
            "commandType": "restoreDoNotAskInterviewBoundary",
            "threadId": self.thread_id,
            "sessionId": self.session_id,
            "expectedSessionVersion": self.expected_session_version,
            "confirmed": True,
            "previousBoundary": InterviewBoundary.DO_NOT_ASK.value,
            "boundary": InterviewBoundary.OPEN.value,
            "state": InterviewSessionState.ACTIVE.value,
        }
        return RestoreDoNotAskInterviewBoundaryWriteRecord(
            receipt_id=_receipt_id(context=context, command_id_hash=command_id_hash),
            command_id_hash=command_id_hash,
            payload_hash=_sha256(_canonical_json(payload)),
            thread_id=self.thread_id,
            session_id=self.session_id,
            expected_session_version=self.expected_session_version,
            previous_boundary=InterviewBoundary.DO_NOT_ASK,
            boundary=InterviewBoundary.OPEN,
            state=InterviewSessionState.ACTIVE,
            vault_id=context.vault_id,
            owner_subject_id=context.owner_subject_id,
            actor_subject_id=context.actor_subject_id,
            policy_version=context.policy_version,
        )


@dataclass(frozen=True)
class PauseInterviewForTopicSwitchCommand:
    """Pause the current private thread when the Owner explicitly changes topic.

    Topic classification remains upstream. This command intentionally carries no
    topic text, topic identifier, model output, or Candidate payload: it only
    records the lifecycle fence that prevents the old thread from receiving a
    subsequent turn.
    """

    command_id: str
    thread_id: str
    session_id: str
    expected_thread_version: int
    expected_session_version: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", require_nonblank(self.command_id, field="command_id"))
        object.__setattr__(self, "thread_id", require_uuid(self.thread_id, field="thread_id"))
        object.__setattr__(self, "session_id", require_uuid(self.session_id, field="session_id"))
        _positive_version(self.expected_thread_version, field="expected_thread_version")
        _positive_version(self.expected_session_version, field="expected_session_version")

    def write_record(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> "PauseInterviewForTopicSwitchWriteRecord":
        command_id_hash = _sha256(self.command_id)
        payload = {
            "schemaVersion": OWNER_TRUTH_CONVERSATION_SCHEMA_VERSION,
            "commandType": "pauseInterviewForTopicSwitch",
            "threadId": self.thread_id,
            "sessionId": self.session_id,
            "expectedThreadVersion": self.expected_thread_version,
            "expectedSessionVersion": self.expected_session_version,
        }
        return PauseInterviewForTopicSwitchWriteRecord(
            receipt_id=_receipt_id(context=context, command_id_hash=command_id_hash),
            command_id_hash=command_id_hash,
            payload_hash=_sha256(_canonical_json(payload)),
            thread_id=self.thread_id,
            session_id=self.session_id,
            expected_thread_version=self.expected_thread_version,
            expected_session_version=self.expected_session_version,
            vault_id=context.vault_id,
            owner_subject_id=context.owner_subject_id,
            actor_subject_id=context.actor_subject_id,
            policy_version=context.policy_version,
        )


@dataclass(frozen=True)
class RecordInterviewPacingCommand:
    command_id: str
    thread_id: str
    session_id: str
    expected_session_version: int
    event: InterviewPacingEvent

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", require_nonblank(self.command_id, field="command_id"))
        object.__setattr__(self, "thread_id", require_uuid(self.thread_id, field="thread_id"))
        object.__setattr__(self, "session_id", require_uuid(self.session_id, field="session_id"))
        _positive_version(self.expected_session_version, field="expected_session_version")
        object.__setattr__(
            self,
            "event",
            _normalise_enum(self.event, InterviewPacingEvent, field="event"),
        )

    def write_record(self, *, context: OwnerTruthCommandContext) -> "RecordInterviewPacingWriteRecord":
        command_id_hash = _sha256(self.command_id)
        payload = {
            "schemaVersion": OWNER_TRUTH_CONVERSATION_SCHEMA_VERSION,
            "commandType": "recordInterviewPacing",
            "threadId": self.thread_id,
            "sessionId": self.session_id,
            "expectedSessionVersion": self.expected_session_version,
            "event": self.event.value,
        }
        return RecordInterviewPacingWriteRecord(
            receipt_id=_receipt_id(context=context, command_id_hash=command_id_hash),
            command_id_hash=command_id_hash,
            payload_hash=_sha256(_canonical_json(payload)),
            thread_id=self.thread_id,
            session_id=self.session_id,
            expected_session_version=self.expected_session_version,
            event=self.event,
            vault_id=context.vault_id,
            owner_subject_id=context.owner_subject_id,
            actor_subject_id=context.actor_subject_id,
            policy_version=context.policy_version,
        )


@dataclass(frozen=True)
class CreateInterviewReviewBatchCommand:
    """Create a private, value-free batch only after persisted policy says due."""

    command_id: str
    thread_id: str
    session_id: str
    expected_session_version: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", require_nonblank(self.command_id, field="command_id"))
        object.__setattr__(self, "thread_id", require_uuid(self.thread_id, field="thread_id"))
        object.__setattr__(self, "session_id", require_uuid(self.session_id, field="session_id"))
        _positive_version(self.expected_session_version, field="expected_session_version")

    def write_record(self, *, context: OwnerTruthCommandContext) -> "CreateInterviewReviewBatchWriteRecord":
        command_id_hash = _sha256(self.command_id)
        payload = {
            "schemaVersion": OWNER_TRUTH_CONVERSATION_SCHEMA_VERSION,
            "commandType": "createInterviewReviewBatch",
            "threadId": self.thread_id,
            "sessionId": self.session_id,
            "expectedSessionVersion": self.expected_session_version,
        }
        return CreateInterviewReviewBatchWriteRecord(
            receipt_id=_receipt_id(context=context, command_id_hash=command_id_hash),
            review_batch_id=_review_batch_id(
                context=context,
                session_id=self.session_id,
                command_id_hash=command_id_hash,
            ),
            command_id_hash=command_id_hash,
            payload_hash=_sha256(_canonical_json(payload)),
            thread_id=self.thread_id,
            session_id=self.session_id,
            expected_session_version=self.expected_session_version,
            vault_id=context.vault_id,
            owner_subject_id=context.owner_subject_id,
            actor_subject_id=context.actor_subject_id,
            policy_version=context.policy_version,
        )


@dataclass(frozen=True)
class AcknowledgeInterviewReviewBatchCommand:
    """Acknowledge a private review boundary without deciding any Candidate."""

    command_id: str
    thread_id: str
    session_id: str
    review_batch_id: str
    expected_session_version: int
    expected_review_batch_version: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", require_nonblank(self.command_id, field="command_id"))
        object.__setattr__(self, "thread_id", require_uuid(self.thread_id, field="thread_id"))
        object.__setattr__(self, "session_id", require_uuid(self.session_id, field="session_id"))
        object.__setattr__(
            self,
            "review_batch_id",
            require_uuid(self.review_batch_id, field="review_batch_id"),
        )
        _positive_version(self.expected_session_version, field="expected_session_version")
        _positive_version(
            self.expected_review_batch_version,
            field="expected_review_batch_version",
        )

    def write_record(self, *, context: OwnerTruthCommandContext) -> "AcknowledgeInterviewReviewBatchWriteRecord":
        command_id_hash = _sha256(self.command_id)
        payload = {
            "schemaVersion": OWNER_TRUTH_CONVERSATION_SCHEMA_VERSION,
            "commandType": "acknowledgeInterviewReviewBatch",
            "threadId": self.thread_id,
            "sessionId": self.session_id,
            "reviewBatchId": self.review_batch_id,
            "expectedSessionVersion": self.expected_session_version,
            "expectedReviewBatchVersion": self.expected_review_batch_version,
        }
        return AcknowledgeInterviewReviewBatchWriteRecord(
            receipt_id=_receipt_id(context=context, command_id_hash=command_id_hash),
            command_id_hash=command_id_hash,
            payload_hash=_sha256(_canonical_json(payload)),
            thread_id=self.thread_id,
            session_id=self.session_id,
            review_batch_id=self.review_batch_id,
            expected_session_version=self.expected_session_version,
            expected_review_batch_version=self.expected_review_batch_version,
            vault_id=context.vault_id,
            owner_subject_id=context.owner_subject_id,
            actor_subject_id=context.actor_subject_id,
            policy_version=context.policy_version,
        )


@dataclass(frozen=True)
class StartInterviewSessionWriteRecord:
    receipt_id: str
    command_id_hash: str
    payload_hash: str
    thread_id: str
    session_id: str
    expected_thread_version: int
    entry_mode: str
    vault_id: str
    owner_subject_id: str
    actor_subject_id: str
    policy_version: str


@dataclass(frozen=True)
class AppendInterviewMessageWriteRecord:
    receipt_id: str
    command_id_hash: str
    payload_hash: str
    thread_id: str
    session_id: str
    message_id: str
    expected_thread_version: int
    expected_session_version: int
    author: ConversationMessageAuthor
    kind: ConversationMessageKind
    content_hash: str
    content_payload: Mapping[str, Any]
    vault_id: str
    owner_subject_id: str
    actor_subject_id: str
    policy_version: str


@dataclass(frozen=True)
class SetInterviewBoundaryWriteRecord:
    receipt_id: str
    command_id_hash: str
    payload_hash: str
    thread_id: str
    session_id: str
    expected_session_version: int
    boundary: InterviewBoundary
    state: InterviewSessionState
    vault_id: str
    owner_subject_id: str
    actor_subject_id: str
    policy_version: str


@dataclass(frozen=True)
class RestoreDoNotAskInterviewBoundaryWriteRecord:
    receipt_id: str
    command_id_hash: str
    payload_hash: str
    thread_id: str
    session_id: str
    expected_session_version: int
    previous_boundary: InterviewBoundary
    boundary: InterviewBoundary
    state: InterviewSessionState
    vault_id: str
    owner_subject_id: str
    actor_subject_id: str
    policy_version: str


@dataclass(frozen=True)
class PauseInterviewForTopicSwitchWriteRecord:
    receipt_id: str
    command_id_hash: str
    payload_hash: str
    thread_id: str
    session_id: str
    expected_thread_version: int
    expected_session_version: int
    vault_id: str
    owner_subject_id: str
    actor_subject_id: str
    policy_version: str


@dataclass(frozen=True)
class RecordInterviewPacingWriteRecord:
    receipt_id: str
    command_id_hash: str
    payload_hash: str
    thread_id: str
    session_id: str
    expected_session_version: int
    event: InterviewPacingEvent
    vault_id: str
    owner_subject_id: str
    actor_subject_id: str
    policy_version: str


@dataclass(frozen=True)
class CreateInterviewReviewBatchWriteRecord:
    receipt_id: str
    review_batch_id: str
    command_id_hash: str
    payload_hash: str
    thread_id: str
    session_id: str
    expected_session_version: int
    vault_id: str
    owner_subject_id: str
    actor_subject_id: str
    policy_version: str


@dataclass(frozen=True)
class AcknowledgeInterviewReviewBatchWriteRecord:
    receipt_id: str
    command_id_hash: str
    payload_hash: str
    thread_id: str
    session_id: str
    review_batch_id: str
    expected_session_version: int
    expected_review_batch_version: int
    vault_id: str
    owner_subject_id: str
    actor_subject_id: str
    policy_version: str


@dataclass(frozen=True)
class OwnerTruthInterviewSessionResult:
    outcome: str
    receipt_id: str
    thread_id: str
    session_id: str
    thread_version: int
    session_version: int
    state: InterviewSessionState
    boundary: InterviewBoundary
    message_id: Optional[str] = None
    message_sequence: Optional[int] = None
    authority_effects: Tuple[str, ...] = ()

    def public_receipt(self) -> Mapping[str, Any]:
        """Return IDs/state only; message content remains in the private domain."""

        result = {
            "schemaVersion": OWNER_TRUTH_CONVERSATION_SCHEMA_VERSION,
            "status": self.outcome,
            "receiptId": self.receipt_id,
            "threadId": self.thread_id,
            "sessionId": self.session_id,
            "threadVersion": self.thread_version,
            "sessionVersion": self.session_version,
            "state": self.state.value,
            "boundary": self.boundary.value,
            "authorityEffects": list(self.authority_effects),
        }
        if self.message_id is not None:
            result["messageId"] = self.message_id
        if self.message_sequence is not None:
            result["messageSequence"] = self.message_sequence
        return result


@dataclass(frozen=True)
class OwnerTruthInterviewSessionSnapshot:
    session_id: str
    vault_id: str
    owner_subject_id: str
    thread_id: str
    state: InterviewSessionState
    boundary: InterviewBoundary
    row_version: int
    thread_version: int
    turn_count: int
    deepening_turn_count: int
    candidate_batch_turn_count: int
    pending_review_batch_id: Optional[str]
    fatigue: InterviewFatigue
    authority_epoch: int


@dataclass(frozen=True)
class OwnerTruthConversationThreadAuthoritySnapshot:
    """Value-free authority binding for one persisted conversation thread.

    Recommendation policy may refer to a conversation thread only after this
    private record proves that the thread still belongs to the current Owner
    Vault and authority epoch.  It intentionally contains no messages,
    metadata, or recommendation content.
    """

    thread_id: str
    vault_id: str
    owner_subject_id: str
    authority_epoch: int
    state: ConversationThreadState

    def __post_init__(self) -> None:
        object.__setattr__(self, "thread_id", require_uuid(self.thread_id, field="thread_id"))
        object.__setattr__(self, "vault_id", require_nonblank(self.vault_id, field="vault_id"))
        object.__setattr__(
            self,
            "owner_subject_id",
            require_nonblank(self.owner_subject_id, field="owner_subject_id"),
        )
        try:
            object.__setattr__(self, "state", ConversationThreadState(self.state))
        except (TypeError, ValueError) as exc:
            raise OwnerTruthConversationError("conversation thread state is not supported") from exc
        if (
            not isinstance(self.authority_epoch, int)
            or isinstance(self.authority_epoch, bool)
            or self.authority_epoch < 0
        ):
            raise OwnerTruthConversationError("authority_epoch must be a non-negative integer")


@dataclass(frozen=True)
class OwnerTruthInterviewReviewBatchSnapshot:
    review_batch_id: str
    vault_id: str
    owner_subject_id: str
    session_id: str
    thread_id: str
    trigger: InterviewReviewBatchTrigger
    state: InterviewReviewBatchState
    captured_candidate_batch_turn_count: int
    owner_turn_start_count: int
    owner_turn_end_count: int
    through_message_sequence: int
    row_version: int
    authority_epoch: int


@dataclass(frozen=True)
class OwnerTruthInterviewReviewBatchResult:
    outcome: str
    receipt_id: str
    thread_id: str
    session_id: str
    session_version: int
    review_batch: OwnerTruthInterviewReviewBatchSnapshot


__all__ = [
    "AppendInterviewMessageCommand",
    "AppendInterviewMessageWriteRecord",
    "AcknowledgeInterviewReviewBatchCommand",
    "AcknowledgeInterviewReviewBatchWriteRecord",
    "ConversationMessageAuthor",
    "ConversationMessageKind",
    "InterviewBoundary",
    "InterviewFatigue",
    "InterviewPacingEvent",
    "InterviewPacingState",
    "InterviewReviewBatchState",
    "InterviewReviewBatchTrigger",
    "InterviewSessionState",
    "OWNER_TRUTH_CONVERSATION_SCHEMA_VERSION",
    "OwnerTruthConversationAccessDenied",
    "OwnerTruthConversationConflict",
    "OwnerTruthConversationError",
    "OwnerTruthConversationVersionConflict",
    "OwnerTruthConversationThreadAuthoritySnapshot",
    "OwnerTruthInterviewSessionResult",
    "OwnerTruthInterviewSessionSnapshot",
    "OwnerTruthInterviewSessionStateConflict",
    "OwnerTruthInterviewReviewBatchResult",
    "OwnerTruthInterviewReviewBatchSnapshot",
    "PauseInterviewForTopicSwitchCommand",
    "PauseInterviewForTopicSwitchWriteRecord",
    "CreateInterviewReviewBatchCommand",
    "CreateInterviewReviewBatchWriteRecord",
    "RecordInterviewPacingCommand",
    "RecordInterviewPacingWriteRecord",
    "SetInterviewBoundaryCommand",
    "SetInterviewBoundaryWriteRecord",
    "StartInterviewSessionCommand",
    "StartInterviewSessionWriteRecord",
]
