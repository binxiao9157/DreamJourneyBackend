#!/usr/bin/env python3
"""Exercise account and formal-memory export jobs in disposable Postgres."""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import sys
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.services.data_export_jobs import (
    FORMAL_MEMORY_MARKDOWN_EXPORT_TYPE,
    create_data_export_download_credential,
    create_data_export_job_record,
    materialize_data_export_job,
)
from app.services.data_rights_module_inventory import build_module_owned_data_export
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.formal_memory_markdown_export import (
    formal_memory_markdown_download,
    materialize_formal_memory_markdown_export_job,
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


def canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def seed_formal_memory(
    dsn: str,
    *,
    vault_id: str,
    owner_subject_id: str,
) -> None:
    source_id = str(uuid.uuid4())
    memory_id = str(uuid.uuid4())
    old_version_id = str(uuid.uuid4())
    current_version_id = str(uuid.uuid4())
    old_content = {"summary": "HISTORY_SECRET_MUST_NOT_EXPORT"}
    current_content = {
        "summary": "在老院子里听外祖父讲故事。",
        "facets": {
            "people": [{"value": "外祖父"}],
            "time": [],
            "places": [{"value": "老院子"}],
            "relationships": [],
            "emotions": [],
            "values": [],
            "personality": [],
        },
    }
    current_hash = canonical_hash(current_content)
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO owner_truth.vaults (vault_id, owner_subject_id) VALUES (%s, %s)",
                (vault_id, owner_subject_id),
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.sources (
                    id, vault_id, owner_subject_id, source_kind, content_hash,
                    policy_version, authority_epoch
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    source_id,
                    vault_id,
                    owner_subject_id,
                    "text",
                    canonical_hash({"privateSource": "SOURCE_SECRET_MUST_NOT_EXPORT"}),
                    "owner-truth-v1",
                    0,
                ),
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.memories (
                    id, vault_id, owner_subject_id, source_id, source_version,
                    memory_kind, perspective_type, epistemic_status, sensitivity,
                    status, policy_version, content_hash, authority_epoch
                ) VALUES (%s, %s, %s, %s, 1, %s, %s, %s, %s, %s, %s, %s, 0)
                """,
                (
                    memory_id,
                    vault_id,
                    owner_subject_id,
                    source_id,
                    "experience",
                    "firstPerson",
                    "recalled",
                    "standard",
                    "active",
                    "owner-truth-v1",
                    current_hash,
                ),
            )
            for version_id, version_number, is_current, content in (
                (old_version_id, 1, False, old_content),
                (current_version_id, 2, True, current_content),
            ):
                cursor.execute(
                    """
                    INSERT INTO owner_truth.memory_versions (
                        id, vault_id, memory_id, version_number, is_current,
                        schema_version, content_hash, payload, source_id, source_version
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1)
                    """,
                    (
                        version_id,
                        vault_id,
                        memory_id,
                        version_number,
                        is_current,
                        "owner-truth-v2",
                        canonical_hash(content),
                        Jsonb(
                            {
                                "content": content,
                                "contentSchemaVersion": "owner-truth-v2",
                                "evidenceRefs": [
                                    {"sourceId": source_id, "sourceVersion": 1}
                                ],
                            }
                        ),
                        source_id,
                    ),
                )
        connection.commit()


def exercise(dsn: str) -> None:
    store = PostgresStore(dsn=dsn, pool_min_size=1, pool_max_size=3)
    store.open_pool(wait=True)
    try:
        owner = store.upsert_user("13900008831", "Postgres export owner")
        other = store.upsert_user("13900008832", "Postgres export other")
        store.save_profile(owner["id"], {"nickname": "Owner", "bio": "owner-only-export"})
        record = create_data_export_job_record(
            owner_user_id=owner["id"],
            request_key="postgres-export-request-key",
            now="2026-08-08T08:00:00+00:00",
            expires_at="2026-08-08T09:00:00+00:00",
        )
        first = store.create_data_export_job(record)
        replay_record = create_data_export_job_record(
            owner_user_id=owner["id"],
            request_key="postgres-export-request-key",
            now="2026-08-08T08:00:01+00:00",
            expires_at="2026-08-08T09:00:00+00:00",
        )
        replay = store.create_data_export_job(replay_record)
        require(first["outcome"] == "created", "first export job must persist")
        require(replay["outcome"] == "deduplicated", "request key must deduplicate")
        require(first["job"]["id"] == replay["job"]["id"], "dedupe must retain job identity")

        job = materialize_data_export_job(
            store,
            job_id=first["job"]["id"],
            owner_user_id=owner["id"],
            export_builder=build_module_owned_data_export,
            now="2026-08-08T08:01:00+00:00",
        )
        require(job["status"] == "partial", "external boundaries must remain partial")
        require(job["attempt"] == 1, "first materialization must record one attempt")
        require(job["manifest"]["packageStatus"] == "partial", "manifest must be honest")
        require(
            store.get_data_export_job(job["id"], owner_user_id=other["id"]) is None,
            "cross-owner job lookup must be denied",
        )
        downloadable = store.get_data_export_job(
            job["id"],
            owner_user_id=owner["id"],
            include_artifact=True,
        )
        serialized = json.dumps(downloadable, ensure_ascii=False, sort_keys=True)
        require("owner-only-export" in serialized, "owner data must be present")
        require("postgres-export-request-key" not in serialized, "raw request key must not persist")

        credential = create_data_export_download_credential(
            job_id=job["id"],
            owner_user_id=owner["id"],
            job_expires_at=job["expiresAt"],
            now="2026-08-08T08:02:00+00:00",
        )
        issued = store.issue_data_export_download_credential(
            job_id=job["id"],
            owner_user_id=owner["id"],
            token_hash=credential["tokenHash"],
            issued_at=credential["issuedAt"],
            expires_at=credential["expiresAt"],
        )
        require(issued["outcome"] == "issued", "download credential must persist")
        consumed = store.consume_data_export_download_credential(
            job_id=job["id"],
            owner_user_id=owner["id"],
            token_hash=hashlib.sha256(credential["token"].encode("utf-8")).hexdigest(),
            consumed_at="2026-08-08T08:02:10+00:00",
        )
        replayed = store.consume_data_export_download_credential(
            job_id=job["id"],
            owner_user_id=owner["id"],
            token_hash=credential["tokenHash"],
            consumed_at="2026-08-08T08:02:11+00:00",
        )
        require(consumed["outcome"] == "consumed", "credential must be consumed once")
        require(replayed["outcome"] == "invalid", "credential replay must fail")

        expired = store.expire_data_export_job(
            job["id"],
            owner_user_id=owner["id"],
            updated_at="2026-08-08T09:00:00+00:00",
        )
        require(expired["job"]["status"] == "expired", "expiry must be durable")
        require(expired["job"].get("artifact") is None, "expiry must clear artifact bytes")

        vault_id = "vault-formal-markdown-postgres-smoke"
        seed_formal_memory(dsn, vault_id=vault_id, owner_subject_id=owner["id"])
        context = OwnerTruthCommandContext(
            vault_id=vault_id,
            owner_subject_id=owner["id"],
            actor_subject_id=owner["id"],
        )
        formal_record = create_data_export_job_record(
            owner_user_id=owner["id"],
            request_key="postgres-export-request-key",
            export_type=FORMAL_MEMORY_MARKDOWN_EXPORT_TYPE,
            scope_id=vault_id,
            now="2026-08-19T08:00:00+00:00",
            expires_at="2026-08-19T09:00:00+00:00",
        )
        formal_created = store.create_data_export_job(formal_record)
        require(
            formal_created["outcome"] == "created",
            "formal export must not deduplicate against the account archive",
        )
        formal_job = materialize_formal_memory_markdown_export_job(
            store,
            job_id=formal_created["job"]["id"],
            context=context,
            now="2026-08-19T08:01:00+00:00",
        )
        require(formal_job["status"] == "ready", "formal Markdown export must be ready")
        require(formal_job["exportType"] == FORMAL_MEMORY_MARKDOWN_EXPORT_TYPE, "export type must persist")
        require(formal_job["scopeId"] == vault_id, "Vault scope must persist")
        require(formal_job["manifest"]["memoryCount"] == 1, "only one current Memory must export")
        formal_downloadable = store.get_data_export_job(
            formal_job["id"],
            owner_user_id=owner["id"],
            include_artifact=True,
        )
        require(formal_downloadable is not None, "formal export artifact must persist")
        markdown, filename, content_hash = formal_memory_markdown_download(formal_downloadable)
        decoded = markdown.decode("utf-8")
        require("在老院子里听外祖父讲故事" in decoded, "current Memory body must export")
        require("外祖父" in decoded and "老院子" in decoded, "confirmed facets must export")
        require("HISTORY_SECRET_MUST_NOT_EXPORT" not in decoded, "historical body must not export")
        require("SOURCE_SECRET_MUST_NOT_EXPORT" not in decoded, "private Source must not export")
        require(filename.endswith(".md"), "formal export filename must be Markdown")
        require(hashlib.sha256(markdown).hexdigest() == content_hash, "download hash must match")
        require(
            store.get_data_export_job(
                formal_job["id"],
                owner_user_id=other["id"],
                include_artifact=True,
            )
            is None,
            "formal export must remain owner scoped",
        )

        cancelled_record = create_data_export_job_record(
            owner_user_id=owner["id"],
            request_key="postgres-formal-export-cancel",
            export_type=FORMAL_MEMORY_MARKDOWN_EXPORT_TYPE,
            scope_id=vault_id,
            now="2026-08-19T08:02:00+00:00",
            expires_at="2026-08-19T09:00:00+00:00",
        )
        cancelled_created = store.create_data_export_job(cancelled_record)
        cancelled = store.cancel_data_export_job(
            cancelled_created["job"]["id"],
            owner_user_id=owner["id"],
            updated_at="2026-08-19T08:02:01+00:00",
        )
        require(cancelled["job"]["status"] == "cancelled", "formal export cancellation must persist")
        require(cancelled["job"].get("artifact") is None, "cancelled export must not retain an artifact")
        print(
            "Data export job Postgres smoke passed "
            "(account archive lifecycle plus formal-memory current-only Markdown, "
            "owner fence, integrity and cancellation verified)."
        )
    finally:
        store.close_pool()


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", "").strip()
    require(base_dsn, "DATABASE_URL is required")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_data_export_jobs_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="data-export-job-postgres-smoke",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        applied = migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")
        require("0084" in applied["appliedVersions"], "0084 must be applied")
        require("0086" in applied["appliedVersions"], "0086 must be applied")
        require("0101" in applied["appliedVersions"], "0101 must be applied")
        require(applied["appliedHead"] == verified["expectedHead"], "migration head mismatch")
        exercise(test_dsn)
    finally:
        drop_database(admin_dsn, database_name)


if __name__ == "__main__":
    main()
