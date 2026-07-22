#!/usr/bin/env python3
"""Exercise server-planned M0-B breadth recommendations in a disposable Postgres DB.

The smoke enables the hidden route only in-process. It proves that planning is
derived from current Owner-confirmed coverage plus one active/open interview
session, accepts no client-supplied candidate state, and writes no product
records. The temporary database is dropped after the run.
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
from app.domain.owner_truth.conversation import (
    InterviewBoundary,
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


def login(client: TestClient, *, phone: str) -> tuple[str, dict[str, str]]:
    response = client.post(
        "/auth/login",
        json={"phone": phone, "nickname": "recommendation planner smoke", "password": "planner-smoke"},
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
        correlation_id=f"knowledge-recommendation-plan-smoke:{command_id}",
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
    content = {"claim": "这是一条仅用于隔离服务端推荐规划验证的知识记忆。"}
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
                    canonical_hash({"source": "recommendation-plan-smoke"}),
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
) -> None:
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
            source_id, source_version = str(source[0]), int(source[1])
            cursor.execute(
                "UPDATE owner_truth.memory_versions SET is_current = FALSE WHERE vault_id = %s AND id = %s",
                (vault_id, old_memory_version_id),
            )
            replacement_content = {"claim": "这是一条替代后的未确认知识记忆版本。"}
            cursor.execute(
                """
                INSERT INTO owner_truth.memory_versions (
                    id, vault_id, memory_id, version_number, is_current,
                    schema_version, content_hash, payload, source_id, source_version,
                    supersedes_version_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(uuid.uuid4()),
                    vault_id,
                    memory_id,
                    2,
                    True,
                    "owner-truth-v1",
                    canonical_hash(replacement_content),
                    Jsonb(
                        {
                            "content": replacement_content,
                            "contentSchemaVersion": "owner-truth-v1",
                            "evidenceRefs": [{"sourceId": source_id, "sourceVersion": source_version}],
                        }
                    ),
                    source_id,
                    source_version,
                    old_memory_version_id,
                ),
            )
        connection.commit()


