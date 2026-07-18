#!/usr/bin/env python3
"""Verify evidence manifest persistence against a disposable deployed Postgres DB.

The smoke checks the public readiness endpoint, then creates a temporary
database inside the deployed API container. It never writes acceptance evidence
or business data to the production database and never prints evidence bodies.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
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
from app.observability.evidence_manifest import EvidenceManifestService
from app.services.postgres_store import PostgresStore


BASE_URL = os.environ.get("BACKEND_BASE_URL", "").strip().rstrip("/")
OUTPUT_PATH = os.environ.get("OUTPUT_PATH", "").strip()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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
        "evidence manifest smoke must run inside the deployed API container",
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


def issue_manifest(service, *, status="passed", source_commit="abcdef1234567"):
    return service.issue(
        manifest_type="echoQaEvidenceBundle",
        source_commit=source_commit,
        command_id="runEchoEvidenceManifestExportSmoke",
        sample_count=2,
        sample_set_hash=digest("sample-set"),
        exclusion_codes=["rawAudio", "providerSecret", "reportBody"],
        source_schema_versions=["echoQaBundle-v2", "echoEvidenceManifest-v1"],
        artifact_hashes=[digest("redacted-evidence-bundle")],
        window_started_at="2026-07-18T10:00:00+00:00",
        window_ended_at="2026-07-18T10:01:00+00:00",
        issuer="deployedSmoke",
        manifest_status=status,
        build="ios-qa-build-42",
        owner_lease_hash=digest("deployed-owner"),
    )


def make_service(store, now):
    return EvidenceManifestService(
        environment="deployedSmoke",
        build="backend-evidence-manifest-g2",
        event_sink=store.append_evidence_event,
        event_source=store.list_evidence_events,
        retention_days=1,
        clock=lambda: now[0],
    )


def exercise_manifest_persistence(dsn):
    private_marker = "EVIDENCE_MANIFEST_DEPLOYED_PRIVATE_MARKER"
    now = [datetime(2026, 7, 18, 10, 5, tzinfo=timezone.utc)]
    store = PostgresStore(
        dsn=dsn,
        pool_min_size=1,
        pool_max_size=2,
        pool_timeout_seconds=4.0,
    )
    store.open_pool(wait=True)
    try:
        service = make_service(store, now)
        first = issue_manifest(service)
        now[0] += timedelta(minutes=1)
        second = issue_manifest(service)
        initial = service.list_manifests()
        verified = service.verify_artifacts(
            evidence_id=first["evidenceId"],
            artifact_hashes=[digest("redacted-evidence-bundle")],
        )
        mismatch = service.verify_artifacts(
            evidence_id=first["evidenceId"],
            artifact_hashes=[digest("tampered-evidence-bundle")],
        )
        # Legacy rows have no manifest metadata and must never verify a gate.
        store.append_evidence_event(
            {
                "eventId": "legacy-evidence-smoke-001",
                "type": "operation",
                "operationId": "legacyEvidence",
                "correlationId": None,
                "principalHash": None,
                "resourceType": "legacyReport",
                "resourceIdHash": None,
                "state": "succeeded",
                "reason": "legacyReportObserved",
                "occurredAt": now[0].isoformat(),
                "env": "deployedSmoke",
                "build": "legacy-build",
                "operation": "legacyEvidence",
            },
            retention_class="operationalTemporary",
            expires_at_iso=(now[0] + timedelta(days=2)).isoformat(),
        )
        legacy = service.verify_artifacts(
            evidence_id="legacy-evidence-smoke-001",
            artifact_hashes=[digest("redacted-evidence-bundle")],
        )
        serialized = json.dumps(initial, ensure_ascii=False, sort_keys=True)
        require(initial.get("manifestCount") == 2, "manifest count before reopen")
        require(initial.get("currentPassedCount") == 2, "current passed manifests before reopen")
        require(verified.get("valid") is True, "artifact hash should verify")
        require(mismatch.get("reason") == "artifactHashMismatch", "tamper must fail")
        require(legacy.get("reason") == "evidenceManifestMissing", "legacy evidence must not verify")
        require(private_marker not in serialized, "raw private marker leaked")
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
        reopened = make_service(reopened_store, now)
        persisted = reopened.list_manifests()
        expired_at = now[0] + timedelta(days=2)
        expired = reopened.verify_artifacts(
            evidence_id=first["evidenceId"],
            artifact_hashes=[digest("redacted-evidence-bundle")],
            now=expired_at,
        )
        receipt = reopened_store.expire_evidence_events(expired_at.isoformat())
        after_retention = reopened.list_manifests(now=expired_at)
        require(persisted.get("manifestCount") == 2, "manifest persistence after reopen")
        require(expired.get("reason") == "evidenceManifestExpired", "expiry must block reuse")
        require(int(receipt.get("expiredCount") or 0) >= 2, "retention must delete expired manifests")
        require(after_retention.get("manifestCount") == 0, "expired manifests must not remain queryable")
        return {
            "manifestCountBeforeRetention": persisted["manifestCount"],
            "currentPassedCountBeforeRetention": persisted["currentPassedCount"],
            "reissueProducedNewEvidenceId": first["evidenceId"] != second["evidenceId"],
            "hashMismatchRejected": mismatch["reason"] == "artifactHashMismatch",
            "legacyEvidenceRejected": legacy["reason"] == "evidenceManifestMissing",
            "expiredEvidenceRejected": expired["reason"] == "evidenceManifestExpired",
            "expiredCount": int(receipt.get("expiredCount") or 0),
            "manifestCountAfterRetention": after_retention["manifestCount"],
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
    database_name = f"dj_evidence_manifest_smoke_{uuid.uuid4().hex[:12]}"
    temporary_dsn = database_dsn(base_dsn, database_name)

    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=temporary_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="evidence-manifest-g2",
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
            **exercise_manifest_persistence(temporary_dsn),
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
