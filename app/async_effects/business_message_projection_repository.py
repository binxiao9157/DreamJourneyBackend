"""Durable, metadata-only shadow persistence for business message projections.

This repository deliberately sits beside, rather than behind, the legacy
``mailbox_letters`` read model.  It does not create a second public inbox,
change the existing mailbox writer, dispatch a notification, or authorize a
family relationship.  A future business writer must first resolve a live
inbox account and delegated authority, then may opt into this shadow writer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
import re
from threading import RLock
from typing import Any, Mapping
from uuid import UUID

from app.async_effects.contracts import AsyncEffectConflict
from app.async_effects.message_notification_effects import (
    BusinessCompletionMessageSource,
    BusinessMessageNotificationContractError,
    InAppMessageKind,
    InAppMessageProjection,
    InAppMessageState,
)


BUSINESS_MESSAGE_PROJECTION_SCHEMA_VERSION = "business-message-projection-v1"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class BusinessMessageProjectionPersistenceError(BusinessMessageNotificationContractError):
    """A message shadow record crosses a persistence or identity boundary."""


class BusinessMessageProjectionConflict(AsyncEffectConflict):
    """An immutable message identity was reused with different metadata."""


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise BusinessMessageProjectionPersistenceError(
            f"{field} must be an opaque identifier"
        )
    return normalized


def _non_negative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BusinessMessageProjectionPersistenceError(
            f"{field} must be a non-negative integer"
        )
    return value


def _uuid(value: object, *, field: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError) as exc:
        raise BusinessMessageProjectionPersistenceError(f"{field} must be a UUID") from exc


def _sha256_hex(value: object, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise BusinessMessageProjectionPersistenceError(
            f"{field} must be a lowercase SHA-256 hex digest"
        )
    return normalized


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class InboxAccountSnapshot:
    """An explicit, active inbox binding supplied by an account authority.

    The V4 shadow does not infer a vault from a subject identifier and does
    not treat a family relationship as authorization.  The caller therefore
    must pass both inbox coordinates and the currently observed account epoch.
    This object is deliberately not a grant or a public authorization result.
    """

    inbox_subject_id: str
    inbox_vault_id: str
    account_epoch: int
    access_state: str = "active"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "inbox_subject_id",
            _identifier(self.inbox_subject_id, field="inbox_subject_id"),
        )
        object.__setattr__(
            self,
            "inbox_vault_id",
            _identifier(self.inbox_vault_id, field="inbox_vault_id"),
        )
        object.__setattr__(
            self,
            "account_epoch",
            _non_negative_int(self.account_epoch, field="account_epoch"),
        )
        if self.access_state != "active":
            raise BusinessMessageProjectionPersistenceError(
                "only an active inbox account may receive a message projection"
            )

    def value_free_summary(self) -> Mapping[str, object]:
        return {
            "accountEpoch": self.account_epoch,
            "accessState": self.access_state,
            "inboxSubjectDigest": _digest(self.inbox_subject_id),
            "inboxVaultDigest": _digest(self.inbox_vault_id),
        }


@dataclass(frozen=True)
class BusinessMessageProjectionRecord:
    """One append-only metadata record, separate from the public mailbox."""

    message: InAppMessageProjection
    operation_id: str
    inbox_account_epoch: int
    projection_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.message, InAppMessageProjection):
            raise BusinessMessageProjectionPersistenceError("message projection is required")
        if self.message.state is not InAppMessageState.UNREAD:
            raise BusinessMessageProjectionPersistenceError(
                "initial durable message projection state must remain unread"
            )
        object.__setattr__(
            self,
            "operation_id",
            _uuid(self.operation_id, field="operation_id"),
        )
        object.__setattr__(
            self,
            "inbox_account_epoch",
            _non_negative_int(self.inbox_account_epoch, field="inbox_account_epoch"),
        )
        object.__setattr__(self, "projection_hash", self._build_projection_hash())

    def _build_projection_hash(self) -> str:
        message = self.message
        return sha256(
            _canonical_json(
                {
                    "authorityEpoch": message.authority_epoch,
                    "businessReceiptId": message.business_receipt_id,
                    "businessTargetKey": message.business_target_key,
                    "inboxAccountEpoch": self.inbox_account_epoch,
                    "inboxSubjectId": message.inbox_subject_id,
                    "inboxVaultId": message.inbox_vault_id,
                    "kind": message.kind.value,
                    "messageId": message.message_id,
                    "operationId": self.operation_id,
                    "resourceId": message.resource_id,
                    "resourceOwnerSubjectId": message.resource_owner_subject_id,
                    "resourceType": message.resource_type,
                    "resourceVaultId": message.resource_vault_id,
                    "resourceVersion": message.resource_version,
                    "schemaVersion": BUSINESS_MESSAGE_PROJECTION_SCHEMA_VERSION,
                    "state": message.state.value,
                }
            ).encode("utf-8")
        ).hexdigest()

    def value_free_summary(self) -> Mapping[str, object]:
        return {
            "inboxAccountEpoch": self.inbox_account_epoch,
            "message": self.message.value_free_summary(),
            "operationIdHash": _digest(self.operation_id),
            "projectionHash": self.projection_hash,
            "schemaVersion": BUSINESS_MESSAGE_PROJECTION_SCHEMA_VERSION,
        }


@dataclass(frozen=True)
class BusinessMessageProjectionPersistenceSummary:
    outcome: str
    record: BusinessMessageProjectionRecord

    def __post_init__(self) -> None:
        if self.outcome not in {"recorded", "deduplicated"}:
            raise BusinessMessageProjectionPersistenceError(
                "message projection persistence outcome is invalid"
            )
        if not isinstance(self.record, BusinessMessageProjectionRecord):
            raise BusinessMessageProjectionPersistenceError("message projection record is required")

    def value_free_summary(self) -> dict[str, object]:
        return {**self.record.value_free_summary(), "outcome": self.outcome}


def _record_from_source(
    source: BusinessCompletionMessageSource,
    inbox_account: InboxAccountSnapshot,
) -> BusinessMessageProjectionRecord:
    if not isinstance(source, BusinessCompletionMessageSource):
        raise BusinessMessageProjectionPersistenceError("business completion source is required")
    if not isinstance(inbox_account, InboxAccountSnapshot):
        raise BusinessMessageProjectionPersistenceError("active inbox account snapshot is required")
    message = source.projection()
    if (
        message.inbox_subject_id != inbox_account.inbox_subject_id
        or message.inbox_vault_id != inbox_account.inbox_vault_id
    ):
        raise BusinessMessageProjectionPersistenceError(
            "inbox account snapshot must match the projected inbox coordinates"
        )
    return BusinessMessageProjectionRecord(
        message=message,
        operation_id=source.intent.operation_id,
        inbox_account_epoch=inbox_account.account_epoch,
    )


def _assert_same(
    existing: BusinessMessageProjectionRecord,
    candidate: BusinessMessageProjectionRecord,
) -> None:
    if existing != candidate:
        raise BusinessMessageProjectionConflict(
            "business message identity is already bound to different immutable metadata"
        )


class InMemoryBusinessMessageProjectionRepository:
    """Thread-safe double for the append-only, internal message projection."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._records: dict[str, BusinessMessageProjectionRecord] = {}

    def record(
        self,
        source: BusinessCompletionMessageSource,
        inbox_account: InboxAccountSnapshot,
    ) -> BusinessMessageProjectionPersistenceSummary:
        candidate = _record_from_source(source, inbox_account)
        with self._lock:
            existing = self._records.get(candidate.message.message_id)
            if existing is None:
                self._records[candidate.message.message_id] = candidate
                return BusinessMessageProjectionPersistenceSummary("recorded", candidate)
            _assert_same(existing, candidate)
            return BusinessMessageProjectionPersistenceSummary("deduplicated", existing)

    def load(self, message_id: str) -> BusinessMessageProjectionRecord:
        normalized_id = _uuid(message_id, field="message_id")
        with self._lock:
            record = self._records.get(normalized_id)
        if record is None:
            raise BusinessMessageProjectionPersistenceError(
                "business message projection is not durably recorded"
            )
        return record

    def record_count(self) -> int:
        with self._lock:
            return len(self._records)


