#!/usr/bin/env python3
"""Prove encrypted APNs registration and restart-safe outbox behavior."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import uuid

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from cryptography.fernet import Fernet
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.core.config import settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.services.apns_delivery import (
    APNSConfiguration,
    APNSDeliveryService,
    FakeAPNSProvider,
)
from app.services.apns_postgres_outbox import (
    EncryptedPostgresAPNSTokenVault,
    PostgresAPNSPersistence,
)
from app.services.postgres_store import PostgresStore


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def dsn_for_database(base_dsn: str, database_name: str) -> str:
    parameters = conninfo_to_dict(base_dsn)
    parameters["dbname"] = database_name
    return make_conninfo(**parameters)


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(bool(base_dsn), "DATABASE_URL is required")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_apns_outbox_smoke_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    store: PostgresStore | None = None
    raw_token = "a1" * 32
    key = Fernet.generate_key().decode("ascii")

    try:
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="apns-postgres-outbox-smoke",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        applied = migrator.apply()
        require("0088" in tuple(applied.get("appliedVersions") or ()), "migration 0088 missing")
        require(migrator.verify()["status"] == "ready", "migration head must verify")

        store = PostgresStore(dsn=test_dsn, pool_min_size=1, pool_max_size=3)
        store.open_pool(wait=True)
        configuration = APNSConfiguration(
            provider="fake",
            token_vault_provider="postgresEncrypted",
            topic="com.yxj.dreamjourney.app",
            environment="sandbox",
            token_encryption_key_configured=True,
        )
        first_persistence = PostgresAPNSPersistence(store)
        first_service = APNSDeliveryService(
            configuration=configuration,
            token_vault=EncryptedPostgresAPNSTokenVault(
                persistence=first_persistence,
                encryption_key=key,
            ),
            provider=FakeAPNSProvider(),
            repository=first_persistence,
            registration_repository=first_persistence,
        )
        registration = first_service.register(
            owner_user_id="synthetic-apns-owner",
            installation_id="synthetic-ios-installation",
            device_token=raw_token,
            topic="com.yxj.dreamjourney.app",
            environment="sandbox",
        )
        queued = first_service.enqueue(
            message_id="synthetic-time-letter-reminder",
            registration=registration,
            payload={
                "aps": {"alert": {"title": "时间信件已抵达"}},
                "kind": "timeLetterReminder",
            },
        )

        rotated = first_service.register(
            owner_user_id="synthetic-apns-owner",
            installation_id="synthetic-ios-installation",
            device_token="b2" * 32,
            topic="com.yxj.dreamjourney.app",
            environment="sandbox",
        )
        require(rotated.generation == 1, "token rotation must advance generation")

        # Recreate every adapter to model an API/worker process restart.
        second_persistence = PostgresAPNSPersistence(store)
        provider = FakeAPNSProvider()
        second_service = APNSDeliveryService(
            configuration=configuration,
            token_vault=EncryptedPostgresAPNSTokenVault(
                persistence=second_persistence,
                encryption_key=key,
            ),
            provider=provider,
            repository=second_persistence,
            registration_repository=second_persistence,
        )
        claimed = second_service.dispatch_due(worker_id="synthetic-worker", limit=10)
        require(len(claimed) == 1, "restart-safe worker must claim one job")
        require(claimed[0].state == "failed", "stale generation must fail closed")
        require(claimed[0].job_id == queued.job_id, "outbox job identity changed")
        require(
            claimed[0].reason_code == "apnsRegistrationSuperseded",
            "stale generation reason missing",
        )
        require(len(provider.calls) == 0, "stale job must not reach provider")
        require(second_persistence.receipt_count(queued.job_id) >= 2, "append-only receipts missing")

        current = second_service.enqueue(
            message_id="synthetic-current-time-letter-reminder",
            registration=rotated,
            payload={
                "aps": {"alert": {"title": "当前时间信件已抵达"}},
                "kind": "timeLetterReminder",
            },
        )
        current_result = second_service.dispatch_due(
            worker_id="synthetic-worker-current",
            limit=10,
        )
        require(len(current_result) == 1, "current generation job must be claimed")
        require(current_result[0].job_id == current.job_id, "current job identity changed")
        require(current_result[0].state == "accepted", "current job must dispatch")
        require(len(provider.calls) == 1, "current job must invoke provider once")
        require(provider.calls[0]["device_token"] == "b2" * 32, "current token decrypt failed")

        with psycopg.connect(test_dsn) as connection:
            ciphertext = connection.execute(
                "SELECT ciphertext FROM notification.apns_token_secrets"
            ).fetchone()[0]
            token_hash = connection.execute(
                "SELECT token_hash FROM notification.apns_device_registrations"
            ).fetchone()[0]
            payload_text = connection.execute(
                "SELECT payload::text FROM notification.apns_delivery_outbox"
            ).fetchone()[0]
        require(raw_token not in ciphertext, "raw token leaked into ciphertext column")
        require(raw_token not in token_hash, "raw token leaked into registration")
        require(raw_token not in payload_text, "raw token leaked into outbox payload")
        print("APNs PostgreSQL outbox smoke passed")
    finally:
        if store is not None:
            store.close_pool()
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (database_name,),
            )
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database_name))
            )


if __name__ == "__main__":
    main()
