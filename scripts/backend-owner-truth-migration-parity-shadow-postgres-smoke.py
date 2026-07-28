#!/usr/bin/env python3
"""Exercise C05 parity evidence in a disposable Postgres database.

The smoke uses a synthetic Owner/Vault and opaque SHA-256 descriptors only.
It proves scope fencing, deterministic replay, immutable mismatch evidence and
zero command/object/Provider side effects. It never accesses deployed data or
calls an external Provider.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import os
from pathlib import Path
import sys
from typing import Optional, Tuple
import uuid

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.core.config import settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.domain.owner_truth.migration_parity_shadow import (
    MigrationParityAllowance,
    MigrationParityComparisonWindow,
    MigrationParityDimension,
    MigrationParityObservation,
    MigrationParitySurface,
    build_migration_parity_scope_hash,
)
from app.domain.owner_truth.source_commands import CreateTextSourceCommand, OwnerTruthCommandContext
from app.services.owner_truth_migration_parity_shadow import (
    OwnerTruthMigrationParityShadowService,
)
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


def authority_epoch(dsn: str, *, vault_id: str) -> int:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT authority_epoch FROM owner_truth.vaults WHERE vault_id = %s",
                (vault_id,),
            )
            row = cursor.fetchone()
    require(row is not None, "synthetic source must establish an active Vault")
    return int(row[0])


def async_effect_counts(dsn: str) -> Tuple[int, int, int, int]:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM async_effects.operations")
            operations = int(cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(*) FROM async_effects.outbox_events")
            outbox_events = int(cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(*) FROM async_effects.jobs")
            jobs = int(cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(*) FROM async_effects.provider_effects")
            provider_effects = int(cursor.fetchone()[0])
    return (operations, outbox_events, jobs, provider_effects)


def build_comparison(
    *,
    vault_id: str,
    owner_subject_id: str,
    epoch: int,
    raw_legacy_marker: str,
):
    now = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
    window = MigrationParityComparisonWindow(
        window_reference_hash=digest("c05-approved-window"),
        scope_hash=build_migration_parity_scope_hash(
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            authority_epoch=epoch,
        ),
        denominator_source_hash=digest("c05-synthetic-denominator"),
        threshold_source_hash=digest("c05-approved-threshold"),
        expected_sample_count=2,
    )
    matching = MigrationParityObservation(
        sample_id_hash=digest("c05-sample-resource"),
        surface=MigrationParitySurface.READ,
        dimension=MigrationParityDimension.RESOURCE_IDENTITY,
        legacy_value_hash=digest("same-resource"),
        v4_value_hash=digest("same-resource"),
    )
    reviewable = MigrationParityObservation(
        sample_id_hash=digest("c05-sample-display"),
        surface=MigrationParitySurface.PROJECTION,
        dimension=MigrationParityDimension.DISPLAY_NORMALIZATION,
        legacy_value_hash=digest(raw_legacy_marker),
        v4_value_hash=digest("normalized-display-v4"),
    )
    allowance = MigrationParityAllowance(
        observation_hash=reviewable.observation_hash,
        reason_code="approvedDisplayNormalization",
        approval_reference_hash=digest("c05-product-data-approval"),
        expires_at=now + timedelta(days=1),
    )
    return window, (matching, reviewable), (allowance,), now


def assert_evidence(
    dsn: str,
    *,
    vault_id: str,
    raw_legacy_marker: str,
    counts_before: Tuple[int, int, int, int],
) -> None:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT command_effect_execution_count, object_copy_execution_count,
                    provider_call_count, provider_cost_charged, write_operation_count,
                    shadow_only, cutover_allowed, legacy_writer_retired,
                    mismatch_count, approved_m08_difference_count,
                    unresolved_m08_difference_count
                FROM owner_truth.migration_parity_shadow_reports
                WHERE vault_id = %s
                """,
                (vault_id,),
            )
            row = cursor.fetchone()
            require(row is not None, "one C05 parity report must be persisted")
            require(tuple(row[:5]) == (0, 0, 0, False, 0), "C05 side effects must remain zero")
            require(
                tuple(row[5:8]) == (True, False, False),
                "C05 report must remain shadow-only and non-authorizing",
            )
            require(tuple(row[8:]) == (1, 1, 0), "approved M08 evidence must be counted")
            cursor.execute(
                """
                SELECT report.mismatch_count, COUNT(mismatch.observation_hash)
                FROM owner_truth.migration_parity_shadow_reports AS report
                LEFT JOIN owner_truth.migration_parity_shadow_mismatches AS mismatch
                  ON mismatch.report_hash = report.report_hash
                WHERE report.vault_id = %s
                GROUP BY report.mismatch_count
                """,
                (vault_id,),
            )
            mismatch_counts = cursor.fetchone()
            require(
                mismatch_counts is not None and int(mismatch_counts[0]) == int(mismatch_counts[1]),
                "C05 mismatch count must match append-only evidence rows",
            )
            cursor.execute(
                "SELECT COUNT(*) FROM owner_truth.migration_parity_shadow_reports WHERE vault_id = %s",
                (vault_id,),
            )
            require(cursor.fetchone()[0] == 1, "replay must not append an equivalent report")
            cursor.execute(
                """
                SELECT COALESCE(string_agg(
                    report_hash || scope_hash || denominator_source_hash || threshold_source_hash, ''
                ), '')
                FROM owner_truth.migration_parity_shadow_reports
                WHERE vault_id = %s
                """,
                (vault_id,),
            )
            report_text = str(cursor.fetchone()[0] or "")
            cursor.execute(
                """
                SELECT COALESCE(string_agg(
                    observation_hash || sample_id_hash || COALESCE(legacy_value_hash, '')
                    || COALESCE(v4_value_hash, ''), ''
                ), '')
                FROM owner_truth.migration_parity_shadow_mismatches
                """,
            )
            mismatch_text = str(cursor.fetchone()[0] or "")
    require(
        raw_legacy_marker not in report_text and raw_legacy_marker not in mismatch_text,
        "C05 evidence must not leak raw legacy comparison content",
    )
    require(
        async_effect_counts(dsn) == counts_before,
        "C05 must not create an async effect, outbox event, job or Provider effect",
    )


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_owner_truth_parity_shadow_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    store: Optional[PostgresStore] = None
    owner_subject_id = "owner-parity-shadow-smoke"
    vault_id = "vault-parity-shadow-smoke"
    raw_legacy_marker = "synthetic C05 private legacy marker must not enter evidence"

    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="owner-truth-migration-parity-shadow-g2",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        applied = migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")
        require("0049" in applied["appliedVersions"], "C05 migration must apply")
        require(
            applied["appliedVersions"][-1] == verified["expectedHead"],
            "temporary database must migrate to the current head",
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
                command_id="c05-parity-shadow-vault-seed",
                source_id=str(uuid.uuid4()),
                expected_version=0,
                text="synthetic owner vault seed for C05 parity evidence",
                metadata={"origin": "ownerTruthMigrationParityShadowPostgresSmoke"},
            ),
            context=context,
        )
        require(source_result.outcome == "created", "synthetic source must establish an active Vault")
        epoch = authority_epoch(test_dsn, vault_id=vault_id)
        window, observations, allowances, as_of = build_comparison(
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            epoch=epoch,
            raw_legacy_marker=raw_legacy_marker,
        )
        counts_before = async_effect_counts(test_dsn)
        service = OwnerTruthMigrationParityShadowService(store, enabled=True)
        created = service.shadow(
            context=context,
            window=window,
            observations=observations,
            allowances=allowances,
            as_of=as_of,
        )
        replayed = service.shadow(
            context=context,
            window=window,
            observations=reversed(observations),
            allowances=allowances,
            as_of=as_of,
        )
        require(created.outcome == "created", "first C05 report must be created")
        require(replayed.outcome == "deduplicated", "same C05 report must deduplicate")
        require(created.report.report_hash == replayed.report.report_hash, "C05 report must be deterministic")
        require(created.report.ready_for_next_gate, "approved synthetic M08 must be review-ready")
        assert_evidence(
            test_dsn,
            vault_id=vault_id,
            raw_legacy_marker=raw_legacy_marker,
            counts_before=counts_before,
        )

        expect_rejected(
            test_dsn,
            lambda cursor: cursor.execute(
                "UPDATE owner_truth.migration_parity_shadow_reports "
                "SET match_count = 0 WHERE vault_id = %s",
                (vault_id,),
            ),
            "C05 reports must be append-only",
        )
        expect_rejected(
            test_dsn,
            lambda cursor: cursor.execute(
                """
                INSERT INTO owner_truth.migration_parity_shadow_mismatches (
                    report_hash, observation_hash, sample_id_hash, surface, dimension,
                    mismatch_code, severity, legacy_value_hash, v4_value_hash,
                    allowance_status
                ) SELECT report_hash, %s, %s, 'command', 'displayNormalization',
                    'M08', 'reviewable', %s, %s, 'missing'
                FROM owner_truth.migration_parity_shadow_reports
                WHERE vault_id = %s
                """,
                (
                    digest("invalid-m08-observation"),
                    digest("invalid-m08-sample"),
                    digest("invalid-m08-legacy"),
                    digest("invalid-m08-v4"),
                    vault_id,
                ),
            ),
            "C05 must reject an M08 command-surface mismatch",
        )

        print(
            "owner truth migration parity shadow postgres smoke passed "
            f"schemaHead={verified['expectedHead']} mismatchCount={len(created.report.mismatches)} "
            "sideEffects=0 replay=deduplicated appendOnly=verified"
        )
    finally:
        if store is not None:
            store.close_pool()
        try:
            drop_database(admin_dsn, database_name)
        except Exception as exc:  # pragma: no cover - cleanup diagnostics only
            print("warning: failed to drop temporary database %s: %s" % (database_name, exc), file=sys.stderr)


if __name__ == "__main__":
    main()
