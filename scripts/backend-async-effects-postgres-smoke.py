#!/usr/bin/env python3
"""Exercise the V1 async-effect kernel in an isolated temporary database.

The script never enqueues a real business task or provider request. It creates
its own database, applies all migrations, validates coordination constraints,
then drops the database again.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import os
import sys
import uuid
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.async_effects.contracts import AsyncEffectConflict, AsyncEffectIntent, AsyncEffectTarget
from app.async_effects.lease_repository import AsyncEffectLeaseCancelled, AsyncEffectLeaseLost
from app.core.config import settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.domain.owner_truth.source_commands import CreateTextSourceCommand, OwnerTruthCommandContext
from app.services.owner_truth_source import OwnerTruthSourceAsyncEffectCommandService
from app.services.postgres_store import PostgresStore


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def payload_hash(value: str) -> str:
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


def expect_rejected(dsn: str, operation, message: str) -> None:
    rejected = False
    try:
        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                operation(cursor)
    except Exception:
        rejected = True
    require(rejected, message)


def revert_terminal_operation(cursor, operation_id: str) -> None:
    """Attempt an illegal terminal-to-nonterminal transition in one transaction."""
    cursor.execute(
        "UPDATE async_effects.operations SET state = 'completed' WHERE operation_id = %s",
        (operation_id,),
    )
    cursor.execute(
        "UPDATE async_effects.operations SET state = 'accepted' WHERE operation_id = %s",
        (operation_id,),
    )


def count_rows(dsn: str, relation: str, *, operation_id: str) -> int:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("SELECT COUNT(*) FROM {} WHERE operation_id = %s").format(
                    sql.Identifier("async_effects", relation)
                ),
                (operation_id,),
            )
            return int(cursor.fetchone()[0])


def count_source_rows(dsn: str, *, vault_id: str, source_id: str) -> int:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM owner_truth.sources WHERE vault_id = %s AND id = %s",
                (vault_id, source_id),
            )
            return int(cursor.fetchone()[0])


def count_effect_resource_rows(dsn: str, *, vault_id: str, resource_id: str) -> int:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM async_effects.operations
                WHERE vault_id = %s AND resource_id = %s
                """,
                (vault_id, resource_id),
            )
            return int(cursor.fetchone()[0])


