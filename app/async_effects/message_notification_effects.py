"""Business-completion, in-app message, and notification-intent contracts.

This is the G0 boundary for the V4 messaging seam.  A completed business
receipt can create a private in-app message projection and one or more
notification intents.  Delivery observations remain separate facts: a local
or APNs failure must never rewrite the business result or make an in-app
message disappear.

The module deliberately contains no persistence, raw device token, APNs
client, or user-visible message body. Device subscription lifecycle is only a
hash-only, in-process G0 contract; durable subscription storage and APNs
provider delivery remain separate G1-G4 concerns.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
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
_DEVICE_SUBSCRIPTION_NAMESPACE = UUID("dcad7bca-96fc-433f-9000-7682945e7a31")


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


class DeviceSubscriptionStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"


class DeviceSubscriptionLifecycleAction(str, Enum):
    REGISTER = "register"
    ROTATE = "rotate"
    REVOKE = "revoke"


class NotificationRouteAuthorizationReason(str, Enum):
    ALLOWED = "allowed"
    MALFORMED_ROUTE = "malformedRoute"
    SUBSCRIPTION_REVOKED = "subscriptionRevoked"
    OWNER_MISMATCH = "ownerMismatch"
    VAULT_MISMATCH = "vaultMismatch"
    AUTHORITY_EPOCH_MISMATCH = "authorityEpochMismatch"
    NOTIFICATION_GENERATION_MISMATCH = "notificationGenerationMismatch"
    SUBSCRIPTION_ID_MISMATCH = "subscriptionIdMismatch"
    SUBSCRIPTION_GENERATION_MISMATCH = "subscriptionGenerationMismatch"
    MESSAGE_MISMATCH = "messageMismatch"
    RESOURCE_MISMATCH = "resourceMismatch"


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
class DeviceSubscription:
    """A hash-only APNs subscription identity with explicit lifecycle state.

    The contract accepts a token hash only.  Raw APNs tokens must be normalized
    and hashed at the HTTP ingress boundary, then kept out of async effects,
    diagnostic exports, and notification route payloads.
    """

    subscription_id: str
    owner_subject_id: str
    vault_id: str
    installation_id: str
    platform: str
    token_hash: str
    generation: int
    authority_epoch: int
    status: DeviceSubscriptionStatus = DeviceSubscriptionStatus.ACTIVE

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "subscription_id",
            _uuid(self.subscription_id, field="subscription_id"),
        )
        object.__setattr__(
            self,
            "owner_subject_id",
            _identifier(self.owner_subject_id, field="owner_subject_id"),
        )
        object.__setattr__(self, "vault_id", _identifier(self.vault_id, field="vault_id"))
        object.__setattr__(
            self,
            "installation_id",
            _identifier(self.installation_id, field="installation_id"),
        )
        platform = str(self.platform or "").strip().lower()
        if platform != "ios":
            raise BusinessMessageNotificationContractError(
                "device subscription platform must be ios"
            )
        object.__setattr__(self, "platform", platform)
        object.__setattr__(self, "token_hash", _sha256_hex(self.token_hash, field="token_hash"))
        object.__setattr__(self, "generation", _non_negative_int(self.generation, field="generation"))
        object.__setattr__(
            self,
            "authority_epoch",
            _non_negative_int(self.authority_epoch, field="authority_epoch"),
        )
        if not isinstance(self.status, DeviceSubscriptionStatus):
            raise BusinessMessageNotificationContractError("device subscription status is required")

    @classmethod
    def register(
        cls,
        *,
        owner_subject_id: str,
        vault_id: str,
        installation_id: str,
        platform: str,
        token_hash: str,
        authority_epoch: int,
    ) -> "DeviceSubscription":
        """Create the first generation without accepting a raw device token."""

        owner = _identifier(owner_subject_id, field="owner_subject_id")
        vault = _identifier(vault_id, field="vault_id")
        installation = _identifier(installation_id, field="installation_id")
        normalized_platform = str(platform or "").strip().lower()
        if normalized_platform != "ios":
            raise BusinessMessageNotificationContractError(
                "device subscription platform must be ios"
            )
        return cls(
            subscription_id=str(
                uuid5(
                    _DEVICE_SUBSCRIPTION_NAMESPACE,
                    ":".join(("device-subscription", owner, vault, installation, normalized_platform)),
                )
            ),
            owner_subject_id=owner,
            vault_id=vault,
            installation_id=installation,
            platform=normalized_platform,
            token_hash=token_hash,
            generation=0,
            authority_epoch=authority_epoch,
        )

    @property
    def is_active(self) -> bool:
        return self.status is DeviceSubscriptionStatus.ACTIVE

    def rotated(self, *, token_hash: str) -> "DeviceSubscription":
        """Rotate the token hash; same-hash replay remains idempotent."""

        if not self.is_active:
            raise BusinessMessageNotificationContractError(
                "revoked device subscription cannot rotate"
            )
        normalized_hash = _sha256_hex(token_hash, field="token_hash")
        if normalized_hash == self.token_hash:
            return self
        return replace(self, token_hash=normalized_hash, generation=self.generation + 1)

    def revoked(self) -> "DeviceSubscription":
        """Revoke delivery without deleting historical subscription identity."""

        if self.status is DeviceSubscriptionStatus.REVOKED:
            return self
        return replace(self, status=DeviceSubscriptionStatus.REVOKED)

    def value_free_summary(self) -> Mapping[str, object]:
        """Safe trace shape: token hash and installation ID are never exported."""

        return {
            "authorityEpoch": self.authority_epoch,
            "generation": self.generation,
            "ownerDigest": _digest(self.owner_subject_id),
            "platform": self.platform,
            "schemaVersion": BUSINESS_MESSAGE_NOTIFICATION_SCHEMA_VERSION,
            "status": self.status.value,
            "subscriptionId": self.subscription_id,
            "vaultDigest": _digest(self.vault_id),
        }


@dataclass(frozen=True)
class DeviceSubscriptionTransition:
    """Value-free register, rotate, or revoke receipt for a subscription."""

    action: DeviceSubscriptionLifecycleAction
    subscription: DeviceSubscription
    previous_generation: int | None
    changed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.action, DeviceSubscriptionLifecycleAction):
            raise BusinessMessageNotificationContractError(
                "device subscription lifecycle action is required"
            )
        if not isinstance(self.subscription, DeviceSubscription):
            raise BusinessMessageNotificationContractError("device subscription is required")
        if self.previous_generation is not None:
            object.__setattr__(
                self,
                "previous_generation",
                _non_negative_int(self.previous_generation, field="previous_generation"),
            )
        if not isinstance(self.changed, bool):
            raise BusinessMessageNotificationContractError("changed must be a boolean")

    def value_free_summary(self) -> Mapping[str, object]:
        return {
            "action": self.action.value,
            "changed": self.changed,
            "previousGeneration": self.previous_generation,
            "schemaVersion": BUSINESS_MESSAGE_NOTIFICATION_SCHEMA_VERSION,
            "subscription": self.subscription.value_free_summary(),
        }


def register_device_subscription(
    *,
    owner_subject_id: str,
    vault_id: str,
    installation_id: str,
    platform: str,
    token_hash: str,
    authority_epoch: int,
) -> DeviceSubscriptionTransition:
    subscription = DeviceSubscription.register(
        owner_subject_id=owner_subject_id,
        vault_id=vault_id,
        installation_id=installation_id,
        platform=platform,
        token_hash=token_hash,
        authority_epoch=authority_epoch,
    )
    return DeviceSubscriptionTransition(
        action=DeviceSubscriptionLifecycleAction.REGISTER,
        subscription=subscription,
        previous_generation=None,
        changed=True,
    )


def rotate_device_subscription(
    subscription: DeviceSubscription,
    *,
    token_hash: str,
) -> DeviceSubscriptionTransition:
    if not isinstance(subscription, DeviceSubscription):
        raise BusinessMessageNotificationContractError("device subscription is required")
    rotated = subscription.rotated(token_hash=token_hash)
    return DeviceSubscriptionTransition(
        action=DeviceSubscriptionLifecycleAction.ROTATE,
        subscription=rotated,
        previous_generation=subscription.generation,
        changed=rotated != subscription,
    )


def revoke_device_subscription(subscription: DeviceSubscription) -> DeviceSubscriptionTransition:
    if not isinstance(subscription, DeviceSubscription):
        raise BusinessMessageNotificationContractError("device subscription is required")
    revoked = subscription.revoked()
    return DeviceSubscriptionTransition(
        action=DeviceSubscriptionLifecycleAction.REVOKE,
        subscription=revoked,
        previous_generation=subscription.generation,
        changed=revoked != subscription,
    )


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
class NotificationRouteAuthorization:
    """Result of fail-closed route validation before opening a message."""

    allowed: bool
    reason: NotificationRouteAuthorizationReason

    def __post_init__(self) -> None:
        if not isinstance(self.allowed, bool):
            raise BusinessMessageNotificationContractError("route authorization allowed must be a boolean")
        if not isinstance(self.reason, NotificationRouteAuthorizationReason):
            raise BusinessMessageNotificationContractError("route authorization reason is required")
        if self.allowed != (self.reason is NotificationRouteAuthorizationReason.ALLOWED):
            raise BusinessMessageNotificationContractError(
                "route authorization allowed must agree with its reason"
            )

    def value_free_summary(self) -> Mapping[str, object]:
        return {
            "allowed": self.allowed,
            "reason": self.reason.value,
            "schemaVersion": BUSINESS_MESSAGE_NOTIFICATION_SCHEMA_VERSION,
        }


@dataclass(frozen=True)
class DeviceSubscriptionNotificationBinding:
    """Bind an APNs intent to one current, hash-only device subscription.

    A later registration rotation increments the subscription generation. Old
    notification routes cannot validate against the new generation, and a
    revoked subscription cannot route a notification at all.
    """

    subscription: DeviceSubscription
    notification_intent: NotificationIntent

    def __post_init__(self) -> None:
        if not isinstance(self.subscription, DeviceSubscription):
            raise BusinessMessageNotificationContractError("device subscription is required")
        if not isinstance(self.notification_intent, NotificationIntent):
            raise BusinessMessageNotificationContractError("notification intent is required")
        if self.notification_intent.channel is not NotificationChannel.APNS:
            raise BusinessMessageNotificationContractError(
                "device subscriptions may only bind APNs notification intents"
            )
        message = self.notification_intent.message
        if message.owner_subject_id != self.subscription.owner_subject_id:
            raise BusinessMessageNotificationContractError(
                "device subscription owner must match notification owner"
            )
        if message.vault_id != self.subscription.vault_id:
            raise BusinessMessageNotificationContractError(
                "device subscription vault must match notification vault"
            )
        if message.authority_epoch != self.subscription.authority_epoch:
            raise BusinessMessageNotificationContractError(
                "device subscription authority epoch must match notification authority"
            )

    @property
    def is_deliverable(self) -> bool:
        return self.subscription.is_active

    def route_contract(self) -> Mapping[str, object]:
        """Opaque click route, with no token, installation, or message body."""

        route = dict(self.notification_intent.route_contract())
        route.update(
            {
                "deviceSubscriptionGeneration": self.subscription.generation,
                "deviceSubscriptionId": self.subscription.subscription_id,
                "deviceSubscriptionOwnerDigest": _digest(self.subscription.owner_subject_id),
                "deviceSubscriptionVaultDigest": _digest(self.subscription.vault_id),
                "deviceSubscriptionPlatform": self.subscription.platform,
            }
        )
        return route

    def authorize_route(self, route: Mapping[str, object]) -> NotificationRouteAuthorization:
        """Verify a route against current owner, authority, and generations."""

        if not isinstance(route, Mapping):
            return NotificationRouteAuthorization(
                allowed=False,
                reason=NotificationRouteAuthorizationReason.MALFORMED_ROUTE,
            )

        try:
            message_id = _uuid(route.get("messageId"), field="route.messageId")
            owner_digest = _sha256_hex(route.get("ownerDigest"), field="route.ownerDigest")
            vault_digest = _sha256_hex(
                route.get("deviceSubscriptionVaultDigest"),
                field="route.deviceSubscriptionVaultDigest",
            )
            subscription_owner_digest = _sha256_hex(
                route.get("deviceSubscriptionOwnerDigest"),
                field="route.deviceSubscriptionOwnerDigest",
            )
            authority_epoch = _non_negative_int(
                route.get("authorityEpoch"),
                field="route.authorityEpoch",
            )
            notification_generation = _non_negative_int(
                route.get("generation"),
                field="route.generation",
            )
            subscription_id = _uuid(
                route.get("deviceSubscriptionId"),
                field="route.deviceSubscriptionId",
            )
            subscription_generation = _non_negative_int(
                route.get("deviceSubscriptionGeneration"),
                field="route.deviceSubscriptionGeneration",
            )
            platform = str(route.get("deviceSubscriptionPlatform") or "").strip().lower()
            resource_type = _identifier(route.get("resourceType"), field="route.resourceType")
            resource_id = _identifier(route.get("resourceId"), field="route.resourceId")
            resource_version = _non_negative_int(
                route.get("resourceVersion"),
                field="route.resourceVersion",
            )
        except BusinessMessageNotificationContractError:
            return NotificationRouteAuthorization(
                allowed=False,
                reason=NotificationRouteAuthorizationReason.MALFORMED_ROUTE,
            )

        message = self.notification_intent.message
        if message_id != message.message_id:
            return NotificationRouteAuthorization(
                allowed=False,
                reason=NotificationRouteAuthorizationReason.MESSAGE_MISMATCH,
            )
        if owner_digest != _digest(message.owner_subject_id) or subscription_owner_digest != _digest(
            self.subscription.owner_subject_id
        ):
            return NotificationRouteAuthorization(
                allowed=False,
                reason=NotificationRouteAuthorizationReason.OWNER_MISMATCH,
            )
        if vault_digest != _digest(self.subscription.vault_id):
            return NotificationRouteAuthorization(
                allowed=False,
                reason=NotificationRouteAuthorizationReason.VAULT_MISMATCH,
            )
        if authority_epoch != message.authority_epoch or authority_epoch != self.subscription.authority_epoch:
            return NotificationRouteAuthorization(
                allowed=False,
                reason=NotificationRouteAuthorizationReason.AUTHORITY_EPOCH_MISMATCH,
            )
        if notification_generation != self.notification_intent.generation:
            return NotificationRouteAuthorization(
                allowed=False,
                reason=NotificationRouteAuthorizationReason.NOTIFICATION_GENERATION_MISMATCH,
            )
        if subscription_id != self.subscription.subscription_id or platform != self.subscription.platform:
            return NotificationRouteAuthorization(
                allowed=False,
                reason=NotificationRouteAuthorizationReason.SUBSCRIPTION_ID_MISMATCH,
            )
        if subscription_generation != self.subscription.generation:
            return NotificationRouteAuthorization(
                allowed=False,
                reason=NotificationRouteAuthorizationReason.SUBSCRIPTION_GENERATION_MISMATCH,
            )
        if (
            resource_type != message.resource_type
            or resource_id != message.resource_id
            or resource_version != message.resource_version
        ):
            return NotificationRouteAuthorization(
                allowed=False,
                reason=NotificationRouteAuthorizationReason.RESOURCE_MISMATCH,
            )
        if not self.subscription.is_active:
            return NotificationRouteAuthorization(
                allowed=False,
                reason=NotificationRouteAuthorizationReason.SUBSCRIPTION_REVOKED,
            )
        return NotificationRouteAuthorization(
            allowed=True,
            reason=NotificationRouteAuthorizationReason.ALLOWED,
        )

    def value_free_summary(self) -> Mapping[str, object]:
        return {
            "notificationIntentId": self.notification_intent.notification_intent_id,
            "route": self.route_contract(),
            "schemaVersion": BUSINESS_MESSAGE_NOTIFICATION_SCHEMA_VERSION,
            "subscription": self.subscription.value_free_summary(),
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
    "DeviceSubscription",
    "DeviceSubscriptionLifecycleAction",
    "DeviceSubscriptionNotificationBinding",
    "DeviceSubscriptionStatus",
    "DeviceSubscriptionTransition",
    "InAppMessageKind",
    "InAppMessageProjection",
    "InAppMessageState",
    "NotificationChannel",
    "NotificationDeliveryReceipt",
    "NotificationDeliveryState",
    "NotificationIntent",
    "NotificationIntentReconciliation",
    "NotificationRouteAuthorization",
    "NotificationRouteAuthorizationReason",
    "build_business_completion_message_notification_plan",
    "register_device_subscription",
    "revoke_device_subscription",
    "rotate_device_subscription",
]
