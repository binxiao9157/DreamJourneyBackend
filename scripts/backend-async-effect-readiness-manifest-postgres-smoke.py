#!/usr/bin/env python3
"""Exercise value-free readiness manifest persistence in a disposable Postgres DB.

The smoke writes only synthetic readiness metadata through the generic evidence
manifest sink. It never starts an async-effect worker, replays a job, or calls
an external Provider.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
from uuid import uuid4

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.async_effects.contracts import AsyncEffectRuntimeStatus
from app.async_effects.readiness_evidence import (
    build_async_effect_worker_readiness_evidence,
)
from app.async_effects.readiness_manifest_projection import (
    persist_async_effect_readiness_manifest,
)
from app.core.config import settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.observability.evidence_manifest import EvidenceManifestService
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
            cursor.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database_name))
            )


def make_service(store: PostgresStore, now: list[datetime]) -> EvidenceManifestService:
    return EvidenceManifestService(
        environment="postgresSmoke",
        build="backend-async-effect-readiness-manifest-g2",
        event_sink=store.append_evidence_event,
        event_source=store.list_evidence_events,
        retention_days=1,
        clock=lambda: now[0],
    )


def exercise(dsn: str) -> None:
    now = [datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc)]
    store = PostgresStore(dsn=dsn, pool_min_size=1, pool_max_size=2)
    store.open_pool(wait=True)
    try:
        service = make_service(store, now)
        evidence = build_async_effect_worker_readiness_evidence(
            runtime_status=AsyncEffectRuntimeStatus(
                enabled=True,
                worker_enabled=True,
                allowed=True,
                reason="asyncEffectRuntimeReady",
            ),
            worker_id="worker-private-marker-not-persisted",
            previews=(),
            runnable_handler_count=1,
            observed_at=now[0],
            expires_at=now[0] + timedelta(minutes=5),
        )
        first = persist_async_effect_readiness_manifest(
            evidence,
            manifest_service=service,
            source_commit="abcdef1234567",
            now=now[0],
        )
        duplicate = persist_async_effect_readiness_manifest(
            evidence,
            manifest_service=service,
            source_commit="abcdef1234567",
            now=now[0],
        )
        summary = service.list_manifests(now=now[0])
        serialized = str({"first": first, "summary": summary})
        require(first["evidenceManifest"]["outcome"] == "appended", "first append")
        require(
            duplicate["evidenceManifest"]["outcome"] == "deduplicated",
            "same observation must deduplicate",
        )
        require(summary["manifestCount"] == 1, "one manifest must persist")
        require(summary["currentPassedCount"] == 1, "ready evidence must be current passed")
        require(
            "worker-private-marker-not-persisted" not in serialized,
            "raw worker identity must not enter the manifest",
        )
    finally:
        store.close_pool()

    reopened_store = PostgresStore(dsn=dsn, pool_min_size=1, pool_max_size=2)
    reopened_store.open_pool(wait=True)
    try:
        reopened = make_service(reopened_store, now)
        persisted = reopened.list_manifests(now=now[0])
        evidence_id = first["evidenceManifest"]["evidenceId"]
        artifact_hash = first["manifestPlan"]["artifactHash"]
        verified = reopened.verify_artifacts(
            evidence_id=evidence_id,
            artifact_hashes=(artifact_hash,),
            now=now[0],
        )
        expired_at = now[0] + timedelta(minutes=6)
        expired = reopened.verify_artifacts(
            evidence_id=evidence_id,
            artifact_hashes=(artifact_hash,),
            now=expired_at,
        )
        receipt = reopened_store.expire_evidence_events(expired_at.isoformat())
        require(persisted["manifestCount"] == 1, "manifest must survive store reopen")
        require(verified["valid"] is True, "artifact hash must verify while current")
        require(expired["reason"] == "evidenceManifestExpired", "expired evidence must not verify")
        require(int(receipt["expiredCount"]) == 1, "expired manifest must be retained then removed")
    finally:
        reopened_store.close_pool()


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_readiness_manifest_{uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    created = False
    try:
        create_database(admin_dsn, database_name)
        created = True
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="async-effect-readiness-manifest-g2",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        migrator.apply()
        require(migrator.verify()["status"] == "ready", "temporary schema must verify")
        exercise(test_dsn)
        print(
            "Async-effect readiness manifest Postgres smoke passed "
            "(value-free evidence only; worker, replay, and Provider calls remain disabled)."
        )
    finally:
        if created:
            drop_database(admin_dsn, database_name)


if __name__ == "__main__":
    main()
