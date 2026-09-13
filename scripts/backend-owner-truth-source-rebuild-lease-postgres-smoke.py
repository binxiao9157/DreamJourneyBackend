#!/usr/bin/env python3
"""Verify bounded source-projection rebuild lease recovery in disposable Postgres."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sys
import uuid

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.services.owner_truth_source_projection_rebuild_request import (
    OwnerTruthSourceProjectionRebuildLeaseLost,
    OwnerTruthSourceProjectionRebuildRequest,
    PostgresOwnerTruthSourceProjectionRebuildRequestRepository,
)


LEASE_EXHAUSTED = "sourceProjectionRebuildLeaseExpiredAttemptsExhausted"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def dsn_for_database(base_dsn: str, database_name: str) -> str:
    parameters = conninfo_to_dict(base_dsn)
    parameters["dbname"] = database_name
    return make_conninfo(**parameters)


def create_database(admin_dsn: str, database_name: str) -> None:
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))


def drop_database(admin_dsn: str, database_name: str) -> None:
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (database_name,),
            )
            cursor.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database_name)))


def seed_request(dsn: str, *, label: str, max_attempts: int = 3) -> int:
    suffix = uuid.uuid4().hex[:12]
    owner_subject_id = f"lease-smoke-owner-{label}-{suffix}"
    vault_id = f"lease-smoke-vault-{label}-{suffix}"
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO owner_truth.vaults (vault_id, owner_subject_id) VALUES (%s, %s)",
                (vault_id, owner_subject_id),
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.source_projection_rebuild_requests (
                    vault_id, owner_subject_id, source_id, source_version,
                    authority_epoch, memory_revision, rights_revision,
                    reason_code, max_attempts
                ) VALUES (%s, %s, %s, 1, 1, 1, 1, %s, %s)
                RETURNING request_id
                """,
                (
                    vault_id,
                    owner_subject_id,
                    str(uuid.uuid4()),
                    f"leaseSmoke{label.title()}",
                    max_attempts,
                ),
            )
            request_id = int(cursor.fetchone()[0])
    return request_id


def claim(
    dsn: str,
    *,
    worker_id: str,
    lease_seconds: int = 30,
) -> OwnerTruthSourceProjectionRebuildRequest | None:
    with psycopg.connect(dsn) as connection:
        return PostgresOwnerTruthSourceProjectionRebuildRequestRepository(
            connection
        ).claim_next(worker_id=worker_id, lease_seconds=lease_seconds)


def expire_lease(dsn: str, request_id: int) -> None:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE owner_truth.source_projection_rebuild_requests
                   SET lease_until = NOW() - INTERVAL '1 second', updated_at = NOW()
                 WHERE request_id = %s AND state = 'processing'
                """,
                (request_id,),
            )
            require(cursor.rowcount == 1, "active synthetic lease must be expirable")


def complete(
    dsn: str,
    lease: OwnerTruthSourceProjectionRebuildRequest,
) -> OwnerTruthSourceProjectionRebuildRequest:
    with psycopg.connect(dsn) as connection:
        return PostgresOwnerTruthSourceProjectionRebuildRequestRepository(
            connection
        ).complete(lease)


def row_state(dsn: str, request_id: int) -> tuple[object, ...]:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT state, attempt, max_attempts, last_error_code,
                       lease_owner, lease_until, heartbeat_at
                  FROM owner_truth.source_projection_rebuild_requests
                 WHERE request_id = %s
                """,
                (request_id,),
            )
            row = cursor.fetchone()
    require(row is not None, "synthetic rebuild request must remain durable")
    return row


def verify_repeated_crash_exhaustion(dsn: str) -> None:
    request_id = seed_request(dsn, label="crash-exhaustion")
    leases = []
    for attempt in (1, 2, 3):
        lease = claim(dsn, worker_id=f"lease-smoke-crash-{attempt}")
        require(lease is not None and lease.request_id == request_id, "expected lease")
        require(lease.attempt == attempt, "attempt must increase once per crash recovery")
        leases.append(lease)
        expire_lease(dsn, request_id)

    require(
        claim(dsn, worker_id="lease-smoke-no-attempt-four") is None,
        "an expired final lease must not produce attempt four",
    )
    require(
        row_state(dsn, request_id)
        == ("blocked", 3, 3, LEASE_EXHAUSTED, None, None, None),
        "crash exhaustion must persist a clean blocked terminal state",
    )
    try:
        complete(dsn, leases[-1])
    except OwnerTruthSourceProjectionRebuildLeaseLost:
        pass
    else:
        raise AssertionError("expired final worker must not complete a blocked request")


