#!/usr/bin/env python3
"""Verify safe candidate failure status through disposable PostgreSQL.

The fixture contains synthetic text only. It injects a provider 401, exercises
the real Worker and persistence repositories, reads the same value-free status
model used by the HTTP route, then drops the disposable database.
"""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import uuid

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import httpx
import psycopg
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb

from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget
from app.async_effects.owner_truth_candidate_extraction_worker import (
    OwnerTruthCandidateExtractionWorkerRuntime,
)
from app.core.config import Settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.domain.owner_truth.ontology import OWNER_TRUTH_SCHEMA_VERSION
from app.services.postgres_store import PostgresStore


SCHEMA_VERSION = "owner-truth-candidate-failure-postgres-smoke-v1"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def digest(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True).encode("utf-8")
    ).hexdigest()


def dsn_for_database(base_dsn: str, database_name: str) -> str:
    values = conninfo_to_dict(base_dsn)
    values["dbname"] = database_name
    return make_conninfo(**values)


def create_database(admin_dsn: str, database_name: str) -> None:
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name))
            )


def drop_database(admin_dsn: str, database_name: str) -> None:
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (database_name,),
            )
            cursor.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(
                    sql.Identifier(database_name)
                )
            )


def seed_failure_fixture(
    dsn: str,
    *,
    owner: str,
) -> tuple[str, str, str, AsyncEffectIntent]:
    vault = owner
    thread = str(uuid.uuid4())
    session = str(uuid.uuid4())
    batch = str(uuid.uuid4())
    source = str(uuid.uuid4())
    synthetic_text = "合成授权失败验证内容，不属于任何真实用户。"
    content_payload = {"text": synthetic_text}
    content_hash = digest(content_payload)
    metadata = {
        "origin": "interviewReviewBatchCandidateProposal",
        "reviewBatchId": batch,
        "captureMode": "naturalInput",
    }
    intent = AsyncEffectIntent(
        operation_type="ownerTruth.source.created",
        target=AsyncEffectTarget(
            owner_subject_id=owner,
            vault_id=vault,
            resource_type="source",
            resource_id=source,
            resource_version=1,
            purpose="candidateExtraction",
            authority_epoch=0,
        ),
        payload_hash=content_hash,
        max_attempts=3,
    )
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO owner_truth.vaults (vault_id, owner_subject_id) VALUES (%s, %s)",
                (vault, owner),
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.conversation_threads (
                    id, vault_id, owner_subject_id, entry_mode, policy_version
                ) VALUES (%s, %s, %s, 'naturalInput', %s)
                """,
                (thread, vault, owner, OWNER_TRUTH_SCHEMA_VERSION),
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.interview_sessions (
                    id, vault_id, owner_subject_id, current_thread_id, policy_version
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (session, vault, owner, thread, OWNER_TRUTH_SCHEMA_VERSION),
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.interview_review_batches (
                    id, vault_id, owner_subject_id, session_id, thread_id,
                    trigger, state, captured_candidate_batch_turn_count,
                    owner_turn_start_count, owner_turn_end_count,
                    through_message_sequence, policy_version, acknowledged_at
                ) VALUES (
                    %s, %s, %s, %s, %s, 'sessionExit', 'acknowledged',
                    1, 1, 1, 1, %s, NOW()
                )
                """,
                (batch, vault, owner, session, thread, OWNER_TRUTH_SCHEMA_VERSION),
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.sources (
                    id, vault_id, owner_subject_id, source_kind, content_hash,
                    policy_version, metadata, content_payload
                ) VALUES (%s, %s, %s, 'conversation', %s, %s, %s, %s)
                """,
                (
                    source,
                    vault,
                    owner,
                    content_hash,
                    OWNER_TRUTH_SCHEMA_VERSION,
                    Jsonb(metadata),
                    Jsonb(content_payload),
                ),
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.interview_review_batch_candidate_admissions (
                    id, vault_id, owner_subject_id, review_batch_id, source_id,
                    source_version, source_content_hash, effect_operation_id,
                    command_id_hash, payload_hash, actor_subject_id, policy_version,
                    owner_message_count, first_message_sequence, last_message_sequence
                ) VALUES (%s, %s, %s, %s, %s, 1, %s, %s, %s, %s, %s, %s, 1, 1, 1)
                """,
                (
                    str(uuid.uuid4()),
                    vault,
                    owner,
                    batch,
                    source,
                    content_hash,
                    intent.operation_id,
                    digest({"command": batch}),
                    intent.payload_hash,
                    owner,
                    OWNER_TRUTH_SCHEMA_VERSION,
                ),
            )
        connection.commit()
    return owner, vault, batch, intent


class AuthorizationRejectedExtractor:
    def extract(self, *, intent, source):
        del intent, source
        request = httpx.Request("POST", "https://provider.invalid/v1/organize")
        response = httpx.Response(
            401,
            request=request,
            text="synthetic private provider response must not escape",
        )
        raise httpx.HTTPStatusError(
            "synthetic private provider response must not escape",
            request=request,
            response=response,
        )


