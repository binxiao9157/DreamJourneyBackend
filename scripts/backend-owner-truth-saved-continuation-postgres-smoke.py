#!/usr/bin/env python3
"""Exercise explicit, value-free M0-B continuation cues in disposable Postgres.

The route is enabled only inside this smoke. It proves that an Owner must
explicitly save a cue, that the cue never contains conversation or memory text,
and that a session-version change removes it from future server planning
without deleting the append-only receipt.
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
        json={"phone": phone, "nickname": "continuation cue smoke", "password": "cue-smoke"},
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


def invoke_conversation(store: PostgresStore, *, command_id: str, operation) -> Any:
    with store.request_unit_of_work(
        correlation_id=f"saved-continuation-smoke:{command_id}",
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
    content = {"claim": "这是一条只用于续聊线索隔离验证的私有知识记忆。"}
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
                    canonical_hash({"source": "saved-continuation-smoke"}),
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


def start_session(
    store: PostgresStore,
    *,
    context: OwnerTruthCommandContext,
) -> tuple[str, str]:
    thread_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    started = invoke_conversation(
        store,
        command_id="saved-continuation-start",
        operation=lambda service: service.start_session(
            command=StartInterviewSessionCommand(
                command_id="saved-continuation-start",
                thread_id=thread_id,
                session_id=session_id,
                expected_thread_version=0,
                entry_mode="recommendation",
            ),
            context=context,
        ),
    )
    require(started.state.value == "active", "smoke must create an active interview session")
    return thread_id, session_id


def counts(dsn: str) -> tuple[int, int, int, int, int]:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    (SELECT count(*) FROM owner_truth.conversation_threads),
                    (SELECT count(*) FROM owner_truth.interview_sessions),
                    (SELECT count(*) FROM owner_truth.memory_versions),
                    (SELECT count(*) FROM owner_truth.knowledge_dimension_confirmation_receipts),
                    (SELECT count(*) FROM owner_truth.saved_continuation_cues)
                """
            )
            row = cursor.fetchone()
    require(row is not None, "owner truth counts are unavailable")
    return tuple(int(value) for value in row)


def bump_session_version(dsn: str, *, vault_id: str, session_id: str) -> None:
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
            require(cursor.rowcount == 1, "session version bump must target exactly one session")
        connection.commit()


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_saved_continuation_smoke_{uuid.uuid4().hex[:12]}"
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
    previous_saved_cue_qa = main_module.OWNER_TRUTH_SAVED_CONTINUATION_CUE_QA_ENABLED

    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="owner-truth-saved-continuation-g0",
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
        main_module.OWNER_TRUTH_SAVED_CONTINUATION_CUE_QA_ENABLED = False

        client = TestClient(main_module.app)
        owner_id, owner_headers = login(client, phone="13900000135")
        other_owner_id, other_headers = login(client, phone="13900000134")
        vault_id = "vault-saved-continuation-smoke"
        _memory_id, memory_version_id, content_hash = seed_current_knowledge_memory(
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
                "commandId": "saved-continuation-confirm",
                "expectedContentHash": content_hash,
                "dimension": "keyDecisions",
                "coveredFacets": ["choice", "reason"],
            },
        )
        require(confirmation.status_code == 201, f"confirmation creation failed: {confirmation.text}")
        thread_id, session_id = start_session(store, context=context)
        cue_path = (
            f"/v2/vaults/{vault_id}/interview-sessions/{session_id}/saved-continuation-cues"
        )
        cue_payload = {
            "commandId": "saved-continuation-cue-001",
            "threadId": thread_id,
            "expectedSessionVersion": 1,
            "memoryVersionId": memory_version_id,
            "targetDimension": "keyDecisions",
            "missingFacet": "outcome",
        }
        hidden = client.post(cue_path, headers=owner_headers, json=cue_payload)
        require(hidden.status_code == 404, "saved continuation route must default hidden")
        require(
            route_code(hidden) == "ownerTruthSavedContinuationCueUnavailable",
            "hidden cue route must expose a stable unavailable code",
        )

        main_module.OWNER_TRUTH_SAVED_CONTINUATION_CUE_QA_ENABLED = True
        created = client.post(cue_path, headers=owner_headers, json=cue_payload)
        replay = client.post(cue_path, headers=owner_headers, json=cue_payload)
        other = client.post(cue_path, headers=other_headers, json=cue_payload)
        injected = client.post(
            cue_path,
            headers=owner_headers,
            json={**cue_payload, "continuationText": "must never persist"},
        )
        require(created.status_code == 201, f"cue creation failed: {created.text}")
        require(replay.status_code == 200, f"cue replay failed: {replay.text}")
        require(other.status_code == 403, f"other owner must be denied: {other.text}")
        require(injected.status_code == 400, "cue route must reject free-form text injection")
        require(
            route_code(injected) == "ownerTruthSavedContinuationCueInvalid",
            "free-form cue injection must have a stable invalid code",
        )
        require(
            created.json()["cue"]["status"] == "created"
            and replay.json()["cue"]["status"] == "deduplicated",
            "cue command replay must be idempotent",
        )
        require(
            "私有知识记忆" not in created.text and "continuationText" not in created.text,
            "cue response must remain value-free",
        )

        plan_path = f"/v2/vaults/{vault_id}/knowledge-recommendations/plan"
        counts_before_plan = counts(test_dsn)
        planned = client.post(plan_path, headers=owner_headers, json={})
        repeated = client.post(plan_path, headers=owner_headers, json={})
        require(planned.status_code == 200, f"continuity plan failed: {planned.text}")
        require(repeated.status_code == 200, f"continuity plan replay failed: {repeated.text}")
        selected = (planned.json().get("recommendations") or {}).get("selected") or []
        require([item.get("slot") for item in selected] == ["continuity"], "explicit cue must plan continuity")
        require(
            selected[0].get("questionTemplateId") == "continueSavedOwnerCue"
            and selected[0].get("reasonCode") == "explicitOwnerSavedContinuation",
            "continuity plan must identify explicit Owner intent without text",
        )
        require(
            planned.json() == repeated.json(),
            "unchanged continuation planning must be deterministic",
        )
        require(counts_before_plan == counts(test_dsn), "continuation planning must remain read-only")
        require(
            "私有知识记忆" not in planned.text and "claim" not in planned.text,
            "continuity plan must not leak memory text",
        )

        bump_session_version(test_dsn, vault_id=vault_id, session_id=session_id)
        version_stale = client.post(plan_path, headers=owner_headers, json={})
        stale_selected = (version_stale.json().get("recommendations") or {}).get("selected") or []
        require(version_stale.status_code == 200, f"version-stale plan failed: {version_stale.text}")
        require(
            [item.get("slot") for item in stale_selected] == ["breadth"],
            "session-version mismatch must suppress continuity while preserving safe breadth planning",
        )
        require(
            counts(test_dsn)[-1] == 1,
            "stale cue receipt must remain append-only rather than being deleted",
        )

        print(
            "owner truth saved continuation postgres smoke passed "
            f"schemaHead={verified['expectedHead']} defaultHidden=true explicitOwnerOnly=true "
            "deduplicated=true crossOwnerDenied=true clientTextRejected=true "
            "continuityPlanned=true sessionVersionSuppressed=true readOnly=true"
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
        main_module.OWNER_TRUTH_SAVED_CONTINUATION_CUE_QA_ENABLED = previous_saved_cue_qa
        if store is not None:
            store.close_pool()
        try:
            drop_database(admin_dsn, database_name)
        except Exception as exc:  # pragma: no cover - cleanup diagnostics only
            print(f"warning: failed to drop temporary database {database_name}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
