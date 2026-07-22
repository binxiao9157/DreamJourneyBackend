#!/usr/bin/env python3
"""Exercise M0-B dimension confirmation receipts in a disposable Postgres DB.

The running deployment is not reconfigured.  This process creates a temporary
database, applies the exact migration head, temporarily enables the hidden QA
route only in-process, and removes the database afterwards.  It proves the
current-version/hash/append-only boundary without writing any product data.
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
from app.domain.owner_truth.knowledge_dimension_read import (
    OwnerTruthKnowledgeDimensionReadService,
)
from app.domain.owner_truth.conversation import (
    InterviewBoundary,
    PauseInterviewForTopicSwitchCommand,
    SetInterviewBoundaryCommand,
    StartInterviewSessionCommand,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_conversation import OwnerTruthConversationService
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


def login(client: TestClient, *, phone: str) -> tuple[str, dict[str, str]]:
    response = client.post(
        "/auth/login",
        json={"phone": phone, "nickname": "dimension smoke", "password": "dimension-smoke"},
    )
    require(response.status_code == 200, f"temporary owner login failed: {response.text}")
    body = response.json()
    return str(body["user"]["id"]), {
        "Authorization": f"Bearer {body['auth']['accessToken']}",
        "X-DreamJourney-QA-Owner-Truth": "1",
    }


def route_code(response: Any) -> str:
    detail = response.json().get("detail") if response.content else None
    return str(detail.get("code") or "") if isinstance(detail, dict) else ""


def invoke_conversation(
    store: PostgresStore,
    *,
    command_id: str,
    operation,
) -> Any:
    with store.request_unit_of_work(
        correlation_id=f"dimension-confirmation-conversation-smoke:{command_id}",
        command_id=command_id,
    ):
        return operation(OwnerTruthConversationService(store.owner_truth_conversation_repository()))


def seed_current_knowledge_memory(
    dsn: str,
    *,
    vault_id: str,
    owner_subject_id: str,
) -> tuple[str, str, str]:
    source_id = str(uuid.uuid4())
    memory_id = str(uuid.uuid4())
    memory_version_id = str(uuid.uuid4())
    content = {"claim": "这是一条只用于隔离确认回执测试的知识记忆。"}
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
                    canonical_hash({"source": "dimension-confirmation-smoke"}),
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
    return memory_id, memory_version_id, content_hash


def replace_current_memory_version(
    dsn: str,
    *,
    vault_id: str,
    memory_id: str,
    old_memory_version_id: str,
) -> str:
    new_version_id = str(uuid.uuid4())
    source_id: str
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT source_id, source_version
                FROM owner_truth.memory_versions
                WHERE vault_id = %s AND id = %s
                """,
                (vault_id, old_memory_version_id),
            )
            source = cursor.fetchone()
            require(source is not None, "current version source provenance is missing")
            source_id = str(source[0])
            source_version = int(source[1])
            cursor.execute(
                "UPDATE owner_truth.memory_versions SET is_current = FALSE WHERE vault_id = %s AND id = %s",
                (vault_id, old_memory_version_id),
            )
            content = {"claim": "这是一条替代后的知识记忆版本。"}
            payload = {
                "content": content,
                "contentSchemaVersion": "owner-truth-v1",
                "evidenceRefs": [{"sourceId": source_id, "sourceVersion": source_version}],
            }
            cursor.execute(
                """
                INSERT INTO owner_truth.memory_versions (
                    id, vault_id, memory_id, version_number, is_current,
                    schema_version, content_hash, payload, source_id, source_version,
                    supersedes_version_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    new_version_id,
                    vault_id,
                    memory_id,
                    2,
                    True,
                    "owner-truth-v1",
                    canonical_hash(content),
                    Jsonb(payload),
                    source_id,
                    source_version,
                    old_memory_version_id,
                ),
            )
        connection.commit()
    return new_version_id


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_dimension_confirmation_smoke_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    store: PostgresStore | None = None

    previous_store = main_module.store
    previous_backend_token = main_module.BACKEND_API_TOKEN
    previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
    previous_route_mode = main_module.AUTH_ROUTE_MODE
    previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
    previous_candidate_qa = main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED
    previous_confirmation_qa = main_module.OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_QA_ENABLED
    previous_recommendation_qa = main_module.OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_READ_QA_ENABLED

    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="owner-truth-dimension-confirmation-g0",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")
        require(
            int(str(verified["expectedHead"])) >= 35,
            "confirmation migration must be applied",
        )

        store = PostgresStore(dsn=test_dsn, pool_min_size=1, pool_max_size=3)
        store.open_pool(wait=True)
        main_module.store = store
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"

        client = TestClient(main_module.app)
        owner_id, owner_headers = login(client, phone="13900000135")
        vault_id = "vault-dimension-confirmation-smoke"
        memory_id, memory_version_id, content_hash = seed_current_knowledge_memory(
            test_dsn,
            vault_id=vault_id,
            owner_subject_id=owner_id,
        )
        owner_context = OwnerTruthCommandContext(
            vault_id=vault_id,
            owner_subject_id=owner_id,
            actor_subject_id=owner_id,
        )
        OwnerTruthMemoryProjectionService(store).rebuild(context=owner_context)
        path = (
            f"/v2/vaults/{vault_id}/memory-versions/{memory_version_id}"
            "/knowledge-dimension-confirmations"
        )
        payload = {
            "commandId": "dimension-confirmation-postgres-smoke-001",
            "expectedContentHash": content_hash,
            "dimension": "keyDecisions",
            "coveredFacets": ["choice", "reason"],
        }

        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = False
        hidden = client.post(path, headers=owner_headers, json=payload)
        require(hidden.status_code == 404, "dimension confirmation route must default hidden")
        require(
            route_code(hidden) == "ownerTruthKnowledgeDimensionConfirmationUnavailable",
            "hidden route must expose a stable unavailable code",
        )

        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = True
        main_module.OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_QA_ENABLED = False
        separately_hidden = client.post(path, headers=owner_headers, json=payload)
        require(separately_hidden.status_code == 404, "separate confirmation flag must default hidden")

        main_module.OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_QA_ENABLED = True
        missing_header = client.post(
            path,
            headers={"Authorization": owner_headers["Authorization"]},
            json=payload,
        )
        require(missing_header.status_code == 404, "confirmation route must require QA header")

        created = client.post(path, headers=owner_headers, json=payload)
        replay = client.post(path, headers=owner_headers, json=payload)
        require(created.status_code == 201, f"confirmation create failed: {created.text}")
        require(replay.status_code == 200, f"confirmation replay failed: {replay.text}")
        require(created.headers.get("cache-control") == "no-store", "confirmation must be no-store")
        created_body = created.json()
        require(
            created_body.get("status") == "created"
            and replay.json().get("status") == "deduplicated",
            "receipt must be immutable and command-idempotent",
        )
        require(
            (created_body.get("confirmation") or {}).get("memoryVersionId") == memory_version_id,
            "receipt must bind the current MemoryVersion",
        )
        require("只用于隔离" not in created.text and "claim" not in created.text, "response leaked memory text")

        with store.request_unit_of_work(
            correlation_id="dimension-confirmation-read-smoke",
            command_id="dimensionConfirmationReadSmoke",
        ):
            read = OwnerTruthKnowledgeDimensionReadService(
                store.owner_truth_memory_projection_repository(),
                store.owner_truth_knowledge_dimension_confirmation_repository(),
            ).read(context=owner_context)
        require(
            read.coverage is not None
            and read.coverage.for_dimension("keyDecisions").covered_facets == ("choice", "reason"),
            "only the explicit receipt may contribute dimension coverage",
        )

        def start_recommendation_session(label: str) -> tuple[str, str]:
            thread_id = str(uuid.uuid4())
            session_id = str(uuid.uuid4())
            started = invoke_conversation(
                store,
                command_id=f"dimension-confirmation-recommendation-thread-start-{label}",
                operation=lambda service: service.start_session(
                    command=StartInterviewSessionCommand(
                        command_id=f"dimension-confirmation-recommendation-thread-start-{label}",
                        thread_id=thread_id,
                        session_id=session_id,
                        expected_thread_version=0,
                        entry_mode="recommendation",
                    ),
                    context=owner_context,
                ),
            )
            require(
                started.state.value == "active",
                "recommendation smoke must create an active private thread",
            )
            return thread_id, session_id

        recommendation_thread_id, recommendation_session_id = start_recommendation_session("active")

        recommendation_path = f"/v2/vaults/{vault_id}/knowledge-recommendations/read"
        recommendation_payload = {
            "candidates": [
                {
                    "candidateId": "confirmation-smoke-continuity",
                    "slot": "continuity",
                    "threadId": recommendation_thread_id,
                    "targetDimension": "keyDecisions",
                    "missingFacet": "outcome",
                    "questionTemplateId": "confirmation-smoke-template",
                    "evidenceKind": "confirmedMemory",
                    "evidenceRefs": [memory_version_id],
                    "reasonCode": "qaConfirmedMemory",
                }
            ]
        }
        main_module.OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_READ_QA_ENABLED = False
        recommendation_hidden = client.post(
            recommendation_path,
            headers=owner_headers,
            json=recommendation_payload,
        )
        require(
            recommendation_hidden.status_code == 404,
            "knowledge recommendation read route must default hidden",
        )
        require(
            route_code(recommendation_hidden) == "ownerTruthKnowledgeRecommendationReadUnavailable",
            "hidden recommendation route must expose a stable unavailable code",
        )

        main_module.OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_READ_QA_ENABLED = True
        recommendation_read = client.post(
            recommendation_path,
            headers=owner_headers,
            json=recommendation_payload,
        )
        require(
            recommendation_read.status_code == 200,
            f"recommendation read failed: {recommendation_read.text}",
        )
        recommendation_body = recommendation_read.json()
        selected = (recommendation_body.get("recommendations") or {}).get("selected") or []
        require(
            [item.get("slot") for item in selected] == ["continuity"],
            "receipt-bound recommendation read must select only the verified continuity candidate",
        )
        require(
            "只用于隔离" not in recommendation_read.text and "claim" not in recommendation_read.text,
            "recommendation read response leaked memory text",
        )

        paused_recommendation_thread = invoke_conversation(
            store,
            command_id="dimension-confirmation-recommendation-thread-pause",
            operation=lambda service: service.pause_for_topic_switch(
                command=PauseInterviewForTopicSwitchCommand(
                    command_id="dimension-confirmation-recommendation-thread-pause",
                    thread_id=recommendation_thread_id,
                    session_id=recommendation_session_id,
                    expected_thread_version=1,
                    expected_session_version=1,
                ),
                context=owner_context,
            ),
        )
        require(
            paused_recommendation_thread.state.value == "paused",
            "topic switch must pause the recommendation thread",
        )
        paused_recommendation_read = client.post(
            recommendation_path,
            headers=owner_headers,
            json=recommendation_payload,
        )
        require(
            paused_recommendation_read.status_code == 400,
            "a paused private thread must be rejected by recommendation reads",
        )
        require(
            route_code(paused_recommendation_read) == "ownerTruthKnowledgeRecommendationReadInvalid",
            "paused recommendation thread must use the typed invalid code",
        )

        def recommendation_payload_for_thread(thread_id: str, suffix: str) -> dict[str, object]:
            candidate = dict(recommendation_payload["candidates"][0])
            candidate["candidateId"] = f"confirmation-smoke-{suffix}"
            candidate["threadId"] = thread_id
            return {"candidates": [candidate]}

        for suffix, boundary in (
            ("cooldown", InterviewBoundary.COOLDOWN),
            ("do-not-ask", InterviewBoundary.DO_NOT_ASK),
            ("skip-once", InterviewBoundary.SKIP_ONCE),
        ):
            boundary_thread_id, boundary_session_id = start_recommendation_session(suffix)
            boundary_result = invoke_conversation(
                store,
                command_id=f"dimension-confirmation-recommendation-boundary-{suffix}",
                operation=lambda service: service.set_boundary(
                    command=SetInterviewBoundaryCommand(
                        command_id=f"dimension-confirmation-recommendation-boundary-{suffix}",
                        thread_id=boundary_thread_id,
                        session_id=boundary_session_id,
                        expected_session_version=1,
                        boundary=boundary,
                    ),
                    context=owner_context,
                ),
            )
            require(
                boundary_result.boundary is boundary,
                f"{suffix} boundary must persist before recommendation validation",
            )
            boundary_recommendation_read = client.post(
                recommendation_path,
                headers=owner_headers,
                json=recommendation_payload_for_thread(boundary_thread_id, suffix),
            )
            require(
                boundary_recommendation_read.status_code == 400,
                f"{suffix} session must be rejected by recommendation reads",
            )
            require(
                route_code(boundary_recommendation_read)
                == "ownerTruthKnowledgeRecommendationReadInvalid",
                f"{suffix} session must use the typed invalid code",
            )

        stale = client.post(
            path,
            headers=owner_headers,
            json={**payload, "commandId": "dimension-confirmation-stale-001", "expectedContentHash": canonical_hash("old")},
        )
        require(stale.status_code == 409, "stale content hash must be rejected")
        require(
            route_code(stale) == "ownerTruthKnowledgeDimensionConfirmationStaleMemory",
            "stale confirmation must use its typed conflict code",
        )

        confirmation_id = str((created_body.get("confirmation") or {}).get("confirmationId") or "")
        expect_rejected(
            test_dsn,
            lambda cursor: cursor.execute(
                "UPDATE owner_truth.knowledge_dimension_confirmation_receipts SET dimension = 'values' WHERE id = %s",
                (confirmation_id,),
            ),
            "dimension confirmation receipts must be append-only",
        )

        replace_current_memory_version(
            test_dsn,
            vault_id=vault_id,
            memory_id=memory_id,
            old_memory_version_id=memory_version_id,
        )
        OwnerTruthMemoryProjectionService(store).rebuild(context=owner_context)
        with store.request_unit_of_work(
            correlation_id="dimension-confirmation-replaced-read-smoke",
            command_id="dimensionConfirmationReplacedReadSmoke",
        ):
            replaced_read = OwnerTruthKnowledgeDimensionReadService(
                store.owner_truth_memory_projection_repository(),
                store.owner_truth_knowledge_dimension_confirmation_repository(),
            ).read(context=owner_context)
        require(
            replaced_read.included_memory_version_ids == (),
            "a receipt for a superseded MemoryVersion must not increase current coverage",
        )

        print(
            "owner truth knowledge dimension confirmation postgres smoke passed "
            f"schemaHead={verified['expectedHead']} defaultHidden=true receiptCreated=true "
            "receiptReplay=true hashBound=true appendOnly=true recommendationRead=true "
            "activeThreadSelected=true pausedThreadRejected=true "
            "cooldownSessionRejected=true doNotAskSessionRejected=true "
            "skipOnceSessionRejected=true "
            "supersededReceiptExcluded=true"
        )
    finally:
        main_module.store = previous_store
        main_module.BACKEND_API_TOKEN = previous_backend_token
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = previous_legacy_phone_login
        main_module.AUTH_ROUTE_MODE = previous_route_mode
        main_module.AUTH_OWNERSHIP_MODE = previous_ownership_mode
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = previous_candidate_qa
        main_module.OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_QA_ENABLED = previous_confirmation_qa
        main_module.OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_READ_QA_ENABLED = previous_recommendation_qa
        if store is not None:
            store.close_pool()
        try:
            drop_database(admin_dsn, database_name)
        except Exception as exc:  # pragma: no cover - cleanup diagnostics only
            print(f"warning: failed to drop temporary database {database_name}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