def main() -> int:
    base = Settings.from_env()
    base_dsn = os.environ.get("DATABASE_URL", base.database_url).strip()
    require(bool(base_dsn), "DATABASE_URL is required")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_candidate_failure_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    store: PostgresStore | None = None
    main_module = None
    previous_runtime: dict[str, object] | None = None
    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="candidate-failure-postgres-smoke",
            lock_timeout_ms=1_000,
            statement_timeout_ms=30_000,
        )
        migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")
        store = PostgresStore(dsn=test_dsn, pool_min_size=1, pool_max_size=2)
        store.open_pool(wait=True)
        import app.main as imported_main_module

        main_module = imported_main_module
        previous_runtime = {
            "store": main_module.store,
            "BACKEND_API_TOKEN": main_module.BACKEND_API_TOKEN,
            "AUTH_LEGACY_PHONE_LOGIN_ENABLED": (
                main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
            ),
            "AUTH_ROUTE_MODE": main_module.AUTH_ROUTE_MODE,
            "AUTH_OWNERSHIP_MODE": main_module.AUTH_OWNERSHIP_MODE,
            "OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED": (
                main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED
            ),
        }
        main_module.store = store
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = True
        client = TestClient(main_module.app)
        login = client.post(
            "/auth/login",
            json={
                "phone": "13900000991",
                "nickname": "Synthetic candidate failure owner",
                "password": "synthetic-candidate-failure-smoke",
            },
        )
        require(login.status_code == 200, "synthetic owner login must succeed")
        login_body = login.json()
        owner = str(login_body["user"]["id"])
        owner_headers = {
            "Authorization": f"Bearer {login_body['auth']['accessToken']}",
            "X-DreamJourney-QA-Owner-Truth": "1",
        }
        owner, vault, batch, intent = seed_failure_fixture(
            test_dsn,
            owner=owner,
        )
        with store.request_unit_of_work(
            correlation_id="synthetic-candidate-failure-effect",
            command_id="syntheticCandidateFailureEffect",
        ):
            store.effect_kernel_repository().accept(intent)
        runtime = replace(
            base,
            database_url=test_dsn,
            async_effect_v1_enabled=True,
            async_effect_worker_enabled=True,
            owner_truth_candidate_extraction_worker_enabled=True,
        )
        worker_result = OwnerTruthCandidateExtractionWorkerRuntime(
            settings=runtime,
            store=store,
            worker_id="synthetic-authorization-failure-worker",
            extractor=AuthorizationRejectedExtractor(),
        ).run_once()
        require(worker_result.get("status") == "failed", "401 must terminally fail")

        # Exercise the actual HTTP route and serializer after the real Worker
        # and PostgreSQL persistence path, using a real synthetic login session.
        api_response = client.get(
            f"/v2/vaults/{vault}/interview-review-batches/{batch}/candidate-proposal/status",
            headers=owner_headers,
        )
        require(api_response.status_code == 200, "status API must return 200")
        require(
            api_response.headers.get("cache-control") == "no-store",
            "status API must remain non-cacheable",
        )
        response = api_response.json()
        require(response.get("vaultId") == vault, "status API vault binding mismatch")
        extraction = response["candidateExtraction"]
        expected_failure = "candidateExtraction.providerAuthorization.rejected"
        require(extraction["firstFailureCode"] == expected_failure, "first cause lost")
        require(extraction["failureCode"] == expected_failure, "final cause overwritten")
        require(extraction["failureCategory"] == "authorization", "category mismatch")
        require(
            extraction["terminationCode"] == "candidateExtractionRetriesExhausted",
            "terminal reason missing",
        )
        with psycopg.connect(test_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT error_code, terminal_reason_code, state
                    FROM async_effects.job_attempts
                    WHERE job_id = %s
                    ORDER BY attempt DESC LIMIT 1
                    """,
                    (intent.job_id,),
                )
                persisted = cursor.fetchone()
        require(
            persisted
            == (
                expected_failure,
                "candidateExtractionRetriesExhausted",
                "terminalFailed",
            ),
            "job attempt did not preserve cause and terminal reason",
        )
        rendered = json.dumps(response, ensure_ascii=False, sort_keys=True)
        for forbidden in (
            "synthetic private provider response",
            "合成授权失败验证内容",
            "provider.invalid",
            "api_key",
            "prompt",
        ):
            require(forbidden not in rendered.lower(), "private diagnostic escaped")
        print(
            json.dumps(
                {
                    "schemaVersion": SCHEMA_VERSION,
                    "status": "passed",
                    "schemaHead": verified["expectedHead"],
                    "statusAPI": api_response.status_code,
                    "workerState": worker_result["status"],
                    "jobAttemptState": persisted[2],
                    "failureCategory": extraction["failureCategory"],
                    "firstFailurePreserved": True,
                    "terminalReasonPreserved": True,
                    "privateContentRetained": False,
                    "providerResponseRetained": False,
                },
                sort_keys=True,
            )
        )
        return 0
    finally:
        if main_module is not None and previous_runtime is not None:
            for name, value in previous_runtime.items():
                setattr(main_module, name, value)
        if store is not None:
            store.close_pool()
        try:
            drop_database(admin_dsn, database_name)
        except Exception as error:
            print(
                f"warning: temporary database cleanup failed: {type(error).__name__}",
                file=sys.stderr,
            )


if __name__ == "__main__":
    raise SystemExit(main())
