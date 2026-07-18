#!/usr/bin/env python3
"""Exercise Owner Truth V1 constraints in an isolated temporary database.

The script creates a disposable database from DATABASE_URL, applies the full
migration set, tests the V1 invariants, then removes the database. It never
writes test records to the configured application database.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import os
import sys
from threading import Barrier
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
from app.domain.owner_truth.candidate_decisions import (
    CandidateReviewAction,
    OwnerTruthCandidateReviewCommand,
    OwnerTruthCandidateReviewConflict,
    OwnerTruthCandidateReviewSourceInactive,
)
from app.services.owner_truth_source import (
    ArchiveOwnerTruthCompatibilityFacade,
    OwnerTruthSourceCommandService,
)
from app.services.owner_truth_candidate_review import OwnerTruthCandidateReviewService
from app.services.owner_truth_memory_projection import OwnerTruthMemoryProjectionService
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


def canonical_hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


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
        require(applied["appliedVersions"][-1] == "0016", "owner truth Memory projection migration must apply")

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

            review_vault_id = "candidate-review-vault"
            review_owner_id = "candidate-review-owner"
            review_source_id = str(uuid.uuid4())
            review_candidate_id = str(uuid.uuid4())
            review_corrected_candidate_id = str(uuid.uuid4())
            review_rejected_candidate_id = str(uuid.uuid4())
            review_stale_evidence_candidate_id = str(uuid.uuid4())
            review_unbacked_corrected_candidate_id = str(uuid.uuid4())
            review_concurrent_candidate_id = str(uuid.uuid4())
            review_content = {"summary": "小时候在院子里听雨。"}
            review_payload = {
                "schemaVersion": "owner-truth-candidate-proposal-v1",
                "content": review_content,
                "contentSchemaVersion": "owner-truth-v1",
                "evidenceRefs": [
                    {
                        "sourceId": review_source_id,
                        "sourceVersion": 1,
                        "span": {"start": 0, "end": 10},
                    }
                ],
                "reviewMode": "single",
            }
            with psycopg.connect(test_dsn) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO owner_truth.vaults (vault_id, owner_subject_id) VALUES (%s, %s)",
                        (review_vault_id, review_owner_id),
                    )
                    cursor.execute(
                        """
                        INSERT INTO owner_truth.sources (
                            id, vault_id, owner_subject_id, source_kind, content_hash,
                            content_payload, policy_version, authority_epoch
                        ) VALUES (%s, %s, %s, 'text', %s, %s, 'owner-truth-v1', 0)
                        """,
                        (
                            review_source_id,
                            review_vault_id,
                            review_owner_id,
                            canonical_hash({"text": "小时候在院子里听雨。"}),
                            json.dumps({"text": "小时候在院子里听雨。"}),
                        ),
                    )
                    for candidate in (
                        review_candidate_id,
                        review_corrected_candidate_id,
                        review_rejected_candidate_id,
                        review_stale_evidence_candidate_id,
                        review_unbacked_corrected_candidate_id,
                        review_concurrent_candidate_id,
                    ):
                        cursor.execute(
                            """
                            INSERT INTO owner_truth.memory_candidates (
                                id, vault_id, owner_subject_id, source_id, candidate_kind,
                                perspective_type, epistemic_status, sensitivity, policy_version,
                                authority_epoch, content_hash, payload_schema_version, payload
                            ) VALUES (%s, %s, %s, %s, 'experience', 'firstPerson', 'recalled',
                                'standard', 'owner-truth-v1', 0, %s, 'owner-truth-v1', %s)
                            """,
                            (
                                candidate,
                                review_vault_id,
                                review_owner_id,
                                review_source_id,
                                canonical_hash(review_content),
                                json.dumps(review_payload),
                            ),
                        )
                    stale_evidence_payload = {
                        **review_payload,
                        "evidenceRefs": [
                            {
                                "sourceId": review_source_id,
                                "sourceVersion": 2,
                                "span": {"start": 0, "end": 10},
                            }
                        ],
                    }
                    cursor.execute(
                        """
                        UPDATE owner_truth.memory_candidates
                        SET payload = %s
                        WHERE vault_id = %s AND id = %s
                        """,
                        (
                            json.dumps(stale_evidence_payload),
                            review_vault_id,
                            review_stale_evidence_candidate_id,
                        ),
                    )

            review_context = OwnerTruthCommandContext(
                vault_id=review_vault_id,
                owner_subject_id=review_owner_id,
                actor_subject_id=review_owner_id,
                policy_version="owner-truth-v1",
            )
            review_service = OwnerTruthCandidateReviewService(store)
            accepted = review_service.decide_and_activate(
                command=OwnerTruthCandidateReviewCommand(
                    command_id="owner-truth-candidate-accept-smoke",
                    candidate_id=review_candidate_id,
                    expected_candidate_version=1,
                    action=CandidateReviewAction.ACCEPT,
                    corrected_value=None,
                    corrected_value_schema_version="owner-truth-v1",
                    reason_code="ownerReviewed",
                ),
                context=review_context,
            )
            accepted_replay = review_service.decide_and_activate(
                command=OwnerTruthCandidateReviewCommand(
                    command_id="owner-truth-candidate-accept-smoke",
                    candidate_id=review_candidate_id,
                    expected_candidate_version=1,
                    action=CandidateReviewAction.ACCEPT,
                    corrected_value=None,
                    corrected_value_schema_version="owner-truth-v1",
                    reason_code="ownerReviewed",
                ),
                context=review_context,
            )
            corrected = review_service.decide_and_activate(
                command=OwnerTruthCandidateReviewCommand(
                    command_id="owner-truth-candidate-correct-smoke",
                    candidate_id=review_corrected_candidate_id,
                    expected_candidate_version=1,
                    action=CandidateReviewAction.CORRECT,
                    corrected_value={"summary": "小时候在院子里听雨，后来常常想起。"},
                    corrected_value_schema_version="owner-truth-v1",
                    reason_code="ownerCorrected",
                ),
                context=review_context,
            )
            rejected = review_service.decide_and_activate(
                command=OwnerTruthCandidateReviewCommand(
                    command_id="owner-truth-candidate-reject-smoke",
                    candidate_id=review_rejected_candidate_id,
                    expected_candidate_version=1,
                    action=CandidateReviewAction.REJECT,
                    corrected_value=None,
                    corrected_value_schema_version="owner-truth-v1",
                    reason_code="ownerRejected",
                ),
                context=review_context,
            )
            require(accepted.review.outcome == "created", "Owner Candidate accept must persist once")
            require(accepted.memory_activation.outcome == "created", "accepted Candidate must activate one MemoryVersion")
            require(accepted_replay.review.outcome == "deduplicated", "Owner Candidate replay must deduplicate")
            require(accepted_replay.memory_activation.outcome == "deduplicated", "Memory activation replay must deduplicate")
            require(
                accepted.review.receipt_id == accepted_replay.review.receipt_id,
                "Owner Candidate replay must preserve receipt",
            )
            require(
                corrected.review.corrected_value_id is not None,
                "corrected Candidate must retain separate Owner value",
            )
            require(
                corrected.memory_activation.outcome == "created",
                "corrected Candidate must activate one MemoryVersion",
            )
            require(
                rejected.memory_activation.outcome == "notApplicable"
                and rejected.memory_activation.memory_id is None,
                "rejected Candidate must not activate a MemoryVersion",
            )

            projection_service = OwnerTruthMemoryProjectionService(store)
            projection_missing = projection_service.read(context=review_context)
            require(
                projection_missing["state"] == "rebuilding"
                and projection_missing["entries"] == [],
                "missing MemoryVersion checkpoint must fail closed",
            )
            projection_rebuilt = projection_service.rebuild(context=review_context)
            projection_replayed = projection_service.rebuild(context=review_context)
            projection_ready = projection_service.read(context=review_context)
            require(
                projection_rebuilt.outcome == "rebuilt"
                and projection_replayed.outcome == "unchanged",
                "MemoryVersion projection rebuild must be deterministic",
            )
            require(
                projection_ready["state"] == "ready"
                and projection_ready["entryCount"] == 2
                and projection_ready["checkpoint"] == projection_rebuilt.snapshot["checkpoint"],
                "accepted and corrected MemoryVersions must be projection-visible",
            )
            corrected_projection = next(
                item
                for item in projection_ready["entries"]
                if item["memoryId"] == corrected.memory_activation.memory_id
            )
            require(
                corrected_projection["content"]
                == {"summary": "小时候在院子里听雨，后来常常想起。"},
                "projection must use the immutable corrected MemoryVersion content",
            )
            require(
                "decisionReceiptId" not in str(projection_ready["entries"])
                and "rationale" not in str(projection_ready["entries"]),
                "projection must not duplicate DecisionReceipt rationale",
            )

            expect_rejected(
                test_dsn,
                lambda cursor: cursor.execute(
                    """
                    UPDATE owner_truth.memory_projection_entries
                    SET payload = payload || '{"decisionReceiptId":"forbidden"}'::JSONB
                    WHERE vault_id = %s
                      AND authority_epoch = %s
                      AND memory_id = %s
                    """,
                    (
                        review_vault_id,
                        projection_ready["authorityEpoch"],
                        accepted.memory_activation.memory_id,
                    ),
                ),
                "projection entries must reject DecisionReceipt payload leakage",
            )

            expect_rejected(
                test_dsn,
                lambda cursor: cursor.execute(
                    """
                    INSERT INTO owner_truth.memory_projection_entries (
                        vault_id, authority_epoch, memory_id, memory_version_id,
                        version_number, source_id, source_version, memory_kind,
                        perspective_type, epistemic_status, sensitivity, visibility,
                        content_schema_version, content_hash, payload
                    ) VALUES (%s, 1, %s, %s, 1, %s, 1, 'experience', 'firstPerson',
                        'recalled', 'standard', 'owner', 'owner-truth-v1', %s, %s)
                    """,
                    (
                        review_vault_id,
                        accepted.memory_activation.memory_id,
                        accepted.memory_activation.memory_version_id,
                        review_source_id,
                        accepted.memory_activation.content_hash,
                        json.dumps(
                            {
                                "content": review_content,
                                "evidenceRefs": review_payload["evidenceRefs"],
                            }
                        ),
                    ),
                ),
                "projection entries must reject stale authority epochs",
            )

            activation_rolled_back = False
            try:
                review_service.decide_and_activate(
                    command=OwnerTruthCandidateReviewCommand(
                        command_id="owner-truth-candidate-stale-evidence-smoke",
                        candidate_id=review_stale_evidence_candidate_id,
                        # The test changes evidenceRefs to an unavailable source version
                        # immediately before this command.  That mutable Candidate update
                        # advances its optimistic row version from 1 to 2; the command must
                        # therefore pass the fresh version so activation reaches the source
                        # validation and proves the whole UoW rolls back.
                        expected_candidate_version=2,
                        action=CandidateReviewAction.ACCEPT,
                        corrected_value=None,
                        corrected_value_schema_version="owner-truth-v1",
                        reason_code="ownerReviewed",
                    ),
                    context=review_context,
                )
            except OwnerTruthCandidateReviewSourceInactive:
                activation_rolled_back = True
            require(
                activation_rolled_back,
                "stale Candidate source evidence must block MemoryVersion activation",
            )
            with psycopg.connect(test_dsn) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT decision_status
                        FROM owner_truth.memory_candidates
                        WHERE vault_id = %s AND id = %s
                        """,
                        (review_vault_id, review_stale_evidence_candidate_id),
                    )
                    require(
                        cursor.fetchone()[0] == "pending",
                        "activation failure must roll back the Candidate terminal transition",
                    )
                    cursor.execute(
                        """
                        SELECT count(*)
                        FROM owner_truth.decision_receipts
                        WHERE vault_id = %s AND candidate_id = %s
                        """,
                        (review_vault_id, review_stale_evidence_candidate_id),
                    )
                    require(
                        int(cursor.fetchone()[0]) == 0,
                        "activation failure must roll back its DecisionReceipt",
                    )
            pending_inbox_ids = {
                item.candidate_id
                for item in review_service.list_pending(context=review_context)
            }
            require(
                review_candidate_id not in pending_inbox_ids
                and review_corrected_candidate_id not in pending_inbox_ids
                and review_rejected_candidate_id not in pending_inbox_ids
                and review_stale_evidence_candidate_id in pending_inbox_ids
                and review_unbacked_corrected_candidate_id in pending_inbox_ids,
                "terminal Candidates must leave the Owner inbox without hiding unrelated pending work",
            )

            with psycopg.connect(test_dsn) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT memory.decision_receipt_id, memory.content_hash,
                            version.id, version.version_number, version.is_current,
                            version.payload
                        FROM owner_truth.memories AS memory
                        JOIN owner_truth.memory_versions AS version
                          ON version.vault_id = memory.vault_id
                         AND version.memory_id = memory.id
                        WHERE memory.vault_id = %s
                          AND memory.decision_receipt_id IN (%s, %s)
                        ORDER BY memory.decision_receipt_id
                        """,
                        (
                            review_vault_id,
                            accepted.review.receipt_id,
                            corrected.review.receipt_id,
                        ),
                    )
                    activated_rows = cursor.fetchall()
                    require(
                        len(activated_rows) == 2
                        and all(row[3] == 1 and row[4] for row in activated_rows),
                        "accepted/corrected receipts must each create exactly one current initial version",
                    )
                    corrected_row = next(
                        row for row in activated_rows
                        if str(row[0]) == corrected.review.receipt_id
                    )
                    corrected_payload = corrected_row[5]
                    if isinstance(corrected_payload, str):
                        corrected_payload = json.loads(corrected_payload)
                    require(
                        corrected_payload["content"] == {"summary": "小时候在院子里听雨，后来常常想起。"},
                        "corrected MemoryVersion must use the immutable Owner value",
                    )
                    cursor.execute(
                        """
                        SELECT count(*)
                        FROM owner_truth.memories
                        WHERE vault_id = %s AND decision_receipt_id = %s
                        """,
                        (review_vault_id, rejected.review.receipt_id),
                    )
                    require(
                        int(cursor.fetchone()[0]) == 0,
                        "rejected DecisionReceipt must not create a MemoryRecord",
                    )

            def insert_unbacked_corrected_decision(cursor):
                cursor.execute(
                    "UPDATE owner_truth.memory_candidates SET decision_status = 'corrected' WHERE id = %s",
                    (review_unbacked_corrected_candidate_id,),
                )
                cursor.execute(
                    """
                    INSERT INTO owner_truth.decision_receipts (
                        id, vault_id, candidate_id, decision, actor_subject_id,
                        authority_epoch, policy_version, command_id_hash, payload_hash,
                        expected_candidate_version, candidate_before_hash, candidate_after_hash,
                        decision_basis
                    ) VALUES (%s, %s, %s, 'corrected', %s, 0, 'owner-truth-v1', %s, %s,
                        1, %s, %s, %s)
                    """,
                    (
                        str(uuid.uuid4()),
                        review_vault_id,
                        review_unbacked_corrected_candidate_id,
                        review_owner_id,
                        canonical_hash("unbacked-command"),
                        canonical_hash("unbacked-payload"),
                        canonical_hash(review_content),
                        canonical_hash({"summary": "缺少纠正值。"}),
                        json.dumps(
                            {
                                "schemaVersion": "owner-truth-decision-basis-v1",
                                "reasonCode": "ownerCorrected",
                                "sourceRefs": review_payload["evidenceRefs"],
                            }
                        ),
                    ),
                )

            expect_rejected(
                test_dsn,
                insert_unbacked_corrected_decision,
                "a corrected Candidate must retain exactly one corrected Owner value",
            )

            # Two distinct commands race on the same pending Candidate.  The
            # service must serialize through the Candidate advisory lock and
            # leave exactly one terminal DecisionReceipt.
            concurrent_start = Barrier(2)

            def decide_concurrently(index: int) -> str:
                concurrent_store = PostgresStore(
                    dsn=test_dsn,
                    pool_min_size=1,
                    pool_max_size=1,
                    pool_timeout_seconds=5.0,
                )
                concurrent_store.open_pool(wait=True)
                try:
                    concurrent_start.wait(timeout=10)
                    result = OwnerTruthCandidateReviewService(concurrent_store).decide_and_activate(
                        command=OwnerTruthCandidateReviewCommand(
                            command_id=f"owner-truth-candidate-race-{index}",
                            candidate_id=review_concurrent_candidate_id,
                            expected_candidate_version=1,
                            action=CandidateReviewAction.ACCEPT,
                            corrected_value=None,
                            corrected_value_schema_version="owner-truth-v1",
                            reason_code="ownerReviewed",
                        ),
                        context=review_context,
                    )
                    return result.review.outcome
                except OwnerTruthCandidateReviewConflict:
                    return "conflict"
                finally:
                    concurrent_store.close_pool()

            with ThreadPoolExecutor(max_workers=2) as executor:
                concurrent_outcomes = list(
                    executor.map(decide_concurrently, (1, 2))
                )
            require(
                sorted(concurrent_outcomes) == ["conflict", "created"],
                "concurrent Candidate decisions must produce one writer and one terminal conflict",
            )
            with psycopg.connect(test_dsn) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT count(*) FROM owner_truth.decision_receipts "
                        "WHERE vault_id = %s AND candidate_id = %s",
                        (review_vault_id, review_concurrent_candidate_id),
                    )
                    require(
                        int(cursor.fetchone()[0]) == 1,
                        "concurrent Candidate decisions must persist exactly one receipt",
                    )
                    cursor.execute(
                        """
                        SELECT count(*)
                        FROM owner_truth.memories
                        WHERE vault_id = %s
                          AND decision_receipt_id = (
                              SELECT id
                              FROM owner_truth.decision_receipts
                              WHERE vault_id = %s AND candidate_id = %s
                          )
                        """,
                        (review_vault_id, review_vault_id, review_concurrent_candidate_id),
                    )
                    require(
                        int(cursor.fetchone()[0]) == 1,
                        "concurrent Candidate activation must persist exactly one MemoryRecord",
                    )
            projection_changed = projection_service.read(context=review_context)
            require(
                projection_changed["state"] == "rebuilding"
                and projection_changed["entries"] == [],
                "a new MemoryVersion must invalidate an older checkpoint",
            )
            projection_after_concurrent = projection_service.rebuild(context=review_context)
            require(
                projection_after_concurrent.snapshot["entryCount"] == 3,
                "rebuild must include each current accepted MemoryVersion exactly once",
            )
            with psycopg.connect(test_dsn) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE owner_truth.sources SET state = 'redacted' WHERE vault_id = %s AND id = %s",
                        (review_vault_id, review_source_id),
                    )
            projection_revoked = projection_service.read(context=review_context)
            require(
                projection_revoked["state"] == "rebuilding"
                and projection_revoked["entries"] == [],
                "Source revocation must fail closed instead of serving a stale projection",
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
                    "candidateReviewIdempotent": True,
                    "candidateReviewConcurrentSingleWriter": True,
                    "candidateCorrectionSeparate": True,
                    "correctedDecisionRequiresValue": True,
                    "decisionMemoryActivation": True,
                    "correctedMemoryUsesOwnerValue": True,
                    "rejectedDecisionNoMemory": True,
                    "candidateMemoryActivationConcurrentSingleWriter": True,
                    "decisionMemoryActivationRollback": True,
                    "memoryProjectionDeterministicRebuild": True,
                    "memoryProjectionCorrectedContent": True,
                    "memoryProjectionRejectsDecisionPayloadLeakage": True,
                    "memoryProjectionStaleEpochRejected": True,
                    "memoryProjectionInvalidatesOnMemoryChange": True,
                    "memoryProjectionFailsClosedOnSourceRevocation": True,
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
