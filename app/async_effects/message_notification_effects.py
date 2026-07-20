"""Business-completion, in-app message, and notification-intent contracts.

This is the G0 boundary for the V4 messaging seam.  A completed business
receipt can create a private in-app message projection and one or more
notification intents.  Delivery observations remain separate facts: a local
or APNs failure must never rewrite the business result or make an in-app
message disappear.

The module deliberately contains no persistence, device token, APNs client,
or user-visible message body.  Those are separate G1-G4 concerns.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import re
from typing import Iterable, Mapping
from uuid import UUID, uuid5

from app.async_effects.consumer_repository import AsyncEffectConsumerReceipt
from app.async_effects.contracts import AsyncEffectIntent


BUSINESS_MESSAGE_NOTIFICATION_SCHEMA_VERSION = "business-message-notification-v1"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MESSAGE_NAMESPACE = UUID("cb4d0d17-5cc7-4330-b177-33f87e43e657")


class BusinessMessageNotificationContractError(ValueError):
    """A business result or notification observation crossed the wrong boundary."""


class InAppMessageKind(str, Enum):
    TIME_LETTER = "timeLetter"
    ECHO_REPLY = "echoReply"
    CARE_SIGNAL = "careSignal"
    FAMILY_INVITATION = "familyInvitation"
    SYSTEM_NOTICE = "systemNotice"


class InAppMessageState(str, Enum):
    UNREAD = "unread"
    READ = "read"
    ARCHIVED = "archived"


class NotificationChannel(str, Enum):
    LOCAL = "local"
    APNS = "apns"


class NotificationDeliveryState(str, Enum):
    QUEUED = "queued"
    ACCEPTED = "accepted"
    FAILED = "failed"
    UNKNOWN = "unknown"
    ARRIVED = "arrived"


def _identifier(value: object, *, field: str, max_length: int = 127) -> str:
    normalized = str(value or "").strip()
    if len(normalized) > max_length or not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise BusinessMessageNotificationContractError(
            f"{field} must be an opaque identifier"
        )
    return normalized


def _non_negative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BusinessMessageNotificationContractError(
            f"{field} must be a non-negative integer"
        )
    return value


def _sha256_hex(value: object, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise BusinessMessageNotificationContractError(
            f"{field} must be a lowercase SHA-256 hex digest"
        )
    return normalized


def _uuid(value: object, *, field: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError) as exc:
        raise BusinessMessageNotificationContractError(f"{field} must be a UUID") from exc


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class BusinessCompletionMessageSource:
    """A completed consumer receipt eligible to create one private message.

    The source binds the generic async-effect receipt back to its immutable
    authorized target.  It intentionally requires a completed business result:
    failed, blocked, and unknown effects can have diagnostics, but may not
    masquerade as user-facing completed messages.
    """

    intent: AsyncEffectIntent
    completion: AsyncEffectConsumerReceipt
    message_kind: InAppMessageKind

    def __post_init__(self) -> None:
        if not isinstance(self.intent, AsyncEffectIntent):
            raise BusinessMessageNotificationContractError("intent is required")
        if not isinstance(self.completion, AsyncEffectConsumerReceipt):
            raise BusinessMessageNotificationContractError("completion receipt is required")
        if not isinstance(self.message_kind, InAppMessageKind):
            raise BusinessMessageNotificationContractError("message_kind is required")
        _uuid(self.completion.business_receipt_id, field="business_receipt_id")
        if self.completion.operation_id != self.intent.operation_id:
            raise BusinessMessageNotificationContractError(
                "completion receipt does not belong to this effect intent"
            )
        if self.completion.business_target_key != self.intent.business_target_key:
            raise BusinessMessageNotificationContractError(
                "completion receipt does not match this business target"
            )
        if self.completion.business_outcome != "completed":
            raise BusinessMessageNotificationContractError(
                "only a completed business receipt may create an in-app message"
            )
        if self.completion.inbox_state != "completed":
            raise BusinessMessageNotificationContractError(
                "completed business receipt requires a terminal completed inbox"
            )

    @property
    def message_id(self) -> str:
        return str(
            uuid5(
                _MESSAGE_NAMESPACE,
                f"in-app-message:{self.completion.business_receipt_id}:{self.message_kind.value}",
            )
        )

    def projection(self) -> "InAppMessageProjection":
        target = self.intent.target
        return InAppMessageProjection(
            message_id=self.message_id,
            kind=self.message_kind,
            owner_subject_id=target.owner_subject_id,
            vault_id=target.vault_id,
            resource_type=target.resource_type,
            resource_id=target.resource_id,
            resource_version=target.resource_version,
            authority_epoch=target.authority_epoch,
            business_receipt_id=self.completion.business_receipt_id,
            business_target_key=self.completion.business_target_key,
        )


@dataclass(frozen=True)
class InAppMessageProjection:
    """A content-redacted, authenticated-server projection for one result."""

    message_id: str
    kind: InAppMessageKind
    owner_subject_id: str
    vault_id: str
    resource_type: str
    resource_id: str
    resource_version: int
    authority_epoch: int
    business_receipt_id: str
    business_target_key: str
    state: InAppMessageState = InAppMessageState.UNREAD

    def __post_init__(self) -> None:
        object.__setattr__(self, "message_id", _uuid(self.message_id, field="message_id"))
        if not isinstance(self.kind, InAppMessageKind):
            raise BusinessMessageNotificationContractError("kind is required")
        object.__setattr__(
            self,
            "owner_subject_id",
            _identifier(self.owner_subject_id, field="owner_subject_id"),
        )
        object.__setattr__(self, "vault_id", _identifier(self.vault_id, field="vault_id"))
        object.__setattr__(
            self,
            "resource_type",
            _identifier(self.resource_type, field="resource_type"),
        )
        object.__setattr__(
            self,
            "resource_id",
            _identifier(self.resource_id, field="resource_id"),
        )
        object.__setattr__(
            self,
            "resource_version",
            _non_negative_int(self.resource_version, field="resource_version"),
        )
        object.__setattr__(
            self,
            "authority_epoch",
            _non_negative_int(self.authority_epoch, field="authority_epoch"),
        )
        object.__setattr__(
            self,
            "business_receipt_id",
            _uuid(self.business_receipt_id, field="business_receipt_id"),
        )
        object.__setattr__(
            self,
            "business_target_key",
            _sha256_hex(self.business_target_key, field="business_target_key"),
        )
        if not isinstance(self.state, InAppMessageState):
            raise BusinessMessageNotificationContractError("state is required")

    def server_projection(self) -> Mapping[str, object]:
        """Return the authenticated API payload; it contains no message body."""

        return {
            "authorityEpoch": self.authority_epoch,
            "businessReceiptId": self.business_receipt_id,
            "businessTargetKey": self.business_target_key,
            "contentRedacted": True,
            "id": self.message_id,
            "kind": self.kind.value,
            "metadataOnly": True,
            "ownerSubjectId": self.owner_subject_id,
            "resourceId": self.resource_id,
            "resourceType": self.resource_type,
            "resourceVersion": self.resource_version,
            "schemaVersion": BUSINESS_MESSAGE_NOTIFICATION_SCHEMA_VERSION,
            "state": self.state.value,
            "vaultId": self.vault_id,
        }

    def value_free_summary(self) -> Mapping[str, object]:
        return {
            "authorityEpoch": self.authority_epoch,
            "businessReceiptId": self.business_receipt_id,
            "kind": self.kind.value,
            "messageId": self.message_id,
            "ownerDigest": _digest(self.owner_subject_id),
            "resourceIdHash": _digest(self.resource_id),
            "resourceType": self.resource_type,
            "resourceVersion": self.resource_version,
            "schemaVersion": BUSINESS_MESSAGE_NOTIFICATION_SCHEMA_VERSION,
            "state": self.state.value,
            "vaultIdHash": _digest(self.vault_id),
        }


@dataclass(frozen=True)
class NotificationIntent:
    """A channel-specific request to notify about an existing message.

    It is a request, not a delivery receipt.  Creating it never means a local
    notification was scheduled, APNs accepted a payload, or a device saw it.
    """

    message: InAppMessageProjection
    channel: NotificationChannel
    generation: int

    def __post_init__(self) -> None:
        if not isinstance(self.message, InAppMessageProjection):
            raise BusinessMessageNotificationContractError("message projection is required")
        if not isinstance(self.channel, NotificationChannel):
            raise BusinessMessageNotificationContractError("notification channel is required")
        object.__setattr__(self, "generation", _non_negative_int(self.generation, field="generation"))

    @property
    def notification_intent_id(self) -> str:
        return str(
            uuid5(
                _MESSAGE_NAMESPACE,
                f"notification-intent:{self.message.message_id}:{self.channel.value}:{self.generation}",
            )
        )

    def route_contract(self) -> Mapping[str, object]:
        """Minimal route coordinates; click handling must re-authorize them."""

        return {
            "authorityEpoch": self.message.authority_epoch,
            "generation": self.generation,
            "messageId": self.message.message_id,
            "ownerDigest": _digest(self.message.owner_subject_id),
            "resourceId": self.message.resource_id,
            "resourceType": self.message.resource_type,
            "resourceVersion": self.message.resource_version,
        }

    def value_free_summary(self) -> Mapping[str, object]:
        return {
            "channel": self.channel.value,
            "notificationIntentId": self.notification_intent_id,
            "route": self.route_contract(),
            "schemaVersion": BUSINESS_MESSAGE_NOTIFICATION_SCHEMA_VERSION,
            "state": NotificationDeliveryState.QUEUED.value,
        }


@dataclass(frozen=True)
class NotificationDeliveryReceipt:
    """An append-only observation for one local/APNs delivery attempt."""

    intent: NotificationIntent
    provider_attempt_id: str
    state: NotificationDeliveryState
    reason_code: str
    observation_origin: str

    def __post_init__(self) -> None:
        if not isinstance(self.intent, NotificationIntent):
            raise BusinessMessageNotificationContractError("notification intent is required")
        object.__setattr__(
            self,
            "provider_attempt_id",
            _identifier(self.provider_attempt_id, field="provider_attempt_id"),
        )
        if self.state not in {
            NotificationDeliveryState.ACCEPTED,
            NotificationDeliveryState.FAILED,
            NotificationDeliveryState.UNKNOWN,
            NotificationDeliveryState.ARRIVED,
        }:
            raise BusinessMessageNotificationContractError(
                "delivery receipt must be accepted, failed, unknown, or arrived"
            )
        object.__setattr__(self, "reason_code", _identifier(self.reason_code, field="reason_code"))
        object.__setattr__(
            self,
            "observation_origin",
            _identifier(self.observation_origin, field="observation_origin"),
        )

    @property
    def receipt_id(self) -> str:
        return str(
            uuid5(
                _MESSAGE_NAMESPACE,
                ":".join(
                    (
                        "notification-delivery-receipt",
                        self.intent.notification_intent_id,
                        self.provider_attempt_id,
                        self.state.value,
                        self.observation_origin,
                    )
                ),
            )
        )

    def value_free_summary(self) -> Mapping[str, object]:
        return {
            "channel": self.intent.channel.value,
            "notificationIntentId": self.intent.notification_intent_id,
            "observationOrigin": self.observation_origin,
            "providerAttemptId": self.provider_attempt_id,
            "reasonCode": self.reason_code,
            "receiptId": self.receipt_id,
            "schemaVersion": BUSINESS_MESSAGE_NOTIFICATION_SCHEMA_VERSION,
            "state": self.state.value,
        }


@dataclass(frozen=True)
class NotificationIntentReconciliation:
    """Project independent delivery observations without mutating business state."""

    intent: NotificationIntent
    receipts: tuple[NotificationDeliveryReceipt, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.intent, NotificationIntent):
            raise BusinessMessageNotificationContractError("notification intent is required")
        receipts = tuple(self.receipts)
        for receipt in receipts:
            if not isinstance(receipt, NotificationDeliveryReceipt):
                raise BusinessMessageNotificationContractError("delivery receipt is required")
            if receipt.intent != self.intent:
                raise BusinessMessageNotificationContractError(
                    "delivery receipt belongs to a different notification intent"
                )
        unique_ids = {receipt.receipt_id for receipt in receipts}
        if len(unique_ids) != len(receipts):
            raise BusinessMessageNotificationContractError(
                "delivery observations must be append-only and unique"
            )
        object.__setattr__(self, "receipts", receipts)

    @property
    def state(self) -> NotificationDeliveryState:
        states = {receipt.state for receipt in self.receipts}
        if not states:
            return NotificationDeliveryState.QUEUED
        if NotificationDeliveryState.ARRIVED in states:
            return (
                NotificationDeliveryState.UNKNOWN
                if NotificationDeliveryState.FAILED in states
                else NotificationDeliveryState.ARRIVED
            )
        if NotificationDeliveryState.UNKNOWN in states:
            return NotificationDeliveryState.UNKNOWN
        if (
            NotificationDeliveryState.ACCEPTED in states
            and NotificationDeliveryState.FAILED in states
        ):
            return NotificationDeliveryState.UNKNOWN
        if NotificationDeliveryState.FAILED in states:
            return NotificationDeliveryState.FAILED
        return NotificationDeliveryState.ACCEPTED

    @property
    def requires_manual_review(self) -> bool:
        states = {receipt.state for receipt in self.receipts}
        return self.state is NotificationDeliveryState.UNKNOWN and bool(states)

    def value_free_summary(self) -> Mapping[str, object]:
        return {
            "deliveryReceiptCount": len(self.receipts),
            "notificationIntent": self.intent.value_free_summary(),
            "requiresManualReview": self.requires_manual_review,
            "schemaVersion": BUSINESS_MESSAGE_NOTIFICATION_SCHEMA_VERSION,
            "state": self.state.value,
        }


@dataclass(frozen=True)
class BusinessCompletionMessageNotificationPlan:
    """The one-way chain from business completion to notification intent."""

    source: BusinessCompletionMessageSource
    message: InAppMessageProjection
    notification_intents: tuple[NotificationIntent, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.source, BusinessCompletionMessageSource):
            raise BusinessMessageNotificationContractError("business completion source is required")
        if self.message != self.source.projection():
            raise BusinessMessageNotificationContractError(
                "message projection must be derived from the completion source"
            )
        intents = tuple(self.notification_intents)
        if not intents:
            raise BusinessMessageNotificationContractError(
                "at least one notification intent is required"
            )
        if any(intent.message != self.message for intent in intents):
            raise BusinessMessageNotificationContractError(
                "notification intent must reference the projected message"
            )
        channels = [intent.channel for intent in intents]
        if len(set(channels)) != len(channels):
            raise BusinessMessageNotificationContractError(
                "only one notification intent is allowed per channel and generation"
            )
        object.__setattr__(self, "notification_intents", intents)

    def value_free_summary(self) -> Mapping[str, object]:
        return {
            "businessOutcome": self.source.completion.business_outcome,
            "businessReceiptId": self.source.completion.business_receipt_id,
            "message": self.message.value_free_summary(),
            "notificationIntents": [
                intent.value_free_summary() for intent in self.notification_intents
            ],
            "schemaVersion": BUSINESS_MESSAGE_NOTIFICATION_SCHEMA_VERSION,
        }


def build_business_completion_message_notification_plan(
    source: BusinessCompletionMessageSource,
    *,
    notification_channels: Iterable[NotificationChannel],
    generation: int,
) -> BusinessCompletionMessageNotificationPlan:
    """Create a private message and default-off notification intents.

    Callers explicitly select channels.  Choosing ``NotificationChannel.APNS``
    merely records an intent; it does not require a device subscription or
    invoke APNs.  That deliberately keeps this G0 contract below the G3/G4
    provider and true-device gates.
    """

    if not isinstance(source, BusinessCompletionMessageSource):
        raise BusinessMessageNotificationContractError("business completion source is required")
    normalized_channels = tuple(notification_channels)
    if any(not isinstance(channel, NotificationChannel) for channel in normalized_channels):
        raise BusinessMessageNotificationContractError("notification channels must be typed")
    message = source.projection()
    return BusinessCompletionMessageNotificationPlan(
        source=source,
        message=message,
        notification_intents=tuple(
            NotificationIntent(message=message, channel=channel, generation=generation)
            for channel in normalized_channels
        ),
    )


__all__ = [
    "BUSINESS_MESSAGE_NOTIFICATION_SCHEMA_VERSION",
    "BusinessCompletionMessageNotificationPlan",
    "BusinessCompletionMessageSource",
    "BusinessMessageNotificationContractError",
    "InAppMessageKind",
    "InAppMessageProjection",
    "InAppMessageState",
    "NotificationChannel",
    "NotificationDeliveryReceipt",
    "NotificationDeliveryState",
    "NotificationIntent",
    "NotificationIntentReconciliation",
    "build_business_completion_message_notification_plan",
]
