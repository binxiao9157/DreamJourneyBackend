"""Durable, inert authorization records for restore-fenced dead-letter replay.

The repository stores one value-free request only after the original terminal
job, owner authority, and restore checkpoint all agree.  Recording this
request is intentionally inert: it never re-enqueues a job, starts a worker,
changes a dead-letter state, or invokes a Provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import re
from threading import RLock
from typing import Any, Mapping
from uuid import UUID

from app.async_effects.contracts import AsyncEffectConflict
from app.async_effects.dead_letter_effects import (
    DeadLetterAdmission,
    DeadLetterContractError,
    DeadLetterReplayCommand,
    DeadLetterState,
)
from app.async_effects.dead_letter_repository import (
    DeadLetterPersistenceError,
    InMemoryAsyncEffectDeadLetterRepository,
    PostgresAsyncEffectDeadLetterRepository,
)
from app.async_effects.recovery_evidence import (
    DeadLetterRestoreReplayContext,
    DeadLetterRestoreReplayDecision,
    authorize_restored_dead_letter_replay,
)


ASYNC_EFFECT_DEAD_LETTER_REPLAY_REQUEST_SCHEMA_VERSION = "async-effect-dead-letter-replay-request-v1"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class DeadLetterReplayRequestError(DeadLetterContractError):
    """A durable inert replay request violated its evidence boundary."""


class DeadLetterReplayRequestConflict(AsyncEffectConflict):
    """A dead letter already has different immutable replay authority."""


class DeadLetterReplayRequestState(str, Enum):
    AUTHORIZED = "authorized"


def _uuid(value: object, *, field: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError) as exc:
        raise DeadLetterReplayRequestError(f"{field} must be a UUID") from exc


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise DeadLetterReplayRequestError(f"{field} must be an opaque identifier")
    return normalized


def _sha256_hex(value: object, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise DeadLetterReplayRequestError(f"{field} must be a lowercase SHA-256 hex digest")
    return normalized


def _positive_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DeadLetterReplayRequestError(f"{field} must be a positive integer")
    return value


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DeadLetterReplayRequest:
    """One immutable, restore-fenced request that is not executable work."""

    replay_id: str
    admission: DeadLetterAdmission
    actor_subject_id: str
    authorization_receipt_hash: str
    reason_code: str
    restore_id_hash: str
    restore_checkpoint_hash: str
    recovery_authorization_receipt_hash: str
    next_attempt: int
    state: DeadLetterReplayRequestState = DeadLetterReplayRequestState.AUTHORIZED

    def __post_init__(self) -> None:
        object.__setattr__(self, "replay_id", _uuid(self.replay_id, field="replay_id"))
        if not isinstance(self.admission, DeadLetterAdmission):
            raise DeadLetterReplayRequestError("dead-letter admission is required")
        if self.admission.state is not DeadLetterState.OPEN:
            raise DeadLetterReplayRequestError("only an open dead letter may receive a replay request")
        object.__setattr__(
            self,
            "actor_subject_id",
            _identifier(self.actor_subject_id, field="actor_subject_id"),
        )
        if self.actor_subject_id != self.admission.intent.target.owner_subject_id:
            raise DeadLetterReplayRequestError("replay request actor must remain the dead-letter owner")
        object.__setattr__(
            self,
            "authorization_receipt_hash",
            _sha256_hex(self.authorization_receipt_hash, field="authorization_receipt_hash"),
        )
        object.__setattr__(self, "reason_code", _identifier(self.reason_code, field="reason_code"))
        object.__setattr__(self, "restore_id_hash", _sha256_hex(self.restore_id_hash, field="restore_id_hash"))
        object.__setattr__(
            self,
            "restore_checkpoint_hash",
            _sha256_hex(self.restore_checkpoint_hash, field="restore_checkpoint_hash"),
        )
        object.__setattr__(
            self,
            "recovery_authorization_receipt_hash",
            _sha256_hex(
                self.recovery_authorization_receipt_hash,
                field="recovery_authorization_receipt_hash",
            ),
        )
        object.__setattr__(self, "next_attempt", _positive_int(self.next_attempt, field="next_attempt"))
        if self.next_attempt != self.admission.attempt + 1:
            raise DeadLetterReplayRequestError("replay request next_attempt must follow its dead letter")
        if self.state is not DeadLetterReplayRequestState.AUTHORIZED:
            raise DeadLetterReplayRequestError("replay request state must remain authorized")

    def value_free_summary(self) -> Mapping[str, object]:
        target = self.admission.intent.target
        return {
            "deadLetterId": self.admission.dead_letter_id,
            "jobId": self.admission.intent.job_id,
            "nextAttempt": self.next_attempt,
            "ownerDigest": _digest(target.owner_subject_id),
            "reasonCode": self.reason_code,
            "recoveryFence": "present",
            "replayId": self.replay_id,
            "resourceIdHash": _digest(target.resource_id),
            "resourceType": target.resource_type,
            "schemaVersion": ASYNC_EFFECT_DEAD_LETTER_REPLAY_REQUEST_SCHEMA_VERSION,
            "stableKey": self.admission.stable_key,
            "state": self.state.value,
            "vaultDigest": _digest(target.vault_id),
        }


@dataclass(frozen=True)
class DeadLetterReplayRequestPersistenceSummary:
    """Value-free result of persisting one inert authorization record."""

    outcome: str
    request: DeadLetterReplayRequest

    def __post_init__(self) -> None:
        if self.outcome not in {"authorized", "deduplicated"}:
            raise DeadLetterReplayRequestError("replay request persistence outcome is invalid")
        if not isinstance(self.request, DeadLetterReplayRequest):
            raise DeadLetterReplayRequestError("replay request is required")

    def value_free_summary(self) -> dict[str, object]:
        return {**self.request.value_free_summary(), "outcome": self.outcome}


def _authorized_request(
    admission: DeadLetterAdmission,
    command: DeadLetterReplayCommand,
    restore_context: DeadLetterRestoreReplayContext,
) -> DeadLetterReplayRequest:
    if not isinstance(admission, DeadLetterAdmission):
        raise DeadLetterReplayRequestError("dead-letter admission is required")
    if not isinstance(command, DeadLetterReplayCommand):
        raise DeadLetterReplayRequestError("dead-letter replay command is required")
    if not isinstance(restore_context, DeadLetterRestoreReplayContext):
        raise DeadLetterReplayRequestError("restore replay context is required")
    decision: DeadLetterRestoreReplayDecision = authorize_restored_dead_letter_replay(
        admission,
        command,
        restore_context,
    )
    if not decision.authorized or decision.replay_id is None:
        raise DeadLetterReplayRequestError(
            f"restore-fenced dead-letter replay is not authorized: {decision.reason.value}"
        )
    return DeadLetterReplayRequest(
        replay_id=decision.replay_id,
        admission=admission,
        actor_subject_id=command.actor_subject_id,
        authorization_receipt_hash=command.authorization_receipt_hash,
        reason_code=command.reason_code,
        restore_id_hash=decision.restore_id_hash,
        restore_checkpoint_hash=decision.restore_checkpoint_hash,
        recovery_authorization_receipt_hash=restore_context.recovery_authorization_receipt_hash,
        next_attempt=decision.next_attempt,
    )


def _assert_same_request(
    existing: DeadLetterReplayRequest,
    candidate: DeadLetterReplayRequest,
) -> None:
    if existing != candidate:
        raise DeadLetterReplayRequestConflict(
            "dead letter already has different immutable restore-fenced replay authority"
        )


class InMemoryAsyncEffectDeadLetterReplayRequestRepository:
    """Thread-safe semantic double for inert replay-request persistence."""

    def __init__(self, dead_letter_repository: InMemoryAsyncEffectDeadLetterRepository) -> None:
        if not isinstance(dead_letter_repository, InMemoryAsyncEffectDeadLetterRepository):
            raise TypeError("an in-memory dead-letter repository is required")
        self._dead_letter_repository = dead_letter_repository
        self._lock = RLock()
        self._records: dict[str, DeadLetterReplayRequest] = {}

    def record(
        self,
        admission: DeadLetterAdmission,
        command: DeadLetterReplayCommand,
        restore_context: DeadLetterRestoreReplayContext,
    ) -> DeadLetterReplayRequestPersistenceSummary:
        durable = self._dead_letter_repository.load(admission.dead_letter_id)
        if durable != admission:
            raise DeadLetterReplayRequestConflict(
                "replay request does not match its durable dead-letter admission"
            )
        candidate = _authorized_request(durable, command, restore_context)
        with self._lock:
            existing = self._records.get(candidate.admission.dead_letter_id)
            if existing is None:
                self._records[candidate.admission.dead_letter_id] = candidate
                return DeadLetterReplayRequestPersistenceSummary("authorized", candidate)
            _assert_same_request(existing, candidate)
            return DeadLetterReplayRequestPersistenceSummary("deduplicated", existing)

    def load(self, replay_id: str) -> DeadLetterReplayRequest:
        normalized_id = _uuid(replay_id, field="replay_id")
        with self._lock:
            for request in self._records.values():
                if request.replay_id == normalized_id:
                    return request
        raise DeadLetterReplayRequestError("replay request is not durably recorded")

    def record_count(self) -> int:
        with self._lock:
            return len(self._records)


class PostgresAsyncEffectDeadLetterReplayRequestRepository:
    """Append-only replay authority writer bound to an active Postgres UoW."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def record(
        self,
        admission: DeadLetterAdmission,
        command: DeadLetterReplayCommand,
        restore_context: DeadLetterRestoreReplayContext,
    ) -> DeadLetterReplayRequestPersistenceSummary:
        durable = self._locked_durable_admission(admission)
        if durable != admission:
            raise DeadLetterReplayRequestConflict(
                "replay request does not match its durable dead-letter admission"
            )
        candidate = _authorized_request(durable, command, restore_context)
        with self._cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO async_effects.dead_letter_replay_requests (
                    replay_id, dead_letter_id, job_id, operation_id, owner_subject_id,
                    vault_id, resource_type, resource_id, resource_version, purpose,
                    authority_epoch, stable_key, actor_subject_id,
                    authorization_receipt_hash, reason_code, restore_id_hash,
                    restore_checkpoint_hash, recovery_authorization_receipt_hash,
                    next_attempt, state
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s)
                ON CONFLICT (dead_letter_id) DO NOTHING
                RETURNING replay_id
                """,
                self._insert_params(candidate),
            )
            inserted = cursor.fetchone()
            if inserted is not None:
                return DeadLetterReplayRequestPersistenceSummary("authorized", candidate)
            existing = self._load_by_dead_letter(cursor, candidate.admission.dead_letter_id, lock=True)
            if existing is None:
                self._raise_duplicate_identifier_conflict(cursor, candidate)
            _assert_same_request(existing, candidate)
            return DeadLetterReplayRequestPersistenceSummary("deduplicated", existing)

    def load(self, replay_id: str) -> DeadLetterReplayRequest:
        normalized_id = _uuid(replay_id, field="replay_id")
        with self._cursor() as cursor:
            cursor.execute(
                self._select_request_sql(where_clause="request.replay_id = %s"),
                (normalized_id,),
            )
            rows = cursor.fetchall()
            if len(rows) != 1:
                raise DeadLetterReplayRequestError("replay request is not durably recorded")
            return self._request_from_row(rows[0])

    def _locked_durable_admission(self, admission: DeadLetterAdmission) -> DeadLetterAdmission:
        if not isinstance(admission, DeadLetterAdmission):
            raise DeadLetterReplayRequestError("dead-letter admission is required")
        try:
            return PostgresAsyncEffectDeadLetterRepository(self._connection).load_for_replay(
                admission.dead_letter_id
            )
        except DeadLetterPersistenceError as exc:
            raise DeadLetterReplayRequestError(
                "replay request requires one durable open dead-letter admission"
            ) from exc

    @staticmethod
    def _insert_params(request: DeadLetterReplayRequest) -> tuple[object, ...]:
        target = request.admission.intent.target
        return (
            request.replay_id,
            request.admission.dead_letter_id,
            request.admission.intent.job_id,
            request.admission.intent.operation_id,
            target.owner_subject_id,
            target.vault_id,
            target.resource_type,
            target.resource_id,
            target.resource_version,
            target.purpose,
            target.authority_epoch,
            request.admission.stable_key,
            request.actor_subject_id,
            request.authorization_receipt_hash,
            request.reason_code,
            request.restore_id_hash,
            request.restore_checkpoint_hash,
            request.recovery_authorization_receipt_hash,
            request.next_attempt,
            request.state.value,
        )

    def _load_by_dead_letter(
        self,
        cursor: Any,
        dead_letter_id: str,
        *,
        lock: bool,
    ) -> DeadLetterReplayRequest | None:
        cursor.execute(
            self._select_request_sql(
                where_clause="request.dead_letter_id = %s",
                lock_clause="FOR UPDATE OF request" if lock else "",
            ),
            (dead_letter_id,),
        )
        rows = cursor.fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise DeadLetterReplayRequestError("dead letter must have at most one replay request")
        return self._request_from_row(rows[0])

    def _raise_duplicate_identifier_conflict(
        self,
        cursor: Any,
        candidate: DeadLetterReplayRequest,
    ) -> None:
        cursor.execute(
            """
            SELECT dead_letter_id
            FROM async_effects.dead_letter_replay_requests
            WHERE replay_id = %s
            FOR UPDATE
            """,
            (candidate.replay_id,),
        )
        existing = cursor.fetchone()
        if existing is None:
            raise DeadLetterReplayRequestError("replay request insert did not produce a durable record")
        raise DeadLetterReplayRequestConflict(
            "replay request identifier is already bound to another dead letter"
        )

    @staticmethod
    def _select_request_sql(*, where_clause: str, lock_clause: str = "") -> str:
        return f"""
            SELECT
                request.replay_id,
                request.dead_letter_id,
                request.job_id,
                request.operation_id,
                request.owner_subject_id,
                request.vault_id,
                request.resource_type,
                request.resource_id,
                request.resource_version,
                request.purpose,
                request.authority_epoch,
                request.stable_key,
                request.actor_subject_id,
                request.authorization_receipt_hash,
                request.reason_code,
                request.restore_id_hash,
                request.restore_checkpoint_hash,
                request.recovery_authorization_receipt_hash,
                request.next_attempt,
                request.state
            FROM async_effects.dead_letter_replay_requests AS request
            WHERE {where_clause}
            {lock_clause}
        """

    def _request_from_row(self, row: Mapping[str, object]) -> DeadLetterReplayRequest:
        admission = self._locked_durable_admission_from_row(row)
        target = admission.intent.target
        expected = {
            "dead_letter_id": admission.dead_letter_id,
            "job_id": admission.intent.job_id,
            "operation_id": admission.intent.operation_id,
            "owner_subject_id": target.owner_subject_id,
            "vault_id": target.vault_id,
            "resource_type": target.resource_type,
            "resource_id": target.resource_id,
            "resource_version": target.resource_version,
            "purpose": target.purpose,
            "authority_epoch": target.authority_epoch,
            "stable_key": admission.stable_key,
        }
        if any(str(row.get(key)) != str(value) for key, value in expected.items()):
            raise DeadLetterReplayRequestError(
                "replay request immutable coordinates do not match its durable dead letter"
            )
        return DeadLetterReplayRequest(
            replay_id=str(row["replay_id"]),
            admission=admission,
            actor_subject_id=str(row["actor_subject_id"]),
            authorization_receipt_hash=str(row["authorization_receipt_hash"]),
            reason_code=str(row["reason_code"]),
            restore_id_hash=str(row["restore_id_hash"]),
            restore_checkpoint_hash=str(row["restore_checkpoint_hash"]),
            recovery_authorization_receipt_hash=str(row["recovery_authorization_receipt_hash"]),
            next_attempt=int(row["next_attempt"]),
            state=DeadLetterReplayRequestState(str(row["state"])),
        )

    def _locked_durable_admission_from_row(
        self,
        row: Mapping[str, object],
    ) -> DeadLetterAdmission:
        try:
            return PostgresAsyncEffectDeadLetterRepository(self._connection).load_for_replay(
                str(row["dead_letter_id"])
            )
        except (KeyError, DeadLetterPersistenceError) as exc:
            raise DeadLetterReplayRequestError(
                "replay request cannot reconstruct a durable open dead letter"
            ) from exc

    def _cursor(self) -> Any:
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - psycopg is a production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)


__all__ = [
    "ASYNC_EFFECT_DEAD_LETTER_REPLAY_REQUEST_SCHEMA_VERSION",
    "DeadLetterReplayRequest",
    "DeadLetterReplayRequestConflict",
    "DeadLetterReplayRequestError",
    "DeadLetterReplayRequestPersistenceSummary",
    "DeadLetterReplayRequestState",
    "InMemoryAsyncEffectDeadLetterReplayRequestRepository",
    "PostgresAsyncEffectDeadLetterReplayRequestRepository",
]
