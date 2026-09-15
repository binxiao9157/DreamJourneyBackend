#!/usr/bin/env python3
"""Exercise the V5 correction-preview binding against disposable PostgreSQL."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import uuid

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.domain.owner_truth.candidate_decisions import (
    CandidateReviewAction,
    OwnerTruthCandidateReviewCommand,
    OwnerTruthCandidateReviewError,
)
from app.domain.owner_truth.contracts import MemoryKind
from app.domain.owner_truth.ontology import (
    OWNER_TRUTH_SCHEMA_VERSION_V5,
    enrich_memory_payload_v5,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_candidate_review import OwnerTruthCandidateReviewService
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
            cursor.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database_name))
            )


def database_state(dsn: str, *, vault_id: str, candidate_id: str) -> dict[str, object]:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT decision_status, row_version, content_hash
                FROM owner_truth.memory_candidates
                WHERE vault_id = %s AND id = %s
                """,
                (vault_id, candidate_id),
            )
            candidate = cursor.fetchone()
            require(candidate is not None, "synthetic candidate must exist")
            counts: dict[str, int] = {}
            for key, table in (
                ("proposals", "memory_changeset_proposals"),
                ("receipts", "decision_receipts"),
                ("correctedValues", "candidate_decision_values"),
                ("memories", "memories"),
                ("memoryVersions", "memory_versions"),
                ("changeSets", "memory_changesets"),
            ):
                cursor.execute(
                    sql.SQL("SELECT COUNT(*) FROM owner_truth.{} WHERE vault_id = %s").format(
                        sql.Identifier(table)
                    ),
                    (vault_id,),
                )
                counts[key] = int(cursor.fetchone()[0])
            cursor.execute(
                "SELECT revision FROM owner_truth.memory_revisions WHERE vault_id = %s",
                (vault_id,),
            )
            revision_row = cursor.fetchone()
            return {
                "decision": str(candidate[0]),
                "candidateVersion": int(candidate[1]),
                "candidateHash": str(candidate[2]),
                "memoryRevision": 0 if revision_row is None else int(revision_row[0]),
                **counts,
            }


def expect_conflict(operation, message: str) -> None:
    try:
        operation()
    except OwnerTruthCandidateReviewError:
        return
    raise AssertionError(message)


