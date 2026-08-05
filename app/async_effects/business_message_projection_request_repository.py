"""Durable, value-free inputs for the message-projection worker.

The async-effect kernel stores a payload hash by design.  A worker therefore
needs a separate immutable input record to rebuild a completed business
receipt and explicit inbox coordinates without putting message content into a
job payload.  This repository owns that narrow internal mapping only.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any, Mapping

from app.async_effects.business_message_projection_effects import (
    BUSINESS_MESSAGE_PROJECTION_EFFECT_SCHEMA_VERSION,
    BusinessMessageProjectionEffectError,
    BusinessMessageProjectionRequest,
    is_business_message_projection_intent,
)
from app.async_effects.business_message_projection_repository import InboxAccountSnapshot
from app.async_effects.consumer_repository import AsyncEffectConsumerReceipt
from app.async_effects.contracts import AsyncEffectConflict, AsyncEffectIntent, AsyncEffectTarget
from app.async_effects.message_notification_effects import (
    BusinessCompletionMessageSource,
    InAppMessageKind,
)


class BusinessMessageProjectionRequestPersistenceError(BusinessMessageProjectionEffectError):
    """A worker input is missing, mutable, or does not match its receipt."""


class BusinessMessageProjectionRequestConflict(AsyncEffectConflict):
    """One job identifier was reused with a different immutable request."""


@dataclass(frozen=True)
class BusinessMessageProjectionRequestPersistenceSummary:
    outcome: str
    request: BusinessMessageProjectionRequest

    def __post_init__(self) -> None:
        if self.outcome not in {"recorded", "deduplicated"}:
            raise BusinessMessageProjectionRequestPersistenceError(
                "message projection request persistence outcome is invalid"
            )
        if not isinstance(self.request, BusinessMessageProjectionRequest):
            raise BusinessMessageProjectionRequestPersistenceError(
                "message projection request is required"
            )

    def value_free_summary(self) -> Mapping[str, object]:
        return {**self.request.value_free_summary(), "outcome": self.outcome}


class InMemoryBusinessMessageProjectionRequestRepository:
    """Thread-safe double for immutable message-projection worker input."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._requests: dict[str, BusinessMessageProjectionRequest] = {}

    def record(
        self,
        request: BusinessMessageProjectionRequest,
    ) -> BusinessMessageProjectionRequestPersistenceSummary:
        if not isinstance(request, BusinessMessageProjectionRequest):
            raise TypeError("message projection request is required")
        intent = request.effect_intent
        with self._lock:
            existing = self._requests.get(intent.job_id)
            if existing is None:
                self._requests[intent.job_id] = request
                return BusinessMessageProjectionRequestPersistenceSummary(
                    outcome="recorded",
                    request=request,
                )
            if existing.request_hash != request.request_hash:
                raise BusinessMessageProjectionRequestConflict(
                    "message projection job is already bound to different immutable input"
                )
            return BusinessMessageProjectionRequestPersistenceSummary(
                outcome="deduplicated",
                request=existing,
            )

    def load_for_intent(self, intent: AsyncEffectIntent) -> BusinessMessageProjectionRequest | None:
        if not is_business_message_projection_intent(intent):
            raise BusinessMessageProjectionRequestPersistenceError(
                "message projection worker requires its typed effect intent"
            )
        with self._lock:
            request = self._requests.get(intent.job_id)
        if request is None:
            return None
        if request.effect_intent != intent:
            raise BusinessMessageProjectionRequestConflict(
                "message projection job input no longer matches its immutable intent"
            )
        return request

    def request_count(self) -> int:
        with self._lock:
            return len(self._requests)


