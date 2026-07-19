"""Value-free TimeLetter delivery targets for the async-effect kernel.

The legacy TimeLetter dispatcher updates an Archive payload before it writes
mailbox reminders.  This module is deliberately separate from that path: it
admits only sealed, versioned envelopes and produces one immutable effect
intent per recipient.  It does not deliver a message, call a provider, or
enable a worker.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import json
import re
from typing import Any, Mapping, Sequence

from app.async_effects.consumer_repository import AsyncEffectConsumerCompletionCommand
from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget


TIME_LETTER_DELIVERY_SCHEMA_VERSION = "time-letter-delivery-v1"
TIME_LETTER_DELIVERY_OPERATION_TYPE = "timeLetter.deliveryTarget.dispatch"
TIME_LETTER_DELIVERY_EVENT_TYPE = "timeLetter.deliveryTarget.dispatchRequested"
TIME_LETTER_DELIVERY_JOB_TYPE = "timeLetter.deliveryTarget.dispatch"
TIME_LETTER_DELIVERY_RESOURCE_TYPE = "timeLetterDeliveryTarget"
TIME_LETTER_DELIVERY_PURPOSE = "timeLetterDelivery"
TIME_LETTER_DELIVERY_CONSUMER_NAME = "timeLetter.deliveryTarget"

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class TimeLetterDeliveryContractError(ValueError):
    """The sealed TimeLetter lacks the immutable delivery coordinates."""


class TimeLetterDeliveryDisposition(str, Enum):
    DELIVERED = "delivered"
    SKIPPED_REVOKED = "skipped_revoked"
    BLOCKED = "blocked"
    NOT_DUE = "not_due"


_SKIPPED_REVOKED_REASONS = frozenset(
    {
        "familyRecipientInactive",
        "grantRevoked",
        "recipientSubjectChanged",
        "recipientSubjectUnavailable",
    }
)
_BLOCKED_REASONS = frozenset(
    {
        "authorityEpochChanged",
        "letterNoLongerSealed",
        "sealedVersionChanged",
        "timeLetterTargetInvalid",
    }
)


def _required(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise TimeLetterDeliveryContractError(f"{field} is required")
    return normalized


def _non_negative_int(value: object, *, field: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise TimeLetterDeliveryContractError(f"{field} must be an integer >= {minimum}")
    return value


def _sha256_hex(value: object, *, field: str) -> str:
    normalized = _required(value, field=field).lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise TimeLetterDeliveryContractError(f"{field} must be a lowercase SHA-256 digest")
    return normalized


def _metadata(item: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = item.get("metadata")
    return raw if isinstance(raw, Mapping) else {}


def _field(item: Mapping[str, Any], metadata: Mapping[str, Any], name: str) -> object:
    return item.get(name) if item.get(name) is not None else metadata.get(name)


def _parse_iso_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TimeLetterDeliveryContractError("openAt must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _canonical_hash(value: Mapping[str, object]) -> str:
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TimeLetterSealedSnapshot:
    """Minimal immutable delivery envelope, intentionally without content."""

    owner_subject_id: str
    vault_id: str
    letter_id: str
    sealed_version: int
    authority_epoch: int
    sealed_payload_hash: str
    open_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner_subject_id", _required(self.owner_subject_id, field="ownerSubjectId"))
        object.__setattr__(self, "vault_id", _required(self.vault_id, field="vaultId"))
        object.__setattr__(self, "letter_id", _required(self.letter_id, field="letterId"))
        object.__setattr__(
            self,
            "sealed_version",
            _non_negative_int(self.sealed_version, field="sealedVersion", minimum=1),
        )
        object.__setattr__(
            self,
            "authority_epoch",
            _non_negative_int(self.authority_epoch, field="authorityEpoch"),
        )
        object.__setattr__(
            self,
            "sealed_payload_hash",
            _sha256_hex(self.sealed_payload_hash, field="sealedPayloadHash"),
        )
        normalized_open_at = _required(self.open_at, field="openAt")
        _parse_iso_datetime(normalized_open_at)
        object.__setattr__(self, "open_at", normalized_open_at)

    @classmethod
    def from_archive_item(cls, item: Mapping[str, Any]) -> "TimeLetterSealedSnapshot":
        if not isinstance(item, Mapping):
            raise TimeLetterDeliveryContractError("timeLetter item is required")
        metadata = _metadata(item)
        if str(item.get("kind") or "").strip() != "timeLetter":
            raise TimeLetterDeliveryContractError("item must be a timeLetter")
        if str(_field(item, metadata, "deliveryState") or "").strip() != "sealed":
            raise TimeLetterDeliveryContractError("timeLetter must be sealed")
        if str(_field(item, metadata, "deliveryStatus") or "").strip() != "scheduled":
            raise TimeLetterDeliveryContractError("timeLetter deliveryStatus must be scheduled")
        return cls(
            owner_subject_id=_field(item, metadata, "ownerSubjectId"),
            vault_id=_field(item, metadata, "vaultId"),
            letter_id=item.get("id"),
            sealed_version=_field(item, metadata, "sealedVersion"),
            authority_epoch=_field(item, metadata, "authorityEpoch"),
            sealed_payload_hash=_field(item, metadata, "sealedPayloadHash"),
            open_at=_field(item, metadata, "openAt"),
        )

    def is_due(self, now_iso: str) -> bool:
        return _parse_iso_datetime(self.open_at) <= _parse_iso_datetime(_required(now_iso, field="now"))

    def value_free_reference(self) -> Mapping[str, object]:
        return {
            "authorityEpoch": self.authority_epoch,
            "letterIdHash": sha256(self.letter_id.encode("utf-8")).hexdigest(),
            "sealedPayloadHash": self.sealed_payload_hash,
            "sealedVersion": self.sealed_version,
            "vaultIdHash": sha256(self.vault_id.encode("utf-8")).hexdigest(),
        }


@dataclass(frozen=True)
class TimeLetterDeliveryTarget:
    """One immutable intended target of a sealed TimeLetter delivery."""

    snapshot: TimeLetterSealedSnapshot
    recipient_id: str
    recipient_subject_id: str | None
    role: str

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, TimeLetterSealedSnapshot):
            raise TimeLetterDeliveryContractError("sealed snapshot is required")
        normalized_recipient_id = _required(self.recipient_id, field="recipientId")
        normalized_role = _required(self.role, field="recipientRole")
        if normalized_role not in {"owner", "recipient"}:
            raise TimeLetterDeliveryContractError("recipientRole must be owner or recipient")
        normalized_subject_id = (
            None
            if self.recipient_subject_id is None
            else _required(self.recipient_subject_id, field="recipientSubjectId")
        )
        if normalized_role == "owner":
            if normalized_recipient_id != "self":
                raise TimeLetterDeliveryContractError("owner recipientId must be self")
            if normalized_subject_id != self.snapshot.owner_subject_id:
                raise TimeLetterDeliveryContractError("owner target must retain the owner subject")
        object.__setattr__(self, "recipient_id", normalized_recipient_id)
        object.__setattr__(self, "recipient_subject_id", normalized_subject_id)
        object.__setattr__(self, "role", normalized_role)

    @property
    def stable_target_key(self) -> str:
        return _canonical_hash(
            {
                "letterId": self.snapshot.letter_id,
                "recipientId": self.recipient_id,
                "schemaVersion": TIME_LETTER_DELIVERY_SCHEMA_VERSION,
                "sealedVersion": self.snapshot.sealed_version,
                "vaultId": self.snapshot.vault_id,
            }
        )

    @property
    def effect_intent(self) -> AsyncEffectIntent:
        return AsyncEffectIntent(
            operation_type=TIME_LETTER_DELIVERY_OPERATION_TYPE,
            target=AsyncEffectTarget(
                owner_subject_id=self.snapshot.owner_subject_id,
                vault_id=self.snapshot.vault_id,
                resource_type=TIME_LETTER_DELIVERY_RESOURCE_TYPE,
                resource_id=self.stable_target_key,
                resource_version=self.snapshot.sealed_version,
                purpose=TIME_LETTER_DELIVERY_PURPOSE,
                authority_epoch=self.snapshot.authority_epoch,
            ),
            payload_hash=self.snapshot.sealed_payload_hash,
            event_type=TIME_LETTER_DELIVERY_EVENT_TYPE,
            job_type=TIME_LETTER_DELIVERY_JOB_TYPE,
        )

    def value_free_reference(self) -> Mapping[str, object]:
        return {
            "recipientIdHash": sha256(self.recipient_id.encode("utf-8")).hexdigest(),
            "recipientSubjectPresent": self.recipient_subject_id is not None,
            "role": self.role,
            "targetKey": self.stable_target_key,
        }


@dataclass(frozen=True)
class TimeLetterDeliveryAdmission:
    target: TimeLetterDeliveryTarget
    disposition: TimeLetterDeliveryDisposition
    reason_code: str

    @property
    def should_enqueue(self) -> bool:
        return self.disposition is not TimeLetterDeliveryDisposition.NOT_DUE


@dataclass(frozen=True)
class TimeLetterDeliveryCompletion:
    """Map one rechecked target outcome to the generic receipt-only kernel."""

    target: TimeLetterDeliveryTarget
    disposition: TimeLetterDeliveryDisposition
    reason_code: str

    def __post_init__(self) -> None:
        if not isinstance(self.target, TimeLetterDeliveryTarget):
            raise TimeLetterDeliveryContractError("delivery target is required")
        if not isinstance(self.disposition, TimeLetterDeliveryDisposition):
            raise TimeLetterDeliveryContractError("delivery disposition is required")
        normalized_reason = _required(self.reason_code, field="reasonCode")
        if self.disposition is TimeLetterDeliveryDisposition.NOT_DUE:
            raise TimeLetterDeliveryContractError("not_due targets cannot write a terminal receipt")
        if self.disposition is TimeLetterDeliveryDisposition.DELIVERED:
            if self.target.recipient_subject_id is None:
                raise TimeLetterDeliveryContractError(
                    "delivered target requires a recipient subject"
                )
            if normalized_reason != "mailboxPersisted":
                raise TimeLetterDeliveryContractError(
                    "delivered target must retain mailboxPersisted"
                )
        elif self.disposition is TimeLetterDeliveryDisposition.SKIPPED_REVOKED:
            if self.target.role != "recipient":
                raise TimeLetterDeliveryContractError("owner target cannot be skipped as revoked")
            if normalized_reason not in _SKIPPED_REVOKED_REASONS:
                raise TimeLetterDeliveryContractError("skipped target has an invalid reasonCode")
        elif self.disposition is TimeLetterDeliveryDisposition.BLOCKED:
            if normalized_reason not in _BLOCKED_REASONS:
                raise TimeLetterDeliveryContractError("blocked target has an invalid reasonCode")
        object.__setattr__(self, "reason_code", normalized_reason)

    @property
    def outcome(self) -> str:
        if self.disposition is TimeLetterDeliveryDisposition.DELIVERED:
            return "completed"
        if self.disposition is TimeLetterDeliveryDisposition.SKIPPED_REVOKED:
            return "skipped"
        return "blocked"

    @property
    def result_ref_hash(self) -> str:
        return _canonical_hash(
            {
                "disposition": self.disposition.value,
                "reasonCode": self.reason_code,
                "schemaVersion": TIME_LETTER_DELIVERY_SCHEMA_VERSION,
                "targetKey": self.target.stable_target_key,
            }
        )

    @property
    def consumer_command(self) -> AsyncEffectConsumerCompletionCommand:
        intent = self.target.effect_intent
        return AsyncEffectConsumerCompletionCommand(
            intent=intent,
            consumer_name=TIME_LETTER_DELIVERY_CONSUMER_NAME,
            business_target_key=intent.business_target_key,
            outcome=self.outcome,
            reason_code=self.reason_code,
            result_ref_hash=self.result_ref_hash,
        )

    def value_free_summary(self) -> Mapping[str, object]:
        return {
            "disposition": self.disposition.value,
            "reasonCode": self.reason_code,
            "resultRefHash": self.result_ref_hash,
            "target": self.target.value_free_reference(),
        }


@dataclass(frozen=True)
class TimeLetterDeliveryPlan:
    snapshot: TimeLetterSealedSnapshot
    targets: tuple[TimeLetterDeliveryTarget, ...]
    admissions: tuple[TimeLetterDeliveryAdmission, ...]

    def __post_init__(self) -> None:
        if len(self.targets) != len(self.admissions):
            raise TimeLetterDeliveryContractError("each target requires one admission")
        if any(admission.target != target for target, admission in zip(self.targets, self.admissions)):
            raise TimeLetterDeliveryContractError("admissions must preserve target order")

    @property
    def is_due(self) -> bool:
        return bool(self.admissions) and all(
            admission.disposition is not TimeLetterDeliveryDisposition.NOT_DUE
            for admission in self.admissions
        )

    @property
    def effect_intents(self) -> tuple[AsyncEffectIntent, ...]:
        return tuple(
            admission.target.effect_intent
            for admission in self.admissions
            if admission.should_enqueue
        )

    def value_free_summary(self) -> Mapping[str, object]:
        return {
            "schemaVersion": TIME_LETTER_DELIVERY_SCHEMA_VERSION,
            "sealedVersion": self.snapshot.sealed_version,
            "targetCount": len(self.targets),
            "due": self.is_due,
            "letter": self.snapshot.value_free_reference(),
            "targets": [
                {
                    "disposition": admission.disposition.value,
                    "reasonCode": admission.reason_code,
                    "target": admission.target.value_free_reference(),
                }
                for admission in self.admissions
            ],
        }


def _target_records(item: Mapping[str, Any], snapshot: TimeLetterSealedSnapshot) -> Sequence[TimeLetterDeliveryTarget]:
    recipients = item.get("recipients")
    records = recipients if isinstance(recipients, list) else []
    targets = [
        TimeLetterDeliveryTarget(
            snapshot=snapshot,
            recipient_id="self",
            recipient_subject_id=snapshot.owner_subject_id,
            role="owner",
        )
    ]
    seen_recipient_ids = {"self"}
    for raw in records:
        if not isinstance(raw, Mapping):
            continue
        recipient_id = str(raw.get("id") or "").strip()
        if not recipient_id or recipient_id in seen_recipient_ids:
            continue
        seen_recipient_ids.add(recipient_id)
        subject_id = raw.get("subjectId")
        if subject_id is None:
            subject_id = raw.get("memberSubjectId")
        if subject_id is None:
            subject_id = raw.get("recipientSubjectId")
        targets.append(
            TimeLetterDeliveryTarget(
                snapshot=snapshot,
                recipient_id=recipient_id,
                recipient_subject_id=None if subject_id is None else str(subject_id),
                role="recipient",
            )
        )
    return tuple(targets)


def build_time_letter_delivery_plan(
    item: Mapping[str, Any],
    *,
    now_iso: str,
) -> TimeLetterDeliveryPlan:
    """Build the hidden V4 target plan without touching legacy dispatch state.

    A not-due letter is deliberately represented as one ``not_due`` admission
    per target and emits no effect.  Relationship/grant checks happen later in
    the target consumer's transaction; a stale subject identifier therefore
    cannot be silently rewritten here.
    """

    snapshot = TimeLetterSealedSnapshot.from_archive_item(item)
    targets = tuple(_target_records(item, snapshot))
    if not targets:
        raise TimeLetterDeliveryContractError("sealed timeLetter requires an owner target")
    if not snapshot.is_due(now_iso):
        admissions = tuple(
            TimeLetterDeliveryAdmission(
                target=target,
                disposition=TimeLetterDeliveryDisposition.NOT_DUE,
                reason_code="timeLetterNotOpen",
            )
            for target in targets
        )
    else:
        admissions = tuple(
            TimeLetterDeliveryAdmission(
                target=target,
                disposition=TimeLetterDeliveryDisposition.DELIVERED,
                reason_code="targetRequiresAtomicConsumer",
            )
            for target in targets
        )
    return TimeLetterDeliveryPlan(snapshot=snapshot, targets=targets, admissions=admissions)


__all__ = [
    "TIME_LETTER_DELIVERY_EVENT_TYPE",
    "TIME_LETTER_DELIVERY_CONSUMER_NAME",
    "TIME_LETTER_DELIVERY_JOB_TYPE",
    "TIME_LETTER_DELIVERY_OPERATION_TYPE",
    "TIME_LETTER_DELIVERY_PURPOSE",
    "TIME_LETTER_DELIVERY_RESOURCE_TYPE",
    "TIME_LETTER_DELIVERY_SCHEMA_VERSION",
    "TimeLetterDeliveryAdmission",
    "TimeLetterDeliveryContractError",
    "TimeLetterDeliveryCompletion",
    "TimeLetterDeliveryDisposition",
    "TimeLetterDeliveryPlan",
    "TimeLetterDeliveryTarget",
    "TimeLetterSealedSnapshot",
    "build_time_letter_delivery_plan",
]
