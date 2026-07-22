#!/usr/bin/env python3
"""Exercise formal interview Candidate confirmation in a disposable Postgres DB.

The smoke intentionally calls the release-policy protected confirmation route,
not its QA-only predecessor. It requires an explicit isolated smoke-admin DSN,
creates a temporary database, applies all migrations, seeds only synthetic
Owner Truth workflow rows, and removes that database on exit. No production
account or business record is read or written.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from threading import Barrier
from typing import Any
import uuid

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb

import app.main as main_module
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.domain.owner_truth.interview_candidate_batch_decision import (
    OwnerTruthInterviewCandidateBatchAcceptCommand,
    OwnerTruthInterviewCandidateBatchSelection,
)
from app.domain.owner_truth.ontology import OWNER_TRUTH_SCHEMA_VERSION
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


def apply_migrations_through(
    dsn: str,
    *,
    build_id: str,
    final_version: str,
) -> dict[str, Any]:
    """Build a temporary historical migration head without mutating the repo.

    The formal smoke needs to prove that an existing 0035 QA root can be
    upgraded by 0036. Copying the immutable migration files into a disposable
    directory is safer than editing the normal migration directory or relying
    on a production database with an older schema head.
    """

    selected = [
        migration
        for migration in sorted(default_migrations_dir().glob("*.sql"))
        if migration.name.split("_", 1)[0] <= final_version
    ]
    require(selected and selected[-1].name.startswith(f"{final_version}_"), "legacy migration head is missing")
    with tempfile.TemporaryDirectory(prefix="dj-formal-confirmation-migrations-") as directory:
        migrations_dir = Path(directory)
        for sql_path in selected:
            shutil.copy2(sql_path, migrations_dir / sql_path.name)
            manifest_path = sql_path.with_suffix(".json")
            shutil.copy2(manifest_path, migrations_dir / manifest_path.name)
        return PostgresMigrator(
            dsn=dsn,
            migrations_dir=migrations_dir,
            build_id=build_id,
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        ).apply()


def canonical_hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def route_code(response: Any) -> str:
    detail = response.json().get("detail") if response.content else None
    return str(detail.get("code") or "") if isinstance(detail, dict) else ""


def login(client: TestClient, *, phone: str) -> tuple[str, dict[str, str], str]:
    response = client.post(
        "/auth/login",
        json={
            "phone": phone,
            "nickname": "Formal confirmation Postgres smoke",
            "password": "formal-confirmation-smoke",
        },
    )
    require(response.status_code == 200, f"temporary owner login failed: {response.text}")
    payload = response.json()
    return (
        str(payload["user"]["id"]),
        {"Authorization": f"Bearer {payload['auth']['accessToken']}"},
        str(payload["auth"]["sessionId"]),
    )


def formal_headers(
    headers: dict[str, str],
    *,
    session_id: str,
    decision_id: str,
) -> dict[str, str]:
    return {
        **headers,
        "X-DreamJourney-Feature": "ownerTruthCandidateReview",
        "X-DreamJourney-Feature-Decision-Id": decision_id,
        "X-DreamJourney-Feature-Allowed": "true",
        "X-DreamJourney-Policy-Version": "release-policy-v1",
        "X-DreamJourney-Policy-Revision": "1",
        "X-DreamJourney-Account-Generation": sha256(session_id.encode("utf-8")).hexdigest()[:24],
    }


def seed_reviewable_batch(
    dsn: str,
    *,
    vault_id: str,
    owner_subject_id: str,
) -> tuple[str, str]:
    thread_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    review_batch_id = str(uuid.uuid4())
    source_id = str(uuid.uuid4())
    admission_id = str(uuid.uuid4())
    extraction_id = str(uuid.uuid4())
    candidate_id = str(uuid.uuid4())
    source_payload = {"text": "Formal confirmation Postgres smoke source"}
    candidate_content = {"summary": "Synthetic batch Candidate for formal confirmation."}
    candidate_payload = {
        "schemaVersion": "owner-truth-candidate-proposal-v1",
        "contentSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION,
        "content": candidate_content,
        "evidenceRefs": [
            {
                "sourceId": source_id,
                "sourceVersion": 1,
                "span": {"start": 0, "end": 1},
            }
        ],
        "reviewMode": "batch",
    }
    review_metadata = {
        "origin": "interviewReviewBatchCandidateProposal",
        "reviewBatchId": review_batch_id,
    }
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO owner_truth.vaults (vault_id, owner_subject_id) VALUES (%s, %s)",
                (vault_id, owner_subject_id),
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.conversation_threads (
                    id, vault_id, owner_subject_id, entry_mode, policy_version
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (thread_id, vault_id, owner_subject_id, "naturalInput", OWNER_TRUTH_SCHEMA_VERSION),
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.interview_sessions (
                    id, vault_id, owner_subject_id, current_thread_id, policy_version
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (session_id, vault_id, owner_subject_id, thread_id, OWNER_TRUTH_SCHEMA_VERSION),
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.interview_review_batches (
                    id, vault_id, owner_subject_id, session_id, thread_id, trigger,
                    state, captured_candidate_batch_turn_count, owner_turn_start_count,
                    owner_turn_end_count, through_message_sequence, policy_version,
                    acknowledged_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """,
                (
                    review_batch_id,
                    vault_id,
                    owner_subject_id,
                    session_id,
                    thread_id,
                    "turnThreshold",
                    "acknowledged",
                    1,
                    1,
                    1,
                    1,
                    OWNER_TRUTH_SCHEMA_VERSION,
                ),
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.sources (
                    id, vault_id, owner_subject_id, source_kind, content_hash,
                    policy_version, metadata
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    source_id,
                    vault_id,
                    owner_subject_id,
                    "conversation",
                    canonical_hash(source_payload),
                    OWNER_TRUTH_SCHEMA_VERSION,
                    Jsonb(review_metadata),
                ),
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.interview_review_batch_candidate_admissions (
                    id, vault_id, owner_subject_id, review_batch_id, source_id,
                    source_version, source_content_hash, effect_operation_id,
                    command_id_hash, payload_hash, actor_subject_id, policy_version,
                    owner_message_count, first_message_sequence, last_message_sequence
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    admission_id,
                    vault_id,
                    owner_subject_id,
                    review_batch_id,
                    source_id,
                    1,
                    canonical_hash(source_payload),
                    str(uuid.uuid4()),
                    canonical_hash({"command": "admit"}),
                    canonical_hash({"batch": review_batch_id, "source": source_id}),
                    owner_subject_id,
                    OWNER_TRUTH_SCHEMA_VERSION,
                    1,
                    1,
                    1,
                ),
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.extraction_results (
                    id, vault_id, source_id, source_version, extractor_id,
                    schema_version, status, result_hash, payload, completed_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """,
                (
                    extraction_id,
                    vault_id,
                    source_id,
                    1,
                    "formal-confirmation-smoke",
                    "owner-truth-candidate-extraction-v1",
                    "succeeded",
                    canonical_hash(candidate_payload),
                    Jsonb({}),
                ),
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.memory_candidates (
                    id, vault_id, owner_subject_id, source_id, extraction_result_id,
                    candidate_kind, perspective_type, epistemic_status, sensitivity,
                    policy_version, content_hash, payload_schema_version, payload
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    candidate_id,
                    vault_id,
                    owner_subject_id,
                    source_id,
                    extraction_id,
                    "experience",
                    "firstPerson",
                    "recalled",
                    "standard",
                    OWNER_TRUTH_SCHEMA_VERSION,
                    canonical_hash(candidate_content),
                    "owner-truth-candidate-proposal-v1",
                    Jsonb(candidate_payload),
                ),
            )
        connection.commit()
    return review_batch_id, candidate_id


def seed_two_reviewable_candidates(
    dsn: str,
    *,
    vault_id: str,
    owner_subject_id: str,
) -> tuple[str, tuple[str, str]]:
    """Seed a two-Candidate batch used only to prove transactional rollback."""

    review_batch_id, first_candidate_id = seed_reviewable_batch(
        dsn,
        vault_id=vault_id,
        owner_subject_id=owner_subject_id,
    )
    second_candidate_id = str(uuid.uuid4())
    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT owner_subject_id, source_id, extraction_result_id,
                       candidate_kind, perspective_type, epistemic_status,
                       sensitivity, policy_version, payload_schema_version, payload
                FROM owner_truth.memory_candidates
                WHERE id = %s AND vault_id = %s
                FOR UPDATE
                """,
                (first_candidate_id, vault_id),
            )
            first = cursor.fetchone()
            require(first is not None, "first rollback Candidate must be seeded")
            payload = first["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            require(isinstance(payload, dict), "seed Candidate payload must be an object")
            second_payload = json.loads(json.dumps(payload, ensure_ascii=False))
            content = second_payload.get("content")
            require(isinstance(content, dict), "seed Candidate content must be an object")
            second_payload["content"] = {
                **content,
                "summary": "Synthetic second Candidate for formal rollback confirmation.",
            }
            cursor.execute(
                """
                INSERT INTO owner_truth.memory_candidates (
                    id, vault_id, owner_subject_id, source_id, extraction_result_id,
                    candidate_kind, perspective_type, epistemic_status, sensitivity,
                    policy_version, content_hash, payload_schema_version, payload
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    second_candidate_id,
                    vault_id,
                    first["owner_subject_id"],
                    first["source_id"],
                    first["extraction_result_id"],
                    first["candidate_kind"],
                    first["perspective_type"],
                    first["epistemic_status"],
                    first["sensitivity"],
                    first["policy_version"],
                    canonical_hash(second_payload["content"]),
                    first["payload_schema_version"],
                    Jsonb(second_payload),
                ),
            )
        connection.commit()
    return review_batch_id, (first_candidate_id, second_candidate_id)


def seed_legacy_qa_root(
    dsn: str,
    *,
    vault_id: str,
    owner_subject_id: str,
) -> tuple[str, str, str]:
    """Persist one pre-0036 QA root to prove the additive upgrade is compatible."""

    review_batch_id, candidate_id = seed_reviewable_batch(
        dsn,
        vault_id=vault_id,
        owner_subject_id=owner_subject_id,
    )
    command = OwnerTruthInterviewCandidateBatchAcceptCommand(
        command_id="legacy-qa-confirmation-root",
        review_batch_id=review_batch_id,
        selections=(
            OwnerTruthInterviewCandidateBatchSelection(
                candidate_id=candidate_id,
                expected_candidate_version=1,
            ),
        ),
        reason_code="ownerConfirmedAtBoundary",
    )
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO owner_truth.interview_review_batch_candidate_decisions (
                    id, vault_id, owner_subject_id, review_batch_id,
                    command_id_hash, payload_hash, selection_count,
                    actor_subject_id, policy_version, authority_epoch
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    command.batch_decision_id(vault_id=vault_id),
                    vault_id,
                    owner_subject_id,
                    review_batch_id,
                    command.command_id_hash,
                    command.payload_hash,
                    command.selection_count,
                    owner_subject_id,
                    OWNER_TRUTH_SCHEMA_VERSION,
                    0,
                ),
            )
        connection.commit()
    return review_batch_id, candidate_id, command.command_id_hash


def counts(dsn: str, *, vault_id: str) -> tuple[int, int, int]:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM owner_truth.interview_review_batch_candidate_decisions WHERE vault_id = %s",
                (vault_id,),
            )
            ledgers = int(cursor.fetchone()[0])
            cursor.execute(
                "SELECT COUNT(*) FROM owner_truth.decision_receipts WHERE vault_id = %s",
                (vault_id,),
            )
            receipts = int(cursor.fetchone()[0])
            cursor.execute(
                "SELECT COUNT(*) FROM owner_truth.interview_review_batch_candidate_decision_receipts WHERE vault_id = %s",
                (vault_id,),
            )
            links = int(cursor.fetchone()[0])
    return ledgers, receipts, links


def candidate_decisions(dsn: str, *, vault_id: str) -> dict[str, str]:
    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id::text AS id, decision
                FROM owner_truth.memory_candidates
                WHERE vault_id = %s
                ORDER BY id
                """,
                (vault_id,),
            )
            rows = cursor.fetchall()
    return {str(row["id"]): str(row["decision"]) for row in rows}


def install_second_receipt_link_rejection(dsn: str) -> None:
    """Inject one disposable DB failure after the first receipt link is written."""

    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE OR REPLACE FUNCTION owner_truth.formal_smoke_reject_second_link()
                RETURNS TRIGGER AS $$
                BEGIN
                    IF (
                        SELECT COUNT(*)
                        FROM owner_truth.interview_review_batch_candidate_decision_receipts
                        WHERE vault_id = NEW.vault_id
                          AND batch_decision_id = NEW.batch_decision_id
                    ) >= 1 THEN
                        RAISE EXCEPTION 'formal smoke rejects second receipt link';
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;

                CREATE TRIGGER formal_smoke_reject_second_link
                BEFORE INSERT ON owner_truth.interview_review_batch_candidate_decision_receipts
                FOR EACH ROW EXECUTE FUNCTION owner_truth.formal_smoke_reject_second_link();
                """
            )
        connection.commit()


def remove_second_receipt_link_rejection(dsn: str) -> None:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                DROP TRIGGER IF EXISTS formal_smoke_reject_second_link
                    ON owner_truth.interview_review_batch_candidate_decision_receipts;
                DROP FUNCTION IF EXISTS owner_truth.formal_smoke_reject_second_link();
                """
            )
        connection.commit()


