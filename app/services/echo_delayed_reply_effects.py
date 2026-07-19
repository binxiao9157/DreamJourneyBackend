"""Value-free V4 contracts for delayed Echo reply completion.

The legacy delayed-reply route only moves a payload from ``scheduled`` to a
provider-ready state.  It cannot prove that an answer exists.  This module
defines the stricter, default-off V4 coordinates used by the future worker:
one immutable delayed-reply envelope maps to one answer, one owner Inbox
projection, and one business receipt.  User text is deliberately absent from
the effect intent and all diagnostic summaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import json
import re
from typing import Any, Mapping

from app.async_effects.consumer_repository import AsyncEffectConsumerCompletionCommand
from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget


ECHO_DELAYED_REPLY_SCHEMA_VERSION = "echo-delayed-reply-v1"
ECHO_DELAYED_REPLY_OPERATION_TYPE = "echo.delayedReply.answer.dispatch"
ECHO_DELAYED_REPLY_EVENT_TYPE = "echo.delayedReply.answer.dispatchRequested"
ECHO_DELAYED_REPLY_JOB_TYPE = "echo.delayedReply.answer.dispatch"
ECHO_DELAYED_REPLY_RESOURCE_TYPE = "echoDelayedReply"
ECHO_DELAYED_REPLY_PURPOSE = "echoDelayedReply"
ECHO_DELAYED_REPLY_CONSUMER_NAME = "echo.delayedReply.answer"

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_ACTIVE_STATES = frozenset({"scheduled", "ready", "generating"})
_TERMINAL_STATES = frozenset({"completed", "failed", "unknown", "cancelled", "blocked"})
_TERMINAL_REASONS = {
    "blocked": frozenset(
        {
            "authorityEpochChanged",
            "authorityNotActive",
            "contextChanged",
            "contextExpired",
            "delayedReplyNotActive",
            "policyVersionChanged",
            "replyGenerationChanged",
            "rowVersionChanged",
        }
    ),
    "failed": frozenset({"providerFailed", "providerRejected"}),
    "unknown": frozenset({"providerOutcomeUnknown", "providerSucceededPersistenceUnknown"}),
    "cancelled": frozenset({"ownerCancelled", "ownerDeleted"}),
}


class EchoDelayedReplyContractError(ValueError):
    """A delayed-reply envelope lacks immutable V4 completion coordinates."""


class EchoDelayedReplyDeliveryState(str, Enum):
    SCHEDULED = "scheduled"
    READY = "ready"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


def _required(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise EchoDelayedReplyContractError(f"{field} is required")
    return normalized


def _sha256_hex(value: object, *, field: str) -> str:
    normalized = _required(value, field=field).lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise EchoDelayedReplyContractError(f"{field} must be a lowercase SHA-256 digest")
    return normalized


def _positive_int(value: object, *, field: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise EchoDelayedReplyContractError(f"{field} must be an integer >= {minimum}")
    return value


def _non_negative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EchoDelayedReplyContractError(f"{field} must be a non-negative integer")
    return value


def _parse_iso_datetime(value: str, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EchoDelayedReplyContractError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _canonical_hash(value: Mapping[str, object]) -> str:
    try:
        serialized = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise EchoDelayedReplyContractError("hash material must be serializable") from exc
    return sha256(serialized.encode("utf-8")).hexdigest()


def _metadata(item: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = item.get("metadata")
    return metadata if isinstance(metadata, Mapping) else {}


def _field(item: Mapping[str, Any], metadata: Mapping[str, Any], name: str) -> object:
    return item.get(name) if item.get(name) is not None else metadata.get(name)


@dataclass(frozen=True)
class EchoDelayedReplySnapshot:
    """Immutable coordinates required before a delayed answer may be created."""

    owner_subject_id: str
    vault_id: str
    delayed_reply_id: str
    conversation_id: str
    request_id: str
    reply_generation: int
    context_hash: str
    context_version: str
    policy_version: str
    authority_epoch: int
    row_version: int
    deliver_at: str
    context_expires_at: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner_subject_id", _required(self.owner_subject_id, field="ownerSubjectId"))
        object.__setattr__(self, "vault_id", _required(self.vault_id, field="vaultId"))
        object.__setattr__(self, "delayed_reply_id", _required(self.delayed_reply_id, field="delayedReplyId"))
        object.__setattr__(self, "conversation_id", _required(self.conversation_id, field="conversationId"))
        object.__setattr__(self, "request_id", _required(self.request_id, field="requestId"))
        object.__setattr__(
            self,
            "reply_generation",
            _positive_int(self.reply_generation, field="replyGeneration"),
        )
        object.__setattr__(self, "context_hash", _sha256_hex(self.context_hash, field="contextHash"))
        object.__setattr__(self, "context_version", _required(self.context_version, field="contextVersion"))
        object.__setattr__(self, "policy_version", _required(self.policy_version, field="policyVersion"))
        object.__setattr__(
            self,
            "authority_epoch",
            _non_negative_int(self.authority_epoch, field="authorityEpoch"),
        )
        object.__setattr__(self, "row_version", _positive_int(self.row_version, field="rowVersion"))
        normalized_deliver_at = _required(self.deliver_at, field="deliverAt")
        _parse_iso_datetime(normalized_deliver_at, field="deliverAt")
        object.__setattr__(self, "deliver_at", normalized_deliver_at)
        if self.context_expires_at is not None:
            normalized_expiry = _required(self.context_expires_at, field="contextExpiresAt")
            _parse_iso_datetime(normalized_expiry, field="contextExpiresAt")
            object.__setattr__(self, "context_expires_at", normalized_expiry)

    @classmethod
    def from_persisted_reply(cls, item: Mapping[str, Any]) -> "EchoDelayedReplySnapshot":
        if not isinstance(item, Mapping):
            raise EchoDelayedReplyContractError("delayed reply is required")
        metadata = _metadata(item)
        protocol = str(_field(item, metadata, "deliveryProtocolVersion") or "").strip()
        if protocol != ECHO_DELAYED_REPLY_SCHEMA_VERSION:
            raise EchoDelayedReplyContractError("delayed reply does not use the V4 completion protocol")
        state = str(_field(item, metadata, "deliveryState") or "").strip()
        if state not in _ACTIVE_STATES:
            raise EchoDelayedReplyContractError("delayed reply is not in an active completion state")
        return cls(
            owner_subject_id=_field(item, metadata, "ownerSubjectId"),
            vault_id=_field(item, metadata, "vaultId"),
            delayed_reply_id=item.get("id") or _field(item, metadata, "delayedReplyId"),
            conversation_id=_field(item, metadata, "conversationId"),
            request_id=_field(item, metadata, "requestId"),
            reply_generation=_field(item, metadata, "replyGeneration"),
            context_hash=_field(item, metadata, "contextHash"),
            context_version=_field(item, metadata, "contextVersion"),
            policy_version=_field(item, metadata, "policyVersion"),
            authority_epoch=_field(item, metadata, "authorityEpoch"),
            row_version=_field(item, metadata, "rowVersion"),
            deliver_at=_field(item, metadata, "deliverAt"),
            context_expires_at=_field(item, metadata, "contextExpiresAt"),
        )

    def is_due(self, now_iso: str) -> bool:
        return _parse_iso_datetime(self.deliver_at, field="deliverAt") <= _parse_iso_datetime(
            _required(now_iso, field="now"),
            field="now",
        )

    def is_context_expired(self, now_iso: str) -> bool:
        return self.context_expires_at is not None and _parse_iso_datetime(
            self.context_expires_at,
            field="contextExpiresAt",
        ) <= _parse_iso_datetime(_required(now_iso, field="now"), field="now")

    @property
    def stable_target_key(self) -> str:
        """The V4 business identity: vault + conversation + request + context."""

        return _canonical_hash(
            {
                "contextHash": self.context_hash,
                "conversationId": self.conversation_id,
                "requestId": self.request_id,
                "schemaVersion": ECHO_DELAYED_REPLY_SCHEMA_VERSION,
                "vaultId": self.vault_id,
            }
        )

    @property
    def payload_hash(self) -> str:
        return _canonical_hash(
            {
                "authorityEpoch": self.authority_epoch,
                "contextExpiresAt": self.context_expires_at,
                "contextHash": self.context_hash,
                "contextVersion": self.context_version,
                "delayedReplyId": self.delayed_reply_id,
                "policyVersion": self.policy_version,
                "replyGeneration": self.reply_generation,
                "requestId": self.request_id,
                "schemaVersion": ECHO_DELAYED_REPLY_SCHEMA_VERSION,
                "vaultId": self.vault_id,
            }
        )

    @property
    def effect_intent(self) -> AsyncEffectIntent:
        return AsyncEffectIntent(
            operation_type=ECHO_DELAYED_REPLY_OPERATION_TYPE,
            target=AsyncEffectTarget(
                owner_subject_id=self.owner_subject_id,
                vault_id=self.vault_id,
                resource_type=ECHO_DELAYED_REPLY_RESOURCE_TYPE,
                resource_id=self.stable_target_key,
                resource_version=self.reply_generation,
                purpose=ECHO_DELAYED_REPLY_PURPOSE,
                authority_epoch=self.authority_epoch,
            ),
            payload_hash=self.payload_hash,
            event_type=ECHO_DELAYED_REPLY_EVENT_TYPE,
            job_type=ECHO_DELAYED_REPLY_JOB_TYPE,
        )

    def value_free_reference(self) -> Mapping[str, object]:
        return {
            "authorityEpoch": self.authority_epoch,
            "contextExpiresAt": self.context_expires_at,
            "contextHash": self.context_hash,
            "contextVersion": self.context_version,
            "delayedReplyIdHash": sha256(self.delayed_reply_id.encode("utf-8")).hexdigest(),
            "policyVersion": self.policy_version,
            "replyGeneration": self.reply_generation,
            "requestIdHash": sha256(self.request_id.encode("utf-8")).hexdigest(),
            "rowVersion": self.row_version,
            "stableTargetKey": self.stable_target_key,
            "vaultIdHash": sha256(self.vault_id.encode("utf-8")).hexdigest(),
        }


@dataclass(frozen=True)
class EchoDelayedReplyGeneratedAnswer:
    """Private generated response plus value-free provenance references.

    ``answer_text`` belongs only in the owning Answer/Message persistence
    adapter.  It must not enter the generic effect kernel, business receipt,
    or Inbox projection.
    """

    answer_text: str
    citation_receipt_hash: str
    provider_result_hash: str

    def __post_init__(self) -> None:
        answer_text = _required(self.answer_text, field="answerText")
        if len(answer_text) > 16_000:
            raise EchoDelayedReplyContractError("answerText exceeds delayed reply limit")
        object.__setattr__(self, "answer_text", answer_text)
        object.__setattr__(
            self,
            "citation_receipt_hash",
            _sha256_hex(self.citation_receipt_hash, field="citationReceiptHash"),
        )
        object.__setattr__(
            self,
            "provider_result_hash",
            _sha256_hex(self.provider_result_hash, field="providerResultHash"),
        )

    @property
    def answer_hash(self) -> str:
        return sha256(self.answer_text.encode("utf-8")).hexdigest()

    @property
    def answer_length(self) -> int:
        return len(self.answer_text)

    def value_free_reference(self) -> Mapping[str, object]:
        return {
            "answerHash": self.answer_hash,
            "answerLength": self.answer_length,
            "citationReceiptHash": self.citation_receipt_hash,
            "providerResultHash": self.provider_result_hash,
        }


@dataclass(frozen=True)
class EchoDelayedReplyCompletion:
    """Terminal receipt coordinates for a delayed Echo reply attempt."""

    snapshot: EchoDelayedReplySnapshot
    outcome: str
    reason_code: str
    answer: EchoDelayedReplyGeneratedAnswer | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, EchoDelayedReplySnapshot):
            raise EchoDelayedReplyContractError("delayed reply snapshot is required")
        outcome = _required(self.outcome, field="outcome")
        reason_code = _required(self.reason_code, field="reasonCode")
        if outcome == "completed":
            if self.answer is None:
                raise EchoDelayedReplyContractError("completed delayed reply requires an Answer")
            if reason_code != "answerInboxPersisted":
                raise EchoDelayedReplyContractError("completed delayed reply must retain answerInboxPersisted")
        elif outcome in _TERMINAL_REASONS:
            if self.answer is not None:
                raise EchoDelayedReplyContractError("non-completed delayed reply cannot retain an Answer")
            if reason_code not in _TERMINAL_REASONS[outcome]:
                raise EchoDelayedReplyContractError("delayed reply has an invalid terminal reasonCode")
        else:
            raise EchoDelayedReplyContractError("delayed reply outcome must be terminal")
        object.__setattr__(self, "outcome", outcome)
        object.__setattr__(self, "reason_code", reason_code)

    @property
    def answer_id(self) -> str:
        digest = sha256(f"echo-delayed-answer:{self.snapshot.stable_target_key}".encode("utf-8")).hexdigest()
        return f"echo-delayed-v1-{digest[:32]}"

    @property
    def result_ref_hash(self) -> str:
        return _canonical_hash(
            {
                "answer": None if self.answer is None else self.answer.value_free_reference(),
                "outcome": self.outcome,
                "reasonCode": self.reason_code,
                "schemaVersion": ECHO_DELAYED_REPLY_SCHEMA_VERSION,
                "targetKey": self.snapshot.stable_target_key,
            }
        )

    @property
    def consumer_command(self) -> AsyncEffectConsumerCompletionCommand:
        intent = self.snapshot.effect_intent
        return AsyncEffectConsumerCompletionCommand(
            intent=intent,
            consumer_name=ECHO_DELAYED_REPLY_CONSUMER_NAME,
            business_target_key=intent.business_target_key,
            outcome=self.outcome,
            reason_code=self.reason_code,
            result_ref_hash=self.result_ref_hash,
        )

    def value_free_summary(self) -> Mapping[str, object]:
        return {
            "answerId": self.answer_id if self.answer is not None else None,
            "answer": None if self.answer is None else self.answer.value_free_reference(),
            "outcome": self.outcome,
            "reasonCode": self.reason_code,
            "resultRefHash": self.result_ref_hash,
            "snapshot": self.snapshot.value_free_reference(),
        }


@dataclass(frozen=True)
class EchoDelayedReplyPlan:
    snapshot: EchoDelayedReplySnapshot
    due: bool

    @property
    def effect_intents(self) -> tuple[AsyncEffectIntent, ...]:
        return (self.snapshot.effect_intent,) if self.due else ()

    def value_free_summary(self) -> Mapping[str, object]:
        return {
            "due": self.due,
            "schemaVersion": ECHO_DELAYED_REPLY_SCHEMA_VERSION,
            "snapshot": self.snapshot.value_free_reference(),
        }


def build_echo_delayed_reply_plan(
    item: Mapping[str, Any],
    *,
    now_iso: str,
) -> EchoDelayedReplyPlan:
    """Build a V4 plan without mutating legacy dispatch state or user content."""

    snapshot = EchoDelayedReplySnapshot.from_persisted_reply(item)
    return EchoDelayedReplyPlan(snapshot=snapshot, due=snapshot.is_due(now_iso))


__all__ = [
    "ECHO_DELAYED_REPLY_CONSUMER_NAME",
    "ECHO_DELAYED_REPLY_EVENT_TYPE",
    "ECHO_DELAYED_REPLY_JOB_TYPE",
    "ECHO_DELAYED_REPLY_OPERATION_TYPE",
    "ECHO_DELAYED_REPLY_PURPOSE",
    "ECHO_DELAYED_REPLY_RESOURCE_TYPE",
    "ECHO_DELAYED_REPLY_SCHEMA_VERSION",
    "EchoDelayedReplyCompletion",
    "EchoDelayedReplyContractError",
    "EchoDelayedReplyDeliveryState",
    "EchoDelayedReplyGeneratedAnswer",
    "EchoDelayedReplyPlan",
    "EchoDelayedReplySnapshot",
    "build_echo_delayed_reply_plan",
]
