#!/usr/bin/env python3
"""Exercise QA-only Owner thread preferences in a disposable Postgres DB.

This smoke proves only the value-minimized M0-A/M0-B authority contract.  It
does not expose a public Echo control, create a Source/Candidate/Memory, or
store topic text.  The disposable database is the only place where a cooldown
is moved into the past to exercise explicit restoration.
"""

from __future__ import annotations

from contextlib import contextmanager
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


def login(client: TestClient, *, phone: str) -> tuple[str, dict[str, str]]:
    response = client.post(
        "/auth/login",
        json={"phone": phone, "nickname": "thread preference smoke", "password": "thread-smoke"},
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


@contextmanager
def patched_main(store: PostgresStore):
    previous = {
        "store": main_module.store,
        "backend_token": main_module.BACKEND_API_TOKEN,
        "legacy_phone_login": main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED,
        "route_mode": main_module.AUTH_ROUTE_MODE,
        "ownership_mode": main_module.AUTH_OWNERSHIP_MODE,
        "candidate_qa": main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED,
        "thread_preference_qa": main_module.OWNER_TRUTH_THREAD_PREFERENCE_QA_ENABLED,
        "cooldown_seconds": main_module.OWNER_TRUTH_THREAD_COOLDOWN_SECONDS,
    }
    try:
        main_module.store = store
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = True
        main_module.OWNER_TRUTH_THREAD_PREFERENCE_QA_ENABLED = False
        main_module.OWNER_TRUTH_THREAD_COOLDOWN_SECONDS = 60
        yield
    finally:
        main_module.store = previous["store"]
        main_module.BACKEND_API_TOKEN = previous["backend_token"]
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = previous["legacy_phone_login"]
        main_module.AUTH_ROUTE_MODE = previous["route_mode"]
        main_module.AUTH_OWNERSHIP_MODE = previous["ownership_mode"]
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = previous["candidate_qa"]
        main_module.OWNER_TRUTH_THREAD_PREFERENCE_QA_ENABLED = previous["thread_preference_qa"]
        main_module.OWNER_TRUTH_THREAD_COOLDOWN_SECONDS = previous["cooldown_seconds"]


def start_session(client: TestClient, *, vault_id: str, headers: dict[str, str]) -> tuple[str, str]:
    thread_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    response = client.post(
        f"/v2/vaults/{vault_id}/interview-sessions",
        headers=headers,
        json={
            "commandId": str(uuid.uuid4()),
            "threadId": thread_id,
            "sessionId": session_id,
        },
    )
    require(response.status_code == 201, f"interview session start failed: {response.text}")
    return thread_id, session_id


def current_preference(dsn: str, *, vault_id: str, thread_id: str) -> tuple[str, bool, int]:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT preference, cooldown_until IS NOT NULL, row_version
                FROM owner_truth.thread_preferences
                WHERE vault_id = %s AND thread_id = %s
                """,
                (vault_id, thread_id),
            )
            row = cursor.fetchone()
    require(row is not None, "thread preference row is missing")
    return str(row[0]), bool(row[1]), int(row[2])


def receipt_count(dsn: str) -> int:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM owner_truth.thread_preference_receipts")
            row = cursor.fetchone()
    require(row is not None, "thread preference receipt count is unavailable")
    return int(row[0])


def expire_cooldown(dsn: str, *, vault_id: str, thread_id: str) -> None:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE owner_truth.thread_preferences
                SET cooldown_until = NOW() - INTERVAL '1 second', updated_at = NOW()
                WHERE vault_id = %s AND thread_id = %s AND preference = 'cooldown'
                """,
                (vault_id, thread_id),
            )
            require(cursor.rowcount == 1, "disposable cooldown expiry must update one preference")
        connection.commit()


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_thread_preference_smoke_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    store: PostgresStore | None = None

    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="owner-truth-thread-preference-g0",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")

        store = PostgresStore(dsn=test_dsn, pool_min_size=1, pool_max_size=3)
        store.open_pool(wait=True)
        with patched_main(store):
            client = TestClient(main_module.app)
            _owner_id, owner_headers = login(client, phone="13900000411")
            _other_id, other_headers = login(client, phone="13900000412")
            vault_id = "vault-thread-preference-smoke"
            thread_id, session_id = start_session(client, vault_id=vault_id, headers=owner_headers)
            boundary_path = f"/v2/vaults/{vault_id}/interview-sessions/{session_id}/boundary"
            restore_cooldown_path = (
                f"/v2/vaults/{vault_id}/interview-sessions/{session_id}/restore-cooldown"
            )

            hidden = client.post(
                restore_cooldown_path,
                headers=owner_headers,
                json={
                    "commandId": str(uuid.uuid4()),
                    "threadId": thread_id,
                    "expectedSessionVersion": 1,
                },
            )
            require(hidden.status_code == 404, "restore cooldown must default hidden")
            require(
                route_code(hidden) == "ownerTruthThreadPreferenceUnavailable",
                "hidden cooldown route must retain stable unavailable code",
            )

            main_module.OWNER_TRUTH_THREAD_PREFERENCE_QA_ENABLED = True
            cooldown_payload = {
                "commandId": "thread-preference-cooldown-001",
                "threadId": thread_id,
                "expectedSessionVersion": 1,
                "boundary": "cooldown",
            }
            created = client.post(boundary_path, headers=owner_headers, json=cooldown_payload)
            replay = client.post(boundary_path, headers=owner_headers, json=cooldown_payload)
            cross_owner = client.post(boundary_path, headers=other_headers, json=cooldown_payload)
            injected = client.post(
                boundary_path,
                headers=owner_headers,
                json={**cooldown_payload, "cooldownUntil": "client-controlled"},
            )
            require(created.status_code == 201, f"cooldown creation failed: {created.text}")
            require(replay.status_code == 200, f"cooldown replay failed: {replay.text}")
            require(cross_owner.status_code == 403, "cross-owner boundary must be denied")
            require(injected.status_code == 400, "client cooldown timestamp must be rejected")
            require(
                created.json()["receipt"]["boundary"] == "cooldown"
                and replay.json()["receipt"]["status"] == "deduplicated",
                "cooldown must use one idempotent session transition",
            )
            require(
                current_preference(test_dsn, vault_id=vault_id, thread_id=thread_id)[:2]
                == ("cooldown", True),
                "server must persist cooldown and server-calculated expiry",
            )

            early = client.post(
                restore_cooldown_path,
                headers=owner_headers,
                json={
                    "commandId": "thread-preference-restore-early",
                    "threadId": thread_id,
                    "expectedSessionVersion": 2,
                },
            )
            require(early.status_code == 409, "cooldown must not reopen early")
            require(route_code(early) == "ownerTruthThreadCooldownActive", "early cooldown code changed")

            expire_cooldown(test_dsn, vault_id=vault_id, thread_id=thread_id)
            restored = client.post(
                restore_cooldown_path,
                headers=owner_headers,
                json={
                    "commandId": "thread-preference-restore-elapsed",
                    "threadId": thread_id,
                    "expectedSessionVersion": 2,
                },
            )
            require(restored.status_code == 201, f"elapsed cooldown restore failed: {restored.text}")
            require(
                current_preference(test_dsn, vault_id=vault_id, thread_id=thread_id)[:2]
                == ("open", False),
                "elapsed cooldown must remain blocked until explicit restore, then reopen",
            )

            do_not_ask = client.post(
                boundary_path,
                headers=owner_headers,
                json={
                    "commandId": "thread-preference-do-not-ask-001",
                    "threadId": thread_id,
                    "expectedSessionVersion": 3,
                    "boundary": "doNotAsk",
                },
            )
            require(do_not_ask.status_code == 201, f"doNotAsk creation failed: {do_not_ask.text}")
            restore_do_not_ask = client.post(
                f"/v2/vaults/{vault_id}/interview-sessions/{session_id}/restore-do-not-ask",
                headers=owner_headers,
                json={
                    "commandId": "thread-preference-do-not-ask-restore-001",
                    "threadId": thread_id,
                    "expectedSessionVersion": 4,
                    "confirmed": True,
                },
            )
            require(
                restore_do_not_ask.status_code == 201,
                f"doNotAsk restore failed: {restore_do_not_ask.text}",
            )
            require(
                current_preference(test_dsn, vault_id=vault_id, thread_id=thread_id)[:2]
                == ("open", False),
                "confirmed doNotAsk restore must clear the same thread preference",
            )
            require(receipt_count(test_dsn) == 4, "each persistent mutation needs one receipt")

        print(
            "owner truth thread preference postgres smoke passed "
            f"schemaHead={verified['expectedHead']} defaultHidden=true serverExpiry=true "
            "deduplicated=true crossOwnerDenied=true cooldownExplicitRestore=true "
            "doNotAskConfirmedRestore=true receiptsAppendOnly=true"
        )
    finally:
        if store is not None:
            store.close_pool()
        try:
            drop_database(admin_dsn, database_name)
        except Exception as exc:  # pragma: no cover - cleanup diagnostics only
            print(f"warning: failed to drop temporary database {database_name}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
