#!/usr/bin/env python3
"""Verify command-scoped Candidate decision lookup in disposable Postgres.

Only synthetic identities and Candidates are used. The script creates a
random database, applies the current migrations, exercises the real FastAPI
route and Postgres repositories, then drops the database on exit.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import time
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
from app.core.config import settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
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


def content_hash(value: dict[str, Any]) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def login(client: TestClient, *, phone: str) -> tuple[str, dict[str, str]]:
    response = client.post(
        "/auth/login",
        json={
            "phone": phone,
            "nickname": "Decision result PG smoke",
            "password": "decision-result-postgres-smoke",
        },
    )
    require(response.status_code == 200, "synthetic login must succeed")
    body = response.json()
    return str(body["user"]["id"]), {
        "Authorization": f"Bearer {body['auth']['accessToken']}",
        "X-DreamJourney-QA-Owner-Truth": "1",
    }


def seed_candidate(
    dsn: str,
    *,
    vault_id: str,
    owner_subject_id: str,
) -> str:
    source_id = str(uuid.uuid4())
    candidate_id = str(uuid.uuid4())
    proposal = {"summary": "synthetic decision result transaction evidence"}
    payload = {
        "content": proposal,
        "contentSchemaVersion": "owner-truth-v1",
        "evidenceRefs": [
            {
                "sourceId": source_id,
                "sourceVersion": 1,
                "span": {"start": 0, "end": 12},
            }
        ],
        "reviewMode": "single",
        "schemaVersion": "owner-truth-candidate-proposal-v1",
    }
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO owner_truth.vaults (vault_id, owner_subject_id)
                VALUES (%s, %s)
                ON CONFLICT (vault_id) DO NOTHING
                """,
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
                    content_hash({"source": source_id}),
                    "owner-truth-v1",
                    0,
                ),
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.memory_candidates (
                    id, vault_id, owner_subject_id, source_id, candidate_kind,
                    perspective_type, epistemic_status, policy_version,
                    authority_epoch, content_hash, payload_schema_version, payload
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    candidate_id,
                    vault_id,
                    owner_subject_id,
                    source_id,
                    "experience",
                    "firstPerson",
                    "recalled",
                    "owner-truth-v1",
                    0,
                    content_hash(proposal),
                    "owner-truth-v1",
                    Jsonb(payload),
                ),
            )
        connection.commit()
    return candidate_id


def decision_headers(headers: dict[str, str], command_id: str) -> dict[str, str]:
    return {**headers, "X-DreamJourney-Review-Command-Id": command_id}


def decision_payload(command_id: str, *, action: str = "accept") -> dict[str, Any]:
    return {
        "commandId": command_id,
        "expectedCandidateVersion": 1,
        "action": action,
        "reasonCode": "isolatedPostgresVerification",
    }


def read_result(
    client: TestClient,
    *,
    vault_id: str,
    candidate_id: str,
    headers: dict[str, str],
    command_id: str,
):
    return client.get(
        f"/v2/vaults/{vault_id}/candidates/{candidate_id}/decision-result",
        headers=decision_headers(headers, command_id),
    )


def install_transaction_fault_probe(dsn: str) -> None:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE public.b4_decision_result_faults (
                    command_id_hash TEXT PRIMARY KEY,
                    action TEXT NOT NULL CHECK (action IN ('sleep', 'fail'))
                )
                """
            )
            cursor.execute(
                """
                CREATE FUNCTION public.b4_decision_result_fault_probe()
                RETURNS TRIGGER AS $$
                DECLARE requested_action TEXT;
                BEGIN
                    SELECT action INTO requested_action
                    FROM public.b4_decision_result_faults
                    WHERE command_id_hash = NEW.command_id_hash;
                    IF requested_action = 'sleep' THEN
                        PERFORM pg_sleep(2.0);
                    ELSIF requested_action = 'fail' THEN
                        RAISE EXCEPTION 'synthetic decision transaction rollback';
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql
                """
            )
            cursor.execute(
                """
                CREATE TRIGGER b4_decision_result_fault_probe
                AFTER INSERT ON owner_truth.decision_receipts
                FOR EACH ROW EXECUTE FUNCTION public.b4_decision_result_fault_probe()
                """
            )
        connection.commit()


def set_transaction_fault(dsn: str, *, command_id: str, action: str) -> None:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO public.b4_decision_result_faults (command_id_hash, action)
                VALUES (%s, %s)
                ON CONFLICT (command_id_hash) DO UPDATE SET action = EXCLUDED.action
                """,
                (sha256(command_id.encode("utf-8")).hexdigest(), action),
            )
        connection.commit()


