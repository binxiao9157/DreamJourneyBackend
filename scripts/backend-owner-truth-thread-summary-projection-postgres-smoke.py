#!/usr/bin/env python3
"""Exercise the private Thread-summary checkpoint in disposable Postgres.

The smoke creates and destroys its own database. It does not contact the
deployed database, turn on a public feature, send a provider request, or store
Owner narrative in a response. It proves checkpoint persistence, source
currentness, and Owner isolation for the default-off Phase 4A QA lane.
"""

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
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb

import app.main as main_module
from app.core.config import settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.domain.owner_truth.conversation import StartInterviewSessionCommand
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_conversation import OwnerTruthConversationService
from app.services.owner_truth_memory_projection import OwnerTruthMemoryProjectionService
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


def route_code(response: Any) -> str:
    detail = response.json().get("detail") if response.content else None
    return str(detail.get("code") or "") if isinstance(detail, dict) else ""


def login(client: TestClient, *, phone: str) -> tuple[str, dict[str, str]]:
    response = client.post(
        "/auth/login",
        json={"phone": phone, "nickname": "thread summary checkpoint smoke", "password": "smoke123"},
    )
    require(response.status_code == 200, f"temporary owner login failed: {response.text}")
    body = response.json()
    return str(body["user"]["id"]), {
        "Authorization": f"Bearer {body['auth']['accessToken']}",
        "X-DreamJourney-QA-Owner-Truth": "1",
    }


def seed_current_knowledge_memory(
    dsn: str,
    *,
    vault_id: str,
    owner_subject_id: str,
) -> tuple[str, str]:
    source_id = str(uuid.uuid4())
    memory_id = str(uuid.uuid4())
    memory_version_id = str(uuid.uuid4())
    content = {"claim": "private checkpoint smoke content must not leak"}
    content_hash = canonical_hash(content)
    payload = {
        "content": content,
        "contentSchemaVersion": "owner-truth-v1",
        "evidenceRefs": [{"sourceId": source_id, "sourceVersion": 1}],
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
                    canonical_hash({"source": "thread-summary-projection-smoke"}),
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
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    memory_id,
                    vault_id,
                    owner_subject_id,
                    source_id,
                    1,
                    "knowledge",
                    "firstPerson",
                    "recalled",
                    "standard",
                    "active",
                    "owner-truth-v1",
                    content_hash,
                    0,
                ),
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.memory_versions (
                    id, vault_id, memory_id, version_number, is_current,
                    schema_version, content_hash, payload, source_id, source_version
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    memory_version_id,
                    vault_id,
                    memory_id,
                    1,
                    True,
                    "owner-truth-v1",
                    content_hash,
                    Jsonb(payload),
                    source_id,
                    1,
                ),
            )
        connection.commit()
    return memory_version_id, content_hash


def start_session(
    store: PostgresStore,
    *,
    context: OwnerTruthCommandContext,
) -> tuple[str, str]:
    thread_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    with store.request_unit_of_work(
        correlation_id="thread-summary-projection-smoke-start",
        command_id="threadSummaryProjectionSmokeStart",
    ):
        result = OwnerTruthConversationService(
            store.owner_truth_conversation_repository()
        ).start_session(
            command=StartInterviewSessionCommand(
                command_id="thread-summary-projection-smoke-start",
                thread_id=thread_id,
                session_id=session_id,
                expected_thread_version=0,
                entry_mode="recommendation",
            ),
            context=context,
        )
    require(result.state.value == "active", "smoke must create an active session")
    return thread_id, session_id


