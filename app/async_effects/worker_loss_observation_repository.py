"""Append-only persistence for value-free async-effect worker-loss evidence.

This repository persists only aggregate evidence from expired leases. It never
reads a job identifier, claims/requeues a job, mutates an attempt, starts a
worker, or invokes a Provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from threading import RLock
from typing import Any, Mapping

from app.async_effects.contracts import AsyncEffectConflict
from app.async_effects.worker_loss_evidence import (
    AsyncEffectWorkerLossEvidence,
    AsyncEffectWorkerLossEvidenceError,
    AsyncEffectWorkerLossObservationState,
)


class AsyncEffectWorkerLossObservationPersistenceError(AsyncEffectWorkerLossEvidenceError):
    """A durable worker-loss observation violates its append-only contract."""


class AsyncEffectWorkerLossObservationConflict(AsyncEffectConflict):
    """An observation identifier was reused with different immutable evidence."""


@dataclass(frozen=True)
class AsyncEffectWorkerLossObservationPersistenceSummary:
    outcome: str
    evidence: AsyncEffectWorkerLossEvidence

    def __post_init__(self) -> None:
        if self.outcome not in {"recorded", "deduplicated"}:
            raise AsyncEffectWorkerLossObservationPersistenceError(
                "worker-loss persistence outcome is invalid"
            )
        if not isinstance(self.evidence, AsyncEffectWorkerLossEvidence):
            raise AsyncEffectWorkerLossObservationPersistenceError("worker-loss evidence is required")

    def value_free_summary(self) -> dict[str, object]:
        return {**self.evidence.value_free_summary(), "outcome": self.outcome}


def _require_recordable(evidence: AsyncEffectWorkerLossEvidence) -> None:
    if not isinstance(evidence, AsyncEffectWorkerLossEvidence):
        raise AsyncEffectWorkerLossObservationPersistenceError("worker-loss evidence is required")
    if evidence.effective_state(now=datetime.now(timezone.utc)) is AsyncEffectWorkerLossObservationState.EXPIRED:
        raise AsyncEffectWorkerLossObservationPersistenceError(
            "expired worker-loss evidence cannot be persisted"
        )


def _assert_same(
    existing: AsyncEffectWorkerLossEvidence,
    candidate: AsyncEffectWorkerLossEvidence,
) -> None:
    if existing != candidate:
        raise AsyncEffectWorkerLossObservationConflict(
            "worker-loss observation identifier is bound to different immutable evidence"
        )


class InMemoryAsyncEffectWorkerLossObservationRepository:
    """Thread-safe semantic double for append-only observation contracts."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._records: dict[str, AsyncEffectWorkerLossEvidence] = {}

    def record(
        self,
        evidence: AsyncEffectWorkerLossEvidence,
    ) -> AsyncEffectWorkerLossObservationPersistenceSummary:
        _require_recordable(evidence)
        with self._lock:
            existing = self._records.get(evidence.observation_id)
            if existing is None:
                self._records[evidence.observation_id] = evidence
                return AsyncEffectWorkerLossObservationPersistenceSummary("recorded", evidence)
            _assert_same(existing, evidence)
            return AsyncEffectWorkerLossObservationPersistenceSummary("deduplicated", existing)

    def load(self, observation_id: str) -> AsyncEffectWorkerLossEvidence:
        normalized_id = str(observation_id or "").strip()
        with self._lock:
            record = self._records.get(normalized_id)
        if record is None:
            raise AsyncEffectWorkerLossObservationPersistenceError(
                "worker-loss observation is not durably recorded"
            )
        return record

    def record_count(self) -> int:
        with self._lock:
            return len(self._records)


