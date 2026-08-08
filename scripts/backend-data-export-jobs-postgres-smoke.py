#!/usr/bin/env python3
"""Exercise owner-scoped data export jobs in a disposable Postgres database."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.services.data_export_jobs import (
    create_data_export_job_record,
    materialize_data_export_job,
)
from app.services.data_rights_module_inventory import build_module_owned_data_export
from app.services.postgres_store import PostgresStore


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


def exercise(dsn: str) -> None:
    store = PostgresStore(dsn=dsn, pool_min_size=1, pool_max_size=3)
    store.open_pool(wait=True)
    try:
        owner = store.upsert_user("13900008831", "Postgres export owner")
        other = store.upsert_user("13900008832", "Postgres export other")
        store.save_profile(owner["id"], {"nickname": "Owner", "bio": "owner-only-export"})
        record = create_data_export_job_record(
            owner_user_id=owner["id"],
            request_key="postgres-export-request-key",
            now="2026-08-08T08:00:00+00:00",
            expires_at="2026-08-08T09:00:00+00:00",
        )
        first = store.create_data_export_job(record)
        replay_record = create_data_export_job_record(
            owner_user_id=owner["id"],
            request_key="postgres-export-request-key",
            now="2026-08-08T08:00:01+00:00",
            expires_at="2026-08-08T09:00:00+00:00",
        )
        replay = store.create_data_export_job(replay_record)
        require(first["outcome"] == "created", "first export job must persist")
        require(replay["outcome"] == "deduplicated", "request key must deduplicate")
        require(first["job"]["id"] == replay["job"]["id"], "dedupe must retain job identity")

        job = materialize_data_export_job(
            store,
            job_id=first["job"]["id"],
            owner_user_id=owner["id"],
            export_builder=build_module_owned_data_export,
            now="2026-08-08T08:01:00+00:00",
        )
        require(job["status"] == "partial", "external boundaries must remain partial")
        require(job["attempt"] == 1, "first materialization must record one attempt")
        require(job["manifest"]["packageStatus"] == "partial", "manifest must be honest")
        require(
            store.get_data_export_job(job["id"], owner_user_id=other["id"]) is None,
            "cross-owner job lookup must be denied",
        )
        downloadable = store.get_data_export_job(
            job["id"],
            owner_user_id=owner["id"],
            include_artifact=True,
        )
        serialized = json.dumps(downloadable, ensure_ascii=False, sort_keys=True)
        require("owner-only-export" in serialized, "owner data must be present")
        require("postgres-export-request-key" not in serialized, "raw request key must not persist")

        expired = store.expire_data_export_job(
            job["id"],
            owner_user_id=owner["id"],
            updated_at="2026-08-08T09:00:00+00:00",
        )
        require(expired["job"]["status"] == "expired", "expiry must be durable")
        require(expired["job"].get("artifact") is None, "expiry must clear artifact bytes")
        print(
            "Data export job Postgres smoke passed "
            "(idempotency, owner fence, partial manifest and expiry verified)."
        )
    finally:
        store.close_pool()


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", "").strip()
    require(base_dsn, "DATABASE_URL is required")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_data_export_jobs_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="data-export-job-postgres-smoke",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        applied = migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")
        require("0084" in applied["appliedVersions"], "0084 must be applied")
        require(applied["appliedHead"] == verified["expectedHead"], "migration head mismatch")
        exercise(test_dsn)
    finally:
        drop_database(admin_dsn, database_name)


if __name__ == "__main__":
    main()