def checkpoint_counts(dsn: str, *, vault_id: str) -> tuple[int, int, int]:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    (SELECT count(*) FROM owner_truth.thread_summary_projection_checkpoints
                        WHERE vault_id = %s),
                    (SELECT count(*) FROM owner_truth.thread_summary_projection_threads
                        WHERE vault_id = %s),
                    (SELECT count(*) FROM owner_truth.thread_summary_projection_anchors
                        WHERE vault_id = %s)
                """,
                (vault_id, vault_id, vault_id),
            )
            row = cursor.fetchone()
    require(row is not None, "thread summary checkpoint counts are unavailable")
    return tuple(int(value) for value in row)


def invalidate_saved_cue_session(dsn: str, *, vault_id: str, session_id: str) -> None:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE owner_truth.interview_sessions
                SET row_version = row_version + 1
                WHERE vault_id = %s AND id = %s
                """,
                (vault_id, session_id),
            )
            require(cursor.rowcount == 1, "session invalidation must target exactly one row")
        connection.commit()


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_thread_summary_projection_smoke_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    store: PostgresStore | None = None

    previous_store = main_module.store
    previous_backend_token = main_module.BACKEND_API_TOKEN
    previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
    previous_route_mode = main_module.AUTH_ROUTE_MODE
    previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
    previous_candidate_qa = main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED
    previous_confirmation_qa = main_module.OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_QA_ENABLED
    previous_recommendation_read_qa = main_module.OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_READ_QA_ENABLED
    previous_recommendation_plan_qa = main_module.OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_PLAN_QA_ENABLED
    previous_saved_cue_qa = main_module.OWNER_TRUTH_SAVED_CONTINUATION_CUE_QA_ENABLED
    previous_thread_preference_qa = main_module.OWNER_TRUTH_THREAD_PREFERENCE_QA_ENABLED
    previous_thread_summary_qa = main_module.OWNER_TRUTH_THREAD_SUMMARY_READ_QA_ENABLED
    previous_thread_summary_projection_qa = (
        main_module.OWNER_TRUTH_THREAD_SUMMARY_PROJECTION_QA_ENABLED
    )

    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="owner-truth-thread-summary-projection-g0",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")

        store = PostgresStore(dsn=test_dsn, pool_min_size=1, pool_max_size=3)
        store.open_pool(wait=True)
        main_module.store = store
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = True
        main_module.OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_QA_ENABLED = True
        main_module.OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_READ_QA_ENABLED = True
        main_module.OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_PLAN_QA_ENABLED = True
        main_module.OWNER_TRUTH_SAVED_CONTINUATION_CUE_QA_ENABLED = True
        main_module.OWNER_TRUTH_THREAD_PREFERENCE_QA_ENABLED = True
        main_module.OWNER_TRUTH_THREAD_SUMMARY_READ_QA_ENABLED = True
        main_module.OWNER_TRUTH_THREAD_SUMMARY_PROJECTION_QA_ENABLED = False

        client = TestClient(main_module.app)
        owner_id, owner_headers = login(client, phone="13900000691")
        _other_owner_id, other_headers = login(client, phone="13900000692")
        vault_id = "vault-thread-summary-projection-smoke"
        memory_version_id, content_hash = seed_current_knowledge_memory(
            test_dsn,
            vault_id=vault_id,
            owner_subject_id=owner_id,
        )
        context = OwnerTruthCommandContext(
            vault_id=vault_id,
            owner_subject_id=owner_id,
            actor_subject_id=owner_id,
        )
        OwnerTruthMemoryProjectionService(store).rebuild(context=context)

        confirmation = client.post(
            f"/v2/vaults/{vault_id}/memory-versions/{memory_version_id}"
            "/knowledge-dimension-confirmations",
            headers=owner_headers,
            json={
                "commandId": "thread-summary-projection-confirm",
                "expectedContentHash": content_hash,
                "dimension": "keyDecisions",
                "coveredFacets": ["choice", "reason"],
            },
        )
        require(confirmation.status_code == 201, f"confirmation failed: {confirmation.text}")

        rebuild_path = f"/v2/vaults/{vault_id}/thread-summary-projections/rebuild"
        read_path = f"/v2/vaults/{vault_id}/thread-summary-projections/read"
        hidden = client.post(rebuild_path, headers=owner_headers, json={})
        require(
            hidden.status_code == 404
            and route_code(hidden) == "ownerTruthThreadSummaryProjectionUnavailable",
            "thread-summary checkpoint route must remain independently default-off",
        )
        main_module.OWNER_TRUTH_THREAD_SUMMARY_PROJECTION_QA_ENABLED = True

        thread_id, session_id = start_session(store, context=context)
        deferred = client.post(
            f"/v2/vaults/{vault_id}/interview-sessions/{session_id}/defer-with-continuation",
            headers=owner_headers,
            json={
                "commandId": "thread-summary-projection-defer",
                "threadId": thread_id,
                "expectedSessionVersion": 1,
                "memoryVersionId": memory_version_id,
                "targetDimension": "keyDecisions",
                "missingFacet": "outcome",
            },
        )
        require(deferred.status_code == 201, f"saved cue creation failed: {deferred.text}")

        before = client.post(read_path, headers=owner_headers, json={})
        require(
            before.status_code == 200
            and before.json()["threadSummaryProjection"]["state"] == "rebuilding",
            "read must fail closed until a checkpoint is explicitly rebuilt",
        )
        rebuilt = client.post(rebuild_path, headers=owner_headers, json={})
        require(rebuilt.status_code == 200, f"checkpoint rebuild failed: {rebuilt.text}")
        rebuild_summary = rebuilt.json()["threadSummaryProjection"]
        require(
            rebuild_summary["status"] == "rebuilt"
            and rebuild_summary["threadCount"] == 1
            and rebuild_summary["associationCount"] == 0,
            "rebuild must persist the current value-free Thread anchor",
        )
        require(
            "private checkpoint smoke content" not in rebuilt.text
            and "continuationText" not in rebuilt.text,
            "checkpoint response must remain value-free",
        )
        require(
            checkpoint_counts(test_dsn, vault_id=vault_id) == (1, 1, 1),
            "one checkpoint must persist one Thread and one current cue anchor",
        )

        replay = client.post(rebuild_path, headers=owner_headers, json={})
        require(
            replay.status_code == 200
            and replay.json()["threadSummaryProjection"]["status"] == "unchanged",
            "same source checkpoint must rebuild idempotently",
        )
        ready = client.post(read_path, headers=owner_headers, json={})
        require(
            ready.status_code == 200
            and ready.json()["threadSummaryProjection"]["state"] == "ready",
            "current persisted checkpoint must be readable",
        )

        invalidate_saved_cue_session(test_dsn, vault_id=vault_id, session_id=session_id)
        stale = client.post(read_path, headers=owner_headers, json={})
        require(
            stale.status_code == 200
            and stale.json()["threadSummaryProjection"]["state"] == "rebuilding",
            "a stale continuation cue must invalidate the old checkpoint",
        )
        repaired = client.post(rebuild_path, headers=owner_headers, json={})
        require(
            repaired.status_code == 200
            and repaired.json()["threadSummaryProjection"]["status"] == "rebuilt",
            "explicit rebuild must repair a source-invalidated checkpoint",
        )

        denied = client.post(read_path, headers=other_headers, json={})
        require(
            denied.status_code == 403
            and route_code(denied) == "ownerTruthThreadSummaryProjectionDenied",
            "cross-owner checkpoint reads must be denied",
        )
        invalid = client.post(read_path, headers=owner_headers, json={"topic": "must not persist"})
        require(
            invalid.status_code == 400
            and route_code(invalid) == "ownerTruthThreadSummaryProjectionInvalid",
            "checkpoint routes must reject free-form payloads",
        )

        print(
            "owner truth thread summary projection postgres smoke passed "
            f"schemaHead={verified['expectedHead']} defaultHidden=true "
            "checkpointBound=true sourceInvalidation=true crossOwnerDenied=true"
        )
    finally:
        main_module.store = previous_store
        main_module.BACKEND_API_TOKEN = previous_backend_token
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = previous_legacy_phone_login
        main_module.AUTH_ROUTE_MODE = previous_route_mode
        main_module.AUTH_OWNERSHIP_MODE = previous_ownership_mode
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = previous_candidate_qa
        main_module.OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_QA_ENABLED = previous_confirmation_qa
        main_module.OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_READ_QA_ENABLED = previous_recommendation_read_qa
        main_module.OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_PLAN_QA_ENABLED = previous_recommendation_plan_qa
        main_module.OWNER_TRUTH_SAVED_CONTINUATION_CUE_QA_ENABLED = previous_saved_cue_qa
        main_module.OWNER_TRUTH_THREAD_PREFERENCE_QA_ENABLED = previous_thread_preference_qa
        main_module.OWNER_TRUTH_THREAD_SUMMARY_READ_QA_ENABLED = previous_thread_summary_qa
        main_module.OWNER_TRUTH_THREAD_SUMMARY_PROJECTION_QA_ENABLED = (
            previous_thread_summary_projection_qa
        )
        if store is not None:
            store.close_pool()
        try:
            drop_database(admin_dsn, database_name)
        except Exception as exc:  # pragma: no cover - cleanup diagnostics only
            print(f"warning: failed to drop temporary database {database_name}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