def owner_truth_counts(dsn: str) -> tuple[int, int, int, int]:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    (SELECT count(*) FROM owner_truth.conversation_threads),
                    (SELECT count(*) FROM owner_truth.interview_sessions),
                    (SELECT count(*) FROM owner_truth.memory_versions),
                    (SELECT count(*) FROM owner_truth.knowledge_dimension_confirmation_receipts)
                """
            )
            row = cursor.fetchone()
    require(row is not None, "owner truth counts are unavailable")
    return tuple(int(value) for value in row)


def start_recommendation_session(
    store: PostgresStore,
    *,
    context: OwnerTruthCommandContext,
    label: str,
) -> tuple[str, str]:
    thread_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    started = invoke_conversation(
        store,
        command_id=f"recommendation-plan-start-{label}",
        operation=lambda service: service.start_session(
            command=StartInterviewSessionCommand(
                command_id=f"recommendation-plan-start-{label}",
                thread_id=thread_id,
                session_id=session_id,
                expected_thread_version=0,
                entry_mode="recommendation",
            ),
            context=context,
        ),
    )
    require(started.state.value == "active", "smoke must create an active recommendation thread")
    return thread_id, session_id


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_recommendation_plan_smoke_{uuid.uuid4().hex[:12]}"
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
    previous_plan_qa = main_module.OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_PLAN_QA_ENABLED

    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="owner-truth-recommendation-plan-g0",
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
        main_module.OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_PLAN_QA_ENABLED = False

        client = TestClient(main_module.app)
        owner_id, owner_headers = login(client, phone="13900000136")
        vault_id = "vault-knowledge-recommendation-plan-smoke"
        memory_id, memory_version_id, content_hash = seed_current_knowledge_memory(
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

        confirmation_path = (
            f"/v2/vaults/{vault_id}/memory-versions/{memory_version_id}"
            "/knowledge-dimension-confirmations"
        )
        confirmation = client.post(
            confirmation_path,
            headers=owner_headers,
            json={
                "commandId": "recommendation-plan-confirm-001",
                "expectedContentHash": content_hash,
                "dimension": "keyDecisions",
                "coveredFacets": ["choice", "reason"],
            },
        )
        require(confirmation.status_code == 201, f"confirmation creation failed: {confirmation.text}")

        plan_path = f"/v2/vaults/{vault_id}/knowledge-recommendations/plan"
        hidden = client.post(plan_path, headers=owner_headers, json={})
        require(hidden.status_code == 404, "server planning route must default hidden")
        require(
            route_code(hidden) == "ownerTruthKnowledgeRecommendationPlanUnavailable",
            "hidden plan route must expose a stable unavailable code",
        )

        main_module.OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_PLAN_QA_ENABLED = True
        thread_id, session_id = start_recommendation_session(
            store,
            context=context,
            label="active",
        )
        counts_before_plan = owner_truth_counts(test_dsn)
        planned = client.post(plan_path, headers=owner_headers, json={})
        repeated = client.post(plan_path, headers=owner_headers, json={})
        require(planned.status_code == 200, f"server plan failed: {planned.text}")
        require(repeated.status_code == 200, f"repeated server plan failed: {repeated.text}")
        require(planned.headers.get("cache-control") == "no-store", "plan must be no-store")
        plan_body = planned.json()
        summary = plan_body.get("recommendations") or {}
        selected = summary.get("selected") or []
        require(
            plan_body.get("schemaVersion") == "owner-truth-knowledge-recommendation-plan-response-v1",
            "plan must expose its dedicated response schema",
        )
        require(summary.get("candidateSource") == "serverPlanned", "plan must identify server planning")
        require([item.get("slot") for item in selected] == ["breadth"], "only breadth may be planned")
        require(
            selected[0].get("threadId") == thread_id,
            "server plan must bind the current eligible session thread",
        )
        require(
            selected[0].get("targetDimension") == "keyDecisions"
            and selected[0].get("missingFacet") == "outcome",
            "server plan must use the confirmed coverage gap only",
        )
        require(
            plan_body == repeated.json(),
            "repeat plan reads must be deterministic while authority is unchanged",
        )
        require(
            counts_before_plan == owner_truth_counts(test_dsn),
            "server planning must not write Owner Truth records",
        )
        require(
            "仅用于隔离" not in planned.text and "claim" not in planned.text,
            "plan response must not leak memory text",
        )

        injected = client.post(
            plan_path,
            headers=owner_headers,
            json={"candidates": [], "threadId": thread_id},
        )
        require(injected.status_code == 400, "plan must reject client candidate or thread injection")
        require(
            route_code(injected) == "ownerTruthKnowledgeRecommendationPlanInvalid",
            "injected plan fields must have a typed invalid code",
        )

        boundary = invoke_conversation(
            store,
            command_id="recommendation-plan-set-do-not-ask",
            operation=lambda service: service.set_boundary(
                command=SetInterviewBoundaryCommand(
                    command_id="recommendation-plan-set-do-not-ask",
                    thread_id=thread_id,
                    session_id=session_id,
                    expected_session_version=1,
                    boundary=InterviewBoundary.DO_NOT_ASK,
                ),
                context=context,
            ),
        )
        require(boundary.boundary is InterviewBoundary.DO_NOT_ASK, "boundary must persist")
        counts_before_boundary_read = owner_truth_counts(test_dsn)
        boundary_plan = client.post(plan_path, headers=owner_headers, json={})
        boundary_selected = (boundary_plan.json().get("recommendations") or {}).get("selected") or []
        require(boundary_plan.status_code == 200, f"boundary plan failed: {boundary_plan.text}")
        require(not boundary_selected, "do-not-ask session must suppress server planning")
        require(
            counts_before_boundary_read == owner_truth_counts(test_dsn),
            "boundary plan must remain read-only",
        )

        replace_current_memory_version(
            test_dsn,
            vault_id=vault_id,
            memory_id=memory_id,
            old_memory_version_id=memory_version_id,
        )
        OwnerTruthMemoryProjectionService(store).rebuild(context=context)
        start_recommendation_session(store, context=context, label="after-supersede")
        counts_before_superseded_read = owner_truth_counts(test_dsn)
        superseded_plan = client.post(plan_path, headers=owner_headers, json={})
        superseded_selected = (superseded_plan.json().get("recommendations") or {}).get("selected") or []
        require(superseded_plan.status_code == 200, f"superseded plan failed: {superseded_plan.text}")
        require(
            not superseded_selected,
            "a superseded confirmation receipt must not produce a server-planned candidate",
        )
        require(
            counts_before_superseded_read == owner_truth_counts(test_dsn),
            "superseded evidence plan must remain read-only",
        )

        print(
            "owner truth knowledge recommendation plan postgres smoke passed "
            f"schemaHead={verified['expectedHead']} defaultHidden=true serverPlanned=true "
            "breadthOnly=true deterministic=true clientInjectionRejected=true "
            "doNotAskSuppressed=true supersededEvidenceExcluded=true readOnly=true"
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
        main_module.OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_PLAN_QA_ENABLED = previous_plan_qa
        if store is not None:
            store.close_pool()
        try:
            drop_database(admin_dsn, database_name)
        except Exception as exc:  # pragma: no cover - cleanup diagnostics only
            print(f"warning: failed to drop temporary database {database_name}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
