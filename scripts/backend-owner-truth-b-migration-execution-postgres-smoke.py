#!/usr/bin/env python3
"""Exercise bounded legacy review replay in a disposable PostgreSQL DB.

The script creates and drops only a uniquely named database.  It proves that
the production PostgresStore keeps the migration checkpoint, imported Source,
and candidate-extraction effect atomic; retries and concurrent invocations do
not duplicate targets; no formal MemoryVersion is written.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
from typing import Any
import uuid

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row

from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.domain.owner_truth.source_commands import (
    CreateTextSourceCommand,
    OwnerTruthCommandContext,
)
from app.services.owner_truth_b_migration_dry_run import OwnerTruthBMigrationDryRunService
from app.services.owner_truth_b_migration_execution import (
    OwnerTruthBMigrationExecutionService,
)
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


def seed_vault(store: PostgresStore, *, context: OwnerTruthCommandContext) -> None:
    OwnerTruthSourceCommandService(store).create_text_source(
        command=CreateTextSourceCommand(
            command_id=f"b-migration-smoke-vault:{context.vault_id}",
            source_id=str(uuid.uuid4()),
            expected_version=0,
            text="synthetic vault seed",
            metadata={"synthetic": True},
        ),
        context=context,
    )


def seed_legacy_rows(dsn: str, *, owner_subject_id: str, suffix: str) -> None:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO archive_items (
                    id, user_id, owner_subject_id, authority_state, payload
                ) VALUES (%s, %s, %s, 'active', %s::jsonb)
                """,
                (
                    f"legacy-archive-{suffix}",
                    owner_subject_id,
                    owner_subject_id,
                    json.dumps(
                        {
                            "id": f"legacy-archive-{suffix}",
                            "kind": "text",
                            "title": "合成旧档案",
                            "note": "这是用于隔离迁移验证的合成正文。",
                            "userId": owner_subject_id,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            cursor.execute(
                """
                INSERT INTO memories (
                    id, user_id, owner_subject_id, authority_state, payload
                ) VALUES (%s, %s, %s, 'active', %s::jsonb)
                """,
                (
                    f"legacy-memory-{suffix}",
                    owner_subject_id,
                    owner_subject_id,
                    json.dumps(
                        {
                            "id": f"legacy-memory-{suffix}",
                            "summary": "我曾在另一座城市求学。",
                            "userId": owner_subject_id,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
        connection.commit()


class FailFirstCompletionStore(PostgresStore):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fail_next_completion = True

    def owner_truth_b_migration_execution_repository(self):
        repository = super().owner_truth_b_migration_execution_repository()
        if not self.fail_next_completion:
            return repository
        self.fail_next_completion = False

        class FailingRepository:
            def prepare_and_claim(inner_self, **kwargs):
                return repository.prepare_and_claim(**kwargs)

            def complete_entry(inner_self, **kwargs):
                del inner_self, kwargs
                raise RuntimeError("synthetic failure after Source and effect")

            def summarize(inner_self, **kwargs):
                return repository.summarize(**kwargs)

        return FailingRepository()


def counts(dsn: str, *, vault_id: str) -> dict[str, int]:
    queries = {
        "executionRuns": "SELECT COUNT(*) FROM owner_truth.b_migration_execution_runs WHERE vault_id = %s",
        "executionReceipts": """
            SELECT COUNT(*)
              FROM owner_truth.b_migration_execution_receipts AS receipt
              JOIN owner_truth.b_migration_execution_runs AS run ON run.id = receipt.run_id
             WHERE run.vault_id = %s
        """,
        "importSources": "SELECT COUNT(*) FROM owner_truth.sources WHERE vault_id = %s AND source_kind = 'import'",
        "effects": "SELECT COUNT(*) FROM async_effects.operations WHERE vault_id = %s AND purpose = 'candidateExtraction'",
        "formalMemories": "SELECT COUNT(*) FROM owner_truth.memories WHERE vault_id = %s",
    }
    result: dict[str, int] = {}
    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            for name, query in queries.items():
                cursor.execute(query, (vault_id,))
                row = cursor.fetchone()
                result[name] = int(next(iter(row.values())))
    return result


def run_smoke(dsn: str) -> dict[str, object]:
    owner_subject_id = f"terra-a-migration-owner-{uuid.uuid4().hex[:10]}"
    vault_id = f"terra-a-migration-vault-{uuid.uuid4().hex[:10]}"
    context = OwnerTruthCommandContext(
        vault_id=vault_id,
        owner_subject_id=owner_subject_id,
        actor_subject_id=owner_subject_id,
    )
    store = FailFirstCompletionStore(dsn=dsn, pool_min_size=1, pool_max_size=3)
    store.open_pool()
    try:
        seed_vault(store, context=context)
        seed_legacy_rows(dsn, owner_subject_id=owner_subject_id, suffix=uuid.uuid4().hex[:8])
        report = OwnerTruthBMigrationDryRunService(store, enabled=True).dry_run(
            context=context
        ).report
        failed = False
        try:
            OwnerTruthBMigrationExecutionService(store, enabled=True).execute_next_batch(
                context=context,
                batch_size=1,
                report_id=report.report_id,
            )
        except RuntimeError as error:
            require("synthetic failure" in str(error), "unexpected injected failure")
            failed = True
        require(failed, "post-Source failure was not observed")
        after_rollback = counts(dsn, vault_id=vault_id)
        require(after_rollback["executionRuns"] == 0, "execution checkpoint did not roll back")
        require(after_rollback["importSources"] == 0, "Source did not roll back")
        require(after_rollback["effects"] == 0, "effect did not roll back")

        service = OwnerTruthBMigrationExecutionService(store, enabled=True)
        first = service.execute_next_batch(
            context=context,
            batch_size=1,
            report_id=report.report_id,
        )
        require(first.status == "running", "first bounded batch did not checkpoint")

        stores = (
            PostgresStore(dsn=dsn, pool_min_size=1, pool_max_size=1),
            PostgresStore(dsn=dsn, pool_min_size=1, pool_max_size=1),
        )
        for concurrent_store in stores:
            concurrent_store.open_pool()
        try:
            def resume(concurrent_store: PostgresStore):
                return OwnerTruthBMigrationExecutionService(
                    concurrent_store,
                    enabled=True,
                ).execute_next_batch(
                    context=context,
                    batch_size=25,
                    report_id=report.report_id,
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                concurrent_results = tuple(executor.map(resume, stores))
        finally:
            for concurrent_store in stores:
                concurrent_store.close_pool()
        require(
            any(result.status == "completed" for result in concurrent_results),
            "concurrent resume never completed",
        )
        replay = service.execute_next_batch(
            context=context,
            batch_size=25,
            report_id=report.report_id,
        )
        require(replay.status == "completed", "idempotent replay was not completed")
        require(replay.batch_processed_count == 0, "idempotent replay claimed an entry")
        final_counts = counts(dsn, vault_id=vault_id)
        require(final_counts["executionRuns"] == 1, "more than one execution run exists")
        require(final_counts["executionReceipts"] == 2, "one receipt per legacy row is required")
        require(final_counts["importSources"] == 2, "legacy text was not replayed exactly once")
        require(final_counts["effects"] == 2, "candidate extraction effects were duplicated or lost")
        require(final_counts["formalMemories"] == 0, "migration wrote formal memory before review")
        return {
            "afterInjectedRollback": after_rollback,
            "databaseBackend": "postgresql",
            "finalCounts": final_counts,
            "formalMemoryWriteCount": 0,
            "reportHash": report.report_hash,
            "runId": replay.run_id,
            "schemaVersion": "owner-truth-b-migration-execution-postgres-smoke-v1",
            "status": "passed",
        }
    finally:
        store.close_pool()


def main() -> int:
    base_dsn = str(os.environ.get("DATABASE_URL") or "").strip()
    if not base_dsn:
        raise SystemExit("DATABASE_URL is required and must target an isolated PostgreSQL server")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dreamjourney_b_migration_{uuid.uuid4().hex[:12]}"
    app_dsn = dsn_for_database(base_dsn, database_name)
    created = False
    try:
        create_database(admin_dsn, database_name)
        created = True
        migrator = PostgresMigrator(
            dsn=app_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="terra-a-b-migration-postgres-smoke-v1",
            lock_timeout_ms=2_000,
            statement_timeout_ms=30_000,
        )
        applied = migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")
        payload = run_smoke(app_dsn)
        payload["appliedMigrationCount"] = len(applied.get("appliedVersions", ()))
        payload["migrationHead"] = verified.get("schemaHead") or verified.get("head")
        payload["databaseNameHash"] = uuid.uuid5(uuid.NAMESPACE_URL, database_name).hex
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0
    finally:
        if created:
            drop_database(admin_dsn, database_name)


if __name__ == "__main__":
    raise SystemExit(main())
