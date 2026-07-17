#!/usr/bin/env python3
import json
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.core.config import settings
from app.db.migrator import PostgresMigrator, default_migrations_dir, load_migrations


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
            cursor.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name))
            )


def drop_database(admin_dsn, database_name):
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (database_name,),
            )
            cursor.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database_name))
            )


def migrator(dsn, build_id):
    return PostgresMigrator(
        dsn=dsn,
        migrations_dir=default_migrations_dir(),
        build_id=build_id,
        lock_timeout_ms=1000,
        statement_timeout_ms=15000,
    )


def main():
    base_dsn = settings.database_url
    parameters = conninfo_to_dict(base_dsn)
    admin_dsn = database_dsn(base_dsn, "postgres")
    prefix = "dj_migration_smoke_"
    database_names = [
        prefix + uuid.uuid4().hex[:10],
        prefix + uuid.uuid4().hex[:10],
    ]
    require(parameters.get("user"), "database user is required")
    migrations = load_migrations(default_migrations_dir())
    expected_versions = [migration.version for migration in migrations]

    try:
        first_name, concurrent_name = database_names
        create_database(admin_dsn, first_name)
        first_dsn = database_dsn(base_dsn, first_name)
        first_migrator = migrator(first_dsn, "g2-fresh")

        plan = first_migrator.plan()
        require(plan["baselineAction"] == "execute", "fresh baseline plan")
        applied = first_migrator.apply()
        verified = first_migrator.verify()
        repeated = first_migrator.apply()
        require(applied["appliedVersions"] == expected_versions, "fresh migration apply")
        require(verified["status"] == "ready", "fresh migration verify")
        require(repeated["skippedVersions"] == expected_versions, "repeat no-op")

        baseline = load_migrations(default_migrations_dir())[0]
        with psycopg.connect(first_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = ANY(%s)",
                    (list(baseline.baseline_columns),),
                )
                table_count = int(cursor.fetchone()[0])
                cursor.execute(
                    "SELECT state, execution_mode FROM schema_migrations WHERE version = '0001'"
                )
                ledger = cursor.fetchone()
                cursor.execute(
                    "SELECT COUNT(*) FROM pg_trigger "
                    "WHERE tgname = 'evidence_events_no_update' AND NOT tgisinternal"
                )
                trigger_count = int(cursor.fetchone()[0])
        require(table_count == 19, "fresh schema table count")
        require(ledger == ("applied", "execute"), "fresh migration receipt")
        require(trigger_count == 1, "append-only trigger")

        create_database(admin_dsn, concurrent_name)
        concurrent_dsn = database_dsn(base_dsn, concurrent_name)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda index: migrator(
                        concurrent_dsn,
                        f"g2-concurrent-{index}",
                    ).apply(),
                    range(2),
                )
            )
        require(
            sum(len(result["appliedVersions"]) for result in results)
            == len(expected_versions),
            "concurrent migrators must apply once",
        )
        require(
            sum(len(result["skippedVersions"]) for result in results)
            == len(expected_versions),
            "second concurrent migrator must observe applied head",
        )

        print(
            json.dumps(
                {
                    "status": "passed",
                    "schemaVersion": 1,
                    "freshApply": True,
                    "freshTableCount": table_count,
                    "repeatNoop": True,
                    "verifyHead": verified["expectedHead"],
                    "concurrentApplyCount": len(expected_versions),
                    "concurrentSkipCount": len(expected_versions),
                },
                sort_keys=True,
            )
        )
    finally:
        for database_name in database_names:
            drop_database(admin_dsn, database_name)


if __name__ == "__main__":
    main()
