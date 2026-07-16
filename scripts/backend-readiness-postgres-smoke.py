#!/usr/bin/env python3
import json
import sys
import uuid
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.core.config import Settings, settings
from app.db.migrator import PostgresMigrator, default_migrations_dir, load_migrations
from app.services.postgres_store import PostgresStore
from app.services.readiness import ReadinessService


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def database_dsn(base_dsn, database_name):
    parameters = conninfo_to_dict(base_dsn)
    parameters["dbname"] = database_name
    return make_conninfo(**parameters)


def create_database(admin_dsn, database_name):
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))


def drop_database(admin_dsn, database_name):
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (database_name,),
            )
            cursor.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database_name)))


def main():
    base_dsn = settings.database_url
    admin_dsn = database_dsn(base_dsn, "postgres")
    database_name = "dj_readiness_smoke_" + uuid.uuid4().hex[:10]
    create_database(admin_dsn, database_name)
    dsn = database_dsn(base_dsn, database_name)
    store = None
    try:
        PostgresMigrator(
            dsn=dsn,
            migrations_dir=default_migrations_dir(),
            build_id="g2-readiness",
        ).apply()
        readiness_settings = Settings(
            environment="production",
            store_backend="postgres",
            database_url=dsn,
            database_pool_min_size=1,
            database_pool_max_size=1,
            database_pool_timeout_seconds=0.1,
            backend_api_token="readiness-smoke-token",
        )
        store = PostgresStore(
            dsn=dsn,
            pool_min_size=1,
            pool_max_size=1,
            pool_timeout_seconds=0.1,
        )
        store.open_pool(wait=True)
        service = ReadinessService(settings=readiness_settings, store=store)

        healthy = service.evaluate()
        require(healthy["status"] == "ready", "fresh migrated database readiness")

        held = store._pool.getconn(timeout=0.1)
        try:
            exhausted = service.evaluate()
        finally:
            store._pool.putconn(held)
        require(exhausted["status"] == "notReady", "pool exhaustion readiness")
        require(
            exhausted["components"][0]["reason"] == "databasePoolExhausted",
            "pool exhaustion reason",
        )

        migration = load_migrations(default_migrations_dir())[0]
        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE schema_migrations SET checksum = %s WHERE version = %s",
                    ("0" * 64, migration.version),
                )
        checksum_drift = service.evaluate()
        require(checksum_drift["status"] == "notReady", "checksum drift readiness")
        require(
            checksum_drift["components"][1]["reason"] == "migrationChecksumMismatch",
            "checksum drift reason",
        )
        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE schema_migrations SET checksum = %s WHERE version = %s",
                    (migration.checksum, migration.version),
                )
        recovered = service.evaluate()
        require(recovered["status"] == "ready", "readiness recovers after checksum repair")

        store.close_pool()
        store = None
        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND state = 'idle in transaction' "
                    "AND pid <> pg_backend_pid()"
                )
                idle_in_transaction = int(cursor.fetchone()[0])
        require(idle_in_transaction == 0, "readiness must not leak idle transactions")

        print(
            json.dumps(
                {
                    "status": "passed",
                    "schemaVersion": 1,
                    "healthy": True,
                    "poolExhaustionFailClosed": True,
                    "checksumDriftFailClosed": True,
                    "recoveryVerified": True,
                    "idleInTransaction": idle_in_transaction,
                },
                sort_keys=True,
            )
        )
    finally:
        if store is not None:
            store.close_pool()
        drop_database(admin_dsn, database_name)


if __name__ == "__main__":
    main()
