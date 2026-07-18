"""Default-disabled worker lease coordination for async effects.

This module owns only job lease and attempt evidence. It does not execute a
provider, create a business completion, or expose a public API. Callers must
hold a request/job Unit of Work for every Postgres mutation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from threading import RLock
from typing import Any, Callable, Iterable, Mapping, Optional
from uuid import UUID, uuid5

from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectJobState


_WORKER_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_ATTEMPT_NAMESPACE = UUID("d3ba9c73-3485-4909-aefd-152728be5e10")
_TERMINAL_JOB_STATES = {
    AsyncEffectJobState.SUCCEEDED.value,
    AsyncEffectJobState.FAILED.value,
    AsyncEffectJobState.UNKNOWN.value,
    AsyncEffectJobState.CANCELLED.value,
    AsyncEffectJobState.BLOCKED.value,
}


class AsyncEffectLeaseError(RuntimeError):
    """The worker lease operation is invalid or no longer authoritative."""


class AsyncEffectLeaseLost(AsyncEffectLeaseError):
    """A worker attempted to mutate a lease it no longer owns."""


class AsyncEffectLeaseCancelled(AsyncEffectLeaseError):
    """A valid worker lease was cancelled before the next heartbeat."""


def _normalize_worker_id(worker_id: object) -> str:
    normalized = str(worker_id or "").strip()
    if not _WORKER_IDENTIFIER_PATTERN.fullmatch(normalized):
        raise AsyncEffectLeaseError("worker_id must be an opaque identifier")
    return normalized


def _normalize_lease_seconds(lease_seconds: object) -> int:
    if isinstance(lease_seconds, bool):
        raise AsyncEffectLeaseError("lease_seconds must be a positive integer")
    try:
        normalized = int(lease_seconds)
    except (TypeError, ValueError) as exc:
        raise AsyncEffectLeaseError("lease_seconds must be a positive integer") from exc
    if normalized < 1 or normalized > 3600:
        raise AsyncEffectLeaseError("lease_seconds must be between 1 and 3600")
    return normalized


def _normalize_retry_seconds(retry_seconds: object) -> int:
    if isinstance(retry_seconds, bool):
        raise AsyncEffectLeaseError("retry_seconds must be a non-negative integer")
    try:
        normalized = int(retry_seconds)
    except (TypeError, ValueError) as exc:
        raise AsyncEffectLeaseError("retry_seconds must be a non-negative integer") from exc
    if normalized < 0 or normalized > 86400:
        raise AsyncEffectLeaseError("retry_seconds must be between 0 and 86400")
    return normalized


def _normalize_job_types(job_types: Iterable[str]) -> tuple[str, ...]:
    normalized = tuple(sorted({str(item or "").strip() for item in job_types if str(item or "").strip()}))
    if not normalized:
        raise AsyncEffectLeaseError("at least one supported job type is required")
    if any(not _WORKER_IDENTIFIER_PATTERN.fullmatch(item) for item in normalized):
        raise AsyncEffectLeaseError("job types must be opaque identifiers")
    return normalized


def _utc_iso(value: object) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value or "")


def _attempt_id(job_id: str, attempt: int) -> str:
    return str(uuid5(_ATTEMPT_NAMESPACE, f"async-effect-attempt:{job_id}:{attempt}"))


@dataclass(frozen=True)
class AsyncEffectJobLease:
    job_id: str
    operation_id: str
    attempt_id: str
    worker_id: str
    job_type: str
    attempt: int
    lease_until: str
    heartbeat_at: str


@dataclass(frozen=True)
class AsyncEffectJobPreview:
    job_id: str
    operation_id: str
    job_type: str
    state: str
    attempt: int
    available_at: str


@dataclass(frozen=True)
class AsyncEffectCancelResult:
    job_id: str
    state: str
    outcome: str
    cancel_requested_at: str


class InMemoryAsyncEffectLeaseRepository:
    """Deterministic lease model used by G0 worker-boundary tests only."""

    def __init__(self, *, now: Optional[Callable[[], datetime]] = None) -> None:
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._lock = RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._attempts: dict[tuple[str, int], dict[str, Any]] = {}

    def seed(self, intent: AsyncEffectIntent, *, available_at: Optional[datetime] = None) -> None:
        if not isinstance(intent, AsyncEffectIntent):
            raise TypeError("intent is required")
        with self._lock:
            self._jobs.setdefault(
                intent.job_id,
                {
                    "jobId": intent.job_id,
                    "operationId": intent.operation_id,
                    "jobType": intent.job_type,
                    "state": AsyncEffectJobState.PENDING.value,
                    "attempt": 0,
                    "availableAt": available_at or self._now(),
                    "leaseOwner": None,
                    "leaseUntil": None,
                    "heartbeatAt": None,
                    "cancelRequestedAt": None,
                },
            )

    def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        supported_job_types: Iterable[str],
    ) -> Optional[AsyncEffectJobLease]:
        normalized_worker_id = _normalize_worker_id(worker_id)
        normalized_lease_seconds = _normalize_lease_seconds(lease_seconds)
        allowed_types = set(_normalize_job_types(supported_job_types))
        now = self._now()
        with self._lock:
            candidates = [
                job
                for job in self._jobs.values()
                if job["jobType"] in allowed_types
                and job["cancelRequestedAt"] is None
                and self._is_claimable(job, now)
            ]
            if not candidates:
                return None
            job = min(candidates, key=lambda item: (item["availableAt"], item["jobId"]))
            previous_state = str(job["state"])
            previous_attempt = int(job["attempt"])
            if previous_state == AsyncEffectJobState.LEASED.value and previous_attempt > 0:
                prior = self._attempts.get((job["jobId"], previous_attempt))
                if prior is not None and prior["state"] == "started":
                    prior.update(state="unknown", errorCode="leaseExpired", finishedAt=now)
            job["attempt"] = previous_attempt + 1
            job["state"] = AsyncEffectJobState.LEASED.value
            job["leaseOwner"] = normalized_worker_id
            job["leaseUntil"] = now + timedelta(seconds=normalized_lease_seconds)
            job["heartbeatAt"] = now
            attempt_id = _attempt_id(job["jobId"], job["attempt"])
            self._attempts[(job["jobId"], job["attempt"])] = {
                "attemptId": attempt_id,
                "state": "started",
                "attempt": job["attempt"],
                "startedAt": now,
                "finishedAt": None,
                "errorCode": None,
            }
            return self._lease_from_job(job, attempt_id=attempt_id)

    def heartbeat(self, lease: AsyncEffectJobLease, *, lease_seconds: int) -> AsyncEffectJobLease:
        normalized_seconds = _normalize_lease_seconds(lease_seconds)
        now = self._now()
        with self._lock:
            job = self._jobs.get(lease.job_id)
            self._assert_active_lease(job, lease, now)
            job["heartbeatAt"] = now
            job["leaseUntil"] = now + timedelta(seconds=normalized_seconds)
            return self._lease_from_job(job, attempt_id=lease.attempt_id)

    def request_cancel(self, job_id: str) -> AsyncEffectCancelResult:
        normalized_job_id = str(job_id or "").strip()
        now = self._now()
        with self._lock:
            job = self._jobs.get(normalized_job_id)
            if job is None:
                raise AsyncEffectLeaseError("job does not exist")
            if job["state"] in _TERMINAL_JOB_STATES:
                return AsyncEffectCancelResult(
                    job_id=normalized_job_id,
                    state=str(job["state"]),
                    outcome="alreadyTerminal",
                    cancel_requested_at=_utc_iso(job["cancelRequestedAt"]),
                )
            job["cancelRequestedAt"] = job["cancelRequestedAt"] or now
            if job["state"] in {
                AsyncEffectJobState.PENDING.value,
                AsyncEffectJobState.RETRY_WAIT.value,
            }:
                job["state"] = AsyncEffectJobState.CANCELLED.value
                return AsyncEffectCancelResult(
                    job_id=normalized_job_id,
                    state=AsyncEffectJobState.CANCELLED.value,
                    outcome="cancelledBeforeLease",
                    cancel_requested_at=_utc_iso(job["cancelRequestedAt"]),
                )
            return AsyncEffectCancelResult(
                job_id=normalized_job_id,
                state=str(job["state"]),
                outcome="cancellationRequested",
                cancel_requested_at=_utc_iso(job["cancelRequestedAt"]),
            )

    def release_retryable(self, lease: AsyncEffectJobLease, *, retry_seconds: int) -> AsyncEffectJobPreview:
        normalized_retry_seconds = _normalize_retry_seconds(retry_seconds)
        now = self._now()
        with self._lock:
            job = self._jobs.get(lease.job_id)
            self._assert_active_lease(job, lease, now)
            attempt = self._attempts[(lease.job_id, lease.attempt)]
            attempt.update(state="retryableFailed", errorCode="shadowOnly", finishedAt=now)
            job.update(
                state=AsyncEffectJobState.RETRY_WAIT.value,
                availableAt=now + timedelta(seconds=normalized_retry_seconds),
                leaseOwner=None,
                leaseUntil=None,
                heartbeatAt=None,
            )
            return self._preview_from_job(job)

    def attempt_state(self, job_id: str, attempt: int) -> Optional[str]:
        with self._lock:
            row = self._attempts.get((job_id, attempt))
            return None if row is None else str(row["state"])

    def _is_claimable(self, job: Mapping[str, Any], now: datetime) -> bool:
        state = str(job["state"])
        if state in {AsyncEffectJobState.PENDING.value, AsyncEffectJobState.RETRY_WAIT.value}:
            return job["availableAt"] <= now
        return state == AsyncEffectJobState.LEASED.value and job["leaseUntil"] <= now

    @staticmethod
    def _lease_from_job(job: Mapping[str, Any], *, attempt_id: str) -> AsyncEffectJobLease:
        return AsyncEffectJobLease(
            job_id=str(job["jobId"]),
            operation_id=str(job["operationId"]),
            attempt_id=attempt_id,
            worker_id=str(job["leaseOwner"]),
            job_type=str(job["jobType"]),
            attempt=int(job["attempt"]),
            lease_until=_utc_iso(job["leaseUntil"]),
            heartbeat_at=_utc_iso(job["heartbeatAt"]),
        )

    @staticmethod
    def _preview_from_job(job: Mapping[str, Any]) -> AsyncEffectJobPreview:
        return AsyncEffectJobPreview(
            job_id=str(job["jobId"]),
            operation_id=str(job["operationId"]),
            job_type=str(job["jobType"]),
            state=str(job["state"]),
            attempt=int(job["attempt"]),
            available_at=_utc_iso(job["availableAt"]),
        )

    @staticmethod
    def _assert_active_lease(
        job: Optional[Mapping[str, Any]],
        lease: AsyncEffectJobLease,
        now: datetime,
    ) -> None:
        if job is None:
            raise AsyncEffectLeaseLost("job no longer exists")
        if job.get("cancelRequestedAt") is not None:
            raise AsyncEffectLeaseCancelled("job cancellation was requested")
        if (
            job.get("state") != AsyncEffectJobState.LEASED.value
            or job.get("leaseOwner") != lease.worker_id
            or int(job.get("attempt") or 0) != lease.attempt
            or job.get("leaseUntil") is None
            or job["leaseUntil"] <= now
        ):
            raise AsyncEffectLeaseLost("worker lease is no longer current")


class PostgresAsyncEffectLeaseRepository:
    """Lease/attempt writer bound to an already-open Postgres Unit of Work."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        supported_job_types: Iterable[str],
    ) -> Optional[AsyncEffectJobLease]:
        normalized_worker_id = _normalize_worker_id(worker_id)
        normalized_lease_seconds = _normalize_lease_seconds(lease_seconds)
        normalized_job_types = _normalize_job_types(supported_job_types)
        with self._cursor() as cursor:
            cursor.execute(
                """
                WITH candidate AS (
                    SELECT job_id, state AS previous_state, attempt AS previous_attempt
                    FROM async_effects.jobs
                    WHERE job_type = ANY(%s)
                      AND cancel_requested_at IS NULL
                      AND (
                          (state IN ('pending', 'retryWait') AND available_at <= NOW())
                          OR (state = 'leased' AND lease_until <= NOW())
                      )
                    ORDER BY available_at ASC, created_at ASC, job_id ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE async_effects.jobs AS job
                SET state = 'leased',
                    attempt = job.attempt + 1,
                    lease_owner = %s,
                    lease_until = NOW() + (%s * INTERVAL '1 second'),
                    heartbeat_at = NOW(),
                    updated_at = NOW()
                FROM candidate
                WHERE job.job_id = candidate.job_id
                RETURNING job.*, candidate.previous_state, candidate.previous_attempt
                """,
                (list(normalized_job_types), normalized_worker_id, normalized_lease_seconds),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            previous_state = str(row["previous_state"])
            previous_attempt = int(row["previous_attempt"])
            if previous_state == AsyncEffectJobState.LEASED.value and previous_attempt > 0:
                cursor.execute(
                    """
                    UPDATE async_effects.job_attempts
                    SET state = 'unknown',
                        error_code = 'leaseExpired',
                        finished_at = NOW(),
                        updated_at = NOW()
                    WHERE job_id = %s AND attempt = %s AND state = 'started'
                    """,
                    (row["job_id"], previous_attempt),
                )
            attempt_id = _attempt_id(str(row["job_id"]), int(row["attempt"]))
            cursor.execute(
                """
                INSERT INTO async_effects.job_attempts (
                    attempt_id, job_id, operation_id, owner_subject_id, vault_id,
                    resource_type, resource_id, resource_version, purpose,
                    authority_epoch, stable_key, state, attempt
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'started', %s)
                """,
                (
                    attempt_id,
                    row["job_id"],
                    row["operation_id"],
                    row["owner_subject_id"],
                    row["vault_id"],
                    row["resource_type"],
                    row["resource_id"],
                    row["resource_version"],
                    row["purpose"],
                    row["authority_epoch"],
                    row["stable_key"],
                    row["attempt"],
                ),
            )
            return self._lease_from_row(row, attempt_id=attempt_id)

    def heartbeat(self, lease: AsyncEffectJobLease, *, lease_seconds: int) -> AsyncEffectJobLease:
        normalized_seconds = _normalize_lease_seconds(lease_seconds)
        with self._cursor() as cursor:
            cursor.execute(
                """
                UPDATE async_effects.jobs
                SET heartbeat_at = NOW(),
                    lease_until = NOW() + (%s * INTERVAL '1 second'),
                    updated_at = NOW()
                WHERE job_id = %s
                  AND state = 'leased'
                  AND lease_owner = %s
                  AND attempt = %s
                  AND cancel_requested_at IS NULL
                  AND lease_until > NOW()
                RETURNING job_id, operation_id, job_type, attempt, lease_owner, lease_until, heartbeat_at
                """,
                (normalized_seconds, lease.job_id, lease.worker_id, lease.attempt),
            )
            row = cursor.fetchone()
            if row is not None:
                return self._lease_from_row(row, attempt_id=lease.attempt_id)
            cursor.execute(
                "SELECT cancel_requested_at FROM async_effects.jobs WHERE job_id = %s",
                (lease.job_id,),
            )
            current = cursor.fetchone()
            if current is not None and current.get("cancel_requested_at") is not None:
                raise AsyncEffectLeaseCancelled("job cancellation was requested")
            raise AsyncEffectLeaseLost("worker lease is no longer current")

    def request_cancel(self, job_id: str) -> AsyncEffectCancelResult:
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            raise AsyncEffectLeaseError("job_id is required")
        with self._cursor() as cursor:
            cursor.execute(
                """
                UPDATE async_effects.jobs
                SET cancel_requested_at = COALESCE(cancel_requested_at, NOW()),
                    state = CASE
                        WHEN state IN ('pending', 'retryWait') THEN 'cancelled'
                        ELSE state
                    END,
                    terminal_at = CASE
                        WHEN state IN ('pending', 'retryWait') THEN NOW()
                        ELSE terminal_at
                    END,
                    updated_at = NOW()
                WHERE job_id = %s
                  AND state NOT IN ('succeeded', 'failed', 'unknown', 'cancelled', 'blocked')
                RETURNING job_id, state, cancel_requested_at
                """,
                (normalized_job_id,),
            )
            row = cursor.fetchone()
            if row is None:
                cursor.execute(
                    "SELECT state, cancel_requested_at FROM async_effects.jobs WHERE job_id = %s",
                    (normalized_job_id,),
                )
                existing = cursor.fetchone()
                if existing is None:
                    raise AsyncEffectLeaseError("job does not exist")
                return AsyncEffectCancelResult(
                    job_id=normalized_job_id,
                    state=str(existing["state"]),
                    outcome="alreadyTerminal",
                    cancel_requested_at=_utc_iso(existing["cancel_requested_at"]),
                )
            state = str(row["state"])
            return AsyncEffectCancelResult(
                job_id=str(row["job_id"]),
                state=state,
                outcome=(
                    "cancelledBeforeLease"
                    if state == AsyncEffectJobState.CANCELLED.value
                    else "cancellationRequested"
                ),
                cancel_requested_at=_utc_iso(row["cancel_requested_at"]),
            )

    def release_retryable(self, lease: AsyncEffectJobLease, *, retry_seconds: int) -> AsyncEffectJobPreview:
        normalized_retry_seconds = _normalize_retry_seconds(retry_seconds)
        with self._cursor() as cursor:
            cursor.execute(
                """
                UPDATE async_effects.jobs
                SET state = 'retryWait',
                    available_at = NOW() + (%s * INTERVAL '1 second'),
                    lease_owner = NULL,
                    lease_until = NULL,
                    heartbeat_at = NULL,
                    updated_at = NOW()
                WHERE job_id = %s
                  AND state = 'leased'
                  AND lease_owner = %s
                  AND attempt = %s
                  AND cancel_requested_at IS NULL
                  AND lease_until > NOW()
                RETURNING job_id, operation_id, job_type, state, attempt, available_at
                """,
                (normalized_retry_seconds, lease.job_id, lease.worker_id, lease.attempt),
            )
            row = cursor.fetchone()
            if row is None:
                self._raise_current_lease_error(cursor, lease)
            cursor.execute(
                """
                UPDATE async_effects.job_attempts
                SET state = 'retryableFailed',
                    error_code = 'shadowOnly',
                    finished_at = NOW(),
                    updated_at = NOW()
                WHERE job_id = %s AND attempt = %s AND state = 'started'
                """,
                (lease.job_id, lease.attempt),
            )
            cursor.execute(
                """
                UPDATE async_effects.operations
                SET attempt = %s, updated_at = NOW()
                WHERE operation_id = %s AND state = 'accepted'
                """,
                (lease.attempt, lease.operation_id),
            )
            return self._preview_from_row(row)

    def preview_eligible(self, *, limit: int = 20) -> list[AsyncEffectJobPreview]:
        normalized_limit = max(1, min(int(limit), 100))
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT job_id, operation_id, job_type, state, attempt, available_at
                FROM async_effects.jobs
                WHERE cancel_requested_at IS NULL
                  AND (
                      (state IN ('pending', 'retryWait') AND available_at <= NOW())
                      OR (state = 'leased' AND lease_until <= NOW())
                  )
                ORDER BY available_at ASC, created_at ASC, job_id ASC
                LIMIT %s
                """,
                (normalized_limit,),
            )
            return [self._preview_from_row(row) for row in cursor.fetchall()]

    def _raise_current_lease_error(self, cursor: Any, lease: AsyncEffectJobLease) -> None:
        cursor.execute(
            "SELECT cancel_requested_at FROM async_effects.jobs WHERE job_id = %s",
            (lease.job_id,),
        )
        current = cursor.fetchone()
        if current is not None and current.get("cancel_requested_at") is not None:
            raise AsyncEffectLeaseCancelled("job cancellation was requested")
        raise AsyncEffectLeaseLost("worker lease is no longer current")

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)

    @staticmethod
    def _lease_from_row(row: Mapping[str, Any], *, attempt_id: str) -> AsyncEffectJobLease:
        return AsyncEffectJobLease(
            job_id=str(row["job_id"]),
            operation_id=str(row["operation_id"]),
            attempt_id=attempt_id,
            worker_id=str(row["lease_owner"]),
            job_type=str(row["job_type"]),
            attempt=int(row["attempt"]),
            lease_until=_utc_iso(row["lease_until"]),
            heartbeat_at=_utc_iso(row["heartbeat_at"]),
        )

    @staticmethod
    def _preview_from_row(row: Mapping[str, Any]) -> AsyncEffectJobPreview:
        return AsyncEffectJobPreview(
            job_id=str(row["job_id"]),
            operation_id=str(row["operation_id"]),
            job_type=str(row["job_type"]),
            state=str(row["state"]),
            attempt=int(row["attempt"]),
            available_at=_utc_iso(row["available_at"]),
        )


__all__ = [
    "AsyncEffectCancelResult",
    "AsyncEffectJobLease",
    "AsyncEffectJobPreview",
    "AsyncEffectLeaseCancelled",
    "AsyncEffectLeaseError",
    "AsyncEffectLeaseLost",
    "InMemoryAsyncEffectLeaseRepository",
    "PostgresAsyncEffectLeaseRepository",
]
