#!/usr/bin/env python3
"""Verify physical private-media deletion with disposable Postgres and files."""

from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.async_effects.owner_truth_media_deletion_worker import (
    OwnerTruthMediaDeletionWorkerRuntime,
)
from app.core.config import Settings, settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_media_deletion import (
    OwnerTruthMediaDeletionCoordinator,
    build_media_source_object_deletion_effect_intent,
)
from app.services.owner_truth_media_source_object import (
    FilesystemPrivateMediaObjectStore,
    MediaDeletionCommand,
    MediaUploadIntentCommand,
    OwnerTruthMediaIngestionService,
    TestOnlyCleanMediaContentSafetyScanner,
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


def seed_owner(dsn: str, *, owner_id: str, vault_id: str) -> None:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO subjects (id, status) VALUES (%s, 'active')", (owner_id,))
            cursor.execute(
                """
                INSERT INTO owner_truth.vaults (vault_id, owner_subject_id, authority_epoch, status)
                VALUES (%s, %s, 0, 'active')
                """,
                (vault_id, owner_id),
            )


def exercise(dsn: str) -> None:
    owner_id = f"owner-media-delete-{uuid4().hex[:12]}"
    vault_id = f"vault-media-delete-{uuid4().hex[:12]}"
    seed_owner(dsn, owner_id=owner_id, vault_id=vault_id)
    context = OwnerTruthCommandContext(
        vault_id=vault_id,
        owner_subject_id=owner_id,
        actor_subject_id=owner_id,
    )
    store = PostgresStore(dsn=dsn, pool_min_size=1, pool_max_size=4)
    store.open_pool(wait=True)
    try:
        with TemporaryDirectory(prefix="dreamjourney-media-delete-focused-") as media_root:
            object_store = FilesystemPrivateMediaObjectStore(root=media_root)
            service = OwnerTruthMediaIngestionService(
                store=store,
                object_store=object_store,
                safety_scanner=TestOnlyCleanMediaContentSafetyScanner(),
                enabled=True,
                max_upload_bytes=1024 * 1024,
                upload_intent_ttl_seconds=900,
            )
            payload = b"Synthetic private media deletion verification payload."
            upload = MediaUploadIntentCommand.from_payload(
                {
                    "commandId": str(uuid4()),
                    "expectedAuthorityEpoch": 0,
                    "mediaKind": "document",
                    "fileName": "synthetic-private-memory.txt",
                    "contentType": "text/plain",
                    "fileSizeBytes": len(payload),
                    "contentSha256": sha256(payload).hexdigest(),
                    "purpose": "memoryCapture",
                    "clientCreatedAt": "2026-09-10T00:00:00Z",
                }
            )
            with store.request_unit_of_work(
                correlation_id="focused-media-upload-intent",
                command_id=f"focusedMediaUploadIntent:{upload.command_id}",
            ):
                created = service.create_upload_intent(context=context, command=upload)
            with store.request_unit_of_work(
                correlation_id="focused-media-upload-content",
                command_id=f"focusedMediaUploadContent:{created.upload_intent['uploadIntentId']}",
            ):
                outcome, verified = service.upload_content(
                    context=context,
                    intent_id=str(created.upload_intent["uploadIntentId"]),
                    upload_token=str(created.upload_token),
                    payload=payload,
                    request_content_type="text/plain",
                )
            require(outcome == "uploaded", f"synthetic upload failed: {outcome}")
            require(sum(path.is_file() for path in Path(media_root).rglob("*")) == 1, "file missing")

            deletion_command = MediaDeletionCommand.from_payload(
                {
                    "commandId": str(uuid4()),
                    "expectedAuthorityEpoch": 0,
                    "clientRequestedAt": "2026-09-10T00:01:00Z",
                }
            )
            with store.request_unit_of_work(
                correlation_id="focused-media-delete-request",
                command_id=f"focusedMediaDeleteRequest:{deletion_command.command_id}",
            ):
                deletion = service.request_deletion(
                    context=context,
                    source_object_id=str(verified["sourceObjectId"]),
                    command=deletion_command,
                )
            with store.request_unit_of_work(
                correlation_id="focused-media-deletion-enqueue",
                command_id=f"focusedMediaDeletion:{verified['sourceObjectId']}",
            ):
                enqueued = OwnerTruthMediaDeletionCoordinator(store).enqueue_accepted_deletion(
                    context=context,
                    result=deletion,
                )
            intent = build_media_source_object_deletion_effect_intent(
                source_object=enqueued.source_object
            )
            worker = OwnerTruthMediaDeletionWorkerRuntime(
                settings=Settings(
                    environment="test",
                    store_backend="postgres",
                    database_url=dsn,
                    async_effect_v1_enabled=True,
                    async_effect_worker_enabled=True,
                    owner_truth_media_capture_enabled=True,
                    owner_truth_media_deletion_worker_enabled=True,
                    owner_truth_media_storage_provider="filesystem",
                    owner_truth_media_storage_root=media_root,
                ),
                store=store,
                worker_id="focused-media-deletion-postgres-smoke",
                object_store=object_store,
            )
            result = worker.run_once()
            require(result.get("status") == "completed", f"deletion worker failed: {result}")
            require(result.get("deletionStatus") == "completed", f"deletion not terminal: {result}")
            require(result.get("jobId") == intent.job_id, "worker completed a different job")
            require(sum(path.is_file() for path in Path(media_root).rglob("*")) == 0, "file remains")
            with store.request_unit_of_work(
                correlation_id="focused-media-delete-read",
                command_id=f"focusedMediaDeleteRead:{verified['sourceObjectId']}",
            ):
                current = store.owner_truth_media_source_object_repository().get_source_object(
                    vault_id=vault_id,
                    source_object_id=str(verified["sourceObjectId"]),
                    owner_subject_id=owner_id,
                )
            require(current["accessState"] == "accessRevoked", "access was restored")
            require(current["deletionStatus"] == "completed", "deletion state was not durable")
            require(current["deletionRetryable"] is False, "completed deletion stayed retryable")
            require(worker.run_once().get("status") == "idle", "completed deletion replayed")
            print(
                "owner truth focused media deletion Postgres smoke passed "
                "schemaHead=0121 physicalFileRemoved=true accessRevoked=true "
                "deletionStatus=completed replayIdle=true"
            )
    finally:
        store.close_pool()


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_media_delete_focused_{uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    created = False
    try:
        create_database(admin_dsn, database_name)
        created = True
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="owner-truth-media-deletion-focused-postgres-smoke",
            lock_timeout_ms=1000,
            statement_timeout_ms=30000,
        )
        migrator.apply()
        require(migrator.verify()["status"] == "ready", "temporary schema must verify")
        exercise(test_dsn)
    finally:
        if created:
            drop_database(admin_dsn, database_name)


if __name__ == "__main__":
    main()
