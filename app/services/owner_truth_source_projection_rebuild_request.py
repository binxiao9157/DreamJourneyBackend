"""Lease-based recovery for source-triggered private projection rebuilds.

The request contains only authority coordinates and opaque identifiers. It is
consumed by the existing memory projection worker and never changes Source,
Candidate, Memory, or MemoryVersion rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from threading import RLock
from typing import Any, Callable, Optional


_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_LEASE_EXHAUSTED_ERROR_CODE = (
    "sourceProjectionRebuildLeaseExpiredAttemptsExhausted"
)


class OwnerTruthSourceProjectionRebuildRequestError(RuntimeError):
    pass


class OwnerTruthSourceProjectionRebuildLeaseLost(
    OwnerTruthSourceProjectionRebuildRequestError
):
    pass


def _positive_int(value: object, *, field: str, maximum: int = 86_400) -> int:
    if isinstance(value, bool):
        raise OwnerTruthSourceProjectionRebuildRequestError(
            f"{field} must be a positive integer"
        )
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise OwnerTruthSourceProjectionRebuildRequestError(
            f"{field} must be a positive integer"
        ) from exc
    if normalized < 1 or normalized > maximum:
        raise OwnerTruthSourceProjectionRebuildRequestError(
            f"{field} must be between 1 and {maximum}"
        )
    return normalized


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(normalized):
        raise OwnerTruthSourceProjectionRebuildRequestError(
            f"{field} must be an opaque identifier"
        )
    return normalized


def _iso(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


@dataclass(frozen=True)
class OwnerTruthSourceProjectionRebuildRequest:
    request_id: int
    vault_id: str
    owner_subject_id: str
    source_id: str
    source_version: int
    authority_epoch: int
    memory_revision: int
    rights_revision: int
    attempt: int
    max_attempts: int
    state: str = "pending"
    available_at: str | None = None
    lease_owner: str | None = None
    lease_until: str | None = None
    heartbeat_at: str | None = None
    last_error_code: str | None = None


class InMemoryOwnerTruthSourceProjectionRebuildRequestRepository:
    def __init__(self, *, now: Optional[Callable[[], datetime]] = None) -> None:
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._rows: dict[int, dict[str, Any]] = {}
        self._lock = RLock()

    def seed(self, request: OwnerTruthSourceProjectionRebuildRequest) -> None:
        now = self._now()
        with self._lock:
            self._rows[request.request_id] = {
                **request.__dict__,
                "available_at": now,
                "lease_until": None,
                "heartbeat_at": None,
                "completed_at": None,
            }

    def authority_is_current(
        self, lease: OwnerTruthSourceProjectionRebuildRequest
    ) -> bool:
        return bool(
            lease.vault_id
            and lease.owner_subject_id
            and lease.authority_epoch > 0
        )

    def claim_next(
        self, *, worker_id: str, lease_seconds: int
    ) -> OwnerTruthSourceProjectionRebuildRequest | None:
        worker = _identifier(worker_id, field="worker_id")
        seconds = _positive_int(lease_seconds, field="lease_seconds", maximum=3_600)
        now = self._now()
        with self._lock:
            while True:
                eligible = [
                    row
                    for row in self._rows.values()
                    if (
                        row["state"] == "pending" and row["available_at"] <= now
                    )
                    or (
                        row["state"] == "processing"
                        and row["lease_until"] is not None
                        and row["lease_until"] <= now
                    )
                ]
                if not eligible:
                    return None
                row = min(
                    eligible,
                    key=lambda item: (item["available_at"], item["request_id"]),
                )
                if int(row["attempt"]) >= int(row["max_attempts"]):
                    row.update(
                        state="blocked",
                        available_at=now,
                        lease_owner=None,
                        lease_until=None,
                        heartbeat_at=None,
                        last_error_code=_LEASE_EXHAUSTED_ERROR_CODE,
                    )
                    continue
                row.update(
                    state="processing",
                    attempt=int(row["attempt"]) + 1,
                    lease_owner=worker,
                    lease_until=now + timedelta(seconds=seconds),
                    heartbeat_at=now,
                )
                return self._from_row(row)

    def heartbeat(
        self,
        lease: OwnerTruthSourceProjectionRebuildRequest,
        *,
        lease_seconds: int,
    ) -> OwnerTruthSourceProjectionRebuildRequest:
        seconds = _positive_int(lease_seconds, field="lease_seconds", maximum=3_600)
        now = self._now()
        with self._lock:
            row = self._active_row(lease, now=now)
            row["heartbeat_at"] = now
            row["lease_until"] = now + timedelta(seconds=seconds)
            return self._from_row(row)

    def complete(
        self, lease: OwnerTruthSourceProjectionRebuildRequest
    ) -> OwnerTruthSourceProjectionRebuildRequest:
        now = self._now()
        with self._lock:
            row = self._active_row(lease, now=now)
            row.update(
                state="completed",
                lease_owner=None,
                lease_until=None,
                heartbeat_at=None,
                completed_at=now,
                last_error_code=None,
            )
            return self._from_row(row)

    def release_retryable(
        self,
        lease: OwnerTruthSourceProjectionRebuildRequest,
        *,
        retry_seconds: int,
        error_code: str,
    ) -> OwnerTruthSourceProjectionRebuildRequest:
        seconds = _positive_int(retry_seconds, field="retry_seconds")
        code = _identifier(error_code, field="error_code")
        now = self._now()
        with self._lock:
            row = self._active_row(lease, now=now)
            exhausted = int(row["attempt"]) >= int(row["max_attempts"])
            row.update(
                state="blocked" if exhausted else "pending",
                available_at=now if exhausted else now + timedelta(seconds=seconds),
                lease_owner=None,
                lease_until=None,
                heartbeat_at=None,
                last_error_code=code,
            )
            return self._from_row(row)

    def block(
        self,
        lease: OwnerTruthSourceProjectionRebuildRequest,
        *,
        error_code: str,
    ) -> OwnerTruthSourceProjectionRebuildRequest:
        code = _identifier(error_code, field="error_code")
        now = self._now()
        with self._lock:
            row = self._active_row(lease, now=now)
            row.update(
                state="blocked",
                lease_owner=None,
                lease_until=None,
                heartbeat_at=None,
                last_error_code=code,
            )
            return self._from_row(row)

    def _active_row(
        self,
        lease: OwnerTruthSourceProjectionRebuildRequest,
        *,
        now: datetime,
    ) -> dict[str, Any]:
        row = self._rows.get(lease.request_id)
        if (
            row is None
            or row["state"] != "processing"
            or row["lease_owner"] != lease.lease_owner
            or int(row["attempt"]) != lease.attempt
            or row["lease_until"] is None
            or row["lease_until"] <= now
        ):
            raise OwnerTruthSourceProjectionRebuildLeaseLost(
                "source projection rebuild lease is no longer current"
            )
        return row

    @staticmethod
    def _from_row(row: dict[str, Any]) -> OwnerTruthSourceProjectionRebuildRequest:
        values = dict(row)
        values.pop("completed_at", None)
        for field in ("available_at", "lease_until", "heartbeat_at"):
            values[field] = _iso(values.get(field))
        return OwnerTruthSourceProjectionRebuildRequest(**values)


class PostgresOwnerTruthSourceProjectionRebuildRequestRepository:
    """Request writer bound to an already-open Postgres Unit of Work."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def claim_next(
        self, *, worker_id: str, lease_seconds: int
    ) -> OwnerTruthSourceProjectionRebuildRequest | None:
        worker = _identifier(worker_id, field="worker_id")
        seconds = _positive_int(lease_seconds, field="lease_seconds", maximum=3_600)
        while True:
            with self._cursor() as cursor:
                cursor.execute(
                    """
                    WITH candidate AS (
                        SELECT request_id
                        FROM owner_truth.source_projection_rebuild_requests
                        WHERE (
                            state = 'pending' AND available_at <= NOW()
                        ) OR (
                            state = 'processing' AND lease_until <= NOW()
                        )
                        ORDER BY available_at, request_id
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    UPDATE owner_truth.source_projection_rebuild_requests AS request
                    SET state = CASE
                            WHEN request.attempt >= request.max_attempts
                                THEN 'blocked'
                            ELSE 'processing'
                        END,
                        attempt = CASE
                            WHEN request.attempt >= request.max_attempts
                                THEN request.attempt
                            ELSE request.attempt + 1
                        END,
                        available_at = CASE
                            WHEN request.attempt >= request.max_attempts
                                THEN NOW()
                            ELSE request.available_at
                        END,
                        lease_owner = CASE
                            WHEN request.attempt >= request.max_attempts THEN NULL
                            ELSE %s
                        END,
                        lease_until = CASE
                            WHEN request.attempt >= request.max_attempts THEN NULL
                            ELSE NOW() + (%s * INTERVAL '1 second')
                        END,
                        heartbeat_at = CASE
                            WHEN request.attempt >= request.max_attempts THEN NULL
                            ELSE NOW()
                        END,
                        last_error_code = CASE
                            WHEN request.attempt >= request.max_attempts THEN %s
                            ELSE request.last_error_code
                        END,
                        updated_at = NOW()
                    FROM candidate
                    WHERE request.request_id = candidate.request_id
                    RETURNING request.*
                    """,
                    (worker, seconds, _LEASE_EXHAUSTED_ERROR_CODE),
                )
                row = cursor.fetchone()
            if row is None:
                return None
            claimed = self._from_row(row)
            if claimed.state == "processing":
                return claimed

    def heartbeat(
        self,
        lease: OwnerTruthSourceProjectionRebuildRequest,
        *,
        lease_seconds: int,
    ) -> OwnerTruthSourceProjectionRebuildRequest:
        seconds = _positive_int(lease_seconds, field="lease_seconds", maximum=3_600)
        return self._update_active(
            lease,
            """
            UPDATE owner_truth.source_projection_rebuild_requests
            SET lease_until = NOW() + (%s * INTERVAL '1 second'),
                heartbeat_at = NOW(), updated_at = NOW()
            WHERE request_id = %s AND state = 'processing'
              AND lease_owner = %s AND attempt = %s AND lease_until > NOW()
            RETURNING *
            """,
            (seconds, lease.request_id, lease.lease_owner, lease.attempt),
        )

    def complete(
        self, lease: OwnerTruthSourceProjectionRebuildRequest
    ) -> OwnerTruthSourceProjectionRebuildRequest:
        return self._update_active(
            lease,
            """
            UPDATE owner_truth.source_projection_rebuild_requests
            SET state = 'completed', completed_at = NOW(), last_error_code = NULL,
                lease_owner = NULL, lease_until = NULL, heartbeat_at = NULL,
                updated_at = NOW()
            WHERE request_id = %s AND state = 'processing'
              AND lease_owner = %s AND attempt = %s AND lease_until > NOW()
            RETURNING *
            """,
            (lease.request_id, lease.lease_owner, lease.attempt),
        )

    def release_retryable(
        self,
        lease: OwnerTruthSourceProjectionRebuildRequest,
        *,
        retry_seconds: int,
        error_code: str,
    ) -> OwnerTruthSourceProjectionRebuildRequest:
        seconds = _positive_int(retry_seconds, field="retry_seconds")
        code = _identifier(error_code, field="error_code")
        return self._update_active(
            lease,
            """
            UPDATE owner_truth.source_projection_rebuild_requests
            SET state = CASE WHEN attempt >= max_attempts THEN 'blocked' ELSE 'pending' END,
                available_at = CASE
                    WHEN attempt >= max_attempts THEN NOW()
                    ELSE NOW() + (%s * INTERVAL '1 second')
                END,
                last_error_code = %s,
                lease_owner = NULL, lease_until = NULL, heartbeat_at = NULL,
                updated_at = NOW()
            WHERE request_id = %s AND state = 'processing'
              AND lease_owner = %s AND attempt = %s AND lease_until > NOW()
            RETURNING *
            """,
            (seconds, code, lease.request_id, lease.lease_owner, lease.attempt),
        )

    def block(
        self,
        lease: OwnerTruthSourceProjectionRebuildRequest,
        *,
        error_code: str,
    ) -> OwnerTruthSourceProjectionRebuildRequest:
        code = _identifier(error_code, field="error_code")
        return self._update_active(
            lease,
            """
            UPDATE owner_truth.source_projection_rebuild_requests
            SET state = 'blocked', last_error_code = %s,
                lease_owner = NULL, lease_until = NULL, heartbeat_at = NULL,
                updated_at = NOW()
            WHERE request_id = %s AND state = 'processing'
              AND lease_owner = %s AND attempt = %s AND lease_until > NOW()
            RETURNING *
            """,
            (code, lease.request_id, lease.lease_owner, lease.attempt),
        )

    def authority_is_current(
        self, lease: OwnerTruthSourceProjectionRebuildRequest
    ) -> bool:
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM owner_truth.vaults
                WHERE vault_id = %s AND owner_subject_id = %s
                  AND authority_epoch = %s AND status = 'active'
                """,
                (lease.vault_id, lease.owner_subject_id, lease.authority_epoch),
            )
            return cursor.fetchone() is not None

    def _update_active(
        self,
        lease: OwnerTruthSourceProjectionRebuildRequest,
        statement: str,
        params: tuple[object, ...],
    ) -> OwnerTruthSourceProjectionRebuildRequest:
        with self._cursor() as cursor:
            cursor.execute(statement, params)
            row = cursor.fetchone()
            if row is None:
                raise OwnerTruthSourceProjectionRebuildLeaseLost(
                    "source projection rebuild lease is no longer current"
                )
            return self._from_row(row)

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)

    @staticmethod
    def _from_row(row: Any) -> OwnerTruthSourceProjectionRebuildRequest:
        return OwnerTruthSourceProjectionRebuildRequest(
            request_id=int(row["request_id"]),
            vault_id=str(row["vault_id"]),
            owner_subject_id=str(row["owner_subject_id"]),
            source_id=str(row["source_id"]),
            source_version=int(row["source_version"]),
            authority_epoch=int(row["authority_epoch"]),
            memory_revision=int(row["memory_revision"]),
            rights_revision=int(row["rights_revision"]),
            attempt=int(row["attempt"]),
            max_attempts=int(row["max_attempts"]),
            state=str(row["state"]),
            available_at=_iso(row.get("available_at")),
            lease_owner=(str(row["lease_owner"]) if row.get("lease_owner") else None),
            lease_until=_iso(row.get("lease_until")),
            heartbeat_at=_iso(row.get("heartbeat_at")),
            last_error_code=(
                str(row["last_error_code"]) if row.get("last_error_code") else None
            ),
        )


__all__ = [
    "InMemoryOwnerTruthSourceProjectionRebuildRequestRepository",
    "OwnerTruthSourceProjectionRebuildLeaseLost",
    "OwnerTruthSourceProjectionRebuildRequest",
    "OwnerTruthSourceProjectionRebuildRequestError",
    "PostgresOwnerTruthSourceProjectionRebuildRequestRepository",
]
