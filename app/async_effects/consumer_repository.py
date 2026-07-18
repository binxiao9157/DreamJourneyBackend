"""Value-free Consumer Inbox and Business Completion Receipt kernel.

The kernel accepts synthetic foundation commands and narrow typed consumers
whose business writers remain in their owning Unit of Work. It stores only
hashes and opaque coordinates; provider payloads and user content never cross
this boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re
from threading import RLock
from typing import Any, Mapping, Optional
from uuid import UUID, uuid5

from app.async_effects.contracts import AsyncEffectConflict, AsyncEffectIntent
from app.async_effects.target_admission import AsyncEffectTargetAdmission
from app.services.owner_truth_memory_projection_effects import (
    MEMORY_PROJECTION_REBUILD_EVENT_TYPE,
    MEMORY_PROJECTION_REBUILD_JOB_TYPE,
    MEMORY_PROJECTION_REBUILD_OPERATION_TYPE,
)


_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_CONSUMER_NAMESPACE = UUID("e767a5e0-01b2-49ea-a61b-75516b4f2dc8")
_SYNTHETIC_OPERATION_PREFIX = "asyncEffect.synthetic."
_TERMINAL_OUTCOMES = {"completed", "skipped", "blocked", "failed", "unknown"}
_OWNER_TRUTH_SOURCE_BLOCKED_CONSUMER = "ownerTruth.source.blocked"
_OWNER_TRUTH_SOURCE_EXTRACTION_CONSUMER = "ownerTruth.source.extraction"
_OWNER_TRUTH_MEMORY_PROJECTION_CONSUMER = "ownerTruth.memoryProjection.rebuild"


class AsyncEffectConsumerError(RuntimeError):
    """The synthetic consumer command is invalid or cannot be reconciled."""


class AsyncEffectConsumerAdmissionDenied(AsyncEffectConsumerError):
    """A non-synthetic operation attempted to enter the foundation consumer."""


class AsyncEffectConsumerIncomplete(AsyncEffectConsumerError):
    """An inbox record lacks its matching immutable completion receipt."""


def _identifier(value: object, *, field: str, max_length: int = 127) -> str:
    normalized = str(value or "").strip()
    if len(normalized) > max_length or not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise AsyncEffectConsumerError(f"{field} must be an opaque identifier")
    return normalized


def _sha256(value: object, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise AsyncEffectConsumerError(f"{field} must be a lowercase SHA-256 hex digest")
    return normalized


def _receipt_type(consumer_name: str) -> str:
    return f"consumer.{consumer_name}.completion"


def _inbox_state(outcome: str) -> str:
    return "skipped" if outcome == "blocked" else outcome


def _inbox_id(intent: AsyncEffectIntent, consumer_name: str) -> str:
    return str(uuid5(_CONSUMER_NAMESPACE, f"async-effect-inbox:{consumer_name}:{intent.outbox_event_id}"))


def _receipt_id(intent: AsyncEffectIntent, consumer_name: str, business_target_key: str) -> str:
    return str(
        uuid5(
            _CONSUMER_NAMESPACE,
            f"async-effect-consumer-receipt:{consumer_name}:{intent.operation_id}:{business_target_key}",
        )
    )


def _source_extraction_business_target_key(extraction_id: str) -> str:
    return sha256(f"owner-truth-extraction:{extraction_id}".encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AsyncEffectConsumerCompletionCommand:
    """Immutable consumer completion coordinates, without a product payload body."""

    intent: AsyncEffectIntent
    consumer_name: str
    business_target_key: str
    outcome: str
    reason_code: str
    result_ref_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.intent, AsyncEffectIntent):
            raise AsyncEffectConsumerError("intent is required")
        object.__setattr__(self, "consumer_name", _identifier(self.consumer_name, field="consumer_name", max_length=64))
        object.__setattr__(self, "business_target_key", _sha256(self.business_target_key, field="business_target_key"))
        normalized_outcome = str(self.outcome or "").strip()
        if normalized_outcome not in _TERMINAL_OUTCOMES:
            raise AsyncEffectConsumerError("outcome must be an explicit terminal business outcome")
        object.__setattr__(self, "outcome", normalized_outcome)
        object.__setattr__(self, "reason_code", _identifier(self.reason_code, field="reason_code"))
        object.__setattr__(self, "result_ref_hash", _sha256(self.result_ref_hash, field="result_ref_hash"))

    @property
    def receipt_type(self) -> str:
        return _receipt_type(self.consumer_name)

    @property
    def inbox_id(self) -> str:
        return _inbox_id(self.intent, self.consumer_name)

    @property
    def receipt_id(self) -> str:
        return _receipt_id(self.intent, self.consumer_name, self.business_target_key)


@dataclass(frozen=True)
class AsyncEffectSyntheticConsumerCommand(AsyncEffectConsumerCompletionCommand):
    """A value-free completion command used only by the synthetic foundation."""

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.intent.operation_type.startswith(_SYNTHETIC_OPERATION_PREFIX):
            raise AsyncEffectConsumerAdmissionDenied(
                "only asyncEffect.synthetic.* operations are admitted in this foundation"
            )


@dataclass(frozen=True)
class OwnerTruthSourceBlockedConsumerCommand(AsyncEffectConsumerCompletionCommand):
    """Record a terminal block only after a live Source target recheck fails."""

    admission: AsyncEffectTargetAdmission

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.intent.operation_type != "ownerTruth.source.created":
            raise AsyncEffectConsumerAdmissionDenied("owner truth Source completion requires its typed operation")
        if self.consumer_name != _OWNER_TRUTH_SOURCE_BLOCKED_CONSUMER:
            raise AsyncEffectConsumerAdmissionDenied("owner truth Source completion has one fixed consumer")
        if self.business_target_key != self.intent.business_target_key:
            raise AsyncEffectConsumerAdmissionDenied("owner truth Source completion has one fixed business target")
        target = self.intent.target
        if target.resource_type != "source" or target.purpose != "candidateExtraction":
            raise AsyncEffectConsumerAdmissionDenied("owner truth Source completion requires its typed target")
        if not isinstance(self.admission, AsyncEffectTargetAdmission):
            raise AsyncEffectConsumerError("live target admission is required")
        if (
            self.admission.operation_id != self.intent.operation_id
            or self.admission.target_stable_key != self.intent.stable_key
        ):
            raise AsyncEffectConsumerError("target admission does not belong to this immutable effect")
        if self.admission.allowed or self.admission.outcome != "blocked":
            raise AsyncEffectConsumerError("current Source target cannot be recorded as blocked")
        if self.outcome != "blocked" or self.reason_code != self.admission.reason_code:
            raise AsyncEffectConsumerError("blocked completion must preserve the live admission result")


@dataclass(frozen=True)
class OwnerTruthSourceCandidateExtractionConsumerCommand(AsyncEffectConsumerCompletionCommand):
    """Complete one admitted Source extraction without exposing result content."""

    admission: AsyncEffectTargetAdmission
    extraction_id: str
    extraction_status: str

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.intent.operation_type != "ownerTruth.source.created":
            raise AsyncEffectConsumerAdmissionDenied("owner truth extraction requires its typed operation")
        if self.consumer_name != _OWNER_TRUTH_SOURCE_EXTRACTION_CONSUMER:
            raise AsyncEffectConsumerAdmissionDenied("owner truth extraction has one fixed consumer")
        target = self.intent.target
        if target.resource_type != "source" or target.purpose != "candidateExtraction":
            raise AsyncEffectConsumerAdmissionDenied("owner truth extraction requires its typed target")
        try:
            normalized_extraction_id = str(UUID(self.extraction_id))
        except (TypeError, ValueError) as exc:
            raise AsyncEffectConsumerError("extraction_id must be a UUID") from exc
        object.__setattr__(self, "extraction_id", normalized_extraction_id)
        normalized_status = str(self.extraction_status or "").strip()
        if normalized_status not in {"succeeded", "failed", "quarantined"}:
            raise AsyncEffectConsumerError("extraction_status must be terminal")
        object.__setattr__(self, "extraction_status", normalized_status)
        if self.business_target_key != _source_extraction_business_target_key(normalized_extraction_id):
            raise AsyncEffectConsumerAdmissionDenied(
                "owner truth extraction has one fixed business target"
            )
        if not isinstance(self.admission, AsyncEffectTargetAdmission):
            raise AsyncEffectConsumerError("live target admission is required")
        if (
            self.admission.operation_id != self.intent.operation_id
            or self.admission.target_stable_key != self.intent.stable_key
            or not self.admission.allowed
            or self.admission.outcome != "admitted"
        ):
            raise AsyncEffectConsumerError("candidate extraction requires an admitted live Source target")
        if normalized_status == "failed":
            if self.outcome != "failed":
                raise AsyncEffectConsumerError("failed extraction must record a failed completion")
        elif self.outcome != "completed":
            raise AsyncEffectConsumerError("successful or quarantined extraction must complete its consumer")
        if normalized_status == "succeeded" and self.reason_code != "candidateProposalsPersisted":
            raise AsyncEffectConsumerError("successful extraction must retain its fixed completion reason")
        if normalized_status == "quarantined" and self.reason_code != "resultQuarantined":
            raise AsyncEffectConsumerError("quarantined extraction must retain its fixed completion reason")


@dataclass(frozen=True)
class OwnerTruthMemoryProjectionRebuildConsumerCommand(AsyncEffectConsumerCompletionCommand):
    """Complete one admitted or blocked MemoryVersion projection rebuild."""

    admission: AsyncEffectTargetAdmission
    projection_outcome: str | None

    def __post_init__(self) -> None:
        super().__post_init__()
        target = self.intent.target
        if (
            self.intent.operation_type != MEMORY_PROJECTION_REBUILD_OPERATION_TYPE
            or self.intent.event_type != MEMORY_PROJECTION_REBUILD_EVENT_TYPE
            or self.intent.job_type != MEMORY_PROJECTION_REBUILD_JOB_TYPE
        ):
            raise AsyncEffectConsumerAdmissionDenied(
                "memory projection completion requires its typed effect contract"
            )
        if target.resource_type != "memoryVersion" or target.purpose != "compatibilityProjection":
            raise AsyncEffectConsumerAdmissionDenied(
                "memory projection completion requires its typed target"
            )
        if self.consumer_name != _OWNER_TRUTH_MEMORY_PROJECTION_CONSUMER:
            raise AsyncEffectConsumerAdmissionDenied(
                "memory projection completion has one fixed consumer"
            )
        if self.business_target_key != self.intent.business_target_key:
            raise AsyncEffectConsumerAdmissionDenied(
                "memory projection completion has one fixed business target"
            )
        if not isinstance(self.admission, AsyncEffectTargetAdmission):
            raise AsyncEffectConsumerError("live MemoryVersion target admission is required")
        if (
            self.admission.operation_id != self.intent.operation_id
            or self.admission.target_stable_key != self.intent.stable_key
        ):
            raise AsyncEffectConsumerError("MemoryVersion target admission does not belong to this effect")
        normalized_projection_outcome = (
            None if self.projection_outcome is None else str(self.projection_outcome).strip()
        )
        object.__setattr__(self, "projection_outcome", normalized_projection_outcome)
        if self.admission.allowed:
            if self.outcome != "completed":
                raise AsyncEffectConsumerError("admitted projection rebuild must complete")
            if normalized_projection_outcome not in {"rebuilt", "unchanged"}:
                raise AsyncEffectConsumerError("admitted projection rebuild requires a projection outcome")
            expected_reason = (
                "memoryProjectionRebuilt"
                if normalized_projection_outcome == "rebuilt"
                else "memoryProjectionUnchanged"
            )
            if self.reason_code != expected_reason:
                raise AsyncEffectConsumerError("projection completion must preserve its fixed reason")
            return
        if self.outcome != "blocked":
            raise AsyncEffectConsumerError("blocked MemoryVersion target must write a blocked completion")
        if self.reason_code != self.admission.reason_code:
            raise AsyncEffectConsumerError("blocked projection completion must preserve live admission")
        if normalized_projection_outcome is not None:
            raise AsyncEffectConsumerError("blocked projection completion cannot claim a rebuild outcome")


@dataclass(frozen=True)
class AsyncEffectConsumerReceipt:
    outcome: str
    inbox_id: str
    business_receipt_id: str
    operation_id: str
    consumer_name: str
    business_target_key: str
    business_outcome: str
    inbox_state: str


class InMemoryAsyncEffectConsumerRepository:
    """Thread-safe semantic double for the synthetic consumer contract."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._inbox: dict[tuple[str, str], dict[str, str]] = {}
        self._receipts: dict[tuple[str, str, str], dict[str, str]] = {}

    def consume(self, command: AsyncEffectConsumerCompletionCommand) -> AsyncEffectConsumerReceipt:
        if not isinstance(command, AsyncEffectConsumerCompletionCommand):
            raise TypeError("consumer completion command is required")
        inbox_key = (command.consumer_name, command.intent.outbox_event_id)
        receipt_key = (command.intent.operation_id, command.receipt_type, command.business_target_key)
        with self._lock:
            existing_inbox = self._inbox.get(inbox_key)
            if existing_inbox is not None:
                self._assert_inbox_matches(existing_inbox, command)
                existing_receipt = self._receipts.get(receipt_key)
                if existing_receipt is None:
                    if self._has_other_target(command):
                        raise AsyncEffectConflict("consumer event cannot complete a different business target")
                    raise AsyncEffectConsumerIncomplete("consumer inbox has no matching completion receipt")
                self._assert_receipt_matches(existing_receipt, command)
                return self._summary(existing_inbox, existing_receipt, outcome="deduplicated")

            inbox = {
                "inboxId": command.inbox_id,
                "operationId": command.intent.operation_id,
                "eventId": command.intent.outbox_event_id,
                "payloadHash": command.intent.payload_hash,
                "consumerName": command.consumer_name,
                "state": "processing",
            }
            receipt = {
                "receiptId": command.receipt_id,
                "operationId": command.intent.operation_id,
                "receiptType": command.receipt_type,
                "businessTargetKey": command.business_target_key,
                "payloadHash": command.intent.payload_hash,
                "state": command.outcome,
                "outcome": command.outcome,
                "reasonCode": command.reason_code,
                "resultRefHash": command.result_ref_hash,
            }
            self._inbox[inbox_key] = inbox
            self._receipts[receipt_key] = receipt
            inbox["state"] = _inbox_state(command.outcome)
            return self._summary(inbox, receipt, outcome="accepted")

    def _has_other_target(self, command: AsyncEffectConsumerCompletionCommand) -> bool:
        return any(
            operation_id == command.intent.operation_id and receipt_type == command.receipt_type
            for operation_id, receipt_type, _target_key in self._receipts
        )

    @staticmethod
    def _assert_inbox_matches(
        inbox: Mapping[str, str],
        command: AsyncEffectConsumerCompletionCommand,
    ) -> None:
        if (
            inbox["operationId"] != command.intent.operation_id
            or inbox["eventId"] != command.intent.outbox_event_id
            or inbox["payloadHash"] != command.intent.payload_hash
            or inbox["consumerName"] != command.consumer_name
        ):
            raise AsyncEffectConflict("consumer event cannot be reused with new immutable meaning")

    @staticmethod
    def _assert_receipt_matches(
        receipt: Mapping[str, str],
        command: AsyncEffectConsumerCompletionCommand,
    ) -> None:
        expected = {
            "operationId": command.intent.operation_id,
            "receiptType": command.receipt_type,
            "businessTargetKey": command.business_target_key,
            "payloadHash": command.intent.payload_hash,
            "state": command.outcome,
            "outcome": command.outcome,
            "reasonCode": command.reason_code,
            "resultRefHash": command.result_ref_hash,
        }
        if any(receipt[key] != value for key, value in expected.items()):
            raise AsyncEffectConflict("consumer completion receipt cannot change immutable meaning")

    @staticmethod
    def _summary(
        inbox: Mapping[str, str],
        receipt: Mapping[str, str],
        *,
        outcome: str,
    ) -> AsyncEffectConsumerReceipt:
        return AsyncEffectConsumerReceipt(
            outcome=outcome,
            inbox_id=inbox["inboxId"],
            business_receipt_id=receipt["receiptId"],
            operation_id=inbox["operationId"],
            consumer_name=inbox["consumerName"],
            business_target_key=receipt["businessTargetKey"],
            business_outcome=receipt["outcome"],
            inbox_state=inbox["state"],
        )


