#!/usr/bin/env python3
"""Exercise Owner Truth V1 constraints in an isolated temporary database.

The script creates a disposable database from DATABASE_URL, applies the full
migration set, tests the V1 invariants, then removes the database. It never
writes test records to the configured application database.
"""

from __future__ import annotations

import json
import os
import sys
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
from app.domain.owner_truth.source_commands import (
    CreateTextSourceCommand,
    OwnerTruthCommandContext,
    OwnerTruthSourceCommandConflict,
    OwnerTruthSourceVersionConflict,
)
from app.services.owner_truth_source import (
    ArchiveOwnerTruthCompatibilityFacade,
    OwnerTruthSourceCommandService,
)
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


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_owner_truth_smoke_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)

    ids = [str(uuid.UUID(int=index)) for index in range(1, 12)]
    source_id, candidate_id, receipt_id, memory_a, version_a, memory_b, version_b, relation_a, relation_cycle, memory_other, version_other = ids

    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="owner-truth-g2",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        applied = migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")
        require(applied["appliedVersions"][-1] == "0012", "owner truth CreateSource migration must apply")

        with psycopg.connect(test_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO owner_truth.vaults (vault_id, owner_subject_id) VALUES (%s, %s)",
                    ("vault-a", "owner-a"),
                )
                cursor.execute(
                    """
                    INSERT INTO owner_truth.sources (
                        id, vault_id, owner_subject_id, source_kind, content_hash,
                        policy_version, authority_epoch
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (source_id, "vault-a", "owner-a", "text", "source-hash", "policy-v1", 0),
                )
                cursor.execute(
                    """
                    INSERT INTO owner_truth.memory_candidates (
                        id, vault_id, owner_subject_id, source_id, candidate_kind,
                        perspective_type, epistemic_status, policy_version,
                        authority_epoch, content_hash, payload_schema_version, payload
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '{}'::jsonb)
                    """,
                    (
                        candidate_id,
                        "vault-a",
                        "owner-a",
                        source_id,
                        "experience",
                        "firstPerson",
                        "recalled",
                        "policy-v1",
                        0,
                        "candidate-hash",
                        "owner-truth-v1",
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO owner_truth.memories (
                        id, vault_id, owner_subject_id, source_id, source_version,
                        memory_kind, perspective_type, epistemic_status, policy_version,
                        content_hash, authority_epoch
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        memory_a,
                        "vault-a",
                        "owner-a",
                        source_id,
                        1,
                        "experience",
                        "firstPerson",
                        "recalled",
                        "policy-v1",
                        "memory-a-hash",
                        0,
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO owner_truth.memory_versions (
                        id, vault_id, memory_id, version_number, is_current,
                        schema_version, content_hash
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (version_a, "vault-a", memory_a, 1, True, "owner-truth-v1", "memory-a-hash"),
                )
                cursor.execute(
                    """
                    INSERT INTO owner_truth.memories (
                        id, vault_id, owner_subject_id, memory_kind, perspective_type,
                        epistemic_status, policy_version, content_hash, authority_epoch
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (memory_b, "vault-a", "owner-a", "knowledge", "reported", "reported", "policy-v1", "memory-b-hash", 0),
                )
                cursor.execute(
                    """
                    INSERT INTO owner_truth.memory_versions (
                        id, vault_id, memory_id, version_number, is_current,
                        schema_version, content_hash
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (version_b, "vault-a", memory_b, 1, True, "owner-truth-v1", "memory-b-hash"),
                )

        with psycopg.connect(test_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO owner_truth.memory_relations (
                        id, vault_id, from_memory_id, to_memory_id, relation_type
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (relation_a, "vault-a", memory_a, memory_b, "references"),
                )
                cursor.execute(
                    "UPDATE owner_truth.memory_candidates SET decision_status = 'accepted' WHERE id = %s",
                    (candidate_id,),
                )
                cursor.execute(
                    """
                    INSERT INTO owner_truth.decision_receipts (
                        id, vault_id, candidate_id, decision, actor_subject_id,
                        authority_epoch, policy_version
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (receipt_id, "vault-a", candidate_id, "accepted", "owner-a", 0, "policy-v1"),
                )

        expect_rejected(
            test_dsn,
            lambda cursor: cursor.execute(
                """
                INSERT INTO owner_truth.memory_versions (
                    id, vault_id, memory_id, version_number, is_current,
                    schema_version, content_hash
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (str(uuid.UUID(int=21)), "vault-a", memory_a, 2, True, "owner-truth-v1", "memory-a-hash-2"),
            ),
            "a memory may have only one current version",
        )
        expect_rejected(
            test_dsn,
            lambda cursor: cursor.execute(
                """
                INSERT INTO owner_truth.memory_relations (
                    id, vault_id, from_memory_id, to_memory_id, relation_type
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (relation_cycle, "vault-a", memory_b, memory_a, "references"),
            ),
            "memory relation cycles must be rejected",
        )
        expect_rejected(
            test_dsn,
            lambda cursor: cursor.execute(
                "UPDATE owner_truth.memory_candidates SET decision_status = 'rejected' WHERE id = %s",
                (candidate_id,),
            ),
            "terminal candidate decisions must be immutable",
        )
        expect_rejected(
            test_dsn,
            lambda cursor: cursor.execute(
                "UPDATE owner_truth.decision_receipts SET decision = 'rejected' WHERE id = %s",
                (receipt_id,),
            ),
            "decision receipts must be append-only",
        )
        expect_rejected(
            test_dsn,
            lambda cursor: cursor.execute(
                "DELETE FROM owner_truth.sources WHERE id = %s",
                (source_id,),
            ),
            "referenced sources must not be deleted",
        )

        store = PostgresStore(
            dsn=test_dsn,
            pool_min_size=1,
            pool_max_size=1,
            pool_timeout_seconds=1.0,
        )
        store.open_pool(wait=True)
        try:
            source_context = OwnerTruthCommandContext(
                vault_id="source-command-vault",
                owner_subject_id="source-command-owner",
                actor_subject_id="source-command-owner",
                policy_version="owner-truth-v1",
            )
            command_id = "owner-truth-create-source-smoke"
            source_command = CreateTextSourceCommand(
                command_id=command_id,
                source_id=str(uuid.uuid4()),
                expected_version=0,
                text="一段只用于 Owner Truth CreateSource 验证的文字记录。",
                metadata={"origin": "postgresSmoke"},
            )
            source_service = OwnerTruthSourceCommandService(store)
            created_source = source_service.create_text_source(
                command=source_command,
                context=source_context,
            )
            replayed_source = source_service.create_text_source(
                command=source_command,
                context=source_context,
            )
            require(created_source.outcome == "created", "CreateSource must create once")
            require(replayed_source.outcome == "deduplicated", "CreateSource replay must deduplicate")
            require(
                replayed_source.receipt_id == created_source.receipt_id,
                "CreateSource replay must preserve the receipt",
            )

            try:
                source_service.create_text_source(
                    command=CreateTextSourceCommand(
                        command_id=command_id,
                        source_id=source_command.source_id,
                        expected_version=0,
                        text="同一 commandId 不能覆盖已经写入的源文本。",
                        metadata={"origin": "postgresSmoke"},
                    ),
                    context=source_context,
                )
            except OwnerTruthSourceCommandConflict:
                pass
            else:
                raise AssertionError("CreateSource must reject a changed replay payload")

            try:
                source_service.create_text_source(
                    command=CreateTextSourceCommand(
                        command_id="owner-truth-create-source-version-conflict",
                        source_id=str(uuid.uuid4()),
                        expected_version=1,
                        text="不允许以非零 expectedVersion 创建新 Source。",
                        metadata={"origin": "postgresSmoke"},
                    ),
                    context=source_context,
                )
            except OwnerTruthSourceVersionConflict:
                pass
            else:
                raise AssertionError("CreateSource must reject a nonzero expectedVersion")

            facade = ArchiveOwnerTruthCompatibilityFacade(store)
            shadow = facade.shadow_archive_item(
                owner_subject_id="archive-shadow-owner",
                item={
                    "id": "archive-shadow-text-smoke",
                    "kind": "text",
                    "title": "Archive shadow",
                    "note": "旧档案文字继续保留原有 authority。",
                },
            )
            media_shadow = facade.shadow_archive_item(
                owner_subject_id="archive-shadow-owner",
                item={"id": "archive-shadow-photo-smoke", "kind": "photo", "title": "设备照片"},
            )
            require(shadow.status == "created", "eligible Archive text must create a shadow Source")
            require(
                media_shadow.public_contract() == {"status": "skipped", "reason": "localOnlyMedia"},
                "Archive media must remain explicitly local-only in V1",
            )
        finally:
            store.close_pool()

        expect_rejected(
            test_dsn,
            lambda cursor: cursor.execute(
                "UPDATE owner_truth.sources SET content_payload = '{\"text\": \"mutated\"}'::jsonb WHERE id = %s",
                (created_source.source_id,),
            ),
            "Owner Truth Source payloads must be immutable",
        )
        expect_rejected(
            test_dsn,
            lambda cursor: cursor.execute(
                "UPDATE owner_truth.source_command_receipts SET payload_hash = 'mutated' WHERE id = %s",
                (created_source.receipt_id,),
            ),
            "CreateSource command receipts must be append-only",
        )

        with psycopg.connect(test_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT to_regclass('public.memories'), to_regclass('public.archive_items'), "
                    "to_regclass('owner_truth.memories')"
                )
                legacy_relation, archive_relation, owner_truth_relation = cursor.fetchone()
        require(legacy_relation == "memories", "legacy public memories table must remain present")
        require(archive_relation == "archive_items", "legacy public Archive table must remain present")
        require(owner_truth_relation == "owner_truth.memories", "owner truth table must be namespaced")

        print(
            json.dumps(
                {
                    "status": "passed",
                    "schemaHead": verified["expectedHead"],
                    "ownerTruthNamespace": True,
                    "singleCurrentVersion": True,
                    "relationCycleRejected": True,
                    "terminalDecisionImmutable": True,
                    "decisionReceiptAppendOnly": True,
                    "sourceDeleteRestricted": True,
                    "createSourceIdempotent": True,
                    "createSourcePayloadImmutable": True,
                    "createSourceReceiptAppendOnly": True,
                    "archiveTextShadowed": True,
                    "archiveMediaLocalOnly": True,
                    "legacyMemoriesUnchanged": True,
                    "legacyArchiveUnchanged": True,
                },
                sort_keys=True,
            )
        )
    finally:
        drop_database(admin_dsn, database_name)


if __name__ == "__main__":
    main()
