"""Default-disabled scheduler lease coordination for async effects.

Scheduler leases coordinate when a future typed scheduler is allowed to inspect
an operation. They persist no payload body and do not dispatch product work.
Every Postgres mutation must run inside the caller's Unit of Work.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from threading import RLock
from typing import Any, Callable, Iterable, Mapping, Optional
from uuid import UUID, uuid5

from app.async_effects.contracts import AsyncEffectConflict, AsyncEffectIntent


_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SCHEDULER_LEASE_NAMESPACE = UUID("d0ed109c-8798-4a0c-9544-6938067b6a8a")


class AsyncEffectSchedulerLeaseError(RuntimeError):
    """The scheduler lease operation is invalid or no longer authoritative."""


class AsyncEffectSchedulerLeaseLost(AsyncEffectSchedulerLeaseError):
    """A scheduler attempted to mutate a lease it no longer owns."""


def _normalize_identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise AsyncEffectSchedulerLeaseError(f"{field} must be an opaque identifier")
    return normalized


def _normalize_lease_seconds(lease_seconds: object) -> int:
    if isinstance(lease_seconds, bool):
        raise AsyncEffectSchedulerLeaseError("lease_seconds must be a positive integer")
    try:
        normalized = int(lease_seconds)
    except (TypeError, ValueError) as exc:
        raise AsyncEffectSchedulerLeaseError("lease_seconds must be a positive integer") from exc
    if normalized < 1 or normalized > 3600:
        raise AsyncEffectSchedulerLeaseError("lease_seconds must be between 1 and 3600")
    return normalized


def _normalize_scheduler_keys(scheduler_keys: Iterable[str]) -> tuple[str, ...]:
    normalized = tuple(
        sorted(
            {
                _normalize_identifier(item, field="scheduler_key")
                for item in scheduler_keys
                if str(item or "").strip()
            }
        )
    )
    if not normalized:
        raise AsyncEffectSchedulerLeaseError("at least one scheduler_key is required")
    return normalized


def _utc_iso(value: object) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value or "")


def _lease_id(intent: AsyncEffectIntent, scheduler_key: str) -> str:
    return str(
        uuid5(
            _SCHEDULER_LEASE_NAMESPACE,
            f"async-effect-scheduler-lease:{scheduler_key}:{intent.operation_id}",
        )
    )


@dataclass(frozen=True)
class AsyncEffectSchedulerLeaseRegistration:
    outcome: str
    lease_id: str
    operation_id: str
    scheduler_key: str
    state: str


@dataclass(frozen=True)
class AsyncEffectSchedulerLease:
    lease_id: str
    operation_id: str
    scheduler_key: str
    scheduler_id: str
    attempt: int
    lease_until: str
    heartbeat_at: str


@dataclass(frozen=True)
class AsyncEffectSchedulerPreview:
    lease_id: str
    operation_id: str
    scheduler_key: str
    state: str
    attempt: int


class InMemoryAsyncEffectSchedulerLeaseRepository:
    """Deterministic scheduler lease double for G0 contract tests only."""

    def __init__(self, *, now: Optional[Callable[[], datetime]] = None) -> None:
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._lock = RLock()
        self._leases: dict[tuple[str, str], dict[str, Any]] = {}

    def register(
        self,
        intent: AsyncEffectIntent,
        *,
        scheduler_key: str,
    ) -> AsyncEffectSchedulerLeaseRegistration:
        if not isinstance(intent, AsyncEffectIntent):
            raise TypeError("intent is required")
        normalized_key = _normalize_identifier(scheduler_key, field="scheduler_key")
        key = (normalized_key, intent.operation_id)
        with self._lock:
            existing = self._leases.get(key)
            if existing is not None:
                if existing["stableKey"] != intent.stable_key:
                    raise AsyncEffectConflict("scheduler lease cannot reuse an operation with new meaning")
                return self._registration_from_row(existing, outcome="deduplicated")
            row = {
                "leaseId": _lease_id(intent, normalized_key),
                "operationId": intent.operation_id,
                "schedulerKey": normalized_key,
                "stableKey": intent.stable_key,
                "state": "available",
                "attempt": 0,
                "leaseOwner": None,
                "leaseUntil": None,
                "heartbeatAt": None,
            }
            self._leases[key] = row
            return self._registration_from_row(row, outcome="accepted")

    def claim_next(
        self,
        *,
        scheduler_id: str,
        lease_seconds: int,
        supported_scheduler_keys: Iterable[str],
    ) -> Optional[AsyncEffectSchedulerLease]:
        normalized_scheduler_id = _normalize_identifier(scheduler_id, field="scheduler_id")
        normalized_seconds = _normalize_lease_seconds(lease_seconds)
        allowed_keys = set(_normalize_scheduler_keys(supported_scheduler_keys))
        now = self._now()
        with self._lock:
            candidates = [
                row
                for row in self._leases.values()
                if row["schedulerKey"] in allowed_keys and self._is_claimable(row, now)
            ]
            if not candidates:
                return None
            row = min(candidates, key=lambda item: (item["schedulerKey"], item["leaseId"]))
            row["state"] = "leased"
            row["attempt"] = int(row["attempt"]) + 1
            row["leaseOwner"] = normalized_scheduler_id
            row["leaseUntil"] = now + timedelta(seconds=normalized_seconds)
            row["heartbeatAt"] = now
            return self._lease_from_row(row)

    def heartbeat(
        self,
        lease: AsyncEffectSchedulerLease,
        *,
        lease_seconds: int,
    ) -> AsyncEffectSchedulerLease:
        normalized_seconds = _normalize_lease_seconds(lease_seconds)
        now = self._now()
        with self._lock:
            row = self._find_by_lease_id(lease.lease_id)
            self._assert_active_lease(row, lease, now)
            row["heartbeatAt"] = now
            row["leaseUntil"] = now + timedelta(seconds=normalized_seconds)
            return self._lease_from_row(row)

    def release(self, lease: AsyncEffectSchedulerLease) -> AsyncEffectSchedulerPreview:
        now = self._now()
        with self._lock:
            row = self._find_by_lease_id(lease.lease_id)
            self._assert_active_lease(row, lease, now)
            row.update(state="released", leaseOwner=None, leaseUntil=None, heartbeatAt=None)
            return self._preview_from_row(row)

    def preview_eligible(self, *, limit: int = 20) -> list[AsyncEffectSchedulerPreview]:
        normalized_limit = max(1, min(int(limit), 100))
        now = self._now()
        with self._lock:
            rows = [row for row in self._leases.values() if self._is_claimable(row, now)]
            rows.sort(key=lambda item: (item["schedulerKey"], item["leaseId"]))
            return [self._preview_from_row(row) for row in rows[:normalized_limit]]

    @staticmethod
    def _is_claimable(row: Mapping[str, Any], now: datetime) -> bool:
        if row["state"] == "available":
            return True
        return row["state"] == "leased" and row["leaseUntil"] is not None and row["leaseUntil"] <= now

    def _find_by_lease_id(self, lease_id: str) -> Optional[dict[str, Any]]:
        for row in self._leases.values():
            if row["leaseId"] == lease_id:
                return row
        return None

    @staticmethod
    def _assert_active_lease(
        row: Optional[Mapping[str, Any]],
        lease: AsyncEffectSchedulerLease,
        now: datetime,
    ) -> None:
        if row is None:
            raise AsyncEffectSchedulerLeaseLost("scheduler lease no longer exists")
        if (
            row.get("state") != "leased"
            or row.get("leaseOwner") != lease.scheduler_id
            or int(row.get("attempt") or 0) != lease.attempt
            or row.get("leaseUntil") is None
            or row["leaseUntil"] <= now
        ):
            raise AsyncEffectSchedulerLeaseLost("scheduler lease is no longer current")

    @staticmethod
    def _registration_from_row(
        row: Mapping[str, Any],
        *,
        outcome: str,
    ) -> AsyncEffectSchedulerLeaseRegistration:
        return AsyncEffectSchedulerLeaseRegistration(
            outcome=outcome,
            lease_id=str(row["leaseId"]),
            operation_id=str(row["operationId"]),
            scheduler_key=str(row["schedulerKey"]),
            state=str(row["state"]),
        )

    @staticmethod
    def _lease_from_row(row: Mapping[str, Any]) -> AsyncEffectSchedulerLease:
        return AsyncEffectSchedulerLease(
            lease_id=str(row["leaseId"]),
            operation_id=str(row["operationId"]),
            scheduler_key=str(row["schedulerKey"]),
            scheduler_id=str(row["leaseOwner"]),
            attempt=int(row["attempt"]),
            lease_until=_utc_iso(row["leaseUntil"]),
            heartbeat_at=_utc_iso(row["heartbeatAt"]),
        )

    @staticmethod
    def _preview_from_row(row: Mapping[str, Any]) -> AsyncEffectSchedulerPreview:
        return AsyncEffectSchedulerPreview(
            lease_id=str(row["leaseId"]),
            operation_id=str(row["operationId"]),
            scheduler_key=str(row["schedulerKey"]),
            state=str(row["state"]),
            attempt=int(row["attempt"]),
        )


class PostgresAsyncEffectSchedulerLeaseRepository:
    """Scheduler lease writer bound to an already-open Postgres Unit of Work."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def register(
        self,
        intent: AsyncEffectIntent,
        *,
        scheduler_key: str,
    ) -> AsyncEffectSchedulerLeaseRegistration:
        if not isinstance(intent, AsyncEffectIntent):
            raise TypeError("intent is required")
        normalized_key = _normalize_identifier(scheduler_key, field="scheduler_key")
        target = intent.target
        with self._cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO async_effects.scheduler_leases (
                    lease_id, operation_id, owner_subject_id, vault_id, resource_type,
                    resource_id, resource_version, purpose, authority_epoch, stable_key,
                    scheduler_key, state, attempt
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'available', 0)
                ON CONFLICT (scheduler_key, operation_id) DO NOTHING
                RETURNING lease_id, operation_id, scheduler_key, state, stable_key
                """,
                (
                    _lease_id(intent, normalized_key),
                    intent.operation_id,
                    target.owner_subject_id,
                    target.vault_id,
                    target.resource_type,
                    target.resource_id,
                    target.resource_version,
                    target.purpose,
                    target.authority_epoch,
                    intent.stable_key,
                    normalized_key,
                ),
            )
            row = cursor.fetchone()
            if row is not None:
                return self._registration_from_row(row, outcome="accepted")
            cursor.execute(
                """
                SELECT lease_id, operation_id, scheduler_key, state, stable_key
                FROM async_effects.scheduler_leases
                WHERE scheduler_key = %s AND operation_id = %s
                FOR UPDATE
                """,
                (normalized_key, intent.operation_id),
            )
            existing = cursor.fetchone()
            if existing is None:
                raise RuntimeError("scheduler lease insert did not produce a row")
            if str(existing["stable_key"]) != intent.stable_key:
                raise AsyncEffectConflict("scheduler lease cannot reuse an operation with new meaning")
            return self._registration_from_row(existing, outcome="deduplicated")

    def claim_next(
        self,
        *,
        scheduler_id: str,
        lease_seconds: int,
        supported_scheduler_keys: Iterable[str],
    ) -> Optional[AsyncEffectSchedulerLease]:
        normalized_scheduler_id = _normalize_identifier(scheduler_id, field="scheduler_id")
        normalized_seconds = _normalize_lease_seconds(lease_seconds)
        normalized_keys = _normalize_scheduler_keys(supported_scheduler_keys)
        with self._cursor() as cursor:
            cursor.execute(
                """
                WITH candidate AS (
                    SELECT lease_id
                    FROM async_effects.scheduler_leases
                    WHERE scheduler_key = ANY(%s)
                      AND (
                          state = 'available'
                          OR (state = 'leased' AND lease_until <= NOW())
                      )
                    ORDER BY scheduler_key ASC, created_at ASC, lease_id ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE async_effects.scheduler_leases AS lease
                SET state = 'leased',
                    attempt = lease.attempt + 1,
                    lease_owner = %s,
                    lease_until = NOW() + (%s * INTERVAL '1 second'),
                    heartbeat_at = NOW(),
                    updated_at = NOW()
                FROM candidate
                WHERE lease.lease_id = candidate.lease_id
                RETURNING lease.lease_id, lease.operation_id, lease.scheduler_key, lease.attempt,
                    lease.lease_owner, lease.lease_until, lease.heartbeat_at
                """,
                (list(normalized_keys), normalized_scheduler_id, normalized_seconds),
            )
            row = cursor.fetchone()
            return None if row is None else self._lease_from_row(row)

    def heartbeat(
        self,
        lease: AsyncEffectSchedulerLease,
        *,
        lease_seconds: int,
    ) -> AsyncEffectSchedulerLease:
        normalized_seconds = _normalize_lease_seconds(lease_seconds)
        with self._cursor() as cursor:
            cursor.execute(
                """
                UPDATE async_effects.scheduler_leases
                SET heartbeat_at = NOW(),
                    lease_until = NOW() + (%s * INTERVAL '1 second'),
                    updated_at = NOW()
                WHERE lease_id = %s
                  AND state = 'leased'
                  AND lease_owner = %s
                  AND attempt = %s
                  AND lease_until > NOW()
                RETURNING lease_id, operation_id, scheduler_key, attempt,
                    lease_owner, lease_until, heartbeat_at
                """,
                (normalized_seconds, lease.lease_id, lease.scheduler_id, lease.attempt),
            )
            row = cursor.fetchone()
            if row is None:
                raise AsyncEffectSchedulerLeaseLost("scheduler lease is no longer current")
            return self._lease_from_row(row)

    def release(self, lease: AsyncEffectSchedulerLease) -> AsyncEffectSchedulerPreview:
        with self._cursor() as cursor:
            cursor.execute(
                """
                UPDATE async_effects.scheduler_leases
                SET state = 'released',
                    lease_owner = NULL,
                    lease_until = NULL,
                    heartbeat_at = NULL,
                    updated_at = NOW()
                WHERE lease_id = %s
                  AND state = 'leased'
                  AND lease_owner = %s
                  AND attempt = %s
                  AND lease_until > NOW()
                RETURNING lease_id, operation_id, scheduler_key, state, attempt
                """,
                (lease.lease_id, lease.scheduler_id, lease.attempt),
            )
            row = cursor.fetchone()
            if row is None:
                raise AsyncEffectSchedulerLeaseLost("scheduler lease is no longer current")
            return self._preview_from_row(row)

    def preview_eligible(self, *, limit: int = 20) -> list[AsyncEffectSchedulerPreview]:
        normalized_limit = max(1, min(int(limit), 100))
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT lease_id, operation_id, scheduler_key, state, attempt
                FROM async_effects.scheduler_leases
                WHERE state = 'available'
                   OR (state = 'leased' AND lease_until <= NOW())
                ORDER BY scheduler_key ASC, created_at ASC, lease_id ASC
                LIMIT %s
                """,
                (normalized_limit,),
            )
            return [self._preview_from_row(row) for row in cursor.fetchall()]

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)

    @staticmethod
    def _registration_from_row(
        row: Mapping[str, Any],
        *,
        outcome: str,
    ) -> AsyncEffectSchedulerLeaseRegistration:
        return AsyncEffectSchedulerLeaseRegistration(
            outcome=outcome,
            lease_id=str(row["lease_id"]),
            operation_id=str(row["operation_id"]),
            scheduler_key=str(row["scheduler_key"]),
            state=str(row["state"]),
        )

    @staticmethod
    def _lease_from_row(row: Mapping[str, Any]) -> AsyncEffectSchedulerLease:
        return AsyncEffectSchedulerLease(
            lease_id=str(row["lease_id"]),
            operation_id=str(row["operation_id"]),
            scheduler_key=str(row["scheduler_key"]),
            scheduler_id=str(row["lease_owner"]),
            attempt=int(row["attempt"]),
            lease_until=_utc_iso(row["lease_until"]),
            heartbeat_at=_utc_iso(row["heartbeat_at"]),
        )

    @staticmethod
    def _preview_from_row(row: Mapping[str, Any]) -> AsyncEffectSchedulerPreview:
        return AsyncEffectSchedulerPreview(
            lease_id=str(row["lease_id"]),
            operation_id=str(row["operation_id"]),
            scheduler_key=str(row["scheduler_key"]),
            state=str(row["state"]),
            attempt=int(row["attempt"]),
        )


__all__ = [
    "AsyncEffectSchedulerLease",
    "AsyncEffectSchedulerLeaseError",
    "AsyncEffectSchedulerLeaseLost",
    "AsyncEffectSchedulerLeaseRegistration",
    "AsyncEffectSchedulerPreview",
    "InMemoryAsyncEffectSchedulerLeaseRepository",
    "PostgresAsyncEffectSchedulerLeaseRepository",
]
