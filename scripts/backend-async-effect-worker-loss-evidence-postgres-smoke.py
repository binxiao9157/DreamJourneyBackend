#!/usr/bin/env python3
"""Disposable Postgres smoke for inert async-effect worker-loss evidence.

The fixture expires one synthetic lease, records only aggregate evidence, and
proves the original job/attempt/operation/outbox stay untouched. It never
starts a worker or performs a Provider action.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import os
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectRuntimeStatus, AsyncEffectTarget
from app.async_effects.worker_loss_evidence import build_async_effect_worker_loss_evidence
from app.core.config import settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.services.postgres_store import PostgresStore


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


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


def build_intent() -> AsyncEffectIntent:
    return AsyncEffectIntent(
        operation_type="asyncEffect.synthetic.workerLossEvidence",
        target=AsyncEffectTarget(
            owner_subject_id="owner-worker-loss-smoke",
            vault_id="vault-worker-loss-smoke",
            resource_type="timeLetter",
            resource_id="worker-loss-evidence-smoke",
            resource_version=1,
            purpose="timeLetterDelivery",
            authority_epoch=0,
        ),
        payload_hash=digest("worker-loss-evidence-payload"),
    )


def seed_expired_lease(store: PostgresStore, dsn: str, intent: AsyncEffectIntent) -> None:
    with store.request_unit_of_work(
        correlation_id=f"worker-loss-accept:{intent.job_id}",
        command_id="workerLossEvidenceAccept",
    ):
        store.effect_kernel_repository().accept(intent)
    with store.request_unit_of_work(
        correlation_id=f"worker-loss-claim:{intent.job_id}",
        command_id="workerLossEvidenceClaim",
    ):
        lease = store.async_effect_lease_repository().claim_next(
            worker_id="worker-loss-smoke-owner",
            lease_seconds=30,
            supported_job_types=[intent.job_type],
        )
        require(lease is not None, "synthetic job must be leased before loss observation")
    # Test fixture only: model a process that died after claiming the job. This
    # write is finished before observation; the evidence repository itself is
    # subsequently required to remain read-only relative to coordination rows.
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE async_effects.jobs
                SET lease_until = NOW() - INTERVAL '5 minutes',
                    heartbeat_at = NOW() - INTERVAL '5 minutes',
                    updated_at = NOW()
                WHERE job_id = %s AND state = 'leased' AND attempt = 1
                RETURNING job_id
                """,
                (intent.job_id,),
            )
            require(cursor.fetchone() is not None, "fixture lease must become expired")


def coordination_snapshot(dsn: str, intent: AsyncEffectIntent) -> tuple[str, int, str, str, str, int]:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT state, attempt FROM async_effects.jobs WHERE job_id = %s",
                (intent.job_id,),
            )
            job = cursor.fetchone()
            cursor.execute(
                "SELECT state FROM async_effects.job_attempts WHERE job_id = %s AND attempt = 1",
                (intent.job_id,),
            )
            attempt = cursor.fetchone()
            cursor.execute(
                "SELECT state FROM async_effects.operations WHERE operation_id = %s",
                (intent.operation_id,),
            )
            operation = cursor.fetchone()
            cursor.execute(
                "SELECT state FROM async_effects.outbox_events WHERE operation_id = %s",
                (intent.operation_id,),
            )
            outbox = cursor.fetchone()
            cursor.execute("SELECT COUNT(*) FROM async_effects.provider_effects")
            provider_effect_count = int(cursor.fetchone()[0])
    require(job is not None and attempt is not None and operation is not None and outbox is not None,
            "synthetic coordination rows must persist")
    return (
        str(job[0]),
        int(job[1]),
        str(attempt[0]),
        str(operation[0]),
        str(outbox[0]),
        provider_effect_count,
    )


def observation_count(dsn: str, observation_id: str) -> int:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM async_effects.worker_loss_observations WHERE observation_id = %s",
                (observation_id,),
            )
            return int(cursor.fetchone()[0])


def expect_rejected(dsn: str, operation, message: str) -> None:
    rejected = False
    try:
        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                operation(cursor)
    except Exception:
        rejected = True
    require(rejected, message)


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_worker_loss_{uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    store: PostgresStore | None = None
    created = False
    try:
        create_database(admin_dsn, database_name)
        created = True
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="worker-loss-evidence-g2",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        applied = migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")
        require(applied["appliedVersions"][-1] == "0028", "worker-loss migration must apply")

        store = PostgresStore(dsn=test_dsn, pool_min_size=1, pool_max_size=4)
        store.open_pool(wait=True)
        intent = build_intent()
        seed_expired_lease(store, test_dsn, intent)
        before = coordination_snapshot(test_dsn, intent)
        observed_at = datetime.now(timezone.utc)
        with store.request_unit_of_work(
            correlation_id="worker-loss-preview",
            command_id="workerLossEvidencePreview",
        ):
            previews = store.async_effect_lease_repository().preview_expired_leases(limit=10)
        require(len(previews) == 1, "exactly one synthetic expired lease must be observed")
        evidence = build_async_effect_worker_loss_evidence(
            runtime_status=AsyncEffectRuntimeStatus(
                enabled=False,
                worker_enabled=False,
                allowed=False,
                reason="asyncEffectV1Disabled",
            ),
            observer_worker_id="worker-loss-observer",
            previews=previews,
            observed_at=observed_at,
            expires_at=observed_at + timedelta(minutes=5),
        )
        summary = evidence.value_free_summary(now=observed_at)
        require(summary["observationState"] == "observed", "expired lease must require observation")
        require(summary["requiresManualReview"] is True, "expired lease must require manual review")
        for forbidden in (intent.job_id, intent.operation_id, "worker-loss-smoke-owner", "owner-worker-loss"):
            require(forbidden not in str(summary), "value-free evidence must not expose raw coordinates")

        def record_once(index: int) -> str:
            with store.request_unit_of_work(
                correlation_id=f"worker-loss-record:{index}",
                command_id=f"workerLossEvidenceRecord{index}",
            ):
                return store.async_effect_worker_loss_observation_repository().record(evidence).outcome

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = set(executor.map(record_once, (1, 2)))
        require(outcomes == {"recorded", "deduplicated"}, "same observation must persist once")
        require(observation_count(test_dsn, evidence.observation_id) == 1, "observation must be append-only")
        with store.request_unit_of_work(
            correlation_id="worker-loss-load",
            command_id="workerLossEvidenceLoad",
        ):
            loaded = store.async_effect_worker_loss_observation_repository().load(evidence.observation_id)
        require(loaded == evidence, "durable worker-loss evidence must reconstruct exactly")
        after = coordination_snapshot(test_dsn, intent)
        require(after == before, "worker-loss evidence must not change job/attempt/operation/outbox/provider state")

        expect_rejected(
            test_dsn,
            lambda cursor: cursor.execute(
                "UPDATE async_effects.worker_loss_observations SET reason_code = 'changed' "
                "WHERE observation_id = %s",
                (evidence.observation_id,),
            ),
            "worker-loss evidence must reject updates",
        )
        expect_rejected(
            test_dsn,
            lambda cursor: cursor.execute(
                "DELETE FROM async_effects.worker_loss_observations WHERE observation_id = %s",
                (evidence.observation_id,),
            ),
            "worker-loss evidence must reject deletes",
        )
        print(
            "Async-effect worker-loss evidence Postgres smoke passed "
            "(expired leases are observed only; worker recovery and Provider calls remain disabled)."
        )
    finally:
        if store is not None:
            store.close_pool()
        if created:
            drop_database(admin_dsn, database_name)


if __name__ == "__main__":
    main()
