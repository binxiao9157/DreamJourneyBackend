#!/usr/bin/env python3
"""Exercise P2-S1 publication authority in a disposable Postgres database.

The smoke never touches the configured application database.  It creates a
temporary database from DATABASE_URL, applies every migration, and verifies
that an Owner-confirmed public copy is independently stored and invalidated
when its Owner Truth authority anchor changes.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from hashlib import sha256
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
from psycopg.types.json import Jsonb

from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.postgres_store import PostgresStore
from app.services.publication_authority import (
    PublicationAuthorityAccessDenied,
    PublicationAuthorityNotPublishable,
    PublicationAuthorityService,
    PublicationConfirmCommand,
    PublicationDraftCommand,
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def canonical_hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


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


def expect_rejected(operation, message: str) -> None:
    rejected = False
    try:
        operation()
    except Exception:
        rejected = True
    require(rejected, message)


@dataclass(frozen=True)
class Seed:
    context: OwnerTruthCommandContext
    source_id: str
    memory_version_id: str


def seed_publishable_memory(dsn: str, *, label: str) -> Seed:
    """Insert one complete Owner Truth admission chain into the disposable DB."""

    vault_id = f"publication-smoke-{label}"
    owner_subject_id = f"publication-owner-{label}"
    source_id = str(uuid.uuid4())
    candidate_id = str(uuid.uuid4())
    decision_receipt_id = str(uuid.uuid4())
    memory_id = str(uuid.uuid4())
    memory_version_id = str(uuid.uuid4())
    content = {"summary": f"{label} 的确认回忆"}
    content_hash = canonical_hash(content)
    source_payload = {"text": f"{label} 的私有来源文本"}

    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO owner_truth.vaults (vault_id, owner_subject_id) VALUES (%s, %s)",
                (vault_id, owner_subject_id),
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.sources (
                    id, vault_id, owner_subject_id, source_kind, state, source_version,
                    content_hash, content_payload, policy_version, authority_epoch
                ) VALUES (%s, %s, %s, 'text', 'active', 1, %s, %s, 'owner-truth-v1', 0)
                """,
                (
                    source_id,
                    vault_id,
                    owner_subject_id,
                    canonical_hash(source_payload),
                    Jsonb(source_payload),
                ),
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.memory_candidates (
                    id, vault_id, owner_subject_id, source_id, candidate_kind,
                    perspective_type, epistemic_status, sensitivity, decision_status,
                    policy_version, authority_epoch, content_hash, payload_schema_version, payload
                ) VALUES (%s, %s, %s, %s, 'experience', 'firstPerson', 'recalled', 'standard',
                    'pending', 'owner-truth-v1', 0, %s, 'owner-truth-v1', %s)
                """,
                (
                    candidate_id,
                    vault_id,
                    owner_subject_id,
                    source_id,
                    content_hash,
                    Jsonb({"content": content, "sourceRefs": [{"sourceId": source_id, "sourceVersion": 1}]}),
                ),
            )
            cursor.execute(
                """
                UPDATE owner_truth.memory_candidates
                SET decision_status = 'accepted'
                WHERE vault_id = %s AND id = %s AND decision_status = 'pending'
                """,
                (vault_id, candidate_id),
            )
            require(cursor.rowcount == 1, "smoke Candidate must enter accepted state")
            cursor.execute(
                """
                INSERT INTO owner_truth.decision_receipts (
                    id, vault_id, candidate_id, decision, actor_subject_id, authority_epoch,
                    policy_version, rationale_hash, command_id_hash, payload_hash,
                    expected_candidate_version, candidate_before_hash, candidate_after_hash,
                    decision_basis
                ) VALUES (%s, %s, %s, 'accepted', %s, 0, 'owner-truth-v1', %s, %s, %s,
                    1, %s, %s, %s)
                """,
                (
                    decision_receipt_id,
                    vault_id,
                    candidate_id,
                    owner_subject_id,
                    canonical_hash({"reason": "ownerConfirmed"}),
                    canonical_hash({"command": label}),
                    canonical_hash({"candidate": candidate_id, "decision": "accepted"}),
                    content_hash,
                    content_hash,
                    Jsonb(
                        {
                            "schemaVersion": "owner-truth-decision-basis-v1",
                            "reasonCode": "ownerConfirmed",
                            "sourceRefs": [{"sourceId": source_id, "sourceVersion": 1}],
                        }
                    ),
                ),
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.memories (
                    id, vault_id, owner_subject_id, source_id, source_version,
                    memory_kind, perspective_type, epistemic_status, sensitivity, status,
                    policy_version, content_hash, authority_epoch, decision_receipt_id
                ) VALUES (%s, %s, %s, %s, 1, 'experience', 'firstPerson', 'recalled',
                    'standard', 'active', 'owner-truth-v1', %s, 0, %s)
                """,
                (
                    memory_id,
                    vault_id,
                    owner_subject_id,
                    source_id,
                    content_hash,
                    decision_receipt_id,
                ),
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.memory_versions (
                    id, vault_id, memory_id, version_number, is_current, schema_version,
                    content_hash, payload, source_id, source_version, decision_receipt_id
                ) VALUES (%s, %s, %s, 1, TRUE, 'owner-truth-memory-version-v1', %s, %s,
                    %s, 1, %s)
                """,
                (
                    memory_version_id,
                    vault_id,
                    memory_id,
                    content_hash,
                    Jsonb({"schemaVersion": "owner-truth-memory-version-v1", "content": content}),
                    source_id,
                    decision_receipt_id,
                ),
            )
        connection.commit()
    return Seed(
        context=OwnerTruthCommandContext(
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            actor_subject_id=owner_subject_id,
        ),
        source_id=source_id,
        memory_version_id=memory_version_id,
    )


def authority_service(store: PostgresStore) -> PublicationAuthorityService:
    return PublicationAuthorityService(store.publication_authority_repository(), enabled=True)


def create_draft(store: PostgresStore, seed: Seed, *, command_id: str | None = None):
    with store.request_unit_of_work(
        correlation_id=f"publication-authority-smoke:draft:{seed.context.vault_id}",
        command_id=command_id or str(uuid.uuid4()),
    ):
        command = PublicationDraftCommand(
            command_id=command_id or str(uuid.uuid4()),
            memory_version_id=seed.memory_version_id,
            public_title="确认的公开回忆",
            public_body="这是由发布者重新整理并确认的公开说明。",
        )
        return authority_service(store).create_draft(context=seed.context, command=command)


def confirm_draft(store: PostgresStore, seed: Seed, draft, *, command_id: str):
    with store.request_unit_of_work(
        correlation_id=f"publication-authority-smoke:confirm:{seed.context.vault_id}",
        command_id=command_id,
    ):
        command = PublicationConfirmCommand(
            command_id=command_id,
            publication_id=draft.publication_id,
            draft_id=draft.draft_id,
            expected_draft_revision=draft.expected_draft_revision,
            expected_draft_snapshot_hash=draft.draft_snapshot_hash,
            second_confirmation=True,
        )
        return authority_service(store).confirm_draft(context=seed.context, command=command)


def projection_state(dsn: str, publication_version_id: str) -> tuple[str, str | None]:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT state, block_reason_code
                FROM publication.public_projections
                WHERE publication_version_id = %s
                """,
                (publication_version_id,),
            )
            row = cursor.fetchone()
    require(row is not None, "publication projection must exist")
    return str(row[0]), str(row[1]) if row[1] is not None else None


