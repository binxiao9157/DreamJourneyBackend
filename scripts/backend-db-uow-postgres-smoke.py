#!/usr/bin/env python3
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from app.core.config import settings
from app.db.pool import ConnectionPoolExhausted
from app.services.postgres_store import PostgresStore


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def work_unit(store, barrier, label):
    with store.request_unit_of_work(
        correlation_id=f"concurrent-{label}-{uuid.uuid4().hex}",
        command_id="postgresConcurrencySmoke",
    ):
        identity = store._fetchone(
            "SELECT pg_backend_pid() AS backend_pid, txid_current() AS transaction_id"
        )
        barrier.wait(timeout=5)
        store._fetchone("SELECT pg_sleep(0.05) AS waited")
        return identity


def main():
    dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(dsn, "DATABASE_URL is required")

    concurrent_store = PostgresStore(
        dsn=dsn,
        pool_min_size=2,
        pool_max_size=2,
        pool_timeout_seconds=0.25,
    )
    concurrent_store.open_pool(wait=True)
    try:
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            identities = list(
                executor.map(
                    lambda label: work_unit(concurrent_store, barrier, label),
                    ("a", "b"),
                )
            )
        require(
            len({row["backend_pid"] for row in identities}) == 2,
            "concurrent work units must use exclusive pooled connections",
        )
        require(
            len({row["transaction_id"] for row in identities}) == 2,
            "concurrent work units must use isolated transactions",
        )
        concurrent_metrics = concurrent_store.uow_metrics()
        require(concurrent_metrics["committed"] == 2, "concurrent commits")
        require(concurrent_metrics["active"] == 0, "concurrent work units returned")
    finally:
        concurrent_store.close_pool()

    recovery_store = PostgresStore(
        dsn=dsn,
        pool_min_size=1,
        pool_max_size=1,
        pool_timeout_seconds=0.2,
    )
    recovery_store.open_pool(wait=True)
    try:
        statement_failed = False
        try:
            with recovery_store.request_unit_of_work(
                correlation_id=f"failure-{uuid.uuid4().hex}",
                command_id="postgresFailureSmoke",
            ):
                recovery_store._fetchone("SELECT 1 / 0 AS value")
        except Exception:
            statement_failed = True
        require(statement_failed, "failing statement must surface")

        with recovery_store.request_unit_of_work(
            correlation_id=f"recovery-{uuid.uuid4().hex}",
            command_id="postgresRecoverySmoke",
        ):
            healthy = recovery_store._fetchone("SELECT 1 AS value")
        require(healthy == {"value": 1}, "connection must be healthy after rollback")

        held_connection = recovery_store._pool.getconn(timeout=0.2)
        pool_exhausted = False
        try:
            try:
                with recovery_store.request_unit_of_work(
                    correlation_id=f"exhaustion-{uuid.uuid4().hex}",
                    command_id="postgresPoolExhaustionSmoke",
                ):
                    pass
            except ConnectionPoolExhausted:
                pool_exhausted = True
        finally:
            recovery_store._pool.putconn(held_connection)
        require(pool_exhausted, "pool exhaustion must fail closed")

        recovery_metrics = recovery_store.uow_metrics()
        require(recovery_metrics["rolledBack"] >= 1, "failed statement rollback")
        require(recovery_metrics["committed"] >= 1, "healthy request commit")
        require(recovery_metrics["poolExhausted"] == 1, "pool exhaustion metric")
        require(recovery_metrics["active"] == 0, "all recovery work units returned")
        require(
            recovery_metrics["connectionReturnFailures"] == 0,
            "connection return failures",
        )
    finally:
        recovery_store.close_pool()

    print(
        json.dumps(
            {
                "status": "passed",
                "schemaVersion": 1,
                "concurrentWorkUnits": 2,
                "exclusiveBackendConnections": True,
                "isolatedTransactions": True,
                "abortedTransactionRecovered": True,
                "poolExhaustionFailClosed": True,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
