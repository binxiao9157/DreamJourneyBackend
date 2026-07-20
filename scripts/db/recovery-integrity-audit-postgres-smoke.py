#!/usr/bin/env python3
"""Prove V3 recovery ownership auditing in a disposable Postgres DB.

The smoke creates and removes a temporary ``dj_recovery_*`` database. It adds
fixtures for each audited ownership domain. It proves that a future table with
a direct ``user_id`` cannot silently evade recovery orphan auditing, and that
Owner Truth and async effect scope mismatches remain explicit NO_GO evidence.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any
import uuid

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.core.config import settings
from app.db.migrator import PostgresMigrator, default_migrations_dir


VERIFY_SCRIPT = ROOT_DIR / "scripts" / "db" / "verify_recovery_integrity.py"


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


def run_integrity_check(*, dsn: str, schema_head: str, target_database: str, output: Path) -> dict[str, Any]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT_DIR)
    result = subprocess.run(
        [
            sys.executable,
            str(VERIFY_SCRIPT),
            "--dsn",
            dsn,
            "--backup-id",
            "dj-20260721T000000Z-a1b2c3d4",
            "--cutoff-lsn",
            "0/16B6A40",
            "--target-database",
            target_database,
            "--production-database",
            "dreamjourney",
            "--expected-schema-head",
            schema_head,
            "--output",
            str(output),
        ],
        cwd=ROOT_DIR,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    require(
        result.returncode == 2,
        "orphaned direct-user fixture must produce a recovery NO_GO report",
    )
    require(output.is_file(), "integrity report must be written even for NO_GO")
    return json.loads(output.read_text(encoding="utf-8"))


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_recovery_audit_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)

    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="recovery-integrity-audit-smoke",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "temporary schema head must verify")

        with psycopg.connect(test_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE public.recovery_audit_orphan_fixture (
                        id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL
                    )
                    """
                )
                cursor.execute(
                    "INSERT INTO public.recovery_audit_orphan_fixture (id, user_id) VALUES (%s, %s)",
                    ("fixture-orphan", "absent-owner"),
                )
                cursor.execute(
                    """
                    INSERT INTO owner_truth.legacy_migration_runs (
                        id, vault_id, owner_subject_id, classifier_version,
                        inventory_hash, entry_count, summary
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                    """,
                    (
                        str(uuid.uuid4()),
                        "fixture-missing-vault",
                        "fixture-owner",
                        "recovery-audit-v1",
                        "a" * 64,
                        0,
                        "{}",
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO owner_truth.vaults (
                        vault_id, owner_subject_id, authority_epoch
                    ) VALUES (%s, %s, %s)
                    """,
                    ("fixture-vault", "fixture-owner", 1),
                )
                operation_id = str(uuid.uuid4())
                cursor.execute(
                    """
                    INSERT INTO async_effects.operations (
                        operation_id, operation_type, owner_subject_id, vault_id,
                        resource_type, resource_id, resource_version, purpose,
                        authority_epoch, stable_key, payload_hash, state
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        operation_id,
                        "recoveryAuditFixture",
                        "fixture-owner",
                        "fixture-vault",
                        "recoveryAuditFixture",
                        "fixture-resource",
                        1,
                        "recoveryAuditFixture",
                        1,
                        "b" * 64,
                        "c" * 64,
                        "accepted",
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO async_effects.outbox_events (
                        event_id, operation_id, owner_subject_id, vault_id,
                        resource_type, resource_id, resource_version, purpose,
                        authority_epoch, stable_key, event_type, payload_hash, state
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(uuid.uuid4()),
                        operation_id,
                        "fixture-other-owner",
                        "fixture-vault",
                        "recoveryAuditFixture",
                        "fixture-resource",
                        1,
                        "recoveryAuditFixture",
                        1,
                        "d" * 64,
                        "recoveryAuditFixture",
                        "e" * 64,
                        "pending",
                    ),
                )
            connection.commit()

        with tempfile.TemporaryDirectory(prefix="dreamjourney-recovery-audit-") as temporary:
            report = run_integrity_check(
                dsn=test_dsn,
                schema_head=str(verified["expectedHead"]),
                target_database=database_name,
                output=Path(temporary) / "integrity-evidence.json",
            )

        fixture_table = "public.recovery_audit_orphan_fixture"
        owner_truth_domain = report["auditDomains"]["ownerTruthVaultScope"]
        async_effects_domain = report["auditDomains"]["asyncEffectsOperationScope"]
        require(report["schemaVersion"] == 3, "integrity report must use V3 coverage evidence")
        require(report["status"] == "failed", "orphan fixture must fail integrity")
        require("ownerOrphansPresent" in report["blockers"], "orphan blocker must be explicit")
        require(report["auditCoverageStatus"] == "complete", "dynamic audit coverage must be complete")
        require(
            fixture_table in report["checkedDirectUserIdTables"],
            "dynamic discovery missed the fixture direct-user table",
        )
        require(
            report["orphanOwnerCountsByTable"].get(fixture_table) == 1,
            "fixture orphan must be attributed to its discovered table",
        )
        require(report["orphanOwnerCount"] >= 1, "fixture orphan must affect aggregate count")
        require(
            owner_truth_domain["missingVaultCountsByTable"].get(
                "owner_truth.legacy_migration_runs"
            )
            == 1,
            "Owner Truth missing Vault fixture must be attributed to its table",
        )
        require(
            "ownerTruthVaultScopeViolation" in report["blockers"],
            "Owner Truth scope mismatch must be an explicit NO_GO blocker",
        )
        require(
            async_effects_domain["scopeMismatchCountsByTable"].get(
                "async_effects.outbox_events"
            )
            == 1,
            "async operation-scope mismatch must be attributed to the child table",
        )
        require(
            "asyncEffectsOperationScopeViolation" in report["blockers"],
            "async effect scope mismatch must be an explicit NO_GO blocker",
        )
        require(
            {
                "table": "async_effects.worker_loss_observations",
                "reason": "valueFreeRuntimeObservation",
            }
            in report["explicitExemptions"],
            "value-free worker observations must be explicitly exempted",
        )

        print(
            json.dumps(
                {
                    "auditCoverageComplete": True,
                    "dynamicFixtureDiscovered": True,
                    "ownerOrphansNoGo": True,
                    "schemaHead": verified["expectedHead"],
                    "asyncScopeMismatchNoGo": True,
                    "explicitExemptionRecorded": True,
                    "ownerTruthScopeViolationNoGo": True,
                    "schemaVersion": 3,
                    "status": "passed",
                },
                sort_keys=True,
            )
        )
    finally:
        try:
            drop_database(admin_dsn, database_name)
        except Exception as exc:  # pragma: no cover - cleanup diagnostics only
            print(f"warning: failed to drop temporary database {database_name}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
