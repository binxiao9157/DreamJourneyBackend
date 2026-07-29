"""Default-off, value-free admission shadow for cross-account TimeLetter messages.

This module is intentionally below any business-message projection writer.  It
does not persist a message, write the public mailbox, emit a notification, or
record an access receipt.  Its only enabled behavior is a same-scope, read-only
revalidation of an already completed TimeLetter target effect, a verified
recipient inbox bridge and the exact delegated read grant.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from typing import Any, Callable, ContextManager, Mapping, Protocol

from app.async_effects.legacy_identity_inbox_bridge import (
    LegacyInboxAccountResolutionError,
    ResolvedLegacyInboxAccount,
)
from app.async_effects.message_notification_effects import (
    BusinessCompletionMessageSource,
    InAppMessageKind,
)
from app.services.delegated_access import (
    AccessGrantPurpose,
    DelegatedAccessDecision,
    DelegatedAccessService,
    GrantOperation,
    ResourceScopeType,
)
from app.services.time_letter_delivery_effects import (
    TIME_LETTER_DELIVERY_CONSUMER_NAME,
    TimeLetterDeliveryCompletion,
    TimeLetterDeliveryDisposition,
    TimeLetterDeliveryTarget,
)


BUSINESS_MESSAGE_RECIPIENT_ADMISSION_SCHEMA_VERSION = "business-message-recipient-admission-v1"


class BusinessMessageRecipientAdmissionError(ValueError):
    """A caller supplied an invalid internal TimeLetter admission contract."""


class _LegacyInboxAccountResolver(Protocol):
    def resolve_active(self, subject_id: str) -> ResolvedLegacyInboxAccount:
        ...


class TimeLetterRecipientMessageAdmissionDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    WOULD_ADMIT = "would_admit"
    DENIED = "denied"
    INVALID = "invalid"


def _required(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise BusinessMessageRecipientAdmissionError(f"{field}Required")
    return normalized


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _parse_now(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(_required(value, field="now").replace("Z", "+00:00"))
    except ValueError as exc:
        raise BusinessMessageRecipientAdmissionError("nowInvalid") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class TimeLetterRecipientMessageAdmissionInput:
    """Immutable evidence from a completed recipient delivery target."""

    source: BusinessCompletionMessageSource
    delivery_completion: TimeLetterDeliveryCompletion
    target: TimeLetterDeliveryTarget
    now_iso: str

    def __post_init__(self) -> None:
        if not isinstance(self.source, BusinessCompletionMessageSource):
            raise BusinessMessageRecipientAdmissionError("businessCompletionSourceRequired")
        if not isinstance(self.delivery_completion, TimeLetterDeliveryCompletion):
            raise BusinessMessageRecipientAdmissionError("timeLetterDeliveryCompletionRequired")
        if not isinstance(self.target, TimeLetterDeliveryTarget):
            raise BusinessMessageRecipientAdmissionError("timeLetterDeliveryTargetRequired")
        object.__setattr__(self, "now_iso", self.now.isoformat())

    @property
    def now(self) -> datetime:
        return _parse_now(self.now_iso)


@dataclass(frozen=True)
class TimeLetterRecipientMessageAdmission:
    """A non-persistent exact-grant proof for a resolved recipient inbox."""

    source: BusinessCompletionMessageSource
    target: TimeLetterDeliveryTarget
    recipient_inbox: ResolvedLegacyInboxAccount
    access_decision: DelegatedAccessDecision

    def __post_init__(self) -> None:
        if not isinstance(self.source, BusinessCompletionMessageSource):
            raise BusinessMessageRecipientAdmissionError("businessCompletionSourceRequired")
        if not isinstance(self.target, TimeLetterDeliveryTarget):
            raise BusinessMessageRecipientAdmissionError("timeLetterDeliveryTargetRequired")
        if not isinstance(self.recipient_inbox, ResolvedLegacyInboxAccount):
            raise BusinessMessageRecipientAdmissionError("verifiedRecipientInboxRequired")
        if not isinstance(self.access_decision, DelegatedAccessDecision):
            raise BusinessMessageRecipientAdmissionError("delegatedAccessDecisionRequired")
        if not self.access_decision.allowed:
            raise BusinessMessageRecipientAdmissionError(
                f"delegatedAccessDenied:{self.access_decision.reason}"
            )
        if not self.access_decision.relationship_id or not self.access_decision.grant_id:
            raise BusinessMessageRecipientAdmissionError("delegatedAccessEvidenceMissing")

        recipient_subject_id = self.target.recipient_subject_id
        if recipient_subject_id is None:
            raise BusinessMessageRecipientAdmissionError("recipientSubjectRequired")
        snapshot = self.recipient_inbox.snapshot
        if (
            snapshot.inbox_subject_id != recipient_subject_id
            or self.source.inbox_subject_id != recipient_subject_id
            or self.source.inbox_vault_id != snapshot.inbox_vault_id
        ):
            raise BusinessMessageRecipientAdmissionError("recipientInboxCoordinatesMismatch")

    def value_free_summary(self) -> Mapping[str, object]:
        return {
            "accessDecision": {
                "allowed": self.access_decision.allowed,
                "grantIdHash": _digest(_required(self.access_decision.grant_id, field="grantId")),
                "reason": self.access_decision.reason,
                "receiptRecorded": self.access_decision.receipt_id is not None,
                "relationshipIdHash": _digest(
                    _required(self.access_decision.relationship_id, field="relationshipId")
                ),
            },
            "recipientInbox": self.recipient_inbox.value_free_summary(),
            "schemaVersion": BUSINESS_MESSAGE_RECIPIENT_ADMISSION_SCHEMA_VERSION,
            "source": self.source.projection().value_free_summary(),
            "target": self.target.value_free_reference(),
        }


@dataclass(frozen=True)
class TimeLetterRecipientMessageAdmissionResult:
    enabled: bool
    disposition: TimeLetterRecipientMessageAdmissionDisposition
    reason_code: str
    admission: TimeLetterRecipientMessageAdmission | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise BusinessMessageRecipientAdmissionError("admissionEnabledBooleanRequired")
        if not isinstance(self.disposition, TimeLetterRecipientMessageAdmissionDisposition):
            raise BusinessMessageRecipientAdmissionError("admissionDispositionRequired")
        object.__setattr__(self, "reason_code", _required(self.reason_code, field="reasonCode"))
        if self.disposition is TimeLetterRecipientMessageAdmissionDisposition.WOULD_ADMIT:
            if not isinstance(self.admission, TimeLetterRecipientMessageAdmission):
                raise BusinessMessageRecipientAdmissionError("wouldAdmitRequiresAdmission")
        elif self.admission is not None:
            raise BusinessMessageRecipientAdmissionError("nonAdmittedResultCannotContainAdmission")

    @property
    def would_admit(self) -> bool:
        return self.disposition is TimeLetterRecipientMessageAdmissionDisposition.WOULD_ADMIT

    def value_free_summary(self) -> Mapping[str, object]:
        return {
            "admission": None if self.admission is None else self.admission.value_free_summary(),
            "enabled": self.enabled,
            "reasonCode": self.reason_code,
            "schemaVersion": BUSINESS_MESSAGE_RECIPIENT_ADMISSION_SCHEMA_VERSION,
            "shadowOnly": True,
            "status": self.disposition.value,
            "wouldAdmit": self.would_admit,
        }


class TimeLetterRecipientMessageAdmissionService:
    """Same-scope default-off evaluator; never a writer or a public reader."""

    def __init__(
        self,
        store: Any,
        *,
        inbox_account_resolver_factory: Callable[[], _LegacyInboxAccountResolver] | None = None,
    ) -> None:
        self._store = store
        factory = inbox_account_resolver_factory
        if factory is None:
            factory = getattr(store, "async_effect_legacy_inbox_account_resolver", None)
        self._inbox_account_resolver_factory = factory if callable(factory) else None

    def evaluate_shadow(
        self,
        input: TimeLetterRecipientMessageAdmissionInput,
        *,
        enabled: bool = False,
    ) -> TimeLetterRecipientMessageAdmissionResult:
        """Return a value-free shadow result without any message/receipt write.

        ``enabled=False`` is intentionally the default and returns before it
        resolves the recipient bridge, takes a relationship lock or authorizes
        anything. An explicit enabled shadow still invokes authorization with
        ``record_receipt=False`` so no access evidence is appended merely by
        observing eligibility.
        """

        if not isinstance(input, TimeLetterRecipientMessageAdmissionInput):
            raise BusinessMessageRecipientAdmissionError("admissionInputRequired")
        if not isinstance(enabled, bool):
            raise BusinessMessageRecipientAdmissionError("admissionEnabledBooleanRequired")
        if not enabled:
            return TimeLetterRecipientMessageAdmissionResult(
                enabled=False,
                disposition=TimeLetterRecipientMessageAdmissionDisposition.SHADOW_DISABLED,
                reason_code="admissionDisabled",
            )

        if self._inbox_account_resolver_factory is None:
            return TimeLetterRecipientMessageAdmissionResult(
                enabled=True,
                disposition=TimeLetterRecipientMessageAdmissionDisposition.DENIED,
                reason_code="recipientInboxResolverUnavailable",
            )

        validation_error = self._validate_completion(input)
        if validation_error is not None:
            return TimeLetterRecipientMessageAdmissionResult(
                enabled=True,
                disposition=TimeLetterRecipientMessageAdmissionDisposition.INVALID,
                reason_code=validation_error,
            )

        target = input.target
        recipient_subject_id = target.recipient_subject_id
        if recipient_subject_id is None:  # `_validate_completion` already fences this.
            return TimeLetterRecipientMessageAdmissionResult(
                enabled=True,
                disposition=TimeLetterRecipientMessageAdmissionDisposition.INVALID,
                reason_code="recipientSubjectRequired",
            )
        with self._relationship_scope(target):
            try:
                recipient_inbox = self._inbox_account_resolver_factory().resolve_active(
                    recipient_subject_id
                )
            except (LegacyInboxAccountResolutionError, ValueError, RuntimeError):
                return TimeLetterRecipientMessageAdmissionResult(
                    enabled=True,
                    disposition=TimeLetterRecipientMessageAdmissionDisposition.DENIED,
                    reason_code="recipientInboxUnavailable",
                )

            snapshot = recipient_inbox.snapshot
            recipient_source = BusinessCompletionMessageSource(
                intent=input.source.intent,
                completion=input.source.completion,
                message_kind=InAppMessageKind.TIME_LETTER,
                inbox_subject_id=snapshot.inbox_subject_id,
                inbox_vault_id=snapshot.inbox_vault_id,
            )
            access = DelegatedAccessService(
                self._store,
                now_provider=lambda: input.now,
            ).authorize(
                owner_subject_id=target.snapshot.owner_subject_id,
                grantee_subject_id=recipient_subject_id,
                family_member_id=target.recipient_id,
                purpose=AccessGrantPurpose.TIME_LETTER_READ,
                operation=GrantOperation.READ,
                resource_type=ResourceScopeType.TIME_LETTER,
                # The access grant scopes the actual letter, not the derived
                # delivery-target stable key held by the async-effect intent.
                resource_id=target.snapshot.letter_id,
                record_receipt=False,
            )
            if not access.allowed:
                return TimeLetterRecipientMessageAdmissionResult(
                    enabled=True,
                    disposition=TimeLetterRecipientMessageAdmissionDisposition.DENIED,
                    reason_code=f"delegatedAccessDenied:{access.reason}",
                )
            try:
                admission = TimeLetterRecipientMessageAdmission(
                    source=recipient_source,
                    target=target,
                    recipient_inbox=recipient_inbox,
                    access_decision=access,
                )
            except BusinessMessageRecipientAdmissionError as exc:
                return TimeLetterRecipientMessageAdmissionResult(
                    enabled=True,
                    disposition=TimeLetterRecipientMessageAdmissionDisposition.DENIED,
                    reason_code=str(exc),
                )
        return TimeLetterRecipientMessageAdmissionResult(
            enabled=True,
            disposition=TimeLetterRecipientMessageAdmissionDisposition.WOULD_ADMIT,
            reason_code="wouldAdmit",
            admission=admission,
        )

    @staticmethod
    def _validate_completion(input: TimeLetterRecipientMessageAdmissionInput) -> str | None:
        source = input.source
        completion = input.delivery_completion
        target = input.target
        if source.message_kind is not InAppMessageKind.TIME_LETTER:
            return "timeLetterMessageKindRequired"
        if target.role != "recipient":
            return "recipientRoleInvalid"
        recipient_subject_id = target.recipient_subject_id
        if recipient_subject_id is None:
            return "recipientSubjectRequired"
        if recipient_subject_id == target.snapshot.owner_subject_id:
            return "crossAccountRecipientMustDifferFromOwner"
        if completion.target != target:
            return "deliveryCompletionTargetMismatch"
        if completion.disposition is not TimeLetterDeliveryDisposition.DELIVERED:
            return "deliveryCompletionDispositionInvalid"
        command = completion.consumer_command
        if command.consumer_name != TIME_LETTER_DELIVERY_CONSUMER_NAME:
            return "completionConsumerMismatch"
        if (
            source.intent != command.intent
            or source.completion.operation_id != command.intent.operation_id
            or source.completion.business_receipt_id != command.receipt_id
            or source.completion.consumer_name != command.consumer_name
            or source.completion.business_target_key != command.business_target_key
        ):
            return "completionSourceMismatch"
        # The source begins owner-scoped. Recipient inbox coordinates are bound
        # only after a fresh verified bridge lookup in the same relationship scope.
        if (
            source.inbox_subject_id != target.snapshot.owner_subject_id
            or source.inbox_vault_id != target.snapshot.vault_id
        ):
            return "sourceMustBeOwnerScoped"
        if not target.snapshot.is_due(input.now_iso):
            return "timeLetterNotDue"
        return None

    def _relationship_scope(self, target: TimeLetterDeliveryTarget) -> ContextManager[Any]:
        scope = getattr(self._store, "delegated_access_relationship_scope", None)
        if not callable(scope):
            return nullcontext()
        return scope(
            owner_subject_id=target.snapshot.owner_subject_id,
            relationship_id=DelegatedAccessService.relationship_id(
                target.snapshot.owner_subject_id,
                target.recipient_id,
            ),
        )


__all__ = [
    "BUSINESS_MESSAGE_RECIPIENT_ADMISSION_SCHEMA_VERSION",
    "BusinessMessageRecipientAdmissionError",
    "TimeLetterRecipientMessageAdmission",
    "TimeLetterRecipientMessageAdmissionDisposition",
    "TimeLetterRecipientMessageAdmissionInput",
    "TimeLetterRecipientMessageAdmissionResult",
    "TimeLetterRecipientMessageAdmissionService",
]