def invalidation_count(dsn: str, publication_version_id: str, reason_code: str) -> int:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM publication.projection_invalidation_requests
                WHERE publication_version_id = %s AND reason_code = %s
                """,
                (publication_version_id, reason_code),
            )
            return int(cursor.fetchone()[0])


def exercise(dsn: str) -> None:
    store = PostgresStore(dsn=dsn, pool_min_size=1, pool_max_size=6)
    store.open_pool(wait=True)
    try:
        source_case = seed_publishable_memory(dsn, label="source")
        draft = create_draft(store, source_case)
        confirm_command_id = str(uuid.uuid4())
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = set(
                executor.map(
                    lambda _: confirm_draft(
                        store,
                        source_case,
                        draft,
                        command_id=confirm_command_id,
                    ).outcome,
                    range(2),
                )
            )
        require(outcomes == {"created", "deduplicated"}, "confirmation replay must serialize")

        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, display_body
                    FROM publication.public_projections
                    WHERE publication_id = %s
                    """,
                    (draft.publication_id,),
                )
                projection = cursor.fetchone()
                require(projection is not None, "confirmation must create an independent projection")
                require(
                    str(projection[1]) == "这是由发布者重新整理并确认的公开说明。",
                    "public projection must use the owner-authored public copy",
                )
                expect_rejected(
                    lambda: cursor.execute(
                        "UPDATE publication.public_projections SET display_body = 'tampered' WHERE id = %s",
                        (projection[0],),
                    ),
                    "public projection content must be immutable",
                )
            connection.rollback()

        confirmed = confirm_draft(store, source_case, draft, command_id=confirm_command_id)
        require(confirmed.outcome == "deduplicated", "explicit replay must remain idempotent")

        expect_rejected(
            lambda: PublicationDraftCommand(
                command_id=str(uuid.uuid4()),
                memory_version_id=source_case.memory_version_id,
                public_title="不安全公开文本",
                public_body="联系电话 13800138000",
            ),
            "direct identifiers must be rejected before public persistence",
        )
        with store.request_unit_of_work(
            correlation_id="publication-authority-smoke:cross-owner",
            command_id=str(uuid.uuid4()),
        ):
            expect_rejected(
                lambda: authority_service(store).create_draft(
                    context=OwnerTruthCommandContext(
                        vault_id=source_case.context.vault_id,
                        owner_subject_id="another-owner",
                        actor_subject_id="another-owner",
                    ),
                    command=PublicationDraftCommand(
                        command_id=str(uuid.uuid4()),
                        memory_version_id=source_case.memory_version_id,
                        public_title="越权公开文本",
                        public_body="不应创建。",
                    ),
                ),
                "cross-owner publication command must fail closed",
            )

        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE owner_truth.sources SET state = 'redacted' WHERE id = %s",
                    (source_case.source_id,),
                )
            connection.commit()
        source_version_id = confirmed.publication_version_id
        require(
            projection_state(dsn, source_version_id) == ("blocked", "sourceAuthorityChanged"),
            "source redaction must block the active public projection",
        )
        require(
            invalidation_count(dsn, source_version_id, "sourceAuthorityChanged") == 1,
            "source redaction must enqueue one invalidation request",
        )

        version_case = seed_publishable_memory(dsn, label="version")
        version_draft = create_draft(store, version_case)
        version_result = confirm_draft(store, version_case, version_draft, command_id=str(uuid.uuid4()))
        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE owner_truth.memory_versions SET is_current = FALSE WHERE id = %s",
                    (version_case.memory_version_id,),
                )
            connection.commit()
        require(
            projection_state(dsn, version_result.publication_version_id)
            == ("blocked", "memoryVersionSuperseded"),
            "superseded MemoryVersion must block the active public projection",
        )

        vault_case = seed_publishable_memory(dsn, label="vault")
        vault_draft = create_draft(store, vault_case)
        vault_result = confirm_draft(store, vault_case, vault_draft, command_id=str(uuid.uuid4()))
        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE owner_truth.vaults SET status = 'suspended' WHERE vault_id = %s",
                    (vault_case.context.vault_id,),
                )
            connection.commit()
        require(
            projection_state(dsn, vault_result.publication_version_id)
            == ("blocked", "vaultAuthorityChanged"),
            "Vault suspension must block every active public projection",
        )
        print(
            "Publication authority Postgres smoke passed "
            "(owner fence, replay, immutable public copy, direct-identifier guard and invalidation verified)."
        )
    finally:
        store.close_pool()


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", "").strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_publication_authority_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="publication-authority-smoke",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        applied = migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")
        require("0079" in applied["appliedVersions"], "publication authority migration must apply")
        require("0080" in applied["appliedVersions"], "publication authority trigger fix must apply")
        exercise(test_dsn)
    finally:
        drop_database(admin_dsn, database_name)


if __name__ == "__main__":  # pragma: no cover
    main()
