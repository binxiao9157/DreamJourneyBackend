#!/usr/bin/env python3
"""Exercise Owner Truth legacy/Projection parity observation in temporary Postgres.

The observer is deliberately a QA-only readiness report.  This smoke creates a
minimal active V4 Vault plus a ready empty Projection, seeds only synthetic
legacy records, and verifies the observer remains value-free and fail-closed.
It must never backfill a Source/Candidate/MemoryVersion, advance authority, or
retire the legacy writer.
"""

from __future__ import annotations

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
from psycopg.types.json import Jsonb

from app.core.config import settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.domain.owner_truth.source_commands import CreateTextSourceCommand, OwnerTruthCommandContext
from app.services.owner_truth_legacy_migration import OwnerTruthLegacyMigrationAccessDenied
from app.services.owner_truth_legacy_shadow_parity import OwnerTruthLegacyShadowParityService
from app.services.owner_truth_memory_projection import OwnerTruthMemoryProjectionService
from app.services.owner_truth_source import OwnerTruthSourceCommandService
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


def seed_legacy_rows(dsn: str, *, owner_subject_id: str) -> tuple[str, str]:
    archive_body = "synthetic archive body must never leave the parity report"
    memory_body = "synthetic legacy memory body must never leave the parity report"
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO archive_items (
                    id, user_id, payload, vault_id, owner_subject_id, authority_state
                ) VALUES (%s, %s, %s, %s, %s, 'active')
                """,
                (
                    "legacy-parity-archive",
                    owner_subject_id,
                    Jsonb({"note": archive_body}),
                    owner_subject_id,
                    owner_subject_id,
                ),
            )
            cursor.execute(
                """
                INSERT INTO memories (
                    id, user_id, payload, vault_id, owner_subject_id, authority_state
                ) VALUES (%s, %s, %s, %s, %s, 'active')
                """,
                (
                    "legacy-parity-memory",
                    owner_subject_id,
                    Jsonb({"summary": memory_body}),
                    owner_subject_id,
                    owner_subject_id,
                ),
            )
        connection.commit()
    return archive_body, memory_body


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_owner_truth_parity_smoke_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    store: PostgresStore | None = None

    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="owner-truth-legacy-shadow-parity-g2",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        applied = migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")
        require(
            applied["appliedVersions"][-1] == "0024",
            "current migration head must be present before parity observation",
        )

        store = PostgresStore(dsn=test_dsn, pool_min_size=1, pool_max_size=2)
        store.open_pool(wait=True)
        context = OwnerTruthCommandContext(
            vault_id="vault-parity-owner-a",
            owner_subject_id="owner-parity-a",
            actor_subject_id="owner-parity-a",
        )
        source = OwnerTruthSourceCommandService(store).create_text_source(
            command=CreateTextSourceCommand(
                command_id="owner-truth-parity-vault-seed",
                source_id=str(uuid.uuid4()),
                expected_version=0,
                text="synthetic V4 source used only to establish an active Vault",
                metadata={"origin": "legacyShadowParityPostgresSmoke"},
            ),
            context=context,
        )
        require(source.outcome == "created", "the synthetic V4 Source must establish the Vault")
        projection = OwnerTruthMemoryProjectionService(store).rebuild(context=context).snapshot
        require(
            projection["state"] == "ready" and projection["entryCount"] == 0,
            "the V4 Projection must be ready before observing legacy parity",
        )
        archive_body, memory_body = seed_legacy_rows(test_dsn, owner_subject_id=context.owner_subject_id)

        observer = OwnerTruthLegacyShadowParityService(store, enabled=True)
        created = observer.observe(context=context)
        replayed = observer.observe(context=context)
        summary = created.public_summary()
        require(created.inventory_outcome == "created", "first observation must create one inventory run")
        require(replayed.inventory_outcome == "deduplicated", "unchanged observation must replay")
        require(created.inventory_run_id == replayed.inventory_run_id, "replay must retain the run")
        require(created.report.report_hash == replayed.report.report_hash, "report must be deterministic")
        require(summary["comparisonStatus"] == "legacyEvidenceIncomplete", "unproven legacy rows must fail closed")
        require(summary["legacyEntryCount"] == 2, "both synthetic legacy rows must be observed")
        require(summary["legacyEligibleEntryCount"] == 0, "unproven rows cannot become eligible")
        require(summary["mappedRecordCount"] == 0, "observer must not create a lineage mapping")
        require(summary["cutoverAllowed"] is False, "observer must never authorize cutover")
        require(summary["authorityEpochChanged"] is False, "observer must not advance authority")
        require(summary["legacyWriterRetired"] is False, "observer must not retire legacy writer")
        require(summary["projection"]["state"] == "ready", "report must include Projection state")
        require(archive_body not in str(summary), "archive body leaked from parity summary")
        require(memory_body not in str(summary), "memory body leaked from parity summary")
        require("legacy-parity-archive" not in str(summary), "legacy archive id leaked from parity summary")
        require("legacy-parity-memory" not in str(summary), "legacy memory id leaked from parity summary")

        attacker_context = OwnerTruthCommandContext(
            vault_id=context.vault_id,
            owner_subject_id="owner-parity-attacker",
            actor_subject_id="owner-parity-attacker",
        )
        try:
            observer.observe(context=attacker_context)
        except OwnerTruthLegacyMigrationAccessDenied:
            pass
        else:
            raise AssertionError("cross-owner parity observation must be denied before inventory")

        with psycopg.connect(test_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM owner_truth.legacy_migration_runs")
                require(cursor.fetchone()[0] == 1, "replay/cross-owner calls must not create extra runs")
                cursor.execute("SELECT COUNT(*) FROM owner_truth.legacy_migration_entries")
                require(cursor.fetchone()[0] == 2, "one audit entry is required per legacy row")
                cursor.execute(
                    "SELECT COUNT(*) FROM owner_truth.legacy_migration_entries "
                    "WHERE target_state <> 'notCreated'"
                )
                require(cursor.fetchone()[0] == 0, "observer must not create migration targets")
                cursor.execute("SELECT authority_epoch FROM owner_truth.vaults WHERE vault_id = %s", (context.vault_id,))
                require(cursor.fetchone()[0] == 0, "observer must not change the authority epoch")
                cursor.execute("SELECT COUNT(*) FROM owner_truth.sources WHERE vault_id = %s", (context.vault_id,))
                require(cursor.fetchone()[0] == 1, "observer must not create another Source")
                cursor.execute("SELECT COUNT(*) FROM owner_truth.memory_candidates WHERE vault_id = %s", (context.vault_id,))
                require(cursor.fetchone()[0] == 0, "observer must not create Candidates")
                cursor.execute("SELECT COUNT(*) FROM owner_truth.memories WHERE vault_id = %s", (context.vault_id,))
                require(cursor.fetchone()[0] == 0, "observer must not create Memories")
                cursor.execute(
                    "SELECT COALESCE(string_agg(summary::text, ''), '') "
                    "FROM owner_truth.legacy_migration_runs"
                )
                report_text = cursor.fetchone()[0]
                require(archive_body not in report_text, "persisted inventory leaked archive body")
                require(memory_body not in report_text, "persisted inventory leaked memory body")

        print(
            "owner truth legacy shadow parity postgres smoke passed "
            f"schemaHead={verified['expectedHead']} entries={summary['legacyEntryCount']} "
            f"status={summary['comparisonStatus']}"
        )
    finally:
        if store is not None:
            store.close_pool()
        try:
            drop_database(admin_dsn, database_name)
        except Exception as exc:  # pragma: no cover - cleanup diagnostics only
            print(f"warning: failed to drop temporary database {database_name}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
