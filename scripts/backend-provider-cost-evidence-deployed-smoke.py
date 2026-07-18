#!/usr/bin/env python3
"""Verify provider cost evidence against a disposable deployed Postgres DB.

The deployed API readiness is checked first. Evidence rows are then written to
a fresh, migrated temporary database inside the deployed API container, never
to the production business database. This proves persistence and privacy
boundaries only; commercial budget thresholds remain a product decision.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.core.config import settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.observability.provider_costs import ProviderCostEvidenceRecorder
from app.services.postgres_store import PostgresStore


BASE_URL = os.environ.get("BACKEND_BASE_URL", "").strip().rstrip("/")
OUTPUT_PATH = os.environ.get("OUTPUT_PATH", "").strip()


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
                sql.SQL("DROP DATABASE IF EXISTS {}").format(
                    sql.Identifier(database_name)
                )
            )


def assert_deployed_container_context():
    require(
        os.environ.get("DREAMJOURNEY_DEPLOYED_CONTAINER_SMOKE") == "1",
        "DREAMJOURNEY_DEPLOYED_CONTAINER_SMOKE=1 is required",
    )
    require(
        any(path.exists() for path in (Path("/.dockerenv"), Path("/run/.containerenv"))),
        "provider cost smoke must run inside the deployed API container",
    )


def assert_deployed_readiness():
    require(BASE_URL, "BACKEND_BASE_URL is required")
    request = urllib.request.Request(
        f"{BASE_URL}/ready",
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = response.status
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        status = error.code
        payload = json.loads(error.read().decode("utf-8") or "{}")
    require(status == 200, f"GET /ready expected 200, got {status}")
    require(payload.get("status") == "ready", "deployed API is not ready")


def exercise_provider_cost_persistence(dsn):
    private_marker = "PROVIDER_COST_DEPLOYED_PRIVATE_MARKER"
    store = PostgresStore(
        dsn=dsn,
        pool_min_size=1,
        pool_max_size=2,
        pool_timeout_seconds=4.0,
    )
    store.open_pool(wait=True)
    try:
        recorder = ProviderCostEvidenceRecorder(
            environment="deployedSmoke",
            build="provider-cost-evidence-g2",
            identifier_hmac_key="provider-cost-deployed-smoke-key-" + ("y" * 32),
            event_sink=store.append_evidence_event,
            event_source=lambda: store.list_evidence_events(event_type="providerCost"),
        )
        recorder.record_attempt(
            request_key=private_marker,
            operation_key="provider-cost-deployed-unknown",
            provider="deepseek",
            capability="kbExtract",
            unit_type="request",
            units=1,
            state="succeeded",
            reason="providerUsageObserved",
            principal_key="provider-cost-deployed-principal",
        )
        recorder.record_attempt(
            request_key="provider-cost-deployed-known-request",
            operation_key="provider-cost-deployed-known",
            provider="volcengineVoiceClone",
            capability="voiceCloneSynthesis",
            unit_type="character",
            units=24,
            state="succeeded",
            reason="providerUsageObserved",
            cost_source="approvedRateCard",
            cost_micros=240,
            rate_card_version="smoke-rate-card-v1",
        )
        before_reopen = recorder.summary()
    finally:
        store.close_pool()

    reopened_store = PostgresStore(
        dsn=dsn,
        pool_min_size=1,
        pool_max_size=2,
        pool_timeout_seconds=4.0,
    )
    reopened_store.open_pool(wait=True)
    try:
        reopened = ProviderCostEvidenceRecorder(
            environment="deployedSmoke",
            build="provider-cost-evidence-g2",
            identifier_hmac_key="provider-cost-deployed-smoke-key-" + ("y" * 32),
            event_source=lambda: reopened_store.list_evidence_events(
                event_type="providerCost"
            ),
        )
        summary = reopened.summary()
        serialized = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        readiness = summary.get("readiness") or {}
        require(before_reopen.get("eventCount") == 2, "pre-restart provider cost count")
        require(summary.get("eventCount") == 2, "provider cost persistence after reopen")
        require(summary.get("knownCostEventCount") == 1, "known cost persistence")
        require(summary.get("unknownCostEventCount") == 1, "unknown cost persistence")
        require(summary.get("knownCostMicros") == 240, "known cost total persistence")
        require(summary.get("retentionClass") == "providerCost", "retention class persistence")
        require(summary.get("evidenceSource") == "persistent", "persistent evidence source")
        require(readiness.get("status") == "notReady", "budget readiness must remain blocked")
        require(readiness.get("reason") == "providerCostUnknown", "unknown cost readiness reason")
        require(readiness.get("costLimitEnforcementAllowed") is False, "no budget enforcement claim")
        require(readiness.get("providerExpansionAllowed") is False, "no provider expansion claim")
        require(private_marker not in serialized, "raw provider marker leaked")
        return {
            "eventCount": summary["eventCount"],
            "knownCostEventCount": summary["knownCostEventCount"],
            "unknownCostEventCount": summary["unknownCostEventCount"],
            "readiness": readiness["status"],
            "readinessReason": readiness["reason"],
            "commercialBudgetDecision": readiness["commercialBudgetDecision"],
            "rawPrivateMarkerLeaked": False,
        }
    finally:
        reopened_store.close_pool()


def main():
    assert_deployed_container_context()
    assert_deployed_readiness()
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    admin_dsn = database_dsn(base_dsn, "postgres")
    database_name = f"dj_provider_cost_smoke_{uuid.uuid4().hex[:12]}"
    temporary_dsn = database_dsn(base_dsn, database_name)

    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=temporary_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="provider-cost-evidence-g2",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        applied = migrator.apply()
        verified = migrator.verify()
        require(verified.get("status") == "ready", "temporary schema is not ready")
        result = {
            "status": "passed",
            "schemaVersion": 1,
            "deployedReadiness": True,
            "deployedContainer": True,
            "temporaryDatabase": True,
            "productionBusinessDataMutated": False,
            "migrationHead": verified.get("expectedHead"),
            "appliedMigrationCount": len(applied.get("appliedVersions") or []),
            **exercise_provider_cost_persistence(temporary_dsn),
        }
        serialized = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if OUTPUT_PATH:
            output = Path(OUTPUT_PATH)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(serialized, encoding="utf-8")
            output.chmod(0o600)
        print(serialized, end="")
    finally:
        drop_database(admin_dsn, database_name)


if __name__ == "__main__":
    main()