class PostgresBusinessMessageProjectionRepository:
    """Append-only internal projection writer bound to an active Postgres UoW."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def record(
        self,
        source: BusinessCompletionMessageSource,
        inbox_account: InboxAccountSnapshot,
    ) -> BusinessMessageProjectionPersistenceSummary:
        candidate = _record_from_source(source, inbox_account)
        with self._cursor() as cursor:
            self._assert_durable_completed_receipt(cursor, source, candidate)
            cursor.execute(
                """
                INSERT INTO async_effects.business_message_projections (
                    message_id, business_receipt_id, operation_id,
                    resource_owner_subject_id, resource_vault_id,
                    resource_type, resource_id, resource_version,
                    resource_authority_epoch, purpose, business_target_key,
                    inbox_subject_id, inbox_vault_id, inbox_account_epoch,
                    message_kind, state, projection_hash, schema_version
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                RETURNING message_id
                """,
                self._insert_params(candidate),
            )
            inserted = cursor.fetchone()
            if inserted is not None:
                return BusinessMessageProjectionPersistenceSummary("recorded", candidate)

            existing = self._load_by_message_id(cursor, candidate.message.message_id)
            if existing is None:
                existing = self._load_by_receipt_inbox_scope(cursor, candidate)
            if existing is None:
                raise BusinessMessageProjectionConflict(
                    "business message projection conflicted without a durable record"
                )
            _assert_same(existing, candidate)
            return BusinessMessageProjectionPersistenceSummary("deduplicated", existing)

    def load(self, message_id: str) -> BusinessMessageProjectionRecord:
        normalized_id = _uuid(message_id, field="message_id")
        with self._cursor() as cursor:
            record = self._load_by_message_id(cursor, normalized_id)
        if record is None:
            raise BusinessMessageProjectionPersistenceError(
                "business message projection is not durably recorded"
            )
        return record

    @staticmethod
    def _insert_params(record: BusinessMessageProjectionRecord) -> tuple[object, ...]:
        message = record.message
        return (
            message.message_id,
            message.business_receipt_id,
            record.operation_id,
            message.resource_owner_subject_id,
            message.resource_vault_id,
            message.resource_type,
            message.resource_id,
            message.resource_version,
            message.authority_epoch,
            "businessCompletionMessage",
            message.business_target_key,
            message.inbox_subject_id,
            message.inbox_vault_id,
            record.inbox_account_epoch,
            message.kind.value,
            message.state.value,
            record.projection_hash,
            BUSINESS_MESSAGE_PROJECTION_SCHEMA_VERSION,
        )

    def _assert_durable_completed_receipt(
        self,
        cursor: Any,
        source: BusinessCompletionMessageSource,
        candidate: BusinessMessageProjectionRecord,
    ) -> None:
        cursor.execute(
            """
            SELECT receipt_id, operation_id, owner_subject_id, vault_id,
                   resource_type, resource_id, resource_version, purpose,
                   authority_epoch, business_target_key, state, outcome
            FROM async_effects.business_receipts
            WHERE receipt_id = %s
            FOR SHARE
            """,
            (candidate.message.business_receipt_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise BusinessMessageProjectionPersistenceError(
                "business completion receipt is not durably recorded"
            )
        message = candidate.message
        expected = {
            "receipt_id": message.business_receipt_id,
            "operation_id": source.intent.operation_id,
            "owner_subject_id": message.resource_owner_subject_id,
            "vault_id": message.resource_vault_id,
            "resource_type": message.resource_type,
            "resource_id": message.resource_id,
            "resource_version": message.resource_version,
            "authority_epoch": message.authority_epoch,
            "business_target_key": message.business_target_key,
            "state": "completed",
            "outcome": "completed",
        }
        actual = {
            "receipt_id": str(row["receipt_id"]),
            "operation_id": str(row["operation_id"]),
            "owner_subject_id": str(row["owner_subject_id"]),
            "vault_id": str(row["vault_id"]),
            "resource_type": str(row["resource_type"]),
            "resource_id": str(row["resource_id"]),
            "resource_version": int(row["resource_version"]),
            "authority_epoch": int(row["authority_epoch"]),
            "business_target_key": str(row["business_target_key"]),
            "state": str(row["state"]),
            "outcome": str(row["outcome"]),
        }
        # A source carries the real purpose. It must match durable receipt data;
        # the stored projection purpose remains a fixed internal classification.
        if str(row["purpose"]) != source.intent.target.purpose:
            raise BusinessMessageProjectionPersistenceError(
                "business completion receipt purpose no longer matches its source"
            )
        if actual != expected:
            raise BusinessMessageProjectionPersistenceError(
                "business completion receipt does not match immutable message coordinates"
            )

    def _load_by_message_id(
        self,
        cursor: Any,
        message_id: str,
    ) -> BusinessMessageProjectionRecord | None:
        cursor.execute(
            self._select_sql("projection.message_id = %s"),
            (message_id,),
        )
        row = cursor.fetchone()
        return None if row is None else self._record_from_row(row)

    def _load_by_receipt_inbox_scope(
        self,
        cursor: Any,
        candidate: BusinessMessageProjectionRecord,
    ) -> BusinessMessageProjectionRecord | None:
        message = candidate.message
        cursor.execute(
            self._select_sql(
                "projection.business_receipt_id = %s "
                "AND projection.message_kind = %s "
                "AND projection.inbox_subject_id = %s "
                "AND projection.inbox_vault_id = %s"
            ),
            (
                message.business_receipt_id,
                message.kind.value,
                message.inbox_subject_id,
                message.inbox_vault_id,
            ),
        )
        row = cursor.fetchone()
        return None if row is None else self._record_from_row(row)

    @staticmethod
    def _select_sql(where_clause: str) -> str:
        return f"""
            SELECT projection.message_id, projection.business_receipt_id,
                   projection.operation_id, projection.resource_owner_subject_id,
                   projection.resource_vault_id, projection.resource_type,
                   projection.resource_id, projection.resource_version,
                   projection.resource_authority_epoch, projection.business_target_key,
                   projection.inbox_subject_id, projection.inbox_vault_id,
                   projection.inbox_account_epoch, projection.message_kind,
                   projection.state, projection.projection_hash, projection.schema_version
            FROM async_effects.business_message_projections AS projection
            WHERE {where_clause}
        """

    @staticmethod
    def _record_from_row(row: Mapping[str, object]) -> BusinessMessageProjectionRecord:
        try:
            schema_version = str(row["schema_version"])
            if schema_version != BUSINESS_MESSAGE_PROJECTION_SCHEMA_VERSION:
                raise BusinessMessageProjectionPersistenceError(
                    "business message projection schema version is unsupported"
                )
            message = InAppMessageProjection(
                message_id=str(row["message_id"]),
                kind=InAppMessageKind(str(row["message_kind"])),
                resource_owner_subject_id=str(row["resource_owner_subject_id"]),
                resource_vault_id=str(row["resource_vault_id"]),
                inbox_subject_id=str(row["inbox_subject_id"]),
                inbox_vault_id=str(row["inbox_vault_id"]),
                resource_type=str(row["resource_type"]),
                resource_id=str(row["resource_id"]),
                resource_version=int(row["resource_version"]),
                authority_epoch=int(row["resource_authority_epoch"]),
                business_receipt_id=str(row["business_receipt_id"]),
                business_target_key=str(row["business_target_key"]),
                state=InAppMessageState(str(row["state"])),
            )
            record = BusinessMessageProjectionRecord(
                message=message,
                operation_id=str(row["operation_id"]),
                inbox_account_epoch=int(row["inbox_account_epoch"]),
            )
            if _sha256_hex(row["projection_hash"], field="projection_hash") != record.projection_hash:
                raise BusinessMessageProjectionPersistenceError(
                    "business message projection hash does not match immutable coordinates"
                )
            return record
        except (KeyError, TypeError, ValueError, BusinessMessageNotificationContractError) as exc:
            if isinstance(exc, BusinessMessageProjectionPersistenceError):
                raise
            raise BusinessMessageProjectionPersistenceError(
                "business message projection row is malformed"
            ) from exc

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)


__all__ = [
    "BUSINESS_MESSAGE_PROJECTION_SCHEMA_VERSION",
    "BusinessMessageProjectionConflict",
    "BusinessMessageProjectionPersistenceError",
    "BusinessMessageProjectionPersistenceSummary",
    "BusinessMessageProjectionRecord",
    "InboxAccountSnapshot",
    "InMemoryBusinessMessageProjectionRepository",
    "PostgresBusinessMessageProjectionRepository",
]
