"""Durable, value-free dead-letter admission persistence.

The repository records a dead-letter only after its immutable async-effect job
is already terminal. It deliberately does not re-enqueue work, change a job
state, invoke a Provider, or validate a replay authorization receipt. Those
actions need later, separately gated wiring.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any, Mapping
from uuid import UUID

from app.async_effects.contracts import (
    AsyncEffectConflict,
    AsyncEffectIntent,
    AsyncEffectJobState,
    AsyncEffectTarget,
)
from app.async_effects.dead_letter_effects import (
    DeadLetterAdmission,
    DeadLetterCause,
    DeadLetterContractError,
    DeadLetterState,
)


_TERMINAL_JOB_STATES = {
    AsyncEffectJobState.FAILED,
    AsyncEffectJobState.UNKNOWN,
    AsyncEffectJobState.BLOCKED,
}


class DeadLetterPersistenceError(DeadLetterContractError):
    """A durable dead-letter record is missing or violates its safety contract."""


class DeadLetterPersistenceConflict(AsyncEffectConflict):
    """An immutable dead-letter coordinate was reused with different evidence."""


@dataclass(frozen=True)
class DeadLetterPersistenceSummary:
    """Value-free outcome of one dead-letter persistence attempt."""

    outcome: str
    admission: DeadLetterAdmission

    def __post_init__(self) -> None:
        if self.outcome not in {"admitted", "deduplicated"}:
            raise DeadLetterPersistenceError("dead-letter persistence outcome is invalid")
        if not isinstance(self.admission, DeadLetterAdmission):
            raise DeadLetterPersistenceError("dead-letter admission is required")

    def value_free_summary(self) -> dict[str, object]:
        return {
            **self.admission.value_free_summary(),
            "outcome": self.outcome,
        }


def _require_open_admission(admission: DeadLetterAdmission) -> None:
    if not isinstance(admission, DeadLetterAdmission):
        raise DeadLetterPersistenceError("dead-letter admission is required")
    if admission.state is not DeadLetterState.OPEN:
        raise DeadLetterPersistenceError("only an open dead letter may be admitted")


def _assert_same_admission(
    existing: DeadLetterAdmission,
    candidate: DeadLetterAdmission,
) -> None:
    if existing != candidate:
        raise DeadLetterPersistenceConflict(
            "dead-letter job attempt cannot be reused with different immutable evidence"
        )


class InMemoryAsyncEffectDeadLetterRepository:
    """Thread-safe semantic double for dead-letter persistence contracts."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._records: dict[tuple[str, int], DeadLetterAdmission] = {}

    def record(self, admission: DeadLetterAdmission) -> DeadLetterPersistenceSummary:
        _require_open_admission(admission)
        key = (admission.intent.job_id, admission.attempt)
        with self._lock:
            existing = self._records.get(key)
            if existing is None:
                self._records[key] = admission
                return DeadLetterPersistenceSummary(outcome="admitted", admission=admission)
            _assert_same_admission(existing, admission)
            return DeadLetterPersistenceSummary(outcome="deduplicated", admission=existing)

    def load(self, dead_letter_id: str) -> DeadLetterAdmission:
        normalized_id = _normalize_dead_letter_id(dead_letter_id)
        with self._lock:
            for record in self._records.values():
                if record.dead_letter_id == normalized_id:
                    return record
        raise DeadLetterPersistenceError("dead letter is not durably recorded")

    def record_count(self) -> int:
        with self._lock:
            return len(self._records)


