#!/usr/bin/env python3
"""Exercise private SearchDocument projection persistence in disposable Postgres.

The script never contacts the deployed database or enables a production
feature. It creates a temporary database, applies the exact migration head,
temporarily enables QA flags in-process, and removes the database afterwards.
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
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
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


def expect_rejected(dsn: str, operation, message: str) -> None:
    rejected = False
    try:
        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                operation(cursor)
    except Exception:
        rejected = True
    require(rejected, message)


def login(client: TestClient, *, phone: str) -> tuple[str, dict[str, str]]:
    response = client.post(
        "/auth/login",
        json={"phone": phone, "nickname": "search projection smoke", "password": "projection-smoke"},
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


def seed_current_memory(
    dsn: str,
    *,
    vault_id: str,
    owner_subject_id: str,
) -> tuple[str, str, str]:
    source_id = str(uuid.uuid4())
    memory_id = str(uuid.uuid4())
    memory_version_id = str(uuid.uuid4())
    content = {
        "claim": "这是一条仅用于隔离 SearchDocument 投影验证的私有职业选择记忆。",
        "tags": ["职业", "选择"],
    }
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
                    canonical_hash({"source": "search-document-projection-smoke"}),
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


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_search_projection_smoke_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    store: PostgresStore | None = None

    previous_store = main_module.store
    previous_backend_token = main_module.BACKEND_API_TOKEN
    previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
    previous_route_mode = main_module.AUTH_ROUTE_MODE
    previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
    previous_candidate_qa = main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED
    previous_search_read_qa = main_module.OWNER_TRUTH_MEMORY_SEARCH_READ_QA_ENABLED
    previous_search_projection_qa = (
        main_module.OWNER_TRUTH_MEMORY_SEARCH_PROJECTION_QA_ENABLED
    )

    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="owner-truth-search-document-projection-g0",
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
        main_module.OWNER_TRUTH_MEMORY_SEARCH_READ_QA_ENABLED = True
        main_module.OWNER_TRUTH_MEMORY_SEARCH_PROJECTION_QA_ENABLED = True

        client = TestClient(main_module.app)
        owner_id, owner_headers = login(client, phone="13900000461")
        vault_id = "vault-memory-search-projection-smoke"
        memory_id, memory_version_id, content_hash = seed_current_memory(
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

        before = client.post(
            f"/v2/vaults/{vault_id}/memory-search/read",
            headers=owner_headers,
            json={"query": "职业选择"},
        )
        require(
            before.status_code == 200 and before.json()["search"]["state"] == "rebuilding",
            "search must fail closed until the private index is rebuilt",
        )

        rebuilt = client.post(
            f"/v2/vaults/{vault_id}/memory-search/projection/rebuild",
            headers=owner_headers,
            json={},
        )
        require(rebuilt.status_code == 200, f"projection rebuild failed: {rebuilt.text}")
        rebuild_summary = rebuilt.json()["searchProjection"]
        require(
            rebuild_summary["state"] == "ready"
            and rebuild_summary["projection"]["documentCount"] == 1,
            "projection rebuild must persist exactly one current document",
        )
        require(
            "私有职业选择记忆" not in json.dumps(rebuilt.json(), ensure_ascii=False),
            "rebuild response must not expose memory content",
        )

        search = client.post(
            f"/v2/vaults/{vault_id}/memory-search/read",
            headers=owner_headers,
            json={"query": "职业选择"},
        )
        require(search.status_code == 200, f"search read failed: {search.text}")
        search_summary = search.json()["search"]
        require(
            search_summary["state"] == "ready"
            and search_summary["hits"][0]["citation"]["memoryVersionId"] == memory_version_id
            and search_summary["queryPlan"]["retrievalMode"] == "deterministicTextFallback"
            and search_summary["queryPlan"]["semanticRankingAvailable"] is False,
            "search must read the persisted current index without claiming semantic ranking",
        )
        rendered = json.dumps(search.json(), ensure_ascii=False)
        require(
            "私有职业选择记忆" not in rendered
            and "职业选择" not in rendered
            and "sourceId" not in rendered
            and "searchText" not in rendered,
            "search response must remain value-free",
        )

        expect_rejected(
            test_dsn,
            lambda cursor: cursor.execute(
                """
                INSERT INTO owner_truth.search_documents (
                    vault_id, authority_epoch, memory_id, memory_version_id,
                    content_hash, memory_kind, perspective_type, sensitivity,
                    search_text, structured_terms, text_was_truncated
                ) VALUES (%s, 0, %s, %s, %s, 'knowledge', 'firstPerson',
                    'standard', 'forbidden', '[]'::JSONB, FALSE)
                """,
                (
                    vault_id,
                    str(uuid.uuid4()),
                    str(uuid.uuid4()),
                    content_hash,
                ),
            ),
            "documents without a current projection entry must be rejected",
        )

        with psycopg.connect(test_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE owner_truth.search_documents
                    SET search_text = 'tampered'
                    WHERE vault_id = %s AND authority_epoch = 0 AND memory_id = %s
                    """,
                    (vault_id, memory_id),
                )
            connection.commit()
        tampered = client.post(
            f"/v2/vaults/{vault_id}/memory-search/read",
            headers=owner_headers,
            json={"query": "职业选择"},
        )
        require(
            tampered.status_code == 200 and tampered.json()["search"]["state"] == "rebuilding",
            "a digest mismatch must fail closed rather than reuse tampered private index data",
        )
        repaired = client.post(
            f"/v2/vaults/{vault_id}/memory-search/projection/rebuild",
            headers=owner_headers,
            json={},
        )
        require(
            repaired.status_code == 200
            and repaired.json()["searchProjection"]["outcome"] == "rebuilt",
            "explicit rebuild must repair a digest-mismatched index",
        )

        _other_owner_id, other_headers = login(client, phone="13900000462")
        denied = client.post(
            f"/v2/vaults/{vault_id}/memory-search/read",
            headers=other_headers,
            json={"query": "职业选择"},
        )
        require(
            denied.status_code == 403
            and route_code(denied) == "ownerTruthMemorySearchReadDenied",
            "cross-owner search must be denied",
        )

        main_module.OWNER_TRUTH_MEMORY_SEARCH_PROJECTION_QA_ENABLED = False
        hidden = client.post(
            f"/v2/vaults/{vault_id}/memory-search/projection/rebuild",
            headers=owner_headers,
            json={},
        )
        require(
            hidden.status_code == 404
            and route_code(hidden) == "ownerTruthMemorySearchProjectionUnavailable",
            "private rebuild route must remain independently default-off",
        )

        print(
            "owner truth memory search projection postgres smoke passed "
            f"schemaHead={verified['expectedHead']} defaultHidden=true "
            "checkpointBound=true digestFailClosed=true crossOwnerDenied=true"
        )
    finally:
        main_module.store = previous_store
        main_module.BACKEND_API_TOKEN = previous_backend_token
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = previous_legacy_phone_login
        main_module.AUTH_ROUTE_MODE = previous_route_mode
        main_module.AUTH_OWNERSHIP_MODE = previous_ownership_mode
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = previous_candidate_qa
        main_module.OWNER_TRUTH_MEMORY_SEARCH_READ_QA_ENABLED = previous_search_read_qa
        main_module.OWNER_TRUTH_MEMORY_SEARCH_PROJECTION_QA_ENABLED = (
            previous_search_projection_qa
        )
        if store is not None:
            store.close_pool()
        try:
            drop_database(admin_dsn, database_name)
        except Exception as exc:  # pragma: no cover - cleanup diagnostics only
            print(f"warning: failed to drop temporary database {database_name}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