def verify_success_before_limit(dsn: str) -> None:
    request_id = seed_request(dsn, label="success-before-limit")
    first = claim(dsn, worker_id="lease-smoke-before-limit-1")
    require(first is not None and first.attempt == 1, "first lease must be issued")
    expire_lease(dsn, request_id)
    second = claim(dsn, worker_id="lease-smoke-before-limit-2")
    require(second is not None and second.attempt == 2, "second lease must recover")
    completed = complete(dsn, second)
    require(completed.state == "completed" and completed.attempt == 2, "attempt two must complete")


def verify_final_attempt_can_complete(dsn: str) -> None:
    request_id = seed_request(dsn, label="final-attempt-complete")
    for attempt in (1, 2):
        lease = claim(dsn, worker_id=f"lease-smoke-final-{attempt}")
        require(lease is not None and lease.attempt == attempt, "pre-final lease expected")
        expire_lease(dsn, request_id)
    final = claim(dsn, worker_id="lease-smoke-final-3")
    require(final is not None and final.attempt == 3, "final legal lease must be issued")
    require(
        claim(dsn, worker_id="lease-smoke-final-concurrent") is None,
        "a valid final lease must not be prematurely reclaimed",
    )
    completed = complete(dsn, final)
    require(completed.state == "completed" and completed.attempt == 3, "final attempt must complete")


def verify_concurrent_recovery(dsn: str) -> None:
    request_id = seed_request(dsn, label="concurrent-recovery")
    original = claim(dsn, worker_id="lease-smoke-concurrent-original")
    require(original is not None, "original lease must be issued")
    expire_lease(dsn, request_id)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda worker: claim(dsn, worker_id=worker),
                ("lease-smoke-concurrent-a", "lease-smoke-concurrent-b"),
            )
        )
    recovered = [item for item in results if item is not None]
    require(len(recovered) == 1, "exactly one concurrent worker may recover an expired lease")
    require(recovered[0].attempt == 2, "concurrent recovery must issue attempt two once")
    complete(dsn, recovered[0])


def verify_stale_worker_late_completion(dsn: str) -> None:
    request_id = seed_request(dsn, label="stale-worker")
    stale = claim(dsn, worker_id="lease-smoke-stale")
    require(stale is not None, "stale fixture lease must be issued")
    expire_lease(dsn, request_id)
    current = claim(dsn, worker_id="lease-smoke-current")
    require(current is not None and current.attempt == 2, "current lease must recover")
    try:
        complete(dsn, stale)
    except OwnerTruthSourceProjectionRebuildLeaseLost:
        pass
    else:
        raise AssertionError("stale worker must not complete a newer lease")
    complete(dsn, current)


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", "").strip()
    require(base_dsn, "DATABASE_URL is required")
    database_name = f"dj_source_rebuild_lease_smoke_{uuid.uuid4().hex[:12]}"
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    test_dsn = dsn_for_database(base_dsn, database_name)
    try:
        create_database(admin_dsn, database_name)
        migration = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="source-rebuild-lease-smoke",
            lock_timeout_ms=1_000,
            statement_timeout_ms=30_000,
        ).apply()
        require(migration["appliedHead"] == "0121", "isolated schema must reach 0121")
        verify_repeated_crash_exhaustion(test_dsn)
        verify_success_before_limit(test_dsn)
        verify_final_attempt_can_complete(test_dsn)
        verify_concurrent_recovery(test_dsn)
        verify_stale_worker_late_completion(test_dsn)
        print(
            "owner truth source rebuild lease postgres smoke passed "
            "schemaHead=0121 crashExhaustion=true successBeforeLimit=true "
            "finalAttemptCompletion=true concurrentRecovery=true "
            "staleWorkerFence=true terminalBlocked=true"
        )
    finally:
        try:
            drop_database(admin_dsn, database_name)
        except Exception as exc:  # pragma: no cover - cleanup diagnostics only
            print(f"warning: failed to drop temporary database {database_name}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
