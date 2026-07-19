#!/usr/bin/env python3
"""Exercise the hidden Owner Truth Candidate routes in a disposable Postgres DB.

The running service never has its QA flag changed.  This script runs in a
separate Python process, creates a temporary database, applies migrations,
temporarily enables the route contract only inside that process, and removes
the database on exit.  It is a route/transaction proof, not a public-release
or production-account smoke.
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


def login(client: TestClient, *, phone: str, nickname: str) -> tuple[str, dict[str, str]]:
    response = client.post(
        "/auth/login",
        json={"phone": phone, "nickname": nickname, "password": "candidate-route-smoke"},
    )
    require(response.status_code == 200, f"temporary owner login failed: {response.text}")
    body = response.json()
    return str(body["user"]["id"]), {
        "Authorization": f"Bearer {body['auth']['accessToken']}",
        "X-DreamJourney-QA-Owner-Truth": "1",
    }


def seed_pending_candidate(
    dsn: str,
    *,
    vault_id: str,
    owner_subject_id: str,
) -> tuple[str, str]:
    source_id = str(uuid.uuid4())
    candidate_id = str(uuid.uuid4())
    proposal = {"summary": "仅用于隔离 Candidate 路由验证的文字摘要"}
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
                    content_hash({"source": "candidate-route-smoke"}),
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
    return candidate_id, proposal["summary"]


def route_code(response: Any) -> str:
    detail = response.json().get("detail") if response.content else None
    if isinstance(detail, dict):
        return str(detail.get("code") or "")
    return ""


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_owner_truth_candidate_route_smoke_{uuid.uuid4().hex[:12]}"
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
            build_id="owner-truth-candidate-route-g2",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")

        store = PostgresStore(dsn=test_dsn, pool_min_size=1, pool_max_size=2)
        store.open_pool(wait=True)
        main_module.store = store
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"

        client = TestClient(main_module.app)
        owner_id, owner_headers = login(
            client,
            phone="13900000101",
            nickname="Candidate route smoke owner",
        )
        vault_id = "vault-candidate-route-smoke"
        candidate_id, proposal_summary = seed_pending_candidate(
            test_dsn,
            vault_id=vault_id,
            owner_subject_id=owner_id,
        )

        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = False
        default_hidden = client.get(
            f"/v2/vaults/{vault_id}/candidates",
            headers=owner_headers,
        )
        require(default_hidden.status_code == 404, "candidate route must remain hidden by default")
        require(
            route_code(default_hidden) == "ownerTruthCandidateReviewUnavailable",
            "hidden candidate route must return its typed unavailable code",
        )

        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = True
        missing_qa_header = client.get(
            f"/v2/vaults/{vault_id}/candidates",
            headers={"Authorization": owner_headers["Authorization"]},
        )
        require(missing_qa_header.status_code == 404, "candidate route must require the QA header")
        require(
            route_code(missing_qa_header) == "ownerTruthCandidateReviewUnavailable",
            "missing QA header must not expose the candidate route",
        )

        inbox = client.get(f"/v2/vaults/{vault_id}/candidates", headers=owner_headers)
        require(inbox.status_code == 200, f"owner inbox failed: {inbox.text}")
        inbox_body = inbox.json()
        require(inbox.headers.get("cache-control") == "no-store", "candidate inbox must be no-store")
        require(inbox_body.get("schemaVersion") == "owner-truth-candidate-inbox-v1", "inbox schema drift")
        require(len(inbox_body.get("candidates") or []) == 1, "owner must see the seeded pending candidate")
        require(
            inbox_body["candidates"][0].get("candidateId") == candidate_id,
            "inbox must bind the exact seeded candidate",
        )
        # The owner-only QA inbox intentionally carries the candidate preview so
        # an Owner can review it. The protection boundary is the default-hidden
        # route plus authenticated owner and QA-header gates above, not a
        # lossy response once that triple gate has been explicitly satisfied.
        require(
            (inbox_body["candidates"][0].get("content") or {}).get("summary")
            == proposal_summary,
            "owner QA inbox must return the reviewable candidate preview",
        )

        other_vault = client.get("/v2/vaults/vault-other/candidates", headers=owner_headers)
        require(other_vault.status_code == 403, "cross-vault candidate lookup must be denied")
        require(
            route_code(other_vault) == "ownerTruthCandidateReviewDenied",
            "cross-vault denial must remain typed",
        )

        other_owner_id, other_owner_headers = login(
            client,
            phone="13900000102",
            nickname="Candidate route smoke observer",
        )
        require(other_owner_id != owner_id, "temporary smoke identities must be distinct")
        other_owner = client.get(f"/v2/vaults/{vault_id}/candidates", headers=other_owner_headers)
        require(other_owner.status_code == 403, "non-owner candidate lookup must be denied")
        require(
            route_code(other_owner) == "ownerTruthCandidateReviewDenied",
            "non-owner denial must remain typed",
        )

        decision_payload = {
            "commandId": "candidate-route-smoke-accept-v1",
            "expectedCandidateVersion": 1,
            "action": "accept",
            "reasonCode": "qaRouteSmoke",
        }
        decision = client.post(
            f"/v2/vaults/{vault_id}/candidates/{candidate_id}/decisions",
            headers=owner_headers,
            json=decision_payload,
        )
        require(decision.status_code == 201, f"owner decision failed: {decision.text}")
        decision_body = decision.json()
        require(decision_body.get("status") == "created", "fresh decision must be created")
        require(
            decision_body.get("schemaVersion") == "owner-truth-candidate-decision-memory-v1",
            "decision schema drift",
        )
        require(
            (decision_body.get("receipt") or {}).get("decision") == "accepted",
            "decision receipt must remain accepted",
        )
        require(
            (decision_body.get("memoryActivation") or {}).get("status") == "created",
            "accepted candidate must activate one MemoryVersion",
        )
        require(proposal_summary not in str(decision_body), "decision response must not echo raw proposal summary")

        replay = client.post(
            f"/v2/vaults/{vault_id}/candidates/{candidate_id}/decisions",
            headers=owner_headers,
            json=decision_payload,
        )
        require(replay.status_code == 200, "same command must replay instead of writing again")
        require(replay.json().get("status") == "deduplicated", "decision replay must be deduplicated")
        require(
            (replay.json().get("receipt") or {}).get("receiptId")
            == (decision_body.get("receipt") or {}).get("receiptId"),
            "decision replay must preserve the immutable receipt",
        )

        empty_inbox = client.get(f"/v2/vaults/{vault_id}/candidates", headers=owner_headers)
        require(empty_inbox.status_code == 200, "post-decision inbox lookup failed")
        require(empty_inbox.json().get("candidates") == [], "terminal candidate must leave pending inbox")

        print(
            "owner truth candidate route postgres smoke passed "
            f"schemaHead={verified['expectedHead']} defaultHidden=true qaHeaderRequired=true "
            "ownerInbox=true crossVaultDenied=true crossOwnerDenied=true "
            "decisionCreated=true decisionDeduplicated=true pendingRemoved=true"
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
        except Exception as exc:  # pragma: no cover - cleanup diagnostics only
            print(
                f"warning: failed to drop temporary database {database_name}: {exc}",
                file=sys.stderr,
            )


if __name__ == "__main__":
    main()
