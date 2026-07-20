#!/usr/bin/env python3
"""Exercise default-off dead-letter persistence in a disposable Postgres DB.

The smoke records only opaque identifiers and hashes. It uses a synthetic
blocked job to prove admission, deduplication, immutable evidence, rollback,
and the absence of any replay or Provider execution.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
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

from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectJobState, AsyncEffectTarget
from app.async_effects.dead_letter_effects import DeadLetterCause, admit_dead_letter
from app.async_effects.dead_letter_repository import (
    DeadLetterPersistenceConflict,
    DeadLetterPersistenceSummary,
)
from app.core.config import settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
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


def build_intent(*, resource_id: str = "dead-letter-persistence-smoke") -> AsyncEffectIntent:
    return AsyncEffectIntent(
        operation_type="asyncEffect.synthetic.deadLetterPersistence",
        target=AsyncEffectTarget(
            owner_subject_id="owner-dead-letter-persistence-smoke",
            vault_id="vault-dead-letter-persistence-smoke",
            resource_type="timeLetter",
            resource_id=resource_id,
            resource_version=1,
            purpose="timeLetterDelivery",
            authority_epoch=0,
        ),
        payload_hash=payload_hash(f"dead-letter-persistence:{resource_id}"),
    )


def terminal_blocked_admission(store: PostgresStore, intent: AsyncEffectIntent):
    with store.request_unit_of_work(
        correlation_id=f"dead-letter-persistence-seed:{intent.job_id}",
        command_id="deadLetterPersistenceSeed",
    ):
        store.effect_kernel_repository().accept(intent)
    with store.request_unit_of_work(
        correlation_id=f"dead-letter-persistence-claim:{intent.job_id}",
        command_id="deadLetterPersistenceClaim",
    ):
        lease = store.async_effect_lease_repository().claim_next(
            worker_id="dead-letter-persistence-smoke-worker",
            lease_seconds=30,
            supported_job_types=[intent.job_type],
        )
        require(lease is not None, "synthetic job must be claimable")
    with store.request_unit_of_work(
        correlation_id=f"dead-letter-persistence-block:{intent.job_id}",
        command_id="deadLetterPersistenceBlock",
    ):
        completion = store.async_effect_lease_repository().complete(
            lease,
            outcome="blocked",
            error_code="syntheticPoisonPayload",
        )
        require(completion.job_state == "blocked", "synthetic job must become terminal blocked")
    return admit_dead_letter(
        intent=intent,
        job_state=AsyncEffectJobState.BLOCKED,
        attempt=1,
        max_attempts=1,
        cause=DeadLetterCause.POISON_PAYLOAD,
        failure_hash=payload_hash(f"dead-letter-failure:{intent.job_id}"),
        last_receipt_hash=payload_hash(f"dead-letter-last-receipt:{intent.job_id}"),
    )


def persist(
    store: PostgresStore,
    admission,
    *,
    command_id: str,
) -> DeadLetterPersistenceSummary:
    with store.request_unit_of_work(
        correlation_id=f"dead-letter-persistence-record:{command_id}",
        command_id=command_id,
    ):
        return store.async_effect_dead_letter_repository().record(admission)


def dead_letter_counts(dsn: str, job_id: str) -> tuple[int, int, str | None]:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*), COUNT(last_receipt_hash), MIN(last_receipt_hash)
                FROM async_effects.dead_letters
                WHERE job_id = %s
                """,
                (job_id,),
            )
            count, receipt_count, receipt_hash = cursor.fetchone()
    return int(count), int(receipt_count), None if receipt_hash is None else str(receipt_hash)


def job_evidence(dsn: str, job_id: str) -> tuple[str, int, int]:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT state, attempt FROM async_effects.jobs WHERE job_id = %s",
                (job_id,),
            )
            job = cursor.fetchone()
            cursor.execute(
                "SELECT COUNT(*) FROM async_effects.job_attempts WHERE job_id = %s",
                (job_id,),
            )
            attempt_count = int(cursor.fetchone()[0])
    require(job is not None, "synthetic job must remain durable")
    return str(job[0]), int(job[1]), attempt_count


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_dead_letter_persistence_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)

    store: PostgresStore | None = None
    database_created = False
    try:
        create_database(admin_dsn, database_name)
        database_created = True
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="dead-letter-persistence-g2",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        applied = migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")
        require(applied["appliedVersions"][-1] == "0026", "dead-letter persistence migration must apply")

        store = PostgresStore(dsn=test_dsn, pool_min_size=1, pool_max_size=4)
        store.open_pool(wait=True)
        admission = terminal_blocked_admission(store, build_intent())

        def persist_once(index: int) -> str:
            return persist(
                store,
                admission,
                command_id=f"dead-letter-persistence-record-{index}",
            ).outcome

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = set(executor.map(persist_once, (1, 2)))
        require(outcomes == {"admitted", "deduplicated"}, "same dead letter must persist once")
        require(
            dead_letter_counts(test_dsn, admission.intent.job_id)
            == (1, 1, admission.last_receipt_hash),
            "dead letter must persist one receipt coordinate",
        )

        with store.request_unit_of_work(
            correlation_id="dead-letter-persistence-load",
            command_id="deadLetterPersistenceLoad",
        ):
            loaded = store.async_effect_dead_letter_repository().load(admission.dead_letter_id)
        require(loaded == admission, "durable dead letter must reconstruct its immutable admission")

        conflict = False
        try:
            persist(
                store,
                replace(admission, failure_hash=payload_hash("changed-dead-letter-failure")),
                command_id="deadLetterPersistenceChangedFailure",
            )
        except DeadLetterPersistenceConflict:
            conflict = True
        require(conflict, "same job attempt cannot overwrite immutable failure evidence")
        require(
            dead_letter_counts(test_dsn, admission.intent.job_id)
            == (1, 1, admission.last_receipt_hash),
            "conflict must not alter stored dead-letter evidence",
        )

        rollback_admission = terminal_blocked_admission(
            store,
            build_intent(resource_id="dead-letter-persistence-rollback"),
        )
        try:
            with store.request_unit_of_work(
                correlation_id="dead-letter-persistence-rollback",
                command_id="deadLetterPersistenceRollback",
            ):
                store.async_effect_dead_letter_repository().record(rollback_admission)
                raise RuntimeError("force dead-letter admission rollback")
        except RuntimeError:
            pass
        require(
            dead_letter_counts(test_dsn, rollback_admission.intent.job_id)[0] == 0,
            "rolled-back admission must leave no durable dead letter",
        )

        state, attempt, attempt_count = job_evidence(test_dsn, admission.intent.job_id)
        require(state == "blocked" and attempt == 1, "admission must not requeue the terminal job")
        require(attempt_count == 1, "admission must not create another worker attempt")

        print(
            "Async-effect dead-letter persistence Postgres smoke passed "
            "(worker replay and Provider calls remain disabled)."
        )
    finally:
        if store is not None:
            store.close_pool()
        if database_created:
            drop_database(admin_dsn, database_name)


if __name__ == "__main__":
    main()