class PostgresAsyncEffectWorkerLossObservationRepository:
    """Append-only worker-loss evidence writer bound to an active Postgres UoW."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def record(
        self,
        evidence: AsyncEffectWorkerLossEvidence,
    ) -> AsyncEffectWorkerLossObservationPersistenceSummary:
        _require_recordable(evidence)
        with self._cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO async_effects.worker_loss_observations (
                    observation_id, observation_state, reason_code, observed_at,
                    expires_at, runtime_enabled, worker_enabled, expired_lease_count,
                    expired_job_type_counts, oldest_expired_lease_age_seconds,
                    lease_owner_hash_count, observer_worker_id_hash, artifact_hash
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (observation_id) DO NOTHING
                RETURNING observation_id
                """,
                self._insert_params(evidence),
            )
            inserted = cursor.fetchone()
            if inserted is not None:
                return AsyncEffectWorkerLossObservationPersistenceSummary("recorded", evidence)
            existing = self._load(cursor, evidence.observation_id)
            _assert_same(existing, evidence)
            return AsyncEffectWorkerLossObservationPersistenceSummary("deduplicated", existing)

    def load(self, observation_id: str) -> AsyncEffectWorkerLossEvidence:
        normalized_id = str(observation_id or "").strip()
        if not normalized_id:
            raise AsyncEffectWorkerLossObservationPersistenceError("observation_id is required")
        with self._cursor() as cursor:
            return self._load(cursor, normalized_id)

    @staticmethod
    def _insert_params(evidence: AsyncEffectWorkerLossEvidence) -> tuple[object, ...]:
        try:
            from psycopg.types.json import Jsonb
        except ImportError:  # pragma: no cover - production dependency
            Jsonb = lambda value: value  # type: ignore[misc,assignment]
        return (
            evidence.observation_id,
            evidence.observation_state.value,
            evidence.reason,
            evidence.observed_at,
            evidence.expires_at,
            evidence.runtime_enabled,
            evidence.worker_enabled,
            evidence.expired_lease_count,
            Jsonb(dict(evidence.expired_job_type_counts)),
            evidence.oldest_expired_lease_age_seconds,
            evidence.lease_owner_hash_count,
            evidence.observer_worker_id_hash,
            evidence.artifact_hash,
        )

    def _load(self, cursor: Any, observation_id: str) -> AsyncEffectWorkerLossEvidence:
        cursor.execute(
            """
            SELECT observation_id, observation_state, reason_code, observed_at,
                   expires_at, runtime_enabled, worker_enabled, expired_lease_count,
                   expired_job_type_counts, oldest_expired_lease_age_seconds,
                   lease_owner_hash_count, observer_worker_id_hash, artifact_hash
            FROM async_effects.worker_loss_observations
            WHERE observation_id = %s
            """,
            (observation_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise AsyncEffectWorkerLossObservationPersistenceError(
                "worker-loss observation is not durably recorded"
            )
        return self._evidence_from_row(row)

    @staticmethod
    def _evidence_from_row(row: Mapping[str, object]) -> AsyncEffectWorkerLossEvidence:
        raw_counts = row.get("expired_job_type_counts")
        if isinstance(raw_counts, str):
            try:
                raw_counts = json.loads(raw_counts)
            except json.JSONDecodeError as exc:
                raise AsyncEffectWorkerLossObservationPersistenceError(
                    "worker-loss observation counts are invalid"
                ) from exc
        if not isinstance(raw_counts, Mapping):
            raise AsyncEffectWorkerLossObservationPersistenceError(
                "worker-loss observation counts are invalid"
            )
        try:
            return AsyncEffectWorkerLossEvidence(
                observation_id=str(row["observation_id"]),
                observation_state=AsyncEffectWorkerLossObservationState(str(row["observation_state"])),
                reason=str(row["reason_code"]),
                observed_at=row["observed_at"],  # type: ignore[arg-type]
                expires_at=row["expires_at"],  # type: ignore[arg-type]
                runtime_enabled=bool(row["runtime_enabled"]),
                worker_enabled=bool(row["worker_enabled"]),
                expired_lease_count=int(row["expired_lease_count"]),
                expired_job_type_counts=tuple(
                    (str(job_type), int(job_count)) for job_type, job_count in raw_counts.items()
                ),
                oldest_expired_lease_age_seconds=(
                    None
                    if row.get("oldest_expired_lease_age_seconds") is None
                    else int(row["oldest_expired_lease_age_seconds"])
                ),
                lease_owner_hash_count=int(row["lease_owner_hash_count"]),
                observer_worker_id_hash=str(row["observer_worker_id_hash"]),
                artifact_hash=str(row["artifact_hash"]),
            )
        except (KeyError, TypeError, ValueError, AsyncEffectWorkerLossEvidenceError) as exc:
            raise AsyncEffectWorkerLossObservationPersistenceError(
                "worker-loss observation is malformed"
            ) from exc

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)


__all__ = [
    "AsyncEffectWorkerLossObservationConflict",
    "AsyncEffectWorkerLossObservationPersistenceError",
    "AsyncEffectWorkerLossObservationPersistenceSummary",
    "InMemoryAsyncEffectWorkerLossObservationRepository",
    "PostgresAsyncEffectWorkerLossObservationRepository",
]