class PostgresAsyncEffectConsumerRepository:
    """Synthetic Consumer Inbox writer bound to an active Postgres Unit of Work."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def consume(self, command: AsyncEffectConsumerCompletionCommand) -> AsyncEffectConsumerReceipt:
        if not isinstance(command, AsyncEffectConsumerCompletionCommand):
            raise TypeError("consumer completion command is required")
        intent = command.intent
        target = intent.target
        with self._cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO async_effects.consumer_inbox (
                    inbox_id, event_id, operation_id, owner_subject_id, vault_id,
                    resource_type, resource_id, resource_version, purpose,
                    authority_epoch, stable_key, consumer_name, payload_hash, state, attempt
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'processing', 1)
                -- inbox_id is deterministic for the same consumer event. A concurrent
                -- replay can therefore conflict on either the primary key or the
                -- consumer/event uniqueness constraint; both must enter replay.
                ON CONFLICT DO NOTHING
                RETURNING inbox_id, operation_id, event_id, consumer_name, payload_hash, state
                """,
                (
                    command.inbox_id,
                    intent.outbox_event_id,
                    intent.operation_id,
                    target.owner_subject_id,
                    target.vault_id,
                    target.resource_type,
                    target.resource_id,
                    target.resource_version,
                    target.purpose,
                    target.authority_epoch,
                    intent.stable_key,
                    command.consumer_name,
                    intent.payload_hash,
                ),
            )
            inbox = cursor.fetchone()
            if inbox is None:
                return self._replay_existing(cursor, command)

            cursor.execute(
                """
                INSERT INTO async_effects.business_receipts (
                    receipt_id, operation_id, owner_subject_id, vault_id, resource_type,
                    resource_id, resource_version, purpose, authority_epoch, stable_key,
                    receipt_type, business_target_key, payload_hash, state, outcome,
                    reason_code, result_ref_hash, attempt
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1)
                -- receipt_id is deterministic too, so accept either equivalent
                -- immutable uniqueness conflict and reconcile through the inbox.
                ON CONFLICT DO NOTHING
                RETURNING receipt_id, operation_id, receipt_type, business_target_key,
                    payload_hash, state, outcome, reason_code, result_ref_hash
                """,
                (
                    command.receipt_id,
                    intent.operation_id,
                    target.owner_subject_id,
                    target.vault_id,
                    target.resource_type,
                    target.resource_id,
                    target.resource_version,
                    target.purpose,
                    target.authority_epoch,
                    intent.stable_key,
                    command.receipt_type,
                    command.business_target_key,
                    intent.payload_hash,
                    command.outcome,
                    command.outcome,
                    command.reason_code,
                    command.result_ref_hash,
                ),
            )
            receipt = cursor.fetchone()
            if receipt is None:
                raise AsyncEffectConsumerIncomplete(
                    "fresh consumer inbox did not produce an immutable completion receipt"
                )
            cursor.execute(
                """
                UPDATE async_effects.consumer_inbox
                SET state = %s, completed_at = NOW(), updated_at = NOW()
                WHERE inbox_id = %s AND state = 'processing'
                RETURNING inbox_id, operation_id, event_id, consumer_name, payload_hash, state
                """,
                (_inbox_state(command.outcome), command.inbox_id),
            )
            completed_inbox = cursor.fetchone()
            if completed_inbox is None:
                raise AsyncEffectConsumerIncomplete("consumer inbox failed to transition to a terminal state")
            return self._summary(completed_inbox, receipt, outcome="accepted")

    def _replay_existing(self, cursor: Any, command: AsyncEffectConsumerCompletionCommand) -> AsyncEffectConsumerReceipt:
        cursor.execute(
            """
            SELECT inbox_id, operation_id, event_id, consumer_name, payload_hash, state
            FROM async_effects.consumer_inbox
            WHERE consumer_name = %s AND event_id = %s
            FOR UPDATE
            """,
            (command.consumer_name, command.intent.outbox_event_id),
        )
        inbox = cursor.fetchone()
        if inbox is None:
            raise RuntimeError("consumer inbox conflict did not produce a row")
        expected_inbox = {
            "operation_id": command.intent.operation_id,
            "event_id": command.intent.outbox_event_id,
            "consumer_name": command.consumer_name,
            "payload_hash": command.intent.payload_hash,
        }
        if any(str(inbox[key]) != str(value) for key, value in expected_inbox.items()):
            raise AsyncEffectConflict("consumer event cannot be reused with new immutable meaning")
        cursor.execute(
            """
            SELECT receipt_id, operation_id, receipt_type, business_target_key,
                payload_hash, state, outcome, reason_code, result_ref_hash
            FROM async_effects.business_receipts
            WHERE operation_id = %s AND receipt_type = %s AND business_target_key = %s
            """,
            (command.intent.operation_id, command.receipt_type, command.business_target_key),
        )
        receipt = cursor.fetchone()
        if receipt is None:
            cursor.execute(
                """
                SELECT business_target_key
                FROM async_effects.business_receipts
                WHERE operation_id = %s AND receipt_type = %s
                LIMIT 1
                """,
                (command.intent.operation_id, command.receipt_type),
            )
            if cursor.fetchone() is not None:
                raise AsyncEffectConflict("consumer event cannot complete a different business target")
            raise AsyncEffectConsumerIncomplete("consumer inbox has no matching completion receipt")
        self._assert_receipt_matches(receipt, command)
        if str(inbox["state"]) == "processing":
            raise AsyncEffectConsumerIncomplete("consumer inbox is not terminal")
        return self._summary(inbox, receipt, outcome="deduplicated")

    @staticmethod
    def _assert_receipt_matches(
        receipt: Mapping[str, Any],
        command: AsyncEffectConsumerCompletionCommand,
    ) -> None:
        expected = {
            "operation_id": command.intent.operation_id,
            "receipt_type": command.receipt_type,
            "business_target_key": command.business_target_key,
            "payload_hash": command.intent.payload_hash,
            "state": command.outcome,
            "outcome": command.outcome,
            "reason_code": command.reason_code,
            "result_ref_hash": command.result_ref_hash,
        }
        if any(str(receipt[key]) != str(value) for key, value in expected.items()):
            raise AsyncEffectConflict("consumer completion receipt cannot change immutable meaning")

    @staticmethod
    def _summary(
        inbox: Mapping[str, Any],
        receipt: Mapping[str, Any],
        *,
        outcome: str,
    ) -> AsyncEffectConsumerReceipt:
        return AsyncEffectConsumerReceipt(
            outcome=outcome,
            inbox_id=str(inbox["inbox_id"]),
            business_receipt_id=str(receipt["receipt_id"]),
            operation_id=str(inbox["operation_id"]),
            consumer_name=str(inbox["consumer_name"]),
            business_target_key=str(receipt["business_target_key"]),
            business_outcome=str(receipt["outcome"]),
            inbox_state=str(inbox["state"]),
        )

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)


__all__ = [
    "AsyncEffectConsumerAdmissionDenied",
    "AsyncEffectConsumerCompletionCommand",
    "AsyncEffectConsumerError",
    "AsyncEffectConsumerIncomplete",
    "AsyncEffectConsumerReceipt",
    "AsyncEffectSyntheticConsumerCommand",
    "InMemoryAsyncEffectConsumerRepository",
    "OwnerTruthMemoryProjectionRebuildConsumerCommand",
    "OwnerTruthSourceBlockedConsumerCommand",
    "PostgresAsyncEffectConsumerRepository",
]