def job_attempt_states(dsn: str, *, job_id: str) -> dict[int, str]:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT attempt, state
                FROM async_effects.job_attempts
                WHERE job_id = %s
                ORDER BY attempt ASC
                """,
                (job_id,),
            )
            return {int(attempt): str(state) for attempt, state in cursor.fetchall()}


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_async_effects_smoke_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)

    intent = AsyncEffectIntent(
        operation_type="timeLetter.delivery",
        target=AsyncEffectTarget(
            owner_subject_id="owner-async-smoke",
            vault_id="vault-async-smoke",
            resource_type="timeLetter",
            resource_id="letter-async-smoke",
            resource_version=1,
            purpose="delivery",
            authority_epoch=0,
        ),
        payload_hash=payload_hash("time-letter-metadata-only"),
    )

    store: PostgresStore | None = None
    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="async-effects-g2",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        applied = migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")
        require(applied["appliedVersions"][-1] == "0013", "async effect migration must apply")

        store = PostgresStore(dsn=test_dsn, pool_min_size=1, pool_max_size=4)
        store.open_pool(wait=True)

        def accept_once(index: int) -> str:
            with store.request_unit_of_work(
                correlation_id=f"async-effect-smoke-{index}",
                command_id="asyncEffectSmokeCommand",
            ):
                return store.effect_kernel_repository().accept(intent).outcome

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = set(executor.map(accept_once, (1, 2)))
        require(outcomes == {"accepted", "deduplicated"}, "same stable key must be idempotent")

        for relation in ("operations", "outbox_events", "jobs", "business_receipts"):
            require(
                count_rows(test_dsn, relation, operation_id=intent.operation_id) == 1,
                f"{relation} must contain one coordination record",
            )

        source_context = OwnerTruthCommandContext(
            vault_id="vault-source-effect-smoke",
            owner_subject_id="owner-source-effect-smoke",
            actor_subject_id="owner-source-effect-smoke",
        )
        source_command = CreateTextSourceCommand(
            command_id="source-effect-smoke-command",
            source_id=str(uuid.uuid4()),
            expected_version=0,
            text="Synthetic source for atomic outbox verification.",
            metadata={"origin": "asyncEffectPostgresSmoke"},
        )
        source_effect_service = OwnerTruthSourceAsyncEffectCommandService(store)
        source_created = source_effect_service.create_text_source(
            command=source_command,
            context=source_context,
        )
        source_replayed = source_effect_service.create_text_source(
            command=source_command,
            context=source_context,
        )
        require(source_created.source.outcome == "created", "source effect command must create once")
        require(source_replayed.source.outcome == "deduplicated", "source command replay must dedupe")
        require(source_created.effect.outcome == "accepted", "source effect must accept once")
        require(source_replayed.effect.outcome == "deduplicated", "source effect replay must dedupe")
        require(
            count_source_rows(
                test_dsn,
                vault_id=source_context.vault_id,
                source_id=source_command.source_id,
            )
            == 1,
            "source must persist exactly once with its effect",
        )
        require(
            count_rows(
                test_dsn,
                "outbox_events",
                operation_id=source_created.effect.operation_id,
            )
            == 1,
            "source effect must create exactly one outbox event",
        )

        rollback_source_command = CreateTextSourceCommand(
            command_id="source-effect-rollback-command",
            source_id=str(uuid.uuid4()),
            expected_version=0,
            text="Synthetic source that must roll back with its effect.",
            metadata={"origin": "asyncEffectPostgresSmoke"},
        )
        try:
            with store.request_unit_of_work(
                correlation_id="async-effect-source-rollback",
                command_id="asyncEffectSourceRollback",
            ):
                source_effect_service.create_text_source(
                    command=rollback_source_command,
                    context=source_context,
                )
                raise RuntimeError("force source effect rollback")
        except RuntimeError:
            pass
        require(
            count_source_rows(
                test_dsn,
                vault_id=source_context.vault_id,
                source_id=rollback_source_command.source_id,
            )
            == 0,
            "source must roll back when its effect request cannot commit",
        )
        require(
            count_effect_resource_rows(
                test_dsn,
                vault_id=source_context.vault_id,
                resource_id=rollback_source_command.source_id,
            )
            == 0,
            "outbox operation must roll back with its source",
        )

        worker_intent = AsyncEffectIntent(
            operation_type="asyncEffect.synthetic.noop",
            target=AsyncEffectTarget(
                owner_subject_id="owner-worker-smoke",
                vault_id="vault-worker-smoke",
                resource_type="syntheticEffect",
                resource_id="worker-lease-smoke",
                resource_version=1,
                purpose="workerFoundation",
                authority_epoch=0,
            ),
            payload_hash=payload_hash("worker-lease-metadata-only"),
        )
        with store.request_unit_of_work(
            correlation_id="async-effect-worker-seed",
            command_id="asyncEffectWorkerSeed",
        ):
            store.effect_kernel_repository().accept(worker_intent)

        def claim_worker_job(worker_id: str):
            with store.request_unit_of_work(
                correlation_id=f"async-effect-worker-claim-{worker_id}",
                command_id="asyncEffectWorkerClaim",
            ):
                return store.async_effect_lease_repository().claim_next(
                    worker_id=worker_id,
                    lease_seconds=30,
                    supported_job_types=[worker_intent.job_type],
                )

        with ThreadPoolExecutor(max_workers=2) as executor:
            worker_claims = list(executor.map(claim_worker_job, ("worker-a", "worker-b")))
        active_claims = [claim for claim in worker_claims if claim is not None]
        require(len(active_claims) == 1, "only one worker may claim the same job")
        first_claim = active_claims[0]
        with store.request_unit_of_work(
            correlation_id="async-effect-worker-heartbeat",
            command_id="asyncEffectWorkerHeartbeat",
        ):
            renewed_claim = store.async_effect_lease_repository().heartbeat(
                first_claim,
                lease_seconds=30,
            )
        require(renewed_claim.attempt == 1, "worker heartbeat must retain the active attempt")
        with psycopg.connect(test_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE async_effects.jobs SET lease_until = NOW() - INTERVAL '1 second' WHERE job_id = %s",
                    (first_claim.job_id,),
                )
        second_claim = claim_worker_job("worker-c")
        require(second_claim is not None and second_claim.attempt == 2, "expired lease must be reclaimed")
        require(
            job_attempt_states(test_dsn, job_id=first_claim.job_id) == {1: "unknown", 2: "started"},
            "expired claim must become unknown before the replacement attempt starts",
        )
        with store.request_unit_of_work(
            correlation_id="async-effect-worker-stale",
            command_id="asyncEffectWorkerStale",
        ):
            try:
                store.async_effect_lease_repository().heartbeat(first_claim, lease_seconds=30)
                raise AssertionError("stale worker heartbeat must be rejected")
            except AsyncEffectLeaseLost:
                pass
        with store.request_unit_of_work(
            correlation_id="async-effect-worker-cancel",
            command_id="asyncEffectWorkerCancel",
        ):
            cancellation = store.async_effect_lease_repository().request_cancel(second_claim.job_id)
        require(cancellation.outcome == "cancellationRequested", "leased job cancellation must be durable")
        with store.request_unit_of_work(
            correlation_id="async-effect-worker-cancelled-heartbeat",
            command_id="asyncEffectWorkerCancelledHeartbeat",
        ):
            try:
                store.async_effect_lease_repository().heartbeat(second_claim, lease_seconds=30)
                raise AssertionError("cancelled worker heartbeat must be rejected")
            except AsyncEffectLeaseCancelled:
                pass

        try:
            with store.request_unit_of_work(
                correlation_id="async-effect-conflict",
                command_id="asyncEffectConflict",
            ):
                store.effect_kernel_repository().accept(
                    AsyncEffectIntent(
                        operation_type=intent.operation_type,
                        target=intent.target,
                        payload_hash=payload_hash("changed metadata"),
                    )
                )
            raise AssertionError("changed effect payload must conflict")
        except AsyncEffectConflict:
            pass

        rollback_intent = AsyncEffectIntent(
            operation_type="echoReply.deliver",
            target=AsyncEffectTarget(
                owner_subject_id="owner-async-smoke",
                vault_id="vault-async-smoke",
                resource_type="echoReply",
                resource_id="reply-rollback",
                resource_version=1,
                purpose="delayedDelivery",
                authority_epoch=0,
            ),
            payload_hash=payload_hash("rollback-metadata"),
        )
        try:
            with store.request_unit_of_work(
                correlation_id="async-effect-rollback",
                command_id="asyncEffectRollback",
            ):
                store.effect_kernel_repository().accept(rollback_intent)
                raise RuntimeError("force rollback")
        except RuntimeError:
            pass
        require(
            count_rows(test_dsn, "operations", operation_id=rollback_intent.operation_id) == 0,
            "all coordination rows must roll back with the caller UoW",
        )

        expect_rejected(
            test_dsn,
            lambda cursor: revert_terminal_operation(cursor, intent.operation_id),
            "terminal effect state must not revert",
        )
        expect_rejected(
            test_dsn,
            lambda cursor: cursor.execute(
                "UPDATE async_effects.business_receipts SET outcome = 'completed' WHERE operation_id = %s",
                (intent.operation_id,),
            ),
            "business receipts must remain append-only",
        )

        with psycopg.connect(test_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'async_effects'
                    """
                )
                column_names = {str(row[0]) for row in cursor.fetchall()}
        require("payload" not in column_names, "kernel must not persist a payload body column")
        require("credential" not in column_names, "kernel must not persist credential columns")
        require("secret" not in column_names, "kernel must not persist secret columns")

        print(
            "Async effect Postgres smoke passed: "
            f"schemaHead={verified['expectedHead']} outcomes={sorted(outcomes)} "
            "sourceOutbox=true workerLease=true rollback=true terminalGuard=true receiptsAppendOnly=true"
        )
    finally:
        if store is not None:
            store.close_pool()
        drop_database(admin_dsn, database_name)


if __name__ == "__main__":
    main()