def main() -> None:
    require(
        os.environ.get("DJ_ALLOW_LOCAL_EPHEMERAL_PG") == "1",
        "DJ_ALLOW_LOCAL_EPHEMERAL_PG=1 is required",
    )
    base_dsn = os.environ.get("DATABASE_URL", "").strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    host = str(parameters.get("host") or "")
    require(host in {"127.0.0.1", "localhost", "::1"}, "only local PostgreSQL is allowed")
    require(bool(parameters.get("user")), "DATABASE_URL must identify a local database user")

    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_b4_correction_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    store: PostgresStore | None = None
    result: dict[str, object] = {
        "schemaVersion": "dreamjourney-b4-correction-postgres-smoke-v1",
        "syntheticDataOnly": True,
        "databaseScope": "disposable-local",
    }

    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="b4-correction-preview-postgres-smoke",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        applied = migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")
        require(
            applied["appliedVersions"][-1] == verified["expectedHead"],
            "all migrations must apply",
        )

        vault_id = "vault-b4-correction-pg-smoke"
        owner_id = "owner-b4-correction-pg-smoke"
        source_id = str(uuid.uuid4())
        candidate_id = str(uuid.uuid4())
        original_content = enrich_memory_payload_v5(
            kind=MemoryKind.KNOWLEDGE,
            payload={
                "statement": "合成测试阅读清单代号是旧值。",
                "knowledgeType": "preference",
                "domains": ["knowledgeSkills"],
                "factType": "knowledge",
                "predicate": "readingListCodeName",
                "object": {"label": "旧值", "category": "codeName"},
            },
            provenance={"mode": "selfReport"},
            memory_subject_id=owner_id,
            claim_subject_id=owner_id,
        )
        original_hash = canonical_hash(original_content)
        candidate_payload = {
            "schemaVersion": "owner-truth-candidate-proposal-v1",
            "content": original_content,
            "contentSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION_V5,
            "evidenceRefs": [
                {
                    "sourceId": source_id,
                    "sourceVersion": 1,
                    "span": {"start": 0, "end": 20},
                }
            ],
            "reviewMode": "single",
        }
        with psycopg.connect(test_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO owner_truth.vaults (vault_id, owner_subject_id) VALUES (%s, %s)",
                    (vault_id, owner_id),
                )
                cursor.execute(
                    """
                    INSERT INTO owner_truth.sources (
                        id, vault_id, owner_subject_id, source_kind, content_hash,
                        content_payload, policy_version, authority_epoch
                    ) VALUES (%s, %s, %s, 'text', %s, %s, %s, 0)
                    """,
                    (
                        source_id,
                        vault_id,
                        owner_id,
                        canonical_hash({"synthetic": True}),
                        json.dumps({"synthetic": True}),
                        OWNER_TRUTH_SCHEMA_VERSION_V5,
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO owner_truth.memory_candidates (
                        id, vault_id, owner_subject_id, source_id, candidate_kind,
                        perspective_type, epistemic_status, sensitivity, policy_version,
                        authority_epoch, content_hash, payload_schema_version, payload
                    ) VALUES (%s, %s, %s, %s, 'knowledge', 'firstPerson', 'recalled',
                        'standard', %s, 0, %s, %s, %s)
                    """,
                    (
                        candidate_id,
                        vault_id,
                        owner_id,
                        source_id,
                        OWNER_TRUTH_SCHEMA_VERSION_V5,
                        original_hash,
                        OWNER_TRUTH_SCHEMA_VERSION_V5,
                        json.dumps(candidate_payload, ensure_ascii=False),
                    ),
                )

        context = OwnerTruthCommandContext(
            vault_id=vault_id,
            owner_subject_id=owner_id,
            actor_subject_id=owner_id,
            policy_version=OWNER_TRUTH_SCHEMA_VERSION_V5,
        )
        store = PostgresStore(dsn=test_dsn, pool_min_size=1, pool_max_size=2)
        store.open_pool(wait=True)
        service = OwnerTruthCandidateReviewService(store)
        corrected_value = {"statement": "合成测试阅读清单代号是新值。"}

        before_preview = database_state(test_dsn, vault_id=vault_id, candidate_id=candidate_id)
        preview = service.preview_changeset_result(
            candidate_id=candidate_id,
            context=context,
            corrected_value=corrected_value,
            corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V5,
        )
        require(preview.proposal is not None, "correction preview must produce a proposal")
        proposal = preview.proposal
        binding = preview.correction_binding_payload()
        require(binding is not None, "correction preview must produce a correction binding")
        require(binding["sourceCandidateContentHash"] == original_hash, "binding must retain H0")
        require(
            binding["resolvedContentHash"] == proposal.candidate_content_hash,
            "binding H1 must match proposal H1",
        )
        require(proposal.candidate_content_hash != original_hash, "H1 must differ from H0")

        after_preview = database_state(test_dsn, vault_id=vault_id, candidate_id=candidate_id)
        require(after_preview["decision"] == "pending", "preview must keep Candidate pending")
        require(after_preview["candidateVersion"] == 1, "preview must not advance Candidate version")
        require(after_preview["memoryRevision"] == 0, "preview must not advance memory revision")
        require(after_preview["receipts"] == 0, "preview must not create a review receipt")
        require(after_preview["memories"] == 0, "preview must not create formal memory")
        require(after_preview["memoryVersions"] == 0, "preview must not create MemoryVersion")
        require(after_preview["proposals"] == 1, "preview must retain one immutable audit proposal")

        repeated_preview = service.preview_changeset_result(
            candidate_id=candidate_id,
            context=context,
            corrected_value=corrected_value,
            corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V5,
        )
        require(
            repeated_preview.proposal is not None
            and repeated_preview.proposal.proposal_hash == proposal.proposal_hash,
            "same preview must be deterministic",
        )
        require(
            database_state(test_dsn, vault_id=vault_id, candidate_id=candidate_id)["proposals"]
            == 1,
            "same preview must remain idempotent",
        )

        def command(
            *,
            command_id: str,
            corrected: dict[str, object] = corrected_value,
            expected_version: int = 1,
            expected_revision: int = 0,
            expected_change_set_id: str = proposal.change_set.change_set_id,
            expected_proposal_hash: str = proposal.proposal_hash,
        ) -> OwnerTruthCandidateReviewCommand:
            return OwnerTruthCandidateReviewCommand(
                command_id=command_id,
                candidate_id=candidate_id,
                expected_candidate_version=expected_version,
                expected_memory_revision=expected_revision,
                expected_change_set_id=expected_change_set_id,
                expected_proposal_hash=expected_proposal_hash,
                action=CandidateReviewAction.CORRECT,
                corrected_value=corrected,
                corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V5,
                reason_code="ownerCorrected",
            )

        stable_preview_state = database_state(test_dsn, vault_id=vault_id, candidate_id=candidate_id)
        expect_conflict(
            lambda: service.decide_and_activate(
                command=command(
                    command_id="b4-pg-tampered-value",
                    corrected={"statement": "合成测试阅读清单代号是篡改值。"},
                ),
                context=context,
            ),
            "tampered corrected value must be rejected",
        )
        expect_conflict(
            lambda: service.decide_and_activate(
                command=command(command_id="b4-pg-wrong-version", expected_version=2),
                context=context,
            ),
            "wrong Candidate version must be rejected",
        )
        expect_conflict(
            lambda: service.decide_and_activate(
                command=command(command_id="b4-pg-wrong-revision", expected_revision=1),
                context=context,
            ),
            "wrong memory revision must be rejected",
        )
        expect_conflict(
            lambda: service.decide_and_activate(
                command=command(
                    command_id="b4-pg-wrong-changeset",
                    expected_change_set_id=str(uuid.uuid4()),
                ),
                context=context,
            ),
            "wrong ChangeSet id must be rejected",
        )
        expect_conflict(
            lambda: service.decide_and_activate(
                command=command(
                    command_id="b4-pg-wrong-proposal-hash",
                    expected_proposal_hash="0" * 64,
                ),
                context=context,
            ),
            "wrong proposal hash must be rejected",
        )
        require(
            database_state(test_dsn, vault_id=vault_id, candidate_id=candidate_id)
            == stable_preview_state,
            "all tampered writes must roll back atomically",
        )

        valid_command = command(command_id="b4-pg-valid-correction")
        created = service.decide_and_activate(command=valid_command, context=context)
        require(created.review.outcome == "created", "valid correction must persist")
        require(created.review.candidate_before_hash == original_hash, "receipt must retain H0")
        require(
            created.review.candidate_after_hash == proposal.candidate_content_hash,
            "receipt must retain H1",
        )
        require(created.review.candidate_row_version == 2, "receipt must report N+1")
        require(created.memory_revision == 1, "formal memory revision must advance once")

        after_created = database_state(test_dsn, vault_id=vault_id, candidate_id=candidate_id)
        require(after_created["decision"] == "corrected", "Candidate must be terminal corrected")
        require(after_created["candidateVersion"] == 2, "Candidate version must advance once")
        require(after_created["memoryRevision"] == 1, "memory revision must advance once")
        require(after_created["receipts"] == 1, "one receipt must exist")
        require(after_created["correctedValues"] == 1, "one corrected value must exist")
        require(after_created["memories"] == 1, "one formal memory must exist")
        require(after_created["memoryVersions"] == 1, "one MemoryVersion must exist")
        require(after_created["changeSets"] == 1, "one applied ChangeSet must exist")

        replay = service.decide_and_activate(command=valid_command, context=context)
        require(replay.review.outcome == "deduplicated", "same command must deduplicate")
        require(
            replay.review.receipt_id == created.review.receipt_id,
            "idempotent replay must return the same receipt",
        )
        require(
            database_state(test_dsn, vault_id=vault_id, candidate_id=candidate_id)
            == after_created,
            "idempotent replay must not create additional rows",
        )

        before_lookup = database_state(test_dsn, vault_id=vault_id, candidate_id=candidate_id)
        found = service.lookup_decision_result(
            candidate_id=candidate_id,
            command_id=valid_command.command_id,
            context=context,
        )
        require(found.result == "found", "read-only lookup must find the persisted command")
        require(
            found.decision_result is not None
            and found.decision_result.review.candidate_before_hash == original_hash
            and found.decision_result.review.candidate_after_hash
            == proposal.candidate_content_hash,
            "read-only lookup must preserve H0/H1",
        )
        not_observed = service.lookup_decision_result(
            candidate_id=candidate_id,
            command_id="b4-pg-never-written-command",
            context=context,
        )
        require(not_observed.result == "notObserved", "unknown command must remain notObserved")
        require(
            database_state(test_dsn, vault_id=vault_id, candidate_id=candidate_id)
            == before_lookup,
            "decision-result lookup must have zero business writes",
        )

        result.update(
            {
                "migrationHead": verified["expectedHead"],
                "previewAuditOnly": True,
                "previewIdempotent": True,
                "tamperCasesRejected": 5,
                "tamperRollbackAtomic": True,
                "validCorrectionPersisted": True,
                "candidateVersionAdvancedOnce": True,
                "memoryRevisionAdvancedOnce": True,
                "decisionReplayDeduplicated": True,
                "decisionResultFoundReadOnly": True,
                "decisionResultNotObservedReadOnly": True,
                "sourceAndResolvedHashesSeparated": True,
            }
        )
        print(json.dumps(result, sort_keys=True))
    finally:
        if store is not None:
            store.close_pool()
        drop_database(admin_dsn, database_name)


if __name__ == "__main__":
    main()
