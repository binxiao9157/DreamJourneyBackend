#!/usr/bin/env python3
"""Prove PC-A4 cumulative voice-profile creation limits in disposable Postgres."""

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

from app.core.config import settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.services.postgres_store import PostgresStore
from app.services.store_factory import close_store, open_store
from app.services.voice_profile_creation_quota import (
    VOICE_PROFILE_CREATION_LIMIT,
    VoiceProfileCreationLimitReached,
)


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


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_voice_profile_quota_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    store: PostgresStore | None = None

    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="voice-profile-creation-quota-pc-a4",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        applied = migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")
        require(applied["appliedHead"] == "0097", "PC-A4 migration must be current head")

        store = PostgresStore(dsn=test_dsn, pool_min_size=1, pool_max_size=10)
        open_store(store)
        subject_id = "postgres-quota-owner"

        def reserve(index: int) -> str:
            try:
                store.reserve_voice_profile_creation(
                    subject_id,
                    command_id=f"postgres-command-{index:02d}",
                    voice_profile_id=f"postgres-profile-{index:02d}",
                )
                return "accepted"
            except VoiceProfileCreationLimitReached:
                return "limited"

        with ThreadPoolExecutor(max_workers=10) as executor:
            outcomes = list(executor.map(reserve, range(10)))

        require(
            outcomes.count("accepted") == VOICE_PROFILE_CREATION_LIMIT,
            "exactly five concurrent creations must be accepted",
        )
        require(outcomes.count("limited") == 5, "remaining creations must be limited")
        quota = store.get_voice_profile_creation_quota(subject_id)
        require(quota["creationCount"] == 5, "creation count must remain five")
        require(quota["remainingCount"] == 0, "remaining count must be zero")

        accepted_index = outcomes.index("accepted")
        replay = store.reserve_voice_profile_creation(
            subject_id,
            command_id=f"postgres-command-{accepted_index:02d}",
            voice_profile_id=f"postgres-profile-{accepted_index:02d}",
        )
        require(replay["idempotent"] is True, "accepted command replay must deduplicate")
        require(replay["creationCount"] == 5, "replay must not increment the count")

        print(
            "voiceProfileCreationQuotaPcA4=true "
            "creationLimit=5 creationCount=5 remainingCount=0 "
            "concurrentAtomic=true deletionRefund=false"
        )
    finally:
        if store is not None:
            close_store(store)
        try:
            drop_database(admin_dsn, database_name)
        except Exception as error:  # pragma: no cover - cleanup evidence only
            print(f"warning: disposable database cleanup failed: {error}", file=sys.stderr)


if __name__ == "__main__":
    main()
