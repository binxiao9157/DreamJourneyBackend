"""Repository ports for the schema-only async effect kernel.

The Postgres implementation intentionally never commits. Its caller must hold
the same request/job Unit of Work as the business aggregate; wiring a concrete
aggregate to this repository is the next Work Item.
"""

from __future__ import annotations

from copy import deepcopy
from threading import RLock
from typing import Any, Dict, Optional

from app.async_effects.contracts import (
    AsyncEffectBusinessOutcome,
    AsyncEffectConflict,
    AsyncEffectIntent,
    AsyncEffectJobState,
    AsyncEffectOperationState,
    AsyncEffectOutboxState,
    EffectReceiptSummary,
)


def _summary(intent: AsyncEffectIntent, *, outcome: str) -> EffectReceiptSummary:
    return EffectReceiptSummary(
        outcome=outcome,
        operation_id=intent.operation_id,
        outbox_event_id=intent.outbox_event_id,
        job_id=intent.job_id,
        business_receipt_id=intent.business_receipt_id,
        stable_key=intent.stable_key,
        operation_state=AsyncEffectOperationState.ACCEPTED,
        outbox_state=AsyncEffectOutboxState.PENDING,
        job_state=AsyncEffectJobState.PENDING,
        business_outcome=AsyncEffectBusinessOutcome.ACCEPTED,
    )


