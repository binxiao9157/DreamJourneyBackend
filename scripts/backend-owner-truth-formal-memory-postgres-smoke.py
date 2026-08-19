#!/usr/bin/env python3
"""Exercise the Owner formal-memory library in a disposable Postgres DB."""

from __future__ import annotations

from hashlib import sha256
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
from psycopg.types.json import Jsonb

from app.core.config import settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.domain.owner_truth.candidate_decisions import (
    CandidateReviewAction,
    OwnerTruthCandidateReviewCommand,
)
from app.domain.owner_truth.ontology import OWNER_TRUTH_SCHEMA_VERSION_V2
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_candidate_review import OwnerTruthCandidateReviewService
from app.services.owner_truth_formal_memory import (
    OwnerTruthFormalMemoryConflict,
    OwnerTruthFormalMemoryCorrectionCommand,
    OwnerTruthFormalMemoryError,
    OwnerTruthFormalMemoryFacetFilter,
    OwnerTruthFormalMemoryQuery,
    OwnerTruthFormalMemoryService,
)
from app.services.postgres_store import PostgresStore


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def canonical_hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
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


def memory_content(summary: str, *, place: str) -> dict[str, Any]:
    return {
        "summary": summary,
        "facets": {
            "people": [],
            "time": [],
            "places": [
                {"value": place, "evidenceMode": "ownerStated", "confidence": 1.0}
            ],
            "relationships": [],
            "emotions": [],
            "values": [],
            "personality": [],
            "confidence": 1.0,
        },
    }


def seed_pending_candidate(
    dsn: str,
    *,
    vault_id: str,
    owner_subject_id: str,
    content: dict[str, Any],
) -> str:
    source_id = str(uuid.uuid4())
    candidate_id = str(uuid.uuid4())
    payload = {
        "schemaVersion": "owner-truth-candidate-v1",
        "candidateKind": "experience",
        "perspectiveType": "firstPerson",
        "epistemicStatus": "recalled",
        "sensitivity": "standard",
        "content": content,
        "contentSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION_V2,
        "evidenceRefs": [{"sourceId": source_id, "sourceVersion": 1}],
        "reviewMode": "single",
    }
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
                    canonical_hash({"seed": content}),
                    "owner-truth-v1",
                    0,
                ),
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.memory_candidates (
                    id, vault_id, owner_subject_id, source_id, candidate_kind,
                    perspective_type, epistemic_status, sensitivity,
                    policy_version, authority_epoch, content_hash,
                    payload_schema_version, payload
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    candidate_id,
                    vault_id,
                    owner_subject_id,
                    source_id,
                    "experience",
                    "firstPerson",
                    "recalled",
                    "standard",
                    "owner-truth-v1",
                    0,
                    canonical_hash(content),
                    OWNER_TRUTH_SCHEMA_VERSION_V2,
                    Jsonb(payload),
                ),
            )
        connection.commit()
    return candidate_id