def assert_concurrent_formal_replay_is_idempotent(
    *,
    path: str,
    headers: dict[str, str],
    session_id: str,
    payload: dict[str, object],
) -> None:
    """Run two independent formal requests through the real Postgres lock path."""

    barrier = Barrier(2)

    def post_once(decision_id: str) -> Any:
        with TestClient(main_module.app, raise_server_exceptions=False) as concurrent_client:
            barrier.wait(timeout=10)
            return concurrent_client.post(
                path,
                headers=formal_headers(
                    headers,
                    session_id=session_id,
                    decision_id=decision_id,
                ),
                json=payload,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(
            executor.map(
                post_once,
                (
                    "formal-confirmation-postgres-smoke-concurrent-a",
                    "formal-confirmation-postgres-smoke-concurrent-b",
                ),
            )
        )
    statuses = sorted(response.status_code for response in responses)
    require(statuses == [200, 201], f"concurrent formal replay statuses mismatch: {statuses}")
    outcomes = sorted(str(response.json().get("status") or "") for response in responses)
    require(outcomes == ["created", "deduplicated"], "concurrent formal replay must create once")


def assert_second_receipt_link_failure_rolls_back(
    *,
    dsn: str,
    path: str,
    headers: dict[str, str],
    session_id: str,
    vault_id: str,
    candidate_ids: tuple[str, str],
) -> None:
    """The root, receipts and Candidate decisions must commit as one transaction."""

    payload = {
        "commandId": "formal-confirmation-postgres-smoke-rollback",
        "selections": [
            {"candidateId": candidate_id, "expectedCandidateVersion": 1}
            for candidate_id in candidate_ids
        ],
    }
    install_second_receipt_link_rejection(dsn)
    try:
        with TestClient(main_module.app, raise_server_exceptions=False) as failure_client:
            response = failure_client.post(
                path,
                headers=formal_headers(
                    headers,
                    session_id=session_id,
                    decision_id="formal-confirmation-postgres-smoke-rollback",
                ),
                json=payload,
            )
        require(response.status_code == 500, "injected second receipt-link failure must reach the error boundary")
    finally:
        remove_second_receipt_link_rejection(dsn)
    require(counts(dsn, vault_id=vault_id) == (0, 0, 0), "failed batch must roll back root, receipts and links")
    decisions = candidate_decisions(dsn, vault_id=vault_id)
    require(
        {candidate_id: decisions.get(candidate_id) for candidate_id in candidate_ids}
        == {candidate_id: "pending" for candidate_id in candidate_ids},
        "failed batch must leave every Candidate pending",
    )


def assert_legacy_qa_root_survives_upgrade(
    dsn: str,
    *,
    vault_id: str,
    command_id_hash: str,
) -> None:
    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT authorization_evidence
                FROM owner_truth.interview_review_batch_candidate_decisions
                WHERE vault_id = %s AND command_id_hash = %s
                """,
                (vault_id, command_id_hash),
            )
            root = cursor.fetchone()
            cursor.execute(
                """
                SELECT COUNT(*) AS count
                FROM owner_truth.interview_review_batch_candidate_decision_receipts
                WHERE vault_id = %s
                """,
                (vault_id,),
            )
            link_count = int(cursor.fetchone()["count"])
    require(root is not None, "legacy QA root must survive the 0036 upgrade")
    evidence = root["authorization_evidence"]
    if isinstance(evidence, str):
        evidence = json.loads(evidence)
    require(evidence == {}, "legacy QA root must remain explicitly uncaptured")
    require(link_count == 0, "0036 must not fabricate receipt links for a legacy QA root")


def assert_non_confirmation_feature_evidence_is_rejected(
    dsn: str,
    *,
    vault_id: str,
    owner_subject_id: str,
    review_batch_id: str,
) -> None:
    """The DB boundary must reject a well-formed capture from another feature."""

    evidence = {
        "schemaVersion": "owner-truth-command-authorization-capture-v1",
        "feature": "echoTextInput",
        "policyVersion": "release-policy-v1",
        "policyRevision": 1,
        "emergencyRevision": 0,
        "accountGenerationHash": "a" * 24,
        "decisionIdHash": "b" * 64,
        "audience": "owner",
        "cohort": "closedPilotAdultSelf",
        "clientBuild": 1,
        "expiresAt": "2026-07-22T00:00:00+00:00",
    }
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            try:
                cursor.execute(
                    """
                    INSERT INTO owner_truth.interview_review_batch_candidate_decisions (
                        id, vault_id, owner_subject_id, review_batch_id,
                        command_id_hash, payload_hash, selection_count,
                        actor_subject_id, policy_version, authority_epoch,
                        authorization_evidence
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(uuid.uuid4()),
                        vault_id,
                        owner_subject_id,
                        review_batch_id,
                        canonical_hash({"command": "wrong-formal-feature"}),
                        canonical_hash({"selection": "wrong-formal-feature"}),
                        1,
                        owner_subject_id,
                        OWNER_TRUTH_SCHEMA_VERSION,
                        0,
                        Jsonb(evidence),
                    ),
                )
            except psycopg.Error:
                connection.rollback()
            else:
                raise AssertionError(
                    "non-confirmation authorization feature unexpectedly passed DB constraint"
                )


