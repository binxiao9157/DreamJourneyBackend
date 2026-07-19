"""Hidden V4 TimeLetter target delivery service.

This service deliberately does not replace the legacy dispatch route or start a
worker.  It consumes only an already-built, sealed V4 delivery plan and keeps
effect admission, live authorization, metadata-only mailbox persistence,
consumer receipt, and aggregate summary inside one Unit of Work.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, ContextManager, Mapping, Protocol

from app.services.delegated_access import (
    AccessGrantPurpose,
    DelegatedAccessService,
    GrantOperation,
    ResourceScopeType,
)
from app.services.time_letter_delivery_effects import (
    TIME_LETTER_DELIVERY_SCHEMA_VERSION,
    TimeLetterDeliveryCompletion,
    TimeLetterDeliveryContractError,
    TimeLetterDeliveryDisposition,
    TimeLetterDeliveryPlan,
    TimeLetterDeliveryTarget,
    TimeLetterSealedSnapshot,
)
from app.services.time_letters import family_member_for_recipient


class TimeLetterAtomicDeliveryPersistenceError(RuntimeError):
    """A V4 target delivery could not atomically retain its terminal outcome."""


class TimeLetterAtomicDeliveryStore(Protocol):
    def request_unit_of_work(
        self,
        *,
        correlation_id: str,
        command_id: str,
    ) -> ContextManager[Any]:
        ...

    def effect_kernel_repository(self) -> Any:
        ...

    def async_effect_consumer_repository(self) -> Any:
        ...

    def get_time_letter_for_atomic_delivery(
        self,
        owner_subject_id: str,
        letter_id: str,
    ) -> Mapping[str, Any] | None:
        ...

    def update_time_letter_delivery_summary(
        self,
        owner_subject_id: str,
        letter_id: str,
        snapshot: TimeLetterSealedSnapshot,
        expected_row_version: int,
        summary: Mapping[str, Any],
        delivered_at_iso: str,
    ) -> Mapping[str, Any] | None:
        ...

    def add_mailbox_letter(self, user_id: str, payload: dict[str, Any]) -> Mapping[str, Any]:
        ...

    def list_family_members(self, user_id: str) -> list[dict[str, Any]]:
        ...

    def get_family_relationship_by_member(
        self,
        owner_subject_id: str,
        family_member_id: str,
    ) -> Mapping[str, Any] | None:
        ...

    def list_access_grants(
        self,
        *,
        owner_subject_id: str,
        relationship_id: str,
    ) -> list[dict[str, Any]]:
        ...


@dataclass(frozen=True)
class TimeLetterAtomicDeliveryResult:
    """Value-free result for one sealed TimeLetter target dispatch attempt."""

    outcome: str
    delivery_status: str
    completions: tuple[TimeLetterDeliveryCompletion, ...]
    summary: Mapping[str, Any]

    def value_free_summary(self) -> Mapping[str, Any]:
        return {
            "completionCount": len(self.completions),
            "deliveryStatus": self.delivery_status,
            "outcome": self.outcome,
            "summary": dict(self.summary),
        }


def _parse_now(now_iso: str) -> datetime:
    normalized = str(now_iso or "").strip()
    if not normalized:
        raise TimeLetterDeliveryContractError("now is required")
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TimeLetterDeliveryContractError("now must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _field(item: Mapping[str, Any], name: str) -> object:
    metadata = item.get("metadata")
    if item.get(name) is not None:
        return item.get(name)
    return metadata.get(name) if isinstance(metadata, Mapping) else None


def _terminal_delivery_status(item: Mapping[str, Any]) -> str | None:
    status = str(_field(item, "deliveryStatus") or "").strip()
    return status if status in {"delivered", "partial", "blocked"} else None


def _live_snapshot_block_reason(
    item: Mapping[str, Any] | None,
    expected: TimeLetterSealedSnapshot,
) -> str | None:
    if item is None:
        return "letterNoLongerSealed"
    try:
        current = TimeLetterSealedSnapshot.from_archive_item(item)
    except TimeLetterDeliveryContractError:
        return "letterNoLongerSealed"
    if current.authority_epoch != expected.authority_epoch:
        return "authorityEpochChanged"
    if (
        current.sealed_version != expected.sealed_version
        or current.sealed_payload_hash != expected.sealed_payload_hash
        or current.open_at != expected.open_at
    ):
        return "sealedVersionChanged"
    if (
        current.owner_subject_id != expected.owner_subject_id
        or current.vault_id != expected.vault_id
        or current.letter_id != expected.letter_id
    ):
        return "letterNoLongerSealed"
    return None


def _mailbox_id(target: TimeLetterDeliveryTarget) -> str:
    digest = sha256(f"timeLetterMailbox:{target.stable_target_key}".encode("utf-8")).hexdigest()
    return f"time-letter-v1-{digest[:32]}"


def _mailbox_payload(
    target: TimeLetterDeliveryTarget,
    *,
    delivered_at_iso: str,
) -> dict[str, Any]:
    """Persist no body, title, recipient name, or other letter content in Inbox."""

    snapshot = target.snapshot
    return {
        "id": _mailbox_id(target),
        "kind": "timeLetterReminder",
        "ownerUserId": snapshot.owner_subject_id,
        "sourceArchiveItemId": snapshot.letter_id,
        "sealedVersion": snapshot.sealed_version,
        "recipientRole": target.role,
        "deliverAt": snapshot.open_at,
        "deliveredAt": delivered_at_iso,
        "status": "unread",
        "metadataOnly": True,
        "contentRedacted": True,
        "deliveryProtocolVersion": TIME_LETTER_DELIVERY_SCHEMA_VERSION,
        "deliveryTargetKey": target.stable_target_key,
        "title": "一封时间信件已到打开时间",
        "privacyMetadata": {
            "content": "redacted",
            "scope": "generationAllowed",
        },
    }


def _delivery_summary(
    plan: TimeLetterDeliveryPlan,
    completions: tuple[TimeLetterDeliveryCompletion, ...],
    *,
    delivered_at_iso: str,
) -> dict[str, Any]:
    if len(completions) != len(plan.targets):
        raise TimeLetterAtomicDeliveryPersistenceError("every delivery target requires a terminal completion")
    delivered_count = sum(
        completion.disposition is TimeLetterDeliveryDisposition.DELIVERED
        for completion in completions
    )
    skipped_count = sum(
        completion.disposition is TimeLetterDeliveryDisposition.SKIPPED_REVOKED
        for completion in completions
    )
    blocked_count = sum(
        completion.disposition is TimeLetterDeliveryDisposition.BLOCKED
        for completion in completions
    )
    if delivered_count == len(completions):
        status = "delivered"
    elif delivered_count:
        status = "partial"
    else:
        status = "blocked"
    return {
        "schemaVersion": TIME_LETTER_DELIVERY_SCHEMA_VERSION,
        "deliveryStatus": status,
        "deliveredAt": delivered_at_iso,
        "targetCount": len(completions),
        "deliveredCount": delivered_count,
        "skippedRevokedCount": skipped_count,
        "blockedCount": blocked_count,
        "letter": plan.snapshot.value_free_reference(),
        "targetOutcomes": [completion.value_free_summary() for completion in completions],
    }


class TimeLetterAtomicDeliveryService:
    """Hidden domain consumer for one due immutable TimeLetter plan."""

    def __init__(self, store: TimeLetterAtomicDeliveryStore):
        self._store = store

    def dispatch(
        self,
        plan: TimeLetterDeliveryPlan,
        *,
        now_iso: str,
    ) -> TimeLetterAtomicDeliveryResult:
        if not isinstance(plan, TimeLetterDeliveryPlan):
            raise TypeError("timeLetter delivery plan is required")
        _parse_now(now_iso)
        # A queued plan may outlive the clock it was built against.  Recheck
        # the immutable opening time at execution so a stale/future plan can
        # never deliver early.
        if not plan.is_due or not plan.snapshot.is_due(now_iso):
            return TimeLetterAtomicDeliveryResult(
                outcome="not_due",
                delivery_status="scheduled",
                completions=(),
                summary=plan.value_free_summary(),
            )

        snapshot = plan.snapshot
        with self._store.request_unit_of_work(
            correlation_id=f"time-letter-delivery-{snapshot.letter_id}-{snapshot.sealed_version}",
            command_id=sha256(snapshot.sealed_payload_hash.encode("utf-8")).hexdigest(),
        ):
            live_item = self._store.get_time_letter_for_atomic_delivery(
                snapshot.owner_subject_id,
                snapshot.letter_id,
            )
            terminal_status = _terminal_delivery_status(live_item or {})
            if terminal_status is not None:
                return TimeLetterAtomicDeliveryResult(
                    outcome="already_terminal",
                    delivery_status=terminal_status,
                    completions=(),
                    summary=self._terminal_summary(live_item, terminal_status),
                )

            kernel = self._store.effect_kernel_repository()
            consumer = self._store.async_effect_consumer_repository()
            for intent in plan.effect_intents:
                kernel.accept(intent)

            blocked_reason = _live_snapshot_block_reason(live_item, snapshot)
            if blocked_reason is not None:
                completions = tuple(
                    TimeLetterDeliveryCompletion(
                        target=target,
                        disposition=TimeLetterDeliveryDisposition.BLOCKED,
                        reason_code=blocked_reason,
                    )
                    for target in plan.targets
                )
                for completion in completions:
                    consumer.consume(completion.consumer_command)
                summary = _delivery_summary(plan, completions, delivered_at_iso=now_iso)
                return TimeLetterAtomicDeliveryResult(
                    outcome="blocked",
                    delivery_status="blocked",
                    completions=completions,
                    summary=summary,
                )

            completions = tuple(
                self._deliver_target(
                    target,
                    now_iso=now_iso,
                    consumer=consumer,
                )
                for target in plan.targets
            )
            summary = _delivery_summary(plan, completions, delivered_at_iso=now_iso)
            updated = self._store.update_time_letter_delivery_summary(
                snapshot.owner_subject_id,
                snapshot.letter_id,
                snapshot,
                int((live_item or {}).get("rowVersion") or 0),
                summary,
                now_iso,
            )
            if updated is None:
                raise TimeLetterAtomicDeliveryPersistenceError(
                    "timeLetter summary changed before atomic delivery could finalize"
                )
            return TimeLetterAtomicDeliveryResult(
                outcome=summary["deliveryStatus"],
                delivery_status=summary["deliveryStatus"],
                completions=completions,
                summary=summary,
            )

    def _deliver_target(
        self,
        target: TimeLetterDeliveryTarget,
        *,
        now_iso: str,
        consumer: Any,
    ) -> TimeLetterDeliveryCompletion:
        with self._target_authorization_scope(target):
            completion = self._authorize_target(target, now_iso=now_iso)
            if completion.disposition is TimeLetterDeliveryDisposition.DELIVERED:
                recipient_subject_id = target.recipient_subject_id
                if recipient_subject_id is None:  # guarded by the completion contract
                    raise TimeLetterAtomicDeliveryPersistenceError("delivered target lacks a recipient subject")
                self._store.add_mailbox_letter(
                    recipient_subject_id,
                    _mailbox_payload(target, delivered_at_iso=now_iso),
                )
            consumer.consume(completion.consumer_command)
            return completion

    def _target_authorization_scope(self, target: TimeLetterDeliveryTarget):
        if target.role == "owner":
            return nullcontext()
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

    def _authorize_target(
        self,
        target: TimeLetterDeliveryTarget,
        *,
        now_iso: str,
    ) -> TimeLetterDeliveryCompletion:
        if target.role == "owner":
            return TimeLetterDeliveryCompletion(
                target=target,
                disposition=TimeLetterDeliveryDisposition.DELIVERED,
                reason_code="mailboxPersisted",
            )
        if target.recipient_subject_id is None:
            return TimeLetterDeliveryCompletion(
                target=target,
                disposition=TimeLetterDeliveryDisposition.SKIPPED_REVOKED,
                reason_code="recipientSubjectUnavailable",
            )

        member = family_member_for_recipient(
            self._store,
            target.snapshot.owner_subject_id,
            target.recipient_id,
        )
        if member is None or not self._member_is_active(member):
            return TimeLetterDeliveryCompletion(
                target=target,
                disposition=TimeLetterDeliveryDisposition.SKIPPED_REVOKED,
                reason_code="familyRecipientInactive",
            )
        relationship = self._store.get_family_relationship_by_member(
            target.snapshot.owner_subject_id,
            target.recipient_id,
        )
        if relationship is None or str(relationship.get("status") or "") != "accepted":
            return TimeLetterDeliveryCompletion(
                target=target,
                disposition=TimeLetterDeliveryDisposition.SKIPPED_REVOKED,
                reason_code="grantRevoked",
            )
        if str(relationship.get("memberSubjectId") or "") != target.recipient_subject_id:
            return TimeLetterDeliveryCompletion(
                target=target,
                disposition=TimeLetterDeliveryDisposition.SKIPPED_REVOKED,
                reason_code="recipientSubjectChanged",
            )
        access = DelegatedAccessService(
            self._store,
            now_provider=lambda: _parse_now(now_iso),
        ).authorize(
            owner_subject_id=target.snapshot.owner_subject_id,
            grantee_subject_id=target.recipient_subject_id,
            family_member_id=target.recipient_id,
            purpose=AccessGrantPurpose.TIME_LETTER_READ,
            operation=GrantOperation.READ,
            resource_type=ResourceScopeType.TIME_LETTER,
            resource_id=target.snapshot.letter_id,
            record_receipt=False,
        )
        if not access.allowed:
            return TimeLetterDeliveryCompletion(
                target=target,
                disposition=TimeLetterDeliveryDisposition.SKIPPED_REVOKED,
                reason_code=(
                    "recipientSubjectChanged"
                    if access.reason == "relationshipSubjectMismatch"
                    else "grantRevoked"
                ),
            )
        return TimeLetterDeliveryCompletion(
            target=target,
            disposition=TimeLetterDeliveryDisposition.DELIVERED,
            reason_code="mailboxPersisted",
        )

    @staticmethod
    def _member_is_active(member: Mapping[str, Any]) -> bool:
        return (
            str(member.get("accessStatus") or "") == "active"
            and str(member.get("invitationStatus") or "") == "accepted"
        )

    @staticmethod
    def _terminal_summary(item: Mapping[str, Any] | None, status: str) -> Mapping[str, Any]:
        raw = item.get("deliverySummary") if isinstance(item, Mapping) else None
        if isinstance(raw, Mapping):
            return dict(raw)
        metadata = item.get("metadata") if isinstance(item, Mapping) else None
        nested = metadata.get("deliverySummary") if isinstance(metadata, Mapping) else None
        if isinstance(nested, Mapping):
            return dict(nested)
        return {
            "schemaVersion": TIME_LETTER_DELIVERY_SCHEMA_VERSION,
            "deliveryStatus": status,
            "legacyTerminal": True,
        }


__all__ = [
    "TimeLetterAtomicDeliveryPersistenceError",
    "TimeLetterAtomicDeliveryResult",
    "TimeLetterAtomicDeliveryService",
    "TimeLetterAtomicDeliveryStore",
]