class PostgresAsyncEffectDeadLetterRepository:
    """Dead-letter admission writer bound to an already-open Postgres UoW."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def record(self, admission: DeadLetterAdmission) -> DeadLetterPersistenceSummary:
        _require_open_admission(admission)
        with self._cursor() as cursor:
            self._lock_and_assert_authoritative_job(cursor, admission)
            cursor.execute(
                """
                INSERT INTO async_effects.dead_letters (
                    dead_letter_id, job_id, operation_id, owner_subject_id, vault_id,
                    resource_type, resource_id, resource_version, purpose,
                    authority_epoch, stable_key, reason_code, failure_hash,
                    last_receipt_hash, state, attempt
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s)
                ON CONFLICT DO NOTHING
                RETURNING dead_letter_id
                """,
                self._insert_params(admission),
            )
            inserted = cursor.fetchone()
            if inserted is not None:
                return DeadLetterPersistenceSummary(outcome="admitted", admission=admission)

            existing = self._load_by_job_attempt(cursor, admission.intent.job_id, admission.attempt)
            if existing is None:
                self._raise_duplicate_identifier_conflict(cursor, admission)
            _assert_same_admission(existing, admission)
            return DeadLetterPersistenceSummary(outcome="deduplicated", admission=existing)

    def load(self, dead_letter_id: str) -> DeadLetterAdmission:
        normalized_id = _normalize_dead_letter_id(dead_letter_id)
        with self._cursor() as cursor:
            cursor.execute(
                self._select_record_sql(where_clause="dead_letter.dead_letter_id = %s"),
                (normalized_id,),
            )
            rows = cursor.fetchall()
            if len(rows) != 1:
                raise DeadLetterPersistenceError("dead letter is not durably recorded")
            return self._admission_from_row(rows[0])

    def load_for_replay(self, dead_letter_id: str) -> DeadLetterAdmission:
        """Lock and reconstruct an open admission before an inert replay request.

        This is deliberately not a replay operation.  It only gives a later
        persistence boundary a transaction-scoped, immutable view of the
        original terminal job and dead-letter coordinates.
        """

        normalized_id = _normalize_dead_letter_id(dead_letter_id)
        with self._cursor() as cursor:
            cursor.execute(
                self._select_record_sql(
                    where_clause="dead_letter.dead_letter_id = %s",
                    lock_clause="FOR UPDATE OF dead_letter, job, operation, outbox",
                ),
                (normalized_id,),
            )
            rows = cursor.fetchall()
            if len(rows) != 1:
                raise DeadLetterPersistenceError("dead letter is not durably recorded")
            admission = self._admission_from_row(rows[0])
            _require_open_admission(admission)
            return admission

    @staticmethod
    def _insert_params(admission: DeadLetterAdmission) -> tuple[object, ...]:
        target = admission.intent.target
        return (
            admission.dead_letter_id,
            admission.intent.job_id,
            admission.intent.operation_id,
            target.owner_subject_id,
            target.vault_id,
            target.resource_type,
            target.resource_id,
            target.resource_version,
            target.purpose,
            target.authority_epoch,
            admission.stable_key,
            admission.cause.value,
            admission.failure_hash,
            admission.last_receipt_hash,
            admission.state.value,
            admission.attempt,
        )

    def _lock_and_assert_authoritative_job(self, cursor: Any, admission: DeadLetterAdmission) -> None:
        cursor.execute(
            self._select_record_sql(where_clause="job.job_id = %s", lock_clause="FOR UPDATE OF job, operation, outbox"),
            (admission.intent.job_id,),
        )
        rows = cursor.fetchall()
        if len(rows) != 1:
            raise DeadLetterPersistenceError(
                "dead-letter admission requires one durable job, operation, and outbox event"
            )
        row = rows[0]
        actual_state = _job_state(row.get("job_state"))
        if actual_state not in _TERMINAL_JOB_STATES:
            raise DeadLetterPersistenceError("dead-letter admission requires a terminal durable job")
        if actual_state is not admission.job_state:
            raise DeadLetterPersistenceConflict(
                "dead-letter admission job state does not match its durable job"
            )
        if int(row["job_attempt"]) != admission.attempt:
            raise DeadLetterPersistenceConflict(
                "dead-letter admission attempt does not match its durable job"
            )
        if int(row["max_attempts"]) != admission.max_attempts:
            raise DeadLetterPersistenceConflict(
                "dead-letter admission max attempts does not match its durable job"
            )
        self._assert_row_matches_intent(row, admission.intent)

    def _load_by_job_attempt(
        self,
        cursor: Any,
        job_id: str,
        attempt: int,
    ) -> DeadLetterAdmission | None:
        cursor.execute(
            self._select_record_sql(
                where_clause="dead_letter.job_id = %s AND dead_letter.attempt = %s",
                lock_clause="FOR UPDATE OF dead_letter, job, operation, outbox",
            ),
            (job_id, attempt),
        )
        rows = cursor.fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise DeadLetterPersistenceError("dead-letter job attempt must have one durable record")
        return self._admission_from_row(rows[0])

    def _raise_duplicate_identifier_conflict(self, cursor: Any, admission: DeadLetterAdmission) -> None:
        cursor.execute(
            """
            SELECT job_id, attempt
            FROM async_effects.dead_letters
            WHERE dead_letter_id = %s
            FOR UPDATE
            """,
            (admission.dead_letter_id,),
        )
        existing = cursor.fetchone()
        if existing is None:
            raise DeadLetterPersistenceError("dead-letter insert did not produce a durable record")
        raise DeadLetterPersistenceConflict(
            "dead-letter identifier is already bound to a different job attempt"
        )

    @staticmethod
    def _select_record_sql(*, where_clause: str, lock_clause: str = "") -> str:
        return f"""
            SELECT
                dead_letter.dead_letter_id,
                dead_letter.job_id AS dead_letter_job_id,
                dead_letter.operation_id AS dead_letter_operation_id,
                dead_letter.owner_subject_id AS dead_letter_owner_subject_id,
                dead_letter.vault_id AS dead_letter_vault_id,
                dead_letter.resource_type AS dead_letter_resource_type,
                dead_letter.resource_id AS dead_letter_resource_id,
                dead_letter.resource_version AS dead_letter_resource_version,
                dead_letter.purpose AS dead_letter_purpose,
                dead_letter.authority_epoch AS dead_letter_authority_epoch,
                dead_letter.stable_key AS dead_letter_stable_key,
                dead_letter.reason_code,
                dead_letter.failure_hash,
                dead_letter.last_receipt_hash,
                dead_letter.state AS dead_letter_state,
                dead_letter.attempt AS dead_letter_attempt,
                job.job_id,
                job.operation_id,
                job.owner_subject_id,
                job.vault_id,
                job.resource_type,
                job.resource_id,
                job.resource_version,
                job.purpose,
                job.authority_epoch,
                job.stable_key,
                job.job_type,
                job.payload_hash AS job_payload_hash,
                job.state AS job_state,
                job.attempt AS job_attempt,
                job.max_attempts,
                operation.operation_type,
                operation.payload_hash AS operation_payload_hash,
                outbox.event_id,
                outbox.event_type,
                outbox.payload_hash AS outbox_payload_hash
            FROM async_effects.jobs AS job
            JOIN async_effects.operations AS operation
              ON operation.operation_id = job.operation_id
            JOIN async_effects.outbox_events AS outbox
              ON outbox.operation_id = job.operation_id
            LEFT JOIN async_effects.dead_letters AS dead_letter
              ON dead_letter.job_id = job.job_id
            WHERE {where_clause}
            {lock_clause}
        """

    @staticmethod
    def _assert_row_matches_intent(row: Mapping[str, object], intent: AsyncEffectIntent) -> None:
        target = intent.target
        expected = {
            "job_id": intent.job_id,
            "operation_id": intent.operation_id,
            "owner_subject_id": target.owner_subject_id,
            "vault_id": target.vault_id,
            "resource_type": target.resource_type,
            "resource_id": target.resource_id,
            "resource_version": target.resource_version,
            "purpose": target.purpose,
            "authority_epoch": target.authority_epoch,
            "stable_key": intent.stable_key,
            "job_type": intent.job_type,
            "operation_type": intent.operation_type,
            "job_payload_hash": intent.payload_hash,
            "operation_payload_hash": intent.payload_hash,
            "event_id": intent.outbox_event_id,
            "event_type": intent.event_type,
            "outbox_payload_hash": intent.payload_hash,
        }
        if any(str(row.get(key)) != str(value) for key, value in expected.items()):
            raise DeadLetterPersistenceConflict(
                "dead-letter admission immutable coordinates do not match its durable job"
            )

    @classmethod
    def _admission_from_row(cls, row: Mapping[str, object]) -> DeadLetterAdmission:
        intent = cls._intent_from_row(row)
        cls._assert_row_matches_intent(row, intent)
        if str(row.get("dead_letter_job_id")) != intent.job_id:
            raise DeadLetterPersistenceError("dead-letter job coordinate is inconsistent")
        if str(row.get("dead_letter_operation_id")) != intent.operation_id:
            raise DeadLetterPersistenceError("dead-letter operation coordinate is inconsistent")
        target = intent.target
        expected_target = {
            "dead_letter_owner_subject_id": target.owner_subject_id,
            "dead_letter_vault_id": target.vault_id,
            "dead_letter_resource_type": target.resource_type,
            "dead_letter_resource_id": target.resource_id,
            "dead_letter_resource_version": target.resource_version,
            "dead_letter_purpose": target.purpose,
            "dead_letter_authority_epoch": target.authority_epoch,
            "dead_letter_stable_key": intent.stable_key,
        }
        if any(str(row.get(key)) != str(value) for key, value in expected_target.items()):
            raise DeadLetterPersistenceError("dead-letter target coordinates are inconsistent")
        if row.get("last_receipt_hash") is None:
            raise DeadLetterPersistenceError("dead-letter record is missing its last receipt hash")
        if int(row["dead_letter_attempt"]) != int(row["job_attempt"]):
            raise DeadLetterPersistenceError("dead-letter attempt is inconsistent with its durable job")
        return DeadLetterAdmission(
            dead_letter_id=str(row["dead_letter_id"]),
            intent=intent,
            job_state=_job_state(row.get("job_state")),
            attempt=int(row["dead_letter_attempt"]),
            max_attempts=int(row["max_attempts"]),
            cause=_dead_letter_cause(row.get("reason_code")),
            failure_hash=str(row["failure_hash"]),
            last_receipt_hash=str(row["last_receipt_hash"]),
            state=_dead_letter_state(row.get("dead_letter_state")),
        )

    @staticmethod
    def _intent_from_row(row: Mapping[str, object]) -> AsyncEffectIntent:
        try:
            return AsyncEffectIntent(
                operation_type=str(row["operation_type"]),
                target=AsyncEffectTarget(
                    owner_subject_id=str(row["owner_subject_id"]),
                    vault_id=str(row["vault_id"]),
                    resource_type=str(row["resource_type"]),
                    resource_id=str(row["resource_id"]),
                    resource_version=int(row["resource_version"]),
                    purpose=str(row["purpose"]),
                    authority_epoch=int(row["authority_epoch"]),
                ),
                payload_hash=str(row["job_payload_hash"]),
                event_type=str(row["event_type"]),
                job_type=str(row["job_type"]),
            )
        except (KeyError, TypeError, ValueError, DeadLetterContractError) as exc:
            raise DeadLetterPersistenceError(
                "dead-letter durable coordinates cannot reconstruct an immutable intent"
            ) from exc

    def _cursor(self) -> Any:
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - psycopg is a production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)


def _normalize_dead_letter_id(value: object) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError) as exc:
        raise DeadLetterPersistenceError("dead_letter_id must be a UUID") from exc


def _job_state(value: object) -> AsyncEffectJobState:
    try:
        state = AsyncEffectJobState(str(value))
    except ValueError as exc:
        raise DeadLetterPersistenceError("dead-letter durable job state is invalid") from exc
    if state not in _TERMINAL_JOB_STATES:
        raise DeadLetterPersistenceError("dead-letter durable job is not terminal")
    return state


def _dead_letter_cause(value: object) -> DeadLetterCause:
    try:
        return DeadLetterCause(str(value))
    except ValueError as exc:
        raise DeadLetterPersistenceError("dead-letter durable cause is invalid") from exc


def _dead_letter_state(value: object) -> DeadLetterState:
    try:
        return DeadLetterState(str(value))
    except ValueError as exc:
        raise DeadLetterPersistenceError("dead-letter durable state is invalid") from exc


__all__ = [
    "DeadLetterPersistenceConflict",
    "DeadLetterPersistenceError",
    "DeadLetterPersistenceSummary",
    "InMemoryAsyncEffectDeadLetterRepository",
    "PostgresAsyncEffectDeadLetterRepository",
]