class PostgresBusinessMessageProjectionRequestRepository:
    """Rebuild typed worker input from append-only metadata in one UoW."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def record(
        self,
        request: BusinessMessageProjectionRequest,
    ) -> BusinessMessageProjectionRequestPersistenceSummary:
        if not isinstance(request, BusinessMessageProjectionRequest):
            raise TypeError("message projection request is required")
        intent = request.effect_intent
        with self._cursor() as cursor:
            self._assert_source_is_durable(cursor, request)
            cursor.execute(
                """
                INSERT INTO async_effects.business_message_projection_requests (
                    job_id, operation_id, source_operation_id,
                    source_business_receipt_id, source_consumer_inbox_id,
                    source_consumer_name, source_business_target_key,
                    message_id, message_kind, inbox_subject_id, inbox_vault_id,
                    inbox_account_epoch, request_hash, schema_version
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (job_id) DO NOTHING
                RETURNING job_id
                """,
                (
                    intent.job_id,
                    intent.operation_id,
                    request.source.intent.operation_id,
                    request.source.completion.business_receipt_id,
                    request.source.completion.inbox_id,
                    request.source.completion.consumer_name,
                    request.source.completion.business_target_key,
                    request.message_id,
                    request.source.message_kind.value,
                    request.inbox_account.inbox_subject_id,
                    request.inbox_account.inbox_vault_id,
                    request.inbox_account.account_epoch,
                    request.request_hash,
                    BUSINESS_MESSAGE_PROJECTION_EFFECT_SCHEMA_VERSION,
                ),
            )
            created = cursor.fetchone()
            if created is not None:
                return BusinessMessageProjectionRequestPersistenceSummary(
                    outcome="recorded",
                    request=request,
                )
            existing = self._load_for_intent(cursor, intent)
            if existing is None:
                raise BusinessMessageProjectionRequestPersistenceError(
                    "message projection request conflict did not retain a row"
                )
            if existing.request_hash != request.request_hash:
                raise BusinessMessageProjectionRequestConflict(
                    "message projection job is already bound to different immutable input"
                )
            return BusinessMessageProjectionRequestPersistenceSummary(
                outcome="deduplicated",
                request=existing,
            )

    def load_for_intent(self, intent: AsyncEffectIntent) -> BusinessMessageProjectionRequest | None:
        if not is_business_message_projection_intent(intent):
            raise BusinessMessageProjectionRequestPersistenceError(
                "message projection worker requires its typed effect intent"
            )
        with self._cursor() as cursor:
            return self._load_for_intent(cursor, intent)

    def _assert_source_is_durable(
        self,
        cursor: Any,
        request: BusinessMessageProjectionRequest,
    ) -> None:
        source = request.source
        target = source.intent.target
        cursor.execute(
            """
            SELECT receipt.operation_id, receipt.owner_subject_id, receipt.vault_id,
                   receipt.resource_type, receipt.resource_id, receipt.resource_version,
                   receipt.purpose, receipt.authority_epoch, receipt.stable_key,
                   receipt.payload_hash, receipt.receipt_type, receipt.business_target_key,
                   receipt.state AS receipt_state, receipt.outcome AS receipt_outcome,
                   inbox.inbox_id, inbox.operation_id AS inbox_operation_id,
                   inbox.owner_subject_id AS inbox_owner_subject_id,
                   inbox.vault_id AS inbox_vault_id,
                   inbox.resource_type AS inbox_resource_type,
                   inbox.resource_id AS inbox_resource_id,
                   inbox.resource_version AS inbox_resource_version,
                   inbox.purpose AS inbox_purpose,
                   inbox.authority_epoch AS inbox_authority_epoch,
                   inbox.stable_key AS inbox_stable_key,
                   inbox.payload_hash AS inbox_payload_hash,
                   inbox.consumer_name, inbox.state AS inbox_state, inbox.event_id
            FROM async_effects.business_receipts AS receipt
            JOIN async_effects.consumer_inbox AS inbox
              ON inbox.inbox_id = %s
            WHERE receipt.receipt_id = %s
            FOR SHARE OF receipt, inbox
            """,
            (source.completion.inbox_id, source.completion.business_receipt_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise BusinessMessageProjectionRequestPersistenceError(
                "message projection source receipt or consumer inbox is missing"
            )
        expected = {
            "operation_id": source.intent.operation_id,
            "owner_subject_id": target.owner_subject_id,
            "vault_id": target.vault_id,
            "resource_type": target.resource_type,
            "resource_id": target.resource_id,
            "resource_version": int(target.resource_version),
            "purpose": target.purpose,
            "authority_epoch": int(target.authority_epoch),
            "stable_key": source.intent.stable_key,
            "payload_hash": source.intent.payload_hash,
            "receipt_type": f"consumer.{source.completion.consumer_name}.completion",
            "business_target_key": source.completion.business_target_key,
            "receipt_state": "completed",
            "receipt_outcome": "completed",
            "inbox_id": source.completion.inbox_id,
            "inbox_operation_id": source.intent.operation_id,
            "inbox_owner_subject_id": target.owner_subject_id,
            "inbox_vault_id": target.vault_id,
            "inbox_resource_type": target.resource_type,
            "inbox_resource_id": target.resource_id,
            "inbox_resource_version": int(target.resource_version),
            "inbox_purpose": target.purpose,
            "inbox_authority_epoch": int(target.authority_epoch),
            "inbox_stable_key": source.intent.stable_key,
            "inbox_payload_hash": source.intent.payload_hash,
            "consumer_name": source.completion.consumer_name,
            "inbox_state": "completed",
            "event_id": source.intent.outbox_event_id,
        }
        actual = {
            key: int(row[key])
            if key in {
                "resource_version",
                "authority_epoch",
                "inbox_resource_version",
                "inbox_authority_epoch",
            }
            else str(row[key])
            for key in expected
        }
        if actual != expected:
            raise BusinessMessageProjectionRequestPersistenceError(
                "message projection source no longer matches its completed business receipt"
            )

    def _load_for_intent(
        self,
        cursor: Any,
        intent: AsyncEffectIntent,
    ) -> BusinessMessageProjectionRequest | None:
        cursor.execute(
            """
            SELECT request.job_id, request.operation_id, request.source_operation_id,
                   request.source_business_receipt_id, request.source_consumer_inbox_id,
                   request.source_consumer_name, request.source_business_target_key,
                   request.message_id, request.message_kind, request.inbox_subject_id,
                   request.inbox_vault_id, request.inbox_account_epoch,
                   request.request_hash, request.schema_version,
                   job.max_attempts,
                   source_operation.operation_type AS source_operation_type,
                   source_operation.owner_subject_id AS source_owner_subject_id,
                   source_operation.vault_id AS source_vault_id,
                   source_operation.resource_type AS source_resource_type,
                   source_operation.resource_id AS source_resource_id,
                   source_operation.resource_version AS source_resource_version,
                   source_operation.purpose AS source_purpose,
                   source_operation.authority_epoch AS source_authority_epoch,
                   source_operation.stable_key AS source_stable_key,
                   source_operation.payload_hash AS source_payload_hash,
                   source_receipt.operation_id AS receipt_operation_id,
                   source_receipt.owner_subject_id AS receipt_owner_subject_id,
                   source_receipt.vault_id AS receipt_vault_id,
                   source_receipt.resource_type AS receipt_resource_type,
                   source_receipt.resource_id AS receipt_resource_id,
                   source_receipt.resource_version AS receipt_resource_version,
                   source_receipt.purpose AS receipt_purpose,
                   source_receipt.authority_epoch AS receipt_authority_epoch,
                   source_receipt.stable_key AS receipt_stable_key,
                   source_receipt.payload_hash AS receipt_payload_hash,
                   source_receipt.receipt_type AS receipt_type,
                   source_receipt.business_target_key AS receipt_business_target_key,
                   source_receipt.state AS receipt_state,
                   source_receipt.outcome AS receipt_outcome,
                   source_inbox.operation_id AS inbox_operation_id,
                   source_inbox.owner_subject_id AS inbox_owner_subject_id,
                   source_inbox.vault_id AS inbox_vault_id,
                   source_inbox.resource_type AS inbox_resource_type,
                   source_inbox.resource_id AS inbox_resource_id,
                   source_inbox.resource_version AS inbox_resource_version,
                   source_inbox.purpose AS inbox_purpose,
                   source_inbox.authority_epoch AS inbox_authority_epoch,
                   source_inbox.stable_key AS inbox_stable_key,
                   source_inbox.payload_hash AS inbox_payload_hash,
                   source_inbox.consumer_name AS inbox_consumer_name,
                   source_inbox.state AS inbox_state,
                   source_inbox.event_id AS inbox_event_id
            FROM async_effects.business_message_projection_requests AS request
            JOIN async_effects.jobs AS job ON job.job_id = request.job_id
            JOIN async_effects.operations AS source_operation
              ON source_operation.operation_id = request.source_operation_id
            JOIN async_effects.business_receipts AS source_receipt
              ON source_receipt.receipt_id = request.source_business_receipt_id
            JOIN async_effects.consumer_inbox AS source_inbox
              ON source_inbox.inbox_id = request.source_consumer_inbox_id
            WHERE request.job_id = %s AND request.operation_id = %s
            FOR UPDATE OF request, job, source_operation, source_receipt, source_inbox
            """,
            (intent.job_id, intent.operation_id),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        if str(row["schema_version"]) != BUSINESS_MESSAGE_PROJECTION_EFFECT_SCHEMA_VERSION:
            raise BusinessMessageProjectionRequestPersistenceError(
                "message projection request schema version is unsupported"
            )
        source_intent = AsyncEffectIntent(
            operation_type=str(row["source_operation_type"]),
            target=AsyncEffectTarget(
                owner_subject_id=str(row["source_owner_subject_id"]),
                vault_id=str(row["source_vault_id"]),
                resource_type=str(row["source_resource_type"]),
                resource_id=str(row["source_resource_id"]),
                resource_version=int(row["source_resource_version"]),
                purpose=str(row["source_purpose"]),
                authority_epoch=int(row["source_authority_epoch"]),
            ),
            payload_hash=str(row["source_payload_hash"]),
        )
        if str(row["source_operation_id"]) != source_intent.operation_id:
            raise BusinessMessageProjectionRequestPersistenceError(
                "message projection source operation does not match immutable coordinates"
            )
        if str(row["source_stable_key"]) != source_intent.stable_key:
            raise BusinessMessageProjectionRequestPersistenceError(
                "message projection source operation stable key does not match immutable coordinates"
            )
        if (
            str(row["receipt_operation_id"]) != source_intent.operation_id
            or str(row["receipt_owner_subject_id"])
            != source_intent.target.owner_subject_id
            or str(row["receipt_vault_id"]) != source_intent.target.vault_id
            or str(row["receipt_resource_type"]) != source_intent.target.resource_type
            or str(row["receipt_resource_id"]) != source_intent.target.resource_id
            or int(row["receipt_resource_version"])
            != int(source_intent.target.resource_version)
            or str(row["receipt_purpose"]) != source_intent.target.purpose
            or int(row["receipt_authority_epoch"])
            != int(source_intent.target.authority_epoch)
            or str(row["receipt_stable_key"]) != source_intent.stable_key
            or str(row["receipt_payload_hash"]) != source_intent.payload_hash
            or str(row["receipt_type"])
            != f"consumer.{str(row['source_consumer_name'])}.completion"
            or str(row["receipt_business_target_key"]) != str(row["source_business_target_key"])
            or str(row["receipt_state"]) != "completed"
            or str(row["receipt_outcome"]) != "completed"
            or str(row["inbox_operation_id"]) != source_intent.operation_id
            or str(row["inbox_owner_subject_id"])
            != source_intent.target.owner_subject_id
            or str(row["inbox_vault_id"]) != source_intent.target.vault_id
            or str(row["inbox_resource_type"]) != source_intent.target.resource_type
            or str(row["inbox_resource_id"]) != source_intent.target.resource_id
            or int(row["inbox_resource_version"])
            != int(source_intent.target.resource_version)
            or str(row["inbox_purpose"]) != source_intent.target.purpose
            or int(row["inbox_authority_epoch"])
            != int(source_intent.target.authority_epoch)
            or str(row["inbox_stable_key"]) != source_intent.stable_key
            or str(row["inbox_payload_hash"]) != source_intent.payload_hash
            or str(row["inbox_consumer_name"]) != str(row["source_consumer_name"])
            or str(row["inbox_state"]) != "completed"
            or str(row["inbox_event_id"]) != source_intent.outbox_event_id
        ):
            raise BusinessMessageProjectionRequestPersistenceError(
                "message projection request source receipt is no longer eligible"
            )
        source = BusinessCompletionMessageSource(
            intent=source_intent,
            completion=AsyncEffectConsumerReceipt(
                outcome="accepted",
                inbox_id=str(row["source_consumer_inbox_id"]),
                business_receipt_id=str(row["source_business_receipt_id"]),
                operation_id=source_intent.operation_id,
                consumer_name=str(row["source_consumer_name"]),
                business_target_key=str(row["source_business_target_key"]),
                business_outcome="completed",
                inbox_state="completed",
            ),
            message_kind=InAppMessageKind(str(row["message_kind"])),
            inbox_subject_id=str(row["inbox_subject_id"]),
            inbox_vault_id=str(row["inbox_vault_id"]),
        )
        request = BusinessMessageProjectionRequest(
            source=source,
            inbox_account=InboxAccountSnapshot(
                inbox_subject_id=str(row["inbox_subject_id"]),
                inbox_vault_id=str(row["inbox_vault_id"]),
                account_epoch=int(row["inbox_account_epoch"]),
            ),
            max_attempts=int(row["max_attempts"]),
        )
        if request.effect_intent != intent or request.request_hash != str(row["request_hash"]):
            raise BusinessMessageProjectionRequestConflict(
                "message projection request does not match immutable job input"
            )
        return request

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)


__all__ = [
    "BusinessMessageProjectionRequestConflict",
    "BusinessMessageProjectionRequestPersistenceError",
    "BusinessMessageProjectionRequestPersistenceSummary",
    "InMemoryBusinessMessageProjectionRequestRepository",
    "PostgresBusinessMessageProjectionRequestRepository",
]