class InMemoryEffectKernelRepository:
    """Thread-safe semantic double used by contract tests only."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._records: Dict[tuple[str, str], Dict[str, Any]] = {}

    def accept(self, intent: AsyncEffectIntent) -> EffectReceiptSummary:
        key = (intent.target.vault_id, intent.stable_key)
        fingerprint = intent.immutable_fingerprint()
        with self._lock:
            existing = self._records.get(key)
            if existing is not None:
                if existing["fingerprint"] != fingerprint:
                    raise AsyncEffectConflict(
                        "stable effect key cannot be reused with a different payload"
                    )
                return _summary(intent, outcome="deduplicated")
            self._records[key] = {
                "fingerprint": fingerprint,
                "intent": intent,
                "job_state": AsyncEffectJobState.PENDING,
                "operation_state": AsyncEffectOperationState.ACCEPTED,
                "summary": _summary(intent, outcome="accepted"),
            }
            return _summary(intent, outcome="accepted")

    def is_runnable(self, intent: AsyncEffectIntent) -> bool:
        """Return whether this exact semantic-double intent can still run.

        Read paths use this only to avoid presenting an already terminal
        effect as an active recovery.  It deliberately returns a boolean and
        never exposes coordination IDs or lifecycle details to product code.
        """

        key = (intent.target.vault_id, intent.stable_key)
        with self._lock:
            record = self._records.get(key)
            if record is None or record.get("fingerprint") != intent.immutable_fingerprint():
                return False
            return (
                record.get("operation_state") is AsyncEffectOperationState.ACCEPTED
                and record.get("job_state")
                in {
                    AsyncEffectJobState.PENDING,
                    AsyncEffectJobState.RETRY_WAIT,
                    AsyncEffectJobState.LEASED,
                }
            )

    def set_job_state_for_test(
        self,
        *,
        job_id: str,
        state: AsyncEffectJobState,
    ) -> None:
        """Set a semantic-double lifecycle state for contract tests only."""

        if not isinstance(state, AsyncEffectJobState):
            raise TypeError("state must be an AsyncEffectJobState")
        with self._lock:
            for record in self._records.values():
                intent = record.get("intent")
                if isinstance(intent, AsyncEffectIntent) and intent.job_id == job_id:
                    record["job_state"] = state
                    return
        raise KeyError(f"unknown async effect job: {job_id}")

    def record_count(self) -> int:
        with self._lock:
            return len(self._records)

    def snapshot(self) -> Dict[tuple[str, str], Dict[str, Any]]:
        with self._lock:
            return deepcopy(self._records)


class PostgresEffectKernelRepository:
    """Postgres writer that requires an already-open Unit of Work connection."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def accept(self, intent: AsyncEffectIntent) -> EffectReceiptSummary:
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - psycopg is a production dependency
            dict_row = None

        with self._connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                INSERT INTO async_effects.operations (
                    operation_id, operation_type, owner_subject_id, vault_id,
                    resource_type, resource_id, resource_version, purpose,
                    authority_epoch, stable_key, payload_hash, state, attempt
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'accepted', 0)
                ON CONFLICT (vault_id, stable_key) DO NOTHING
                RETURNING operation_id, operation_type, owner_subject_id, vault_id,
                    resource_type, resource_id, resource_version, purpose,
                    authority_epoch, stable_key, payload_hash
                """,
                self._operation_params(intent),
            )
            row = cursor.fetchone()
            if row is None:
                cursor.execute(
                    """
                    SELECT operation_id, operation_type, owner_subject_id, vault_id,
                        resource_type, resource_id, resource_version, purpose,
                        authority_epoch, stable_key, payload_hash
                    FROM async_effects.operations
                    WHERE vault_id = %s AND stable_key = %s
                    FOR UPDATE
                    """,
                    (intent.target.vault_id, intent.stable_key),
                )
                existing = cursor.fetchone()
                if existing is None:
                    raise RuntimeError("effect operation insert did not produce a row")
                self._assert_immutable_match(existing, intent)
                self._assert_job_policy_match(cursor, intent)
                return _summary(intent, outcome="deduplicated")

            self._insert_initial_records(cursor, intent)
            return _summary(intent, outcome="accepted")

    @staticmethod
    def _operation_params(intent: AsyncEffectIntent) -> tuple[object, ...]:
        target = intent.target
        return (
            intent.operation_id,
            intent.operation_type,
            target.owner_subject_id,
            target.vault_id,
            target.resource_type,
            target.resource_id,
            target.resource_version,
            target.purpose,
            target.authority_epoch,
            intent.stable_key,
            intent.payload_hash,
        )

    @staticmethod
    def _assert_immutable_match(row: Dict[str, Any], intent: AsyncEffectIntent) -> None:
        expected = {
            "operation_id": intent.operation_id,
            "operation_type": intent.operation_type,
            "owner_subject_id": intent.target.owner_subject_id,
            "vault_id": intent.target.vault_id,
            "resource_type": intent.target.resource_type,
            "resource_id": intent.target.resource_id,
            "resource_version": intent.target.resource_version,
            "purpose": intent.target.purpose,
            "authority_epoch": intent.target.authority_epoch,
            "stable_key": intent.stable_key,
            "payload_hash": intent.payload_hash,
        }
        if any(str(row.get(key)) != str(value) for key, value in expected.items()):
            raise AsyncEffectConflict(
                "stable effect key cannot be reused with a different payload"
            )

    @staticmethod
    def _assert_job_policy_match(cursor: Any, intent: AsyncEffectIntent) -> None:
        cursor.execute(
            """
            SELECT max_attempts
            FROM async_effects.jobs
            WHERE operation_id = %s AND job_type = %s
            FOR UPDATE
            """,
            (intent.operation_id, intent.job_type),
        )
        row = cursor.fetchone()
        if row is None or int(row["max_attempts"]) != intent.max_attempts:
            raise AsyncEffectConflict(
                "stable effect key cannot be reused with a different job retry policy"
            )

    @staticmethod
    def _insert_initial_records(cursor: Any, intent: AsyncEffectIntent) -> None:
        target = intent.target
        common = (
            intent.operation_id,
            target.owner_subject_id,
            target.vault_id,
            target.resource_type,
            target.resource_id,
            target.resource_version,
            target.purpose,
            target.authority_epoch,
            intent.stable_key,
        )
        cursor.execute(
            """
            INSERT INTO async_effects.outbox_events (
                event_id, operation_id, owner_subject_id, vault_id, resource_type,
                resource_id, resource_version, purpose, authority_epoch, stable_key,
                event_type, payload_hash, state, attempt
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', 0)
            ON CONFLICT (operation_id, event_type) DO NOTHING
            """,
            (intent.outbox_event_id, *common, intent.event_type, intent.payload_hash),
        )
        cursor.execute(
            """
            INSERT INTO async_effects.jobs (
                job_id, operation_id, owner_subject_id, vault_id, resource_type,
                resource_id, resource_version, purpose, authority_epoch, stable_key,
                job_type, payload_hash, state, attempt, max_attempts
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', 0, %s)
            ON CONFLICT (operation_id, job_type) DO NOTHING
            """,
            (intent.job_id, *common, intent.job_type, intent.payload_hash, intent.max_attempts),
        )
        cursor.execute(
            """
            INSERT INTO async_effects.business_receipts (
                receipt_id, operation_id, owner_subject_id, vault_id, resource_type,
                resource_id, resource_version, purpose, authority_epoch, stable_key,
                receipt_type, business_target_key, payload_hash, state, outcome, attempt
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'operationAccepted', %s, %s,
                    'accepted', 'accepted', 0)
            ON CONFLICT (operation_id, receipt_type, business_target_key) DO NOTHING
            """,
            (
                intent.business_receipt_id,
                *common,
                intent.business_target_key,
                intent.payload_hash,
            ),
        )
