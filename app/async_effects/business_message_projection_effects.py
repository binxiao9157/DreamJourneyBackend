"""Typed, default-disabled async-effect contract for private message projections.

The existing ``business_message_projections`` table is intentionally a
metadata-only shadow and does not replace the public mailbox or dispatch a
notification.  This module gives its durable write a narrow job family so a
completed business receipt can be projected asynchronously without claiming
that a user-visible delivery occurred.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from typing import TYPE_CHECKING, Mapping

from app.async_effects.consumer_repository import AsyncEffectConsumerCompletionCommand
from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget
from app.async_effects.message_notification_effects import BusinessCompletionMessageSource

if TYPE_CHECKING:
    from app.async_effects.business_message_projection_repository import InboxAccountSnapshot


BUSINESS_MESSAGE_PROJECTION_EFFECT_SCHEMA_VERSION = "business-message-projection-effect-v1"
BUSINESS_MESSAGE_PROJECTION_OPERATION_TYPE = "businessMessage.projection.persist"
BUSINESS_MESSAGE_PROJECTION_EVENT_TYPE = "businessMessage.projection.persistRequested"
BUSINESS_MESSAGE_PROJECTION_JOB_TYPE = "businessMessage.projection"
BUSINESS_MESSAGE_PROJECTION_RESOURCE_TYPE = "businessMessageProjection"
BUSINESS_MESSAGE_PROJECTION_PURPOSE = "inAppMessageProjection"
BUSINESS_MESSAGE_PROJECTION_CONSUMER_NAME = "businessMessage.projection"
BUSINESS_MESSAGE_PROJECTION_DEFAULT_MAX_ATTEMPTS = 3

_COMPLETED_REASONS = {
    "recorded": "businessMessageProjectionRecorded",
    "deduplicated": "businessMessageProjectionDeduplicated",
}
_TERMINAL_FAILURE_REASON = "businessMessageProjectionRetriesExhausted"
_BLOCKED_INPUT_UNAVAILABLE_REASON = "businessMessageProjectionInputUnavailable"
_BLOCKED_INBOX_UNAVAILABLE_REASON = "businessMessageProjectionInboxUnavailable"
_BLOCKED_INBOX_SNAPSHOT_MISMATCH_REASON = "businessMessageProjectionInboxSnapshotMismatch"
_BLOCKED_CROSS_ACCOUNT_UNSUPPORTED_REASON = "businessMessageProjectionCrossAccountUnsupported"
_BLOCKED_REASONS = {
    _BLOCKED_INPUT_UNAVAILABLE_REASON,
    _BLOCKED_INBOX_UNAVAILABLE_REASON,
    _BLOCKED_INBOX_SNAPSHOT_MISMATCH_REASON,
    _BLOCKED_CROSS_ACCOUNT_UNSUPPORTED_REASON,
}


class BusinessMessageProjectionEffectError(ValueError):
    """The private message-projection effect crossed an immutable boundary."""


def _canonical_hash(value: Mapping[str, object]) -> str:
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def is_business_message_projection_intent(intent: object) -> bool:
    if not isinstance(intent, AsyncEffectIntent):
        return False
    target = intent.target
    return (
        intent.operation_type == BUSINESS_MESSAGE_PROJECTION_OPERATION_TYPE
        and intent.event_type == BUSINESS_MESSAGE_PROJECTION_EVENT_TYPE
        and intent.job_type == BUSINESS_MESSAGE_PROJECTION_JOB_TYPE
        and target.resource_type == BUSINESS_MESSAGE_PROJECTION_RESOURCE_TYPE
        and target.purpose == BUSINESS_MESSAGE_PROJECTION_PURPOSE
    )


@dataclass(frozen=True)
class BusinessMessageProjectionRequest:
    """One immutable request to write a private metadata-only message shadow.

    The original completed receipt and explicit active inbox snapshot remain
    separate: the source resource may belong to the owner while the inbox is
    later allowed to be another account.  No title, body, notification token,
    or provider payload is part of this request.
    """

    source: BusinessCompletionMessageSource
    inbox_account: "InboxAccountSnapshot"
    max_attempts: int = BUSINESS_MESSAGE_PROJECTION_DEFAULT_MAX_ATTEMPTS
    request_hash: str = field(init=False)

    def __post_init__(self) -> None:
        # Keep the request contract independent from its persistence adapter.
        # The local import avoids a module cycle when the repository rebuilds a
        # request from durable metadata.
        from app.async_effects.business_message_projection_repository import InboxAccountSnapshot

        if not isinstance(self.source, BusinessCompletionMessageSource):
            raise BusinessMessageProjectionEffectError("business completion source is required")
        if not isinstance(self.inbox_account, InboxAccountSnapshot):
            raise BusinessMessageProjectionEffectError("active inbox account snapshot is required")
        message = self.source.projection()
        if (
            message.inbox_subject_id != self.inbox_account.inbox_subject_id
            or message.inbox_vault_id != self.inbox_account.inbox_vault_id
        ):
            raise BusinessMessageProjectionEffectError(
                "inbox account snapshot must match projected inbox coordinates"
            )
        if isinstance(self.max_attempts, bool) or not isinstance(self.max_attempts, int):
            raise BusinessMessageProjectionEffectError("max_attempts must be a positive integer")
        if self.max_attempts < 1 or self.max_attempts > 10:
            raise BusinessMessageProjectionEffectError("max_attempts must be between 1 and 10")
        object.__setattr__(self, "request_hash", self._build_request_hash())

    @property
    def message_id(self) -> str:
        return self.source.message_id

    @property
    def effect_intent(self) -> AsyncEffectIntent:
        source_target = self.source.intent.target
        return AsyncEffectIntent(
            operation_type=BUSINESS_MESSAGE_PROJECTION_OPERATION_TYPE,
            target=AsyncEffectTarget(
                owner_subject_id=source_target.owner_subject_id,
                vault_id=source_target.vault_id,
                resource_type=BUSINESS_MESSAGE_PROJECTION_RESOURCE_TYPE,
                resource_id=self.message_id,
                resource_version=source_target.resource_version,
                purpose=BUSINESS_MESSAGE_PROJECTION_PURPOSE,
                authority_epoch=source_target.authority_epoch,
            ),
            payload_hash=self.request_hash,
            event_type=BUSINESS_MESSAGE_PROJECTION_EVENT_TYPE,
            job_type=BUSINESS_MESSAGE_PROJECTION_JOB_TYPE,
            max_attempts=self.max_attempts,
        )

    def completion_command(
        self,
        *,
        projection_outcome: str,
        result_ref_hash: str,
    ) -> "BusinessMessageProjectionConsumerCommand":
        return BusinessMessageProjectionConsumerCommand(
            intent=self.effect_intent,
            consumer_name=BUSINESS_MESSAGE_PROJECTION_CONSUMER_NAME,
            business_target_key=self.effect_intent.business_target_key,
            outcome="completed",
            reason_code=_COMPLETED_REASONS.get(
                projection_outcome,
                "businessMessageProjectionInvalidOutcome",
            ),
            result_ref_hash=result_ref_hash,
            projection_outcome=projection_outcome,
        )

    def failed_completion_command(
        self,
        *,
        result_ref_hash: str,
    ) -> "BusinessMessageProjectionConsumerCommand":
        return BusinessMessageProjectionConsumerCommand(
            intent=self.effect_intent,
            consumer_name=BUSINESS_MESSAGE_PROJECTION_CONSUMER_NAME,
            business_target_key=self.effect_intent.business_target_key,
            outcome="failed",
            reason_code=_TERMINAL_FAILURE_REASON,
            result_ref_hash=result_ref_hash,
            projection_outcome="failed",
        )

    @staticmethod
    def blocked_completion_command(
        *,
        intent: AsyncEffectIntent,
        result_ref_hash: str,
        reason_code: str = _BLOCKED_INPUT_UNAVAILABLE_REASON,
    ) -> "BusinessMessageProjectionConsumerCommand":
        return BusinessMessageProjectionConsumerCommand(
            intent=intent,
            consumer_name=BUSINESS_MESSAGE_PROJECTION_CONSUMER_NAME,
            business_target_key=intent.business_target_key,
            outcome="blocked",
            reason_code=reason_code,
            result_ref_hash=result_ref_hash,
            projection_outcome=None,
        )

    def value_free_summary(self) -> Mapping[str, object]:
        return {
            "effect": {
                "jobId": self.effect_intent.job_id,
                "jobType": self.effect_intent.job_type,
                "maxAttempts": self.effect_intent.max_attempts,
                "operationId": self.effect_intent.operation_id,
                "stableKey": self.effect_intent.stable_key,
            },
            "inbox": self.inbox_account.value_free_summary(),
            "message": self.source.projection().value_free_summary(),
            "requestHash": self.request_hash,
            "schemaVersion": BUSINESS_MESSAGE_PROJECTION_EFFECT_SCHEMA_VERSION,
        }

    def _build_request_hash(self) -> str:
        message = self.source.projection()
        return _canonical_hash(
            {
                "businessReceiptId": message.business_receipt_id,
                "businessTargetKey": message.business_target_key,
                "inboxAccountEpoch": self.inbox_account.account_epoch,
                "inboxSubjectId": message.inbox_subject_id,
                "inboxVaultId": message.inbox_vault_id,
                "maxAttempts": self.max_attempts,
                "messageId": message.message_id,
                "messageKind": message.kind.value,
                "schemaVersion": BUSINESS_MESSAGE_PROJECTION_EFFECT_SCHEMA_VERSION,
                "sourceConsumerInboxId": self.source.completion.inbox_id,
                "sourceConsumerName": self.source.completion.consumer_name,
                "sourceOperationId": self.source.intent.operation_id,
            }
        )


@dataclass(frozen=True)
class BusinessMessageProjectionConsumerCommand(AsyncEffectConsumerCompletionCommand):
    """Typed terminal receipt for the private message-projection worker."""

    projection_outcome: str | None

    def __post_init__(self) -> None:
        super().__post_init__()
        if not is_business_message_projection_intent(self.intent):
            raise BusinessMessageProjectionEffectError(
                "message projection completion requires its typed effect intent"
            )
        if self.consumer_name != BUSINESS_MESSAGE_PROJECTION_CONSUMER_NAME:
            raise BusinessMessageProjectionEffectError(
                "message projection completion has one fixed consumer"
            )
        if self.business_target_key != self.intent.business_target_key:
            raise BusinessMessageProjectionEffectError(
                "message projection completion has one fixed business target"
            )
        normalized_outcome = (
            None if self.projection_outcome is None else str(self.projection_outcome).strip()
        )
        object.__setattr__(self, "projection_outcome", normalized_outcome)
        if normalized_outcome in _COMPLETED_REASONS:
            if (
                self.outcome != "completed"
                or self.reason_code != _COMPLETED_REASONS[normalized_outcome]
            ):
                raise BusinessMessageProjectionEffectError(
                    "completed projection receipt must retain its fixed outcome and reason"
                )
            return
        if normalized_outcome == "failed":
            if self.outcome != "failed" or self.reason_code != _TERMINAL_FAILURE_REASON:
                raise BusinessMessageProjectionEffectError(
                    "failed projection receipt must retain its fixed terminal reason"
                )
            return
        if normalized_outcome is None:
            if self.outcome != "blocked" or self.reason_code not in _BLOCKED_REASONS:
                raise BusinessMessageProjectionEffectError(
                    "blocked projection receipt must retain its fixed input reason"
                )
            return
        raise BusinessMessageProjectionEffectError("message projection outcome is unsupported")


__all__ = [
    "BUSINESS_MESSAGE_PROJECTION_CONSUMER_NAME",
    "BUSINESS_MESSAGE_PROJECTION_DEFAULT_MAX_ATTEMPTS",
    "BUSINESS_MESSAGE_PROJECTION_EFFECT_SCHEMA_VERSION",
    "BUSINESS_MESSAGE_PROJECTION_EVENT_TYPE",
    "BUSINESS_MESSAGE_PROJECTION_JOB_TYPE",
    "BUSINESS_MESSAGE_PROJECTION_OPERATION_TYPE",
    "BUSINESS_MESSAGE_PROJECTION_PURPOSE",
    "BUSINESS_MESSAGE_PROJECTION_RESOURCE_TYPE",
    "BusinessMessageProjectionConsumerCommand",
    "BusinessMessageProjectionEffectError",
    "BusinessMessageProjectionRequest",
    "is_business_message_projection_intent",
]