def wait_for_sleeping_review(dsn: str, *, database_name: str) -> None:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM pg_stat_activity
                    WHERE datname = %s AND wait_event = 'PgSleep'
                    """,
                    (database_name,),
                )
                if int(cursor.fetchone()[0]) > 0:
                    return
        time.sleep(0.05)
    raise AssertionError("review transaction did not reach the synthetic uncommitted window")


def relevant_row_counts(dsn: str) -> tuple[int, ...]:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM owner_truth.memory_candidates),
                    (SELECT COUNT(*) FROM owner_truth.decision_receipts),
                    (SELECT COUNT(*) FROM owner_truth.candidate_decision_values),
                    (SELECT COUNT(*) FROM owner_truth.memories),
                    (SELECT COUNT(*) FROM owner_truth.memory_versions),
                    (SELECT COUNT(*) FROM owner_truth.memory_changesets),
                    (SELECT COUNT(*) FROM owner_truth.memory_changeset_operations),
                    (SELECT COUNT(*) FROM owner_truth.memory_revisions),
                    (SELECT COUNT(*) FROM async_effects.operations),
                    (SELECT COUNT(*) FROM async_effects.outbox_events),
                    (SELECT COUNT(*) FROM async_effects.jobs),
                    (SELECT COUNT(*) FROM async_effects.business_receipts)
                """
            )
            return tuple(int(value) for value in cursor.fetchone())


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_b4_decision_result_smoke_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    store: PostgresStore | None = None

    previous_store = main_module.store
    previous_backend_token = main_module.BACKEND_API_TOKEN
    previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
    previous_route_mode = main_module.AUTH_ROUTE_MODE
    previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
    previous_qa_enabled = main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED

    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="b4-candidate-decision-result-postgres-smoke",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")
        install_transaction_fault_probe(test_dsn)

        store = PostgresStore(dsn=test_dsn, pool_min_size=1, pool_max_size=8)
        store.open_pool(wait=True)
        main_module.store = store
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = True

        client = TestClient(main_module.app, raise_server_exceptions=False)
        owner_id, owner_headers = login(client, phone="13900000931")
        other_owner_id, other_headers = login(client, phone="13900000932")
        require(owner_id != other_owner_id, "synthetic owners must be distinct")
        vault_id = "vault-b4-decision-result-smoke"

        # T19: owner, vault, Candidate and command must all match.
        scope_candidate = seed_candidate(
            test_dsn, vault_id=vault_id, owner_subject_id=owner_id
        )
        scope_command = "b4-pg-scope-001"
        created = client.post(
            f"/v2/vaults/{vault_id}/candidates/{scope_candidate}/decisions",
            headers=owner_headers,
            json=decision_payload(scope_command, action="reject"),
        )
        require(created.status_code == 201, "scope fixture decision must commit")
        wrong_command = read_result(
            client,
            vault_id=vault_id,
            candidate_id=scope_candidate,
            headers=owner_headers,
            command_id="b4-pg-scope-other",
        )
        require(wrong_command.status_code == 200, "unknown command lookup must be readable")
        require(wrong_command.json().get("result") == "notObserved", "unknown command must not leak state")
        wrong_owner = read_result(
            client,
            vault_id=vault_id,
            candidate_id=scope_candidate,
            headers=other_headers,
            command_id=scope_command,
        )
        require(wrong_owner.status_code == 403, "cross-owner lookup must fail closed")
        other_candidate = seed_candidate(
            test_dsn, vault_id=vault_id, owner_subject_id=owner_id
        )
        wrong_candidate = read_result(
            client,
            vault_id=vault_id,
            candidate_id=other_candidate,
            headers=owner_headers,
            command_id=scope_command,
        )
        require(wrong_candidate.status_code == 200, "same-owner candidate lookup must remain scoped")
        require(wrong_candidate.json().get("result") == "notObserved", "other Candidate must not inherit a receipt")

        # T20: a real POST is invisible before commit, found after commit, and
        # a transaction fault rolls back Candidate, receipt, version and effect.
        delayed_candidate = seed_candidate(
            test_dsn, vault_id=vault_id, owner_subject_id=owner_id
        )
        delayed_command = "b4-pg-uncommitted-001"
        set_transaction_fault(test_dsn, command_id=delayed_command, action="sleep")
        with ThreadPoolExecutor(max_workers=2) as executor:
            future = executor.submit(
                client.post,
                f"/v2/vaults/{vault_id}/candidates/{delayed_candidate}/decisions",
                headers=owner_headers,
                json=decision_payload(delayed_command),
            )
            wait_for_sleeping_review(test_dsn, database_name=database_name)
            uncommitted = read_result(
                client,
                vault_id=vault_id,
                candidate_id=delayed_candidate,
                headers=owner_headers,
                command_id=delayed_command,
            )
            require(uncommitted.status_code == 200, "uncommitted lookup must complete")
            require(uncommitted.json().get("result") == "notObserved", "uncommitted receipt must be invisible")
            delayed_created = future.result(timeout=10)
        require(delayed_created.status_code == 201, "delayed decision must commit")
        committed = read_result(
            client,
            vault_id=vault_id,
            candidate_id=delayed_candidate,
            headers=owner_headers,
            command_id=delayed_command,
        )
        require(committed.status_code == 200, "committed lookup must succeed")
        require(committed.json().get("result") == "found", "committed receipt must be found")

        rollback_candidate = seed_candidate(
            test_dsn, vault_id=vault_id, owner_subject_id=owner_id
        )
        rollback_command = "b4-pg-rollback-001"
        before_rollback = relevant_row_counts(test_dsn)
        set_transaction_fault(test_dsn, command_id=rollback_command, action="fail")
        rolled_back = client.post(
            f"/v2/vaults/{vault_id}/candidates/{rollback_candidate}/decisions",
            headers=owner_headers,
            json=decision_payload(rollback_command),
        )
        require(rolled_back.status_code == 500, "synthetic transaction fault must fail")
        require(relevant_row_counts(test_dsn) == before_rollback, "failed POST must roll back every business row")
        rollback_lookup = read_result(
            client,
            vault_id=vault_id,
            candidate_id=rollback_candidate,
            headers=owner_headers,
            command_id=rollback_command,
        )
        require(rollback_lookup.status_code == 200, "rolled-back lookup must remain readable")
        require(rollback_lookup.json().get("result") == "notObserved", "rolled-back command must not be observed")

        # T21: concurrent same-command requests are idempotent; a payload
        # change conflicts, and rejection never creates a MemoryVersion.
        concurrent_candidate = seed_candidate(
            test_dsn, vault_id=vault_id, owner_subject_id=owner_id
        )
        concurrent_command = "b4-pg-concurrent-001"
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    client.post,
                    f"/v2/vaults/{vault_id}/candidates/{concurrent_candidate}/decisions",
                    headers=owner_headers,
                    json=decision_payload(concurrent_command),
                )
                for _ in range(2)
            ]
            responses = [future.result(timeout=10) for future in futures]
        require(sorted(response.status_code for response in responses) == [200, 201], "concurrent replay must create once and deduplicate once")
        receipt_ids = {
            str((response.json().get("receipt") or {}).get("receiptId"))
            for response in responses
        }
        require(len(receipt_ids) == 1, "concurrent replay must share one immutable receipt")
        changed_payload = client.post(
            f"/v2/vaults/{vault_id}/candidates/{concurrent_candidate}/decisions",
            headers=owner_headers,
            json=decision_payload(concurrent_command, action="reject"),
        )
        require(changed_payload.status_code == 409, "same command with changed payload must conflict")
        with psycopg.connect(test_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) FROM owner_truth.decision_receipts WHERE vault_id = %s AND candidate_id = %s",
                    (vault_id, concurrent_candidate),
                )
                require(int(cursor.fetchone()[0]) == 1, "concurrent command must persist one receipt")
                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM owner_truth.memory_versions AS version
                    JOIN owner_truth.memories AS memory
                      ON memory.vault_id = version.vault_id AND memory.id = version.memory_id
                    WHERE memory.vault_id = %s AND memory.source_id = (
                        SELECT source_id FROM owner_truth.memory_candidates
                        WHERE vault_id = %s AND id = %s
                    )
                    """,
                    (vault_id, vault_id, scope_candidate),
                )
                require(int(cursor.fetchone()[0]) == 0, "rejected Candidate must not create a MemoryVersion")

        # T22: repeated GETs are read-only, and a later formal-memory commit
        # does not rewrite the earlier immutable receipt/activation identity.
        historical_before = committed.json()
        counts_before_get = relevant_row_counts(test_dsn)
        for _ in range(3):
            repeated = read_result(
                client,
                vault_id=vault_id,
                candidate_id=delayed_candidate,
                headers=owner_headers,
                command_id=delayed_command,
            )
            require(repeated.status_code == 200 and repeated.json().get("result") == "found", "repeated GET must remain stable")
        require(relevant_row_counts(test_dsn) == counts_before_get, "decision-result GET must not write business or effect rows")

        later_candidate = seed_candidate(
            test_dsn, vault_id=vault_id, owner_subject_id=owner_id
        )
        later_created = client.post(
            f"/v2/vaults/{vault_id}/candidates/{later_candidate}/decisions",
            headers=owner_headers,
            json=decision_payload("b4-pg-later-memory-001"),
        )
        require(later_created.status_code == 201, "later memory fixture must commit")
        historical_after = read_result(
            client,
            vault_id=vault_id,
            candidate_id=delayed_candidate,
            headers=owner_headers,
            command_id=delayed_command,
        ).json()
        for key in (
            "expectedCandidateVersion",
            "candidateBeforeHash",
            "expectedChangeSetId",
            "expectedProposalHash",
        ):
            require(historical_after.get(key) == historical_before.get(key), f"historical binding changed: {key}")
        require(
            historical_after["decisionResult"]["receipt"]
            == historical_before["decisionResult"]["receipt"],
            "later memory must not rewrite the historical receipt",
        )
        before_activation = historical_before["decisionResult"]["memoryActivation"]
        after_activation = historical_after["decisionResult"]["memoryActivation"]
        for key in ("memoryId", "memoryVersionId", "memoryVersion", "contentHash"):
            require(after_activation.get(key) == before_activation.get(key), f"historical activation changed: {key}")

        print(
            "owner truth candidate decision result postgres smoke passed "
            f"schemaHead={verified['expectedHead']} scope=true uncommittedInvisible=true "
            "rollbackAtomic=true concurrentIdempotent=true readOnly=true historicalStable=true"
        )
    finally:
        main_module.store = previous_store
        main_module.BACKEND_API_TOKEN = previous_backend_token
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = previous_legacy_phone_login
        main_module.AUTH_ROUTE_MODE = previous_route_mode
        main_module.AUTH_OWNERSHIP_MODE = previous_ownership_mode
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = previous_qa_enabled
        if store is not None:
            store.close_pool()
        try:
            drop_database(admin_dsn, database_name)
        except Exception as exc:
            print(f"warning: failed to drop temporary database {database_name}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
