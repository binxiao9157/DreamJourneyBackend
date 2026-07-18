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


def expect_rejected(dsn: str, operation, message: str) -> None:
    rejected = False
    try:
        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                operation(cursor)
    except Exception:
        rejected = True
    require(rejected, message)


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
            lambda cursor: cursor.execute(
                "UPDATE async_effects.operations SET state = 'completed' WHERE operation_id = %s",
                (intent.operation_id,),
            )
            or cursor.execute(
                "UPDATE async_effects.operations SET state = 'accepted' WHERE operation_id = %s",
                (intent.operation_id,),
            ),
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
            "rollback=true terminalGuard=true receiptsAppendOnly=true"
        )
    finally:
        if store is not None:
            store.close_pool()
        drop_database(admin_dsn, database_name)


if __name__ == "__main__":
    main()
