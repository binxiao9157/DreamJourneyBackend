#!/usr/bin/env python3
"""Exercise C04 tail shadow persistence in a disposable Postgres database.

The smoke uses only synthetic legacy content in a temporary database.  It
proves C03-plan binding, deterministic replay, append-only checkpoints and
zero real side effects.  It never creates an async effect/job, invokes a
Provider, touches object storage, or writes deployed application data.
"""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from typing import Optional
import uuid

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb

from app.async_effects.provider_effects import provider_effect_catalog
from app.core.config import settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.domain.owner_truth.legacy_migration import LegacyMigrationDomain
from app.domain.owner_truth.legacy_tail_shadow import (
    LegacyTailShadowChannel,
    LegacyTailShadowOperation,
)
from app.domain.owner_truth.source_commands import CreateTextSourceCommand, OwnerTruthCommandContext
from app.services.owner_truth_legacy_backfill import OwnerTruthLegacyBackfillPlanService
from app.services.owner_truth_legacy_tail_shadow import OwnerTruthLegacyTailShadowService
from app.services.owner_truth_source import OwnerTruthSourceCommandService
from app.services.postgres_store import PostgresStore


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


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


def seed_synthetic_legacy_archive(
    dsn: str,
    *,
    vault_id: str,
    owner_subject_id: str,
    raw_body: str,
) -> None:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO archive_items (
                    id, user_id, payload, vault_id, owner_subject_id, authority_state
                ) VALUES (%s, %s, %s, %s, %s, 'active')
                """,
                (
                    "legacy-tail-shadow-archive",
                    owner_subject_id,
                    Jsonb({"note": raw_body}),
                    vault_id,
                    owner_subject_id,
                ),
            )
        connection.commit()


def build_operations(plan) -> list[LegacyTailShadowOperation]:
    eligible = [
        entry
        for entry in plan.entries
        if entry.action.value
        in {
            "requireIndependentLineageReplay",
            "requireOwnerCandidateReview",
            "requireEvidenceReview",
        }
    ]
    require(eligible, "synthetic legacy inventory must create at least one eligible C03 entry")
    operations: list[LegacyTailShadowOperation] = []
    for index, entry in enumerate(eligible, start=1):
        operations.append(
            LegacyTailShadowOperation(
                channel=LegacyTailShadowChannel.OUTBOX_JOB,
                domain=entry.domain,
                legacy_id_hash=entry.legacy_id_hash,
                record_hash=entry.record_hash,
                tail_cursor_hash=digest(f"outbox-tail:{entry.legacy_id_hash}:{index}"),
                source_version=1,
            )
        )
    archive = next(
        entry for entry in eligible if entry.domain is LegacyMigrationDomain.ARCHIVE_ITEM
    )
    operations.append(
        LegacyTailShadowOperation(
            channel=LegacyTailShadowChannel.OBJECT_REFERENCE,
            domain=archive.domain,
            legacy_id_hash=archive.legacy_id_hash,
            record_hash=archive.record_hash,
            tail_cursor_hash=digest("archive-object-tail"),
            source_version=1,
            object_reference_hash=digest("synthetic-object-reference"),
        )
    )
    for index, catalog_entry in enumerate(provider_effect_catalog(), start=100):
        if catalog_entry.requires_stable_provider_effect:
            operations.append(
                LegacyTailShadowOperation(
                    channel=LegacyTailShadowChannel.PROVIDER_EFFECT,
                    domain=archive.domain,
                    legacy_id_hash=archive.legacy_id_hash,
                    record_hash=archive.record_hash,
                    tail_cursor_hash=digest(f"provider-tail:{index}"),
                    source_version=1,
                    provider_catalog_key=catalog_entry.key,
                    callback_fixture_hash=digest(f"callback-fixture:{catalog_entry.key}"),
                )
            )
    return operations


def assert_zero_side_effects(dsn: str, *, vault_id: str, raw_body: str) -> None:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT effect_execution_count, outbox_write_count, job_write_count,
                    object_storage_operation_count, provider_call_count,
                    provider_callback_processed_count, callback_accepted_count,
                    shadow_only, cutover_allowed, legacy_writer_retired
                FROM owner_truth.legacy_migration_tail_shadow_reports
                WHERE vault_id = %s
                """,
                (vault_id,),
            )
            row = cursor.fetchone()
            require(row is not None, "one C04 shadow report must be persisted")
            require(
                tuple(row[:7]) == (0, 0, 0, 0, 0, 0, 0),
                "C04 report must prove every real side-effect count remains zero",
            )
            require(
                tuple(row[7:]) == (True, False, False),
                "C04 report must remain shadow-only and non-authorizing",
            )
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM owner_truth.legacy_migration_tail_shadow_reports
                WHERE vault_id = %s
                """,
                (vault_id,),
            )
            require(cursor.fetchone()[0] == 1, "replay must not append a second equivalent report")
            cursor.execute(
                """
                SELECT report.mapping_count, COUNT(mapping.mapping_hash)
                FROM owner_truth.legacy_migration_tail_shadow_reports AS report
                LEFT JOIN owner_truth.legacy_migration_tail_shadow_mappings AS mapping
                  ON mapping.plan_id = report.plan_id
                 AND mapping.report_hash = report.report_hash
                WHERE report.vault_id = %s
                GROUP BY report.mapping_count
                """,
                (vault_id,),
            )
            mapping_counts = cursor.fetchone()
            require(
                mapping_counts is not None and int(mapping_counts[0]) == int(mapping_counts[1]),
                "C04 mapping checkpoint must match persisted mapping rows",
            )
            cursor.execute(
                "SELECT COUNT(*) FROM async_effects.operations")
            require(cursor.fetchone()[0] == 0, "C04 must not create an async effect operation")
            cursor.execute("SELECT COUNT(*) FROM async_effects.outbox_events")
            require(cursor.fetchone()[0] == 0, "C04 must not write an outbox event")
            cursor.execute("SELECT COUNT(*) FROM async_effects.jobs")
            require(cursor.fetchone()[0] == 0, "C04 must not create a job")
            cursor.execute("SELECT COUNT(*) FROM async_effects.provider_effects")
            require(cursor.fetchone()[0] == 0, "C04 must not create a Provider effect")
            cursor.execute(
                "SELECT COALESCE(string_agg(report_hash || plan_hash || unmapped_provider_catalog_keys::text, ''), '') "
                "FROM owner_truth.legacy_migration_tail_shadow_reports WHERE vault_id = %s",
                (vault_id,),
            )
            report_text = str(cursor.fetchone()[0] or "")
            cursor.execute(
                "SELECT COALESCE(string_agg(mapping_hash || source_legacy_id_hash || source_record_hash, ''), '') "
                "FROM owner_truth.legacy_migration_tail_shadow_mappings",
            )
            mapping_text = str(cursor.fetchone()[0] or "")
            require(raw_body not in report_text and raw_body not in mapping_text, "C04 rows leaked raw legacy content")
            require(
                "legacy-tail-shadow-archive" not in report_text
                and "legacy-tail-shadow-archive" not in mapping_text,
                "C04 rows leaked raw legacy identifiers",
            )


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_owner_truth_tail_shadow_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    store: Optional[PostgresStore] = None
    owner_subject_id = "owner-tail-shadow-smoke"
    vault_id = "vault-tail-shadow-smoke"
    raw_body = "synthetic C04 archive body must never enter shadow evidence"

    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="owner-truth-legacy-tail-shadow-g2",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        applied = migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")
        require("0047" in applied["appliedVersions"], "C03 plan migration must apply")
        require("0048" in applied["appliedVersions"], "C04 tail shadow migration must apply")
        require(
            applied["appliedVersions"][-1] == verified["expectedHead"],
            "temporary database must be migrated to the current head",
        )

        store = PostgresStore(dsn=test_dsn, pool_min_size=1, pool_max_size=3)
        store.open_pool(wait=True)
        context = OwnerTruthCommandContext(
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            actor_subject_id=owner_subject_id,
        )
        source_result = OwnerTruthSourceCommandService(store).create_text_source(
            command=CreateTextSourceCommand(
                command_id="c04-tail-shadow-vault-seed",
                source_id=str(uuid.uuid4()),
                expected_version=0,
                text="synthetic owner vault seed for C04 shadow persistence",
                metadata={"origin": "ownerTruthLegacyTailShadowPostgresSmoke"},
            ),
            context=context,
        )
        require(source_result.outcome == "created", "synthetic Source must establish an active Vault")
        seed_synthetic_legacy_archive(
            test_dsn,
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            raw_body=raw_body,
        )

        c03 = OwnerTruthLegacyBackfillPlanService(store, enabled=True).plan(context=context)
        require(c03.outcome == "created", "C03 plan must persist before C04")
        operations = build_operations(c03.plan)
        c04_service = OwnerTruthLegacyTailShadowService(store, enabled=True)
        created = c04_service.shadow(context=context, plan=c03.plan, operations=operations)
        replayed = c04_service.shadow(
            context=context,
            plan=c03.plan,
            operations=list(reversed(operations)),
        )
        require(created.outcome == "created", "first C04 tail report must be created")
        require(replayed.outcome == "deduplicated", "same C04 tail report must deduplicate")
        require(created.report.report_hash == replayed.report.report_hash, "C04 report must be deterministic")
        require(created.report.ready_for_next_gate, "complete synthetic C04 mapping must have no gaps")
        assert_zero_side_effects(test_dsn, vault_id=vault_id, raw_body=raw_body)

        expect_rejected(
            test_dsn,
            lambda cursor: cursor.execute(
                "UPDATE owner_truth.legacy_migration_tail_shadow_reports "
                "SET mapping_count = 0 WHERE vault_id = %s",
                (vault_id,),
            ),
            "C04 shadow reports must be append-only",
        )
        expect_rejected(
            test_dsn,
            lambda cursor: cursor.execute(
                """
                INSERT INTO owner_truth.legacy_migration_tail_shadow_reports (
                    plan_id, report_hash, plan_hash, vault_id, owner_subject_id,
                    authority_epoch, input_operation_count, duplicate_input_count,
                    required_outbox_entry_count, missing_outbox_mapping_count,
                    archive_object_evidence_gap_count, unmapped_provider_catalog_keys,
                    mapping_count, tail_checkpoint_hash, schema_version
                ) VALUES (%s, %s, %s, %s, %s, %s, 1, 0, 1, 1, 0, %s, 1, %s, %s)
                """,
                (
                    c03.plan.plan_id,
                    digest("incomplete-report"),
                    c03.plan.plan_hash,
                    vault_id,
                    owner_subject_id,
                    c03.plan.authority_epoch,
                    Jsonb([]),
                    digest("incomplete-checkpoint"),
                    "owner-truth-legacy-tail-shadow-v1",
                ),
            ),
            "C04 report must fail at commit when mapping checkpoint is incomplete",
        )

        print(
            "owner truth legacy tail shadow postgres smoke passed "
            f"schemaHead={verified['expectedHead']} mappings={len(created.report.mappings)} "
            "sideEffects=0 replay=deduplicated appendOnly=verified"
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
