#!/usr/bin/env python3
"""Exercise the Owner formal-memory library in a disposable Postgres DB."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
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
from app.async_effects.owner_truth_memory_projection_worker import (
    OwnerTruthMemoryProjectionWorkerRuntime,
)
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
from app.services.formal_memory_conversation_snapshot import (
    FormalMemoryConversationSnapshotError,
    FormalMemoryConversationSnapshotService,
)
from app.services.owner_truth_memory_projection import OwnerTruthMemoryProjectionService
from app.services.owner_truth_memory_search_projection import (
    OwnerTruthMemorySearchDocumentProjectionService,
)
from app.services.postgres_store import PostgresStore
from app.services.realtime_voice_proxy import RealtimeVoiceSessionBroker


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


def apply_migrations_through(
    dsn: str,
    *,
    build_id: str,
    final_version: str,
) -> dict[str, Any]:
    selected = [
        migration
        for migration in sorted(default_migrations_dir().glob("*.sql"))
        if migration.name.split("_", 1)[0] <= final_version
    ]
    require(
        selected and selected[-1].name.startswith(f"{final_version}_"),
        "historical migration head is missing",
    )
    with tempfile.TemporaryDirectory(
        prefix="dj-source-dependency-migrations-"
    ) as directory:
        migrations_dir = Path(directory)
        for sql_path in selected:
            shutil.copy2(sql_path, migrations_dir / sql_path.name)
            manifest_path = sql_path.with_suffix(".json")
            shutil.copy2(manifest_path, migrations_dir / manifest_path.name)
        return PostgresMigrator(
            dsn=dsn,
            migrations_dir=migrations_dir,
            build_id=build_id,
            lock_timeout_ms=1_000,
            statement_timeout_ms=30_000,
        ).apply()


def seed_legacy_rebuild_requests(dsn: str) -> None:
    owner_subject_id = f"legacy-rebuild-owner-{uuid.uuid4().hex[:10]}"
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO owner_truth.vaults (vault_id, owner_subject_id) VALUES (%s, %s)",
                (owner_subject_id, owner_subject_id),
            )
            for state in ("pending", "processing", "completed"):
                cursor.execute(
                    """
                    INSERT INTO owner_truth.source_projection_rebuild_requests (
                        vault_id, owner_subject_id, source_id, source_version,
                        authority_epoch, memory_revision, rights_revision,
                        reason_code, state
                    ) VALUES (%s, %s, %s, 1, 0, 0, 0, %s, %s)
                    """,
                    (
                        owner_subject_id,
                        owner_subject_id,
                        str(uuid.uuid4()),
                        f"legacy{state.title()}",
                        state,
                    ),
                )
        connection.commit()


def verify_legacy_rebuild_request_upgrade(dsn: str) -> None:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT reason_code, state, completed_at IS NOT NULL,
                       lease_owner, lease_until, heartbeat_at
                FROM owner_truth.source_projection_rebuild_requests
                WHERE reason_code LIKE 'legacy%'
                ORDER BY reason_code
                """
            )
            rows = cursor.fetchall()
    by_reason = {row[0]: row[1:] for row in rows}
    require(
        by_reason["legacyPending"] == ("pending", False, None, None, None),
        "legacy pending request must remain claimable",
    )
    require(
        by_reason["legacyProcessing"] == ("pending", False, None, None, None),
        "legacy processing request must become safely reclaimable",
    )
    require(
        by_reason["legacyCompleted"] == ("completed", True, None, None, None),
        "legacy completed request must gain a terminal timestamp",
    )


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
    additional_evidence_source_ids: tuple[str, ...] = (),
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
        "evidenceRefs": [
            {"sourceId": item, "sourceVersion": 1}
            for item in (source_id, *additional_evidence_source_ids)
        ],
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
            for additional_source_id in additional_evidence_source_ids:
                cursor.execute(
                    """
                    INSERT INTO owner_truth.sources (
                        id, vault_id, owner_subject_id, source_kind, content_hash,
                        policy_version, authority_epoch
                    ) VALUES (%s, %s, %s, 'text', %s, 'owner-truth-v1', 0)
                    """,
                    (
                        additional_source_id,
                        vault_id,
                        owner_subject_id,
                        canonical_hash({"syntheticEvidence": additional_source_id}),
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


def verify_source_dependency_projection_boundary(
    dsn: str,
    *,
    store: PostgresStore,
) -> None:
    """Exercise migration 0121 in a disposable database using synthetic facts."""

    owner_subject_id = f"source-boundary-owner-{uuid.uuid4().hex[:12]}"
    # Live's targetPersonaId is also the private Owner Truth vault key.
    vault_id = owner_subject_id
    context = OwnerTruthCommandContext(
        vault_id=vault_id,
        owner_subject_id=owner_subject_id,
        actor_subject_id=owner_subject_id,
    )
    candidate_id = seed_pending_candidate(
        dsn,
        vault_id=vault_id,
        owner_subject_id=owner_subject_id,
        content=memory_content("合成正式记忆用于来源失效边界验证", place="测试地点"),
    )
    activation = OwnerTruthCandidateReviewService(store).decide_and_activate(
        command=OwnerTruthCandidateReviewCommand(
            command_id="source-boundary-activate-v1",
            candidate_id=candidate_id,
            expected_candidate_version=1,
            action=CandidateReviewAction.ACCEPT,
            corrected_value=None,
            corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V2,
            reason_code="ownerReviewed",
        ),
        context=context,
    ).memory_activation
    require(activation.memory_version_id is not None, "boundary formal memory must activate")

    # Use the real correction flow to create a current MemoryVersion backed by
    # two Sources. Candidate evidence remains single-source by design; the
    # immutable predecessor evidence is merged during formal activation.
    memory_id = str(activation.memory_id or "")
    formal_service = OwnerTruthFormalMemoryService(store)
    initial_detail = formal_service.detail(context=context, memory_id=memory_id)
    correction = formal_service.correct(
        context=context,
        memory_id=memory_id,
        command=OwnerTruthFormalMemoryCorrectionCommand(
            command_id="source-boundary-correction-v2",
            expected_version=initial_detail.current_version.version_number,
            expected_content_hash=initial_detail.current_version.content_hash,
            expected_content_schema_version=(
                initial_detail.current_version.content_schema_version
            ),
            content_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V2,
            corrected_content=memory_content(
                "合成正式记忆经第二来源确认",
                place="测试地点",
            ),
            second_confirmation=True,
        ),
    )
    require(correction.replacement_version == 2, "correction must create v2")
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT source_id::TEXT, payload -> 'evidenceRefs'
                FROM owner_truth.memory_versions
                WHERE vault_id = %s AND memory_id = %s AND is_current = TRUE
                """,
                (vault_id, memory_id),
            )
            current_source_id, evidence_refs = cursor.fetchone()
    evidence_source_ids = {
        str(reference.get("sourceId") or "")
        for reference in evidence_refs
        if isinstance(reference, dict)
    }
    require(
        current_source_id in evidence_source_ids and len(evidence_source_ids) == 2,
        "formal correction must preserve predecessor and current evidence Sources",
    )
    secondary_source_id = next(
        source_id for source_id in evidence_source_ids if source_id != current_source_id
    )

    memory_projection = OwnerTruthMemoryProjectionService(store).rebuild(context=context)
    search_projection = OwnerTruthMemorySearchDocumentProjectionService(store).rebuild(
        context=context
    )
    require(memory_projection.snapshot["state"] == "ready", "memory projection must be ready")
    require(search_projection.projection is not None, "search projection must be ready")
    snapshot = FormalMemoryConversationSnapshotService(store).build(context=context)
    baseline_revision = int(snapshot["memoryRevision"])
    baseline_checkpoint = str(snapshot["projectionCheckpoint"])
    broker = RealtimeVoiceSessionBroker(settings, store)

    unreviewed_source_id = str(uuid.uuid4())
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO owner_truth.sources (
                    id, vault_id, owner_subject_id, source_kind, content_hash,
                    policy_version, authority_epoch
                ) VALUES (%s, %s, %s, 'text', %s, 'owner-truth-v1', 0)
                """,
                (
                    unreviewed_source_id,
                    vault_id,
                    owner_subject_id,
                    canonical_hash({"synthetic": "unreviewed"}),
                ),
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.extraction_results (
                    id, vault_id, source_id, source_version, extractor_id,
                    schema_version, status, payload, failure_code, completed_at
                ) VALUES (%s, %s, %s, 1, 'syntheticFailure',
                          'owner-truth-candidate-extraction-v1', 'failed',
                          '{}'::jsonb, 'syntheticPermanentFailure', NOW())
                """,
                (str(uuid.uuid4()), vault_id, unreviewed_source_id),
            )
        connection.commit()

    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT memory.state, search.state, revision.revision,
                       COUNT(request.request_id)
                FROM owner_truth.memory_projection_checkpoints AS memory
                JOIN owner_truth.search_document_checkpoints AS search
                  ON search.vault_id = memory.vault_id
                 AND search.authority_epoch = memory.authority_epoch
                JOIN owner_truth.memory_revisions AS revision
                  ON revision.vault_id = memory.vault_id
                LEFT JOIN owner_truth.source_projection_rebuild_requests AS request
                  ON request.vault_id = memory.vault_id
                WHERE memory.vault_id = %s
                GROUP BY memory.state, search.state, revision.revision
                """,
                (vault_id,),
            )
            unchanged = cursor.fetchone()
    require(
        unchanged == ("ready", "ready", baseline_revision, 0),
        "unreviewed Source and failed extraction must not invalidate formal projections",
    )
    require(
        FormalMemoryConversationSnapshotService(store).build(context=context)[
            "projectionCheckpoint"
        ]
        == baseline_checkpoint,
        "existing formal snapshot must remain readable",
    )

    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            formal_source_id = current_source_id
            cursor.execute(
                "UPDATE owner_truth.sources SET state = 'redacted' WHERE vault_id = %s AND id = %s",
                (vault_id, formal_source_id),
            )
            cursor.execute(
                "SELECT state FROM owner_truth.memory_projection_checkpoints WHERE vault_id = %s",
                (vault_id,),
            )
            require(
                cursor.fetchone()[0] == "rebuilding",
                "referenced Source must invalidate inside its transaction",
            )
        connection.rollback()

    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT source.state, memory.state, search.state,
                       COUNT(request.request_id)
                FROM owner_truth.sources AS source
                JOIN owner_truth.memory_projection_checkpoints AS memory
                  ON memory.vault_id = source.vault_id
                JOIN owner_truth.search_document_checkpoints AS search
                  ON search.vault_id = memory.vault_id
                 AND search.authority_epoch = memory.authority_epoch
                LEFT JOIN owner_truth.source_projection_rebuild_requests AS request
                  ON request.vault_id = source.vault_id
                WHERE source.vault_id = %s AND source.id = %s
                GROUP BY source.state, memory.state, search.state
                """,
                (vault_id, formal_source_id),
            )
            rolled_back = cursor.fetchone()
    require(
        rolled_back == ("active", "ready", "ready", 0),
        "rollback must preserve Source, projections, and rebuild intent atomically",
    )

    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            try:
                cursor.execute(
                    """
                    UPDATE owner_truth.sources
                    SET source_version = source_version + 1
                    WHERE vault_id = %s AND id = %s
                    """,
                    (vault_id, secondary_source_id),
                )
            except psycopg.Error:
                connection.rollback()
            else:
                raise AssertionError(
                    "Source versions are immutable; a version rewrite must be rejected"
                )

    def insert_concurrent_source() -> None:
        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO owner_truth.sources (
                        id, vault_id, owner_subject_id, source_kind, content_hash,
                        policy_version, authority_epoch
                    ) VALUES (%s, %s, %s, 'text', %s, 'owner-truth-v1', 0)
                    """,
                    (
                        str(uuid.uuid4()),
                        vault_id,
                        owner_subject_id,
                        canonical_hash({"synthetic": "concurrent-source"}),
                    ),
                )
            connection.commit()

    def advance_formal_revision() -> None:
        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE owner_truth.memory_revisions SET revision = revision + 1 WHERE vault_id = %s",
                    (vault_id,),
                )
            connection.commit()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(insert_concurrent_source), executor.submit(advance_formal_revision)]
        for future in futures:
            future.result()

    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT memory.state, search.state, revision.revision
                FROM owner_truth.memory_projection_checkpoints AS memory
                JOIN owner_truth.search_document_checkpoints AS search
                  ON search.vault_id = memory.vault_id
                 AND search.authority_epoch = memory.authority_epoch
                JOIN owner_truth.memory_revisions AS revision
                  ON revision.vault_id = memory.vault_id
                WHERE memory.vault_id = %s
                """,
                (vault_id,),
            )
            require(
                cursor.fetchone() == ("rebuilding", "rebuilding", baseline_revision + 1),
                "formal revision must win a concurrent Source insertion",
            )
            try:
                cursor.execute(
                    """
                    UPDATE owner_truth.memory_projection_checkpoints
                    SET state = 'ready', memory_revision = %s
                    WHERE vault_id = %s
                    """,
                    (baseline_revision, vault_id),
                )
            except psycopg.Error:
                connection.rollback()
            else:
                raise AssertionError("stale projection revision must fail closed")

    OwnerTruthMemoryProjectionService(store).rebuild(context=context)
    OwnerTruthMemorySearchDocumentProjectionService(store).rebuild(context=context)
    recovery_runtime = replace(
        settings,
        async_effect_v1_enabled=True,
        async_effect_worker_enabled=True,
        owner_truth_memory_projection_worker_enabled=True,
        owner_truth_memory_search_projection_worker_enabled=True,
    )
    recovery_worker = OwnerTruthMemoryProjectionWorkerRuntime(
        settings=recovery_runtime,
        store=store,
        worker_id="synthetic-source-recovery-worker",
        retry_seconds=1,
    )
    completed_projection_jobs = 0
    blocked_stale_projection_jobs = 0
    for _ in range(20):
        drained = recovery_worker.run_once()
        if drained.get("status") == "idle":
            break
        if drained.get("status") == "completed":
            completed_projection_jobs += 1
            continue
        if (
            drained.get("status") == "blocked"
            and drained.get("reason") == "memoryVersionNotCurrent"
        ):
            blocked_stale_projection_jobs += 1
            continue
        raise AssertionError(
            "baseline projection effects must complete or fail closed as stale"
        )
    else:
        raise AssertionError("baseline projection effects did not drain")
    require(
        completed_projection_jobs >= 1 and blocked_stale_projection_jobs >= 1,
        "current projection must complete and superseded projection must be blocked",
    )
    refreshed_snapshot = FormalMemoryConversationSnapshotService(store).build(context=context)
    refreshed_lease = {
        "projectionCheckpoint": str(refreshed_snapshot["projectionCheckpoint"]),
        "contextHash": str(refreshed_snapshot["contextHash"]),
        "memoryRevision": int(refreshed_snapshot["memoryRevision"]),
    }
    require(
        broker._is_formal_memory_binding_current(
            refreshed_lease,
            target_persona_id=owner_subject_id,
            expected_authority_epoch=0,
        ),
        "refreshed Live binding must start current",
    )

    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE owner_truth.sources SET state = 'redacted' WHERE vault_id = %s AND id = %s",
                (vault_id, secondary_source_id),
            )
        connection.commit()

    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT memory.state, search.state, COUNT(request.request_id),
                       MIN(request.source_id::TEXT), MIN(request.source_version)
                FROM owner_truth.memory_projection_checkpoints AS memory
                JOIN owner_truth.search_document_checkpoints AS search
                  ON search.vault_id = memory.vault_id
                 AND search.authority_epoch = memory.authority_epoch
                LEFT JOIN owner_truth.source_projection_rebuild_requests AS request
                  ON request.vault_id = memory.vault_id
                WHERE memory.vault_id = %s
                GROUP BY memory.state, search.state
                """,
                (vault_id,),
            )
            revoked = cursor.fetchone()
    require(
        revoked == ("rebuilding", "rebuilding", 1, secondary_source_id, 1),
        "secondary evidence mutation must create one OLD-version rebuild request",
    )
    try:
        FormalMemoryConversationSnapshotService(store).build(context=context)
    except FormalMemoryConversationSnapshotError:
        pass
    else:
        raise AssertionError("revoked formal evidence must make the snapshot unavailable")
    require(
        not broker._is_formal_memory_binding_current(
            refreshed_lease,
            target_persona_id=owner_subject_id,
            expected_authority_epoch=0,
        ),
        "active Live binding must fail closed after formal evidence revocation",
    )
    recovery = recovery_worker.run_once()
    require(
        recovery.get("status") == "blocked"
        and recovery.get("reason") == "formalEvidenceSourceUnavailable"
        and recovery.get("requestType") == "sourceProjectionRebuild",
        "automatic recovery must explicitly block stale secondary evidence",
    )
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT state, attempt, last_error_code
                FROM owner_truth.source_projection_rebuild_requests
                WHERE vault_id = %s AND source_id = %s
                """,
                (vault_id, secondary_source_id),
            )
            request_state = cursor.fetchone()
    require(
        request_state == ("blocked", 1, "formalEvidenceSourceUnavailable"),
        "automatic recovery must persist an explicit terminal block",
    )


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
        historical = apply_migrations_through(
            test_dsn,
            build_id="owner-truth-source-dependency-0120",
            final_version="0120",
        )
        require(
            historical["appliedHead"] == "0120",
            "historical fixture must stop at migration 0120",
        )
        seed_legacy_rebuild_requests(test_dsn)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="owner-truth-formal-memory-pc-b1",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        upgrade = migrator.apply()
        require(
            upgrade["appliedVersions"] == ["0121"],
            "upgrade fixture must apply only migration 0121",
        )
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")
        verify_legacy_rebuild_request_upgrade(test_dsn)

        store = PostgresStore(dsn=test_dsn, pool_min_size=1, pool_max_size=2)
        store.open_pool(wait=True)
        verify_source_dependency_projection_boundary(test_dsn, store=store)
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
            "staleConflict=true idempotent=true publicationPinned=true noDeleteRoute=true "
            "sourceProjectionBoundary=true rollback=true concurrencyFence=true liveRevocation=true"
            " legacy0120Upgrade=true"
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