def pin_publication(
    dsn: str,
    *,
    vault_id: str,
    owner_subject_id: str,
    memory_version_id: str,
    content_hash: str,
) -> tuple[str, str]:
    publication_id = str(uuid.uuid4())
    publication_version_id = str(uuid.uuid4())
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO publication.publications (
                    id, vault_id, owner_subject_id, authority_epoch, state
                ) VALUES (%s, %s, %s, 0, 'confirmed')
                """,
                (publication_id, vault_id, owner_subject_id),
            )
            cursor.execute(
                """
                INSERT INTO publication.publication_versions (
                    id, publication_id, vault_id, pinned_memory_version_id,
                    version_number, content_hash, policy_version, confirmed_at
                ) VALUES (%s, %s, %s, %s, 1, %s, %s, NOW())
                """,
                (
                    publication_version_id,
                    publication_id,
                    vault_id,
                    memory_version_id,
                    content_hash,
                    "owner-truth-formal-memory-smoke-v1",
                ),
            )
        connection.commit()
    return publication_id, publication_version_id


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_formal_memory_smoke_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    store: PostgresStore | None = None

    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="owner-truth-formal-memory-pc-b1",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")

        store = PostgresStore(dsn=test_dsn, pool_min_size=1, pool_max_size=2)
        store.open_pool(wait=True)
        owner_subject_id = f"formal-memory-owner-{uuid.uuid4().hex[:12]}"
        vault_id = f"formal-memory-vault-{uuid.uuid4().hex[:12]}"
        context = OwnerTruthCommandContext(
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            actor_subject_id=owner_subject_id,
        )
        candidate_id = seed_pending_candidate(
            test_dsn,
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            content=memory_content("在老院子里听外祖父讲故事", place="老院子"),
        )
        activation = OwnerTruthCandidateReviewService(store).decide_and_activate(
            command=OwnerTruthCandidateReviewCommand(
                command_id="formal-memory-smoke-activate-v1",
                candidate_id=candidate_id,
                expected_candidate_version=1,
                action=CandidateReviewAction.ACCEPT,
                corrected_value=None,
                corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V2,
                reason_code="ownerReviewed",
            ),
            context=context,
        ).memory_activation
        memory_id = str(activation.memory_id or "")
        initial_version_id = str(activation.memory_version_id or "")
        initial_content_hash = str(activation.content_hash or "")
        require(memory_id and initial_version_id, "baseline activation must create formal memory")
        publication_id, publication_version_id = pin_publication(
            test_dsn,
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            memory_version_id=initial_version_id,
            content_hash=initial_content_hash,
        )

        service = OwnerTruthFormalMemoryService(store)
        filtered = service.list(
            context=context,
            query=OwnerTruthFormalMemoryQuery(
                kind="experience",
                query="外祖父",
                facets=(OwnerTruthFormalMemoryFacetFilter(name="places", value="老院子"),),
                limit=20,
            ),
        )
        require([item.memory_id for item in filtered.items] == [memory_id], "search/facet must find current memory")
        wildcard = service.list(
            context=context,
            query=OwnerTruthFormalMemoryQuery(query="%", limit=20),
        )
        require(not wildcard.items, "search wildcard characters must remain literal")

        first_command: OwnerTruthFormalMemoryCorrectionCommand | None = None
        first_receipt_id = ""
        for version in range(2, 6):
            detail = service.detail(context=context, memory_id=memory_id)
            command = OwnerTruthFormalMemoryCorrectionCommand(
                command_id=f"formal-memory-smoke-revision-{version}",
                expected_version=detail.current_version.version_number,
                expected_content_hash=detail.current_version.content_hash,
                expected_content_schema_version=detail.current_version.content_schema_version,
                content_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V2,
                corrected_content=memory_content(f"第{version}版正式记忆", place="老院子"),
                second_confirmation=True,
            )
            result = service.correct(context=context, memory_id=memory_id, command=command)
            require(result.replacement_version == version, "correction must create the next version")
            if version == 2:
                first_command = command
                first_receipt_id = result.receipt_id

        detail = service.detail(context=context, memory_id=memory_id)
        require(detail.current_version.version_number == 5, "latest version must be current")
        require(
            [item.version_number for item in detail.versions] == [5, 4, 3, 2],
            "Owner surface must expose current plus three historical snapshots",
        )
        require(detail.history_truncated, "older internal history must be reported as truncated")

        require(first_command is not None, "first correction command must be captured")
        replay = service.correct(context=context, memory_id=memory_id, command=first_command)
        require(replay.outcome == "deduplicated", "correction replay must be idempotent")
        require(replay.receipt_id == first_receipt_id, "replay must preserve the receipt")

        stale_detail = detail
        try:
            service.correct(
                context=context,
                memory_id=memory_id,
                command=OwnerTruthFormalMemoryCorrectionCommand(
                    command_id="formal-memory-smoke-stale-revision",
                    expected_version=stale_detail.current_version.version_number - 1,
                    expected_content_hash=stale_detail.versions[1].content_hash,
                    expected_content_schema_version=stale_detail.current_version.content_schema_version,
                    content_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V2,
                    corrected_content=memory_content("陈旧纠正不得写入", place="老院子"),
                    second_confirmation=True,
                ),
            )
        except OwnerTruthFormalMemoryConflict:
            pass
        else:
            raise AssertionError("stale correction must fail with conflict")

        try:
            OwnerTruthFormalMemoryCorrectionCommand(
                command_id="formal-memory-smoke-unconfirmed",
                expected_version=detail.current_version.version_number,
                expected_content_hash=detail.current_version.content_hash,
                expected_content_schema_version=detail.current_version.content_schema_version,
                content_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V2,
                corrected_content=memory_content("未确认草稿不得写入", place="老院子"),
                second_confirmation=False,
            )
        except OwnerTruthFormalMemoryError:
            pass
        else:
            raise AssertionError("unconfirmed correction must be rejected before write")

        with psycopg.connect(test_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT pinned_memory_version_id
                    FROM publication.publication_versions
                    WHERE id = %s AND publication_id = %s
                    """,
                    (publication_version_id, publication_id),
                )
                pinned = cursor.fetchone()
                cursor.execute(
                    """
                    SELECT COUNT(*), COUNT(*) FILTER (WHERE is_current)
                    FROM owner_truth.memory_versions
                    WHERE vault_id = %s AND memory_id = %s
                    """,
                    (vault_id, memory_id),
                )
                version_counts = cursor.fetchone()
        require(pinned is not None and str(pinned[0]) == initial_version_id, "PublicationVersion must stay pinned to the original immutable version")
        require(version_counts == (5, 1), "internal ledger must retain five versions with one current")

        print(
            "owner truth formal memory postgres smoke passed "
            f"schemaHead={verified['expectedHead']} listSearchFacet=true currentPlusThree=true "
            "staleConflict=true idempotent=true publicationPinned=true noDeleteRoute=true"
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
