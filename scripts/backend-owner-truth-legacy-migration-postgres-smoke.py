#!/usr/bin/env python3
"""Exercise hash-only Owner Truth legacy inventory in a disposable Postgres DB.

The smoke intentionally seeds only synthetic legacy data.  It verifies that
inventory/replay/checkpoint behavior is deterministic, that no raw body is
persisted in the V4 audit rows, and that no V4 Source/Candidate/Memory target
is created by this read-only slice.
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
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_legacy_migration import OwnerTruthLegacyMigrationInventoryService
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


def expect_rejected(dsn: str, operation, message: str) -> None:
    rejected = False
    try:
        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                operation(cursor)
    except Exception:
        rejected = True
    require(rejected, message)


def seed_legacy_rows(dsn: str) -> None:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO archive_items (
                    id, user_id, payload, vault_id, owner_subject_id, authority_state
                ) VALUES (%s, %s, %s, %s, %s, 'active')
                """,
                (
                    "archive-legacy-observed",
                    "owner-a",
                    Jsonb({"note": "archive secret body must never enter V4 inventory rows"}),
                    "owner-a",
                    "owner-a",
                ),
            )
            cursor.execute(
                """
                INSERT INTO archive_items (
                    id, user_id, payload, vault_id, owner_subject_id, authority_state
                ) VALUES (%s, %s, %s, %s, %s, 'quarantined')
                """,
                (
                    "archive-legacy-quarantined",
                    "owner-a",
                    Jsonb({"note": "ambiguous legacy owner body"}),
                    "owner-a",
                    "owner-a",
                ),
            )
            cursor.execute(
                """
                INSERT INTO memories (
                    id, user_id, payload, vault_id, owner_subject_id, authority_state
                ) VALUES (%s, %s, %s, %s, %s, 'active')
                """,
                (
                    "memory-legacy-needs-review",
                    "owner-a",
                    Jsonb({"summary": "legacy memory body without source decision evidence"}),
                    "owner-a",
                    "owner-a",
                ),
            )
            cursor.execute(
                """
                INSERT INTO kb_snapshots (user_id, graph, revision)
                VALUES (%s, %s, %s)
                """,
                ("owner-a", Jsonb({"facts": ["private KBLite snapshot"]}), 7),
            )
            cursor.execute(
                """
                INSERT INTO kb_changes (user_id, revision, operation_id, graph, mutation)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    "owner-a",
                    7,
                    "legacy-kb-operation",
                    Jsonb({"facts": ["private KBLite change"]}),
                    Jsonb({"op": "replace"}),
                ),
            )
            cursor.execute(
                """
                INSERT INTO kb_operation_receipts (
                    user_id, operation_id, operation_kind, schema_version, payload_hash, result
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    "owner-a",
                    "legacy-kb-operation",
                    "sync",
                    1,
                    "a" * 64,
                    Jsonb({"result": "private operation receipt body"}),
                ),
            )
            cursor.execute(
                """
                INSERT INTO archive_items (
                    id, user_id, payload, vault_id, owner_subject_id, authority_state
                ) VALUES (%s, %s, %s, %s, %s, 'active')
                """,
                (
                    "archive-other-owner",
                    "owner-b",
                    Jsonb({"note": "must not enter owner-a inventory"}),
                    "owner-b",
                    "owner-b",
                ),
            )
        connection.commit()


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_owner_truth_legacy_smoke_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    store: PostgresStore | None = None

    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="owner-truth-legacy-inventory-g2",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        applied = migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")
        require(
            applied["appliedVersions"][-1] == "0023",
            "owner truth legacy inventory migration must apply",
        )
        seed_legacy_rows(test_dsn)

        store = PostgresStore(dsn=test_dsn, pool_min_size=1, pool_max_size=2)
        store.open_pool(wait=True)
        service = OwnerTruthLegacyMigrationInventoryService(store, enabled=True)
        context = OwnerTruthCommandContext(
            vault_id="vault-owner-a",
            owner_subject_id="owner-a",
            actor_subject_id="owner-a",
        )
        created = service.inventory(context=context)
        replayed = service.inventory(context=context)
        require(created.outcome == "created", "first inventory must create one immutable run")
        require(replayed.outcome == "deduplicated", "unchanged inventory must replay")
        require(created.run_id == replayed.run_id, "replay must retain the same run id")
        require(created.inventory.summary()["entryCount"] == 6, "all scoped legacy rows must classify")
        require(
            created.inventory.summary()["classificationCounts"]
            == {"needs_review": 1, "observed_candidate": 4, "quarantine": 1},
            "legacy rows must retain conservative classifications",
        )
        conversation_checkpoint = next(
            checkpoint
            for checkpoint in created.checkpoints
            if checkpoint.domain.value == "conversationCache"
        )
        require(
            conversation_checkpoint.availability == "unavailable",
            "non-persistent conversation cache must be explicitly unavailable",
        )

        with psycopg.connect(test_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM owner_truth.legacy_migration_runs")
                require(cursor.fetchone()[0] == 1, "replay must not create a second run")
                cursor.execute("SELECT COUNT(*) FROM owner_truth.legacy_migration_entries")
                require(cursor.fetchone()[0] == 6, "every legacy item needs one audit entry")
                cursor.execute(
                    "SELECT COUNT(*) FROM owner_truth.legacy_migration_entries "
                    "WHERE target_state <> 'notCreated'"
                )
                require(cursor.fetchone()[0] == 0, "inventory must never create a migration target")
                cursor.execute("SELECT COUNT(*) FROM owner_truth.sources")
                require(cursor.fetchone()[0] == 0, "inventory must not create a Source")
                cursor.execute("SELECT COUNT(*) FROM owner_truth.memory_candidates")
                require(cursor.fetchone()[0] == 0, "inventory must not create a Candidate")
                cursor.execute("SELECT COUNT(*) FROM owner_truth.memories")
                require(cursor.fetchone()[0] == 0, "inventory must not create a Memory")
                cursor.execute(
                    "SELECT COALESCE(string_agg(summary::text, ''), '') "
                    "FROM owner_truth.legacy_migration_runs"
                )
                report_text = cursor.fetchone()[0]
                require("archive secret body" not in report_text, "run summary leaked archive body")
                require("private KBLite" not in report_text, "run summary leaked KBLite body")
                cursor.execute(
                    "SELECT COALESCE(string_agg(record_hash || reason_code, ''), '') "
                    "FROM owner_truth.legacy_migration_entries"
                )
                entry_text = cursor.fetchone()[0]
                require("archive secret body" not in entry_text, "entry leaked archive body")
        expect_rejected(
            test_dsn,
            lambda cursor: cursor.execute(
                "UPDATE owner_truth.legacy_migration_entries SET reason_code = 'tampered' "
                "WHERE run_id = %s AND domain = 'archiveItem'",
                (created.run_id,),
            ),
            "legacy migration entries must be append-only",
        )

        with psycopg.connect(test_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE kb_snapshots SET graph = %s WHERE user_id = %s",
                    (Jsonb({"facts": ["same revision but changed legacy body"]}), "owner-a"),
                )
            connection.commit()
        changed = service.inventory(context=context)
        require(changed.outcome == "created", "changed legacy body must produce a new run")
        require(
            changed.inventory.inventory_hash != created.inventory.inventory_hash,
            "changed legacy body must change the inventory hash",
        )
        with psycopg.connect(test_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM owner_truth.legacy_migration_runs")
                require(cursor.fetchone()[0] == 2, "changed inventory must append a second run")
                cursor.execute(
                    "SELECT run_id FROM owner_truth.legacy_migration_checkpoints "
                    "WHERE vault_id = %s AND classifier_version = %s AND domain = 'kbSnapshot'",
                    (context.vault_id, changed.inventory.classifier_version),
                )
                require(
                    str(cursor.fetchone()[0]) == changed.run_id,
                    "latest checkpoint must move to the new inventory run",
                )

        print(
            "owner truth legacy inventory postgres smoke passed "
            f"schemaHead={verified['migrationVersion']} entries={len(created.inventory.entries)}"
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