def assert_persisted_authority_evidence(
    dsn: str,
    *,
    vault_id: str,
    session_id: str,
    decision_id: str,
    candidate_id: str,
) -> None:
    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT root.policy_version, root.authorization_evidence,
                    receipt.policy_version AS receipt_policy_version,
                    receipt.command_id_hash AS receipt_command_id_hash,
                    link.candidate_id AS linked_candidate_id,
                    link.candidate_command_id_hash AS linked_command_id_hash,
                    extraction.source_version AS extracted_source_version,
                    admission.source_version AS admitted_source_version
                FROM owner_truth.interview_review_batch_candidate_decisions AS root
                JOIN owner_truth.interview_review_batch_candidate_decision_receipts AS link
                  ON link.vault_id = root.vault_id AND link.batch_decision_id = root.id
                JOIN owner_truth.decision_receipts AS receipt
                  ON receipt.vault_id = link.vault_id AND receipt.id = link.decision_receipt_id
                JOIN owner_truth.memory_candidates AS candidate
                  ON candidate.vault_id = receipt.vault_id AND candidate.id = receipt.candidate_id
                JOIN owner_truth.extraction_results AS extraction
                  ON extraction.vault_id = candidate.vault_id
                 AND extraction.id = candidate.extraction_result_id
                JOIN owner_truth.interview_review_batch_candidate_admissions AS admission
                  ON admission.vault_id = root.vault_id
                 AND admission.review_batch_id = root.review_batch_id
                WHERE root.vault_id = %s
                """,
                (vault_id,),
            )
            row = cursor.fetchone()
    require(row is not None, "formal command must persist one root/receipt link")
    evidence = row["authorization_evidence"]
    if isinstance(evidence, str):
        evidence = json.loads(evidence)
    require(isinstance(evidence, dict), "authorization evidence must be a JSON object")
    require(
        evidence.get("policyVersion") == "release-policy-v1",
        "authorization evidence must retain the release policy version",
    )
    require(
        evidence.get("accountGenerationHash") == sha256(session_id.encode("utf-8")).hexdigest()[:24],
        "authorization evidence must retain only the account generation hash",
    )
    require(
        evidence.get("decisionIdHash") == sha256(decision_id.encode("utf-8")).hexdigest(),
        "authorization evidence must retain only the decision ID hash",
    )
    serialized = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
    require(session_id not in serialized, "authorization evidence must not store the raw session ID")
    require(decision_id not in serialized, "authorization evidence must not store the raw decision ID")
    require(
        row["policy_version"] == OWNER_TRUTH_SCHEMA_VERSION
        and row["receipt_policy_version"] == OWNER_TRUTH_SCHEMA_VERSION,
        "release capture must not overwrite Owner Truth entity policy versions",
    )
    require(str(row["linked_candidate_id"]) == candidate_id, "receipt link must target the selected Candidate")
    require(
        row["receipt_command_id_hash"] == row["linked_command_id_hash"],
        "receipt link must retain the selected child command hash",
    )
    require(
        row["extracted_source_version"] == row["admitted_source_version"],
        "receipt link must retain the admitted Source version",
    )


def assert_wrong_child_command_link_is_rejected(dsn: str, *, vault_id: str) -> None:
    """Prove the database trigger rejects a receipt/root association mismatch."""

    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT batch_decision_id, decision_receipt_id, candidate_id,
                       candidate_command_id_hash
                FROM owner_truth.interview_review_batch_candidate_decision_receipts
                WHERE vault_id = %s
                """,
                (vault_id,),
            )
            link = cursor.fetchone()
            require(link is not None, "formal smoke must create a receipt link before tamper check")
            wrong_hash = "0" * 64
            if str(link["candidate_command_id_hash"]) == wrong_hash:
                wrong_hash = "f" * 64
            try:
                cursor.execute(
                    """
                    INSERT INTO owner_truth.interview_review_batch_candidate_decision_receipts (
                        vault_id, batch_decision_id, decision_receipt_id, candidate_id,
                        candidate_command_id_hash
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        vault_id,
                        link["batch_decision_id"],
                        link["decision_receipt_id"],
                        link["candidate_id"],
                        wrong_hash,
                    ),
                )
            except psycopg.Error:
                connection.rollback()
            else:
                raise AssertionError("wrong child command hash unexpectedly passed receipt-link trigger")


def main() -> None:
    require(
        os.environ.get("DREAMJOURNEY_OWNER_TRUTH_FORMAL_SMOKE") == "1",
        "DREAMJOURNEY_OWNER_TRUTH_FORMAL_SMOKE=1 is required",
    )
    base_dsn = os.environ.get("OWNER_TRUTH_FORMAL_SMOKE_ADMIN_DATABASE_URL", "").strip()
    require(base_dsn, "OWNER_TRUTH_FORMAL_SMOKE_ADMIN_DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_formal_confirmation_smoke_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    store: PostgresStore | None = None

    previous_store = main_module.store
    previous_backend_token = main_module.BACKEND_API_TOKEN
    previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
    previous_route_mode = main_module.AUTH_ROUTE_MODE
    previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
    policy_service = main_module.RELEASE_POLICY_SERVICE
    previous_visible = set(policy_service._CLOSED_PILOT_OWNER_VISIBLE)

    try:
        create_database(admin_dsn, database_name)
        legacy_apply = apply_migrations_through(
            test_dsn,
            build_id="formal-interview-confirmation-legacy-0035",
            final_version="0035",
        )
        require(legacy_apply["appliedHead"] == "0035", "legacy fixture must stop at migration 0035")
        legacy_vault_id = "vault-formal-confirmation-legacy-qa-root"
        _, _, legacy_command_id_hash = seed_legacy_qa_root(
            test_dsn,
            vault_id=legacy_vault_id,
            owner_subject_id="formal-confirmation-legacy-owner",
        )
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="formal-interview-confirmation-smoke",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        upgrade = migrator.apply()
        require(
            upgrade["appliedVersions"] == ["0036", "0037"],
            "upgrade must apply authority receipts and the feature constraint",
        )
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")
        require(
            verified["expectedHead"] == "0037",
            "authority receipt feature-constraint migration must be present",
        )
        assert_legacy_qa_root_survives_upgrade(
            test_dsn,
            vault_id=legacy_vault_id,
            command_id_hash=legacy_command_id_hash,
        )

        store = PostgresStore(dsn=test_dsn, pool_min_size=1, pool_max_size=3)
        store.open_pool(wait=True)
        main_module.store = store
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        policy_service._CLOSED_PILOT_OWNER_VISIBLE = previous_visible | {
            "ownerTruthCandidateReview"
        }

        client = TestClient(main_module.app)
        owner_id, owner_headers, session_id = login(client, phone="13900000361")
        vault_id = "vault-formal-confirmation-postgres-smoke"
        review_batch_id, candidate_id = seed_reviewable_batch(
            test_dsn,
            vault_id=vault_id,
            owner_subject_id=owner_id,
        )
        assert_non_confirmation_feature_evidence_is_rejected(
            test_dsn,
            vault_id=vault_id,
            owner_subject_id=owner_id,
            review_batch_id=review_batch_id,
        )
        path = (
            f"/v2/vaults/{vault_id}/interview-review-batches/"
            f"{review_batch_id}/confirmation/batch-accept"
        )
        payload = {
            "commandId": "formal-confirmation-postgres-smoke-accept",
            "selections": [{"candidateId": candidate_id, "expectedCandidateVersion": 1}],
        }

        qa_only = client.post(
            path,
            headers={**owner_headers, "X-DreamJourney-QA-Owner-Truth": "1"},
            json=payload,
        )
        require(qa_only.status_code == 403, "QA header must not bypass formal policy capture")
        require(route_code(qa_only) == "release_policy_denied", "QA bypass denial must stay typed")
        require(counts(test_dsn, vault_id=vault_id) == (0, 0, 0), "QA bypass must write nothing")

        decision_id = "formal-confirmation-postgres-smoke-decision"
        accepted = client.post(
            path,
            headers=formal_headers(owner_headers, session_id=session_id, decision_id=decision_id),
            json=payload,
        )
        require(accepted.status_code == 201, f"formal confirmation failed: {accepted.text}")
        require(accepted.headers.get("cache-control") == "no-store", "formal result must remain no-store")
        require(accepted.json().get("status") == "created", "formal confirmation must create once")
        require(counts(test_dsn, vault_id=vault_id) == (1, 1, 1), "formal command must atomically create root, receipt and link")
        assert_persisted_authority_evidence(
            test_dsn,
            vault_id=vault_id,
            session_id=session_id,
            decision_id=decision_id,
            candidate_id=candidate_id,
        )
        assert_wrong_child_command_link_is_rejected(test_dsn, vault_id=vault_id)

        replay = client.post(
            path,
            headers=formal_headers(
                owner_headers,
                session_id=session_id,
                decision_id="formal-confirmation-postgres-smoke-replay",
            ),
            json=payload,
        )
        require(replay.status_code == 200, f"formal confirmation replay failed: {replay.text}")
        require(replay.json().get("status") == "deduplicated", "formal replay must deduplicate")
        require(counts(test_dsn, vault_id=vault_id) == (1, 1, 1), "replay must not duplicate root, receipt or link")

        concurrent_vault_id = "vault-formal-confirmation-concurrent-smoke"
        concurrent_review_batch_id, concurrent_candidate_id = seed_reviewable_batch(
            test_dsn,
            vault_id=concurrent_vault_id,
            owner_subject_id=owner_id,
        )
        concurrent_path = (
            f"/v2/vaults/{concurrent_vault_id}/interview-review-batches/"
            f"{concurrent_review_batch_id}/confirmation/batch-accept"
        )
        concurrent_payload = {
            "commandId": "formal-confirmation-postgres-smoke-concurrent",
            "selections": [
                {"candidateId": concurrent_candidate_id, "expectedCandidateVersion": 1}
            ],
        }
        assert_concurrent_formal_replay_is_idempotent(
            path=concurrent_path,
            headers=owner_headers,
            session_id=session_id,
            payload=concurrent_payload,
        )
        require(
            counts(test_dsn, vault_id=concurrent_vault_id) == (1, 1, 1),
            "concurrent formal command must produce one root, receipt and link",
        )

        stale_generation = client.post(
            path,
            headers={
                **formal_headers(
                    owner_headers,
                    session_id=session_id,
                    decision_id="formal-confirmation-postgres-smoke-denied",
                ),
                "X-DreamJourney-Account-Generation": "invalid-generation",
            },
            json={**payload, "commandId": "formal-confirmation-postgres-smoke-denied"},
        )
        require(stale_generation.status_code == 403, "invalid account generation must be denied")
        require(route_code(stale_generation) == "release_policy_denied", "account denial must stay typed")
        require(counts(test_dsn, vault_id=vault_id) == (1, 1, 1), "denied capture must write nothing")

        rollback_vault_id = "vault-formal-confirmation-rollback-smoke"
        rollback_review_batch_id, rollback_candidate_ids = seed_two_reviewable_candidates(
            test_dsn,
            vault_id=rollback_vault_id,
            owner_subject_id=owner_id,
        )
        rollback_path = (
            f"/v2/vaults/{rollback_vault_id}/interview-review-batches/"
            f"{rollback_review_batch_id}/confirmation/batch-accept"
        )
        assert_second_receipt_link_failure_rolls_back(
            dsn=test_dsn,
            path=rollback_path,
            headers=owner_headers,
            session_id=session_id,
            vault_id=rollback_vault_id,
            candidate_ids=rollback_candidate_ids,
        )

        print(
            "formal interview confirmation postgres smoke passed "
            "migration0037=true legacyQaUpgradeCompatible=true "
            "wrongFeatureAuthorityRejected=true qaBypassDenied=true "
            "authorityCapturePersisted=true "
            "receiptLinkPersisted=true receiptLinkTamperDenied=true "
            "replayDeduplicated=true concurrentCommandDeduplicated=true "
            "batchLinkFailureRolledBack=true accountCaptureDenied=true"
        )
    finally:
        main_module.store = previous_store
        main_module.BACKEND_API_TOKEN = previous_backend_token
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = previous_legacy_phone_login
        main_module.AUTH_ROUTE_MODE = previous_route_mode
        main_module.AUTH_OWNERSHIP_MODE = previous_ownership_mode
        policy_service._CLOSED_PILOT_OWNER_VISIBLE = previous_visible
        if store is not None:
            store.close_pool()
        try:
            drop_database(admin_dsn, database_name)
        except Exception as exc:  # pragma: no cover - cleanup diagnostics only
            print(
                f"warning: failed to drop temporary database {database_name}: {exc}",
                file=sys.stderr,
            )


if __name__ == "__main__":
    main()
