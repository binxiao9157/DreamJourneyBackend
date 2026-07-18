#!/usr/bin/env python3
"""Verify access-first account suspension against a disposable deployed Postgres DB.

The deployed API is checked for readiness first. The destructive route exercise
then runs inside the deployed container against a fresh, migrated database, so
the production business database is never seeded or mutated by this smoke.
"""

import argparse
import json
import os
import secrets
import sys
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, BrokenBarrierError

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app import main as main_module
from app.core.config import settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.services.auth_sessions import AuthSessionService
from app.services.postgres_store import PostgresStore


BASE_URL = os.environ.get("BACKEND_BASE_URL", "").strip().rstrip("/")
OUTPUT_PATH = os.environ.get("OUTPUT_PATH", "").strip()
MIGRATION_VERSION = "0008"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def database_dsn(base_dsn, database_name):
    parameters = conninfo_to_dict(base_dsn)
    parameters["dbname"] = database_name
    return make_conninfo(**parameters)


def create_database(admin_dsn, database_name):
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name))
            )


def drop_database(admin_dsn, database_name):
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


def deployed_json(path):
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        headers={
            "Accept": "application/json",
            "X-DreamJourney-Client-Build": "9008",
            "X-DreamJourney-Runtime-Contract-Version": "2",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = response.status
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        status = error.code
        body = error.read().decode("utf-8", errors="replace")
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError as error:
        raise AssertionError(f"GET {path} returned non-JSON") from error
    require(status == 200, f"GET {path} expected 200, got {status}")
    return payload


def assert_deployed_readiness():
    require(BASE_URL, "BACKEND_BASE_URL is required")
    readiness = deployed_json("/ready")
    require(readiness.get("status") == "ready", "deployed API is not ready")
    components = {
        str(item.get("component") or ""): str(item.get("status") or "")
        for item in readiness.get("components") or []
        if isinstance(item, dict)
    }
    require(components.get("database") == "ready", "deployed database is not ready")
    require(components.get("schema") == "ready", "deployed schema is not ready")


def assert_deployed_container_context():
    require(
        os.environ.get("DREAMJOURNEY_DEPLOYED_CONTAINER_SMOKE") == "1",
        "DREAMJOURNEY_DEPLOYED_CONTAINER_SMOKE=1 is required",
    )
    require(
        any(path.exists() for path in (Path("/.dockerenv"), Path("/run/.containerenv"))),
        "access-first suspend smoke must run inside the deployed API container",
    )


def app_request(method, path, *, token=None, payload=None):
    headers = {
        "Accept": "application/json",
        "X-DreamJourney-Client-Build": "9008",
        "X-DreamJourney-Runtime-Contract-Version": "2",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    # Do not use a TestClient context manager: this smoke shares one temporary
    # PostgresStore across route calls and concurrent workers.
    client = TestClient(main_module.app, raise_server_exceptions=True)
    response = client.request(method, path, headers=headers, json=payload)
    try:
        body = response.json() if response.content else {}
    except ValueError:
        body = {}
    return response.status_code, body


def issue_session(store, user_id):
    return AuthSessionService(
        store,
        access_ttl_seconds=300,
        refresh_ttl_seconds=900,
    ).issue(user_id)


def seeded_user(store, suffix):
    phone = f"198{secrets.randbelow(100_000_000):08d}"
    user = store.upsert_user(phone=phone, nickname=f"access-first smoke {suffix}")
    return str(user["id"]), phone


def delete_payload(phone, command_id):
    return {
        "phone": phone,
        "commandId": command_id,
        "firstConfirmation": True,
        "secondConfirmation": True,
    }


def exercise_access_first_suspend(dsn):
    store = PostgresStore(
        dsn=dsn,
        pool_min_size=1,
        pool_max_size=6,
        pool_timeout_seconds=4.0,
    )
    store.open_pool(wait=True)
    previous_store = main_module.store
    main_module.store = store
    try:
        suffix = secrets.token_hex(8)
        user_id, phone = seeded_user(store, suffix)
        first_session = issue_session(store, user_id)
        second_session = issue_session(store, user_id)
        command_id = f"access-first-suspend-{suffix}"
        payload = delete_payload(phone, command_id)

        # Both requests must pass authentication before either can suspend the
        # account; the route lock then proves duplicate commands share one row.
        original_owned_payload = main_module._principal_owned_payload
        authorization_barrier = Barrier(2)

        def synchronized_owned_payload(*args, **kwargs):
            result = original_owned_payload(*args, **kwargs)
            try:
                authorization_barrier.wait(timeout=10)
            except BrokenBarrierError as error:
                raise AssertionError(
                    "duplicate deletion requests did not both finish authentication"
                ) from error
            return result

        main_module._principal_owned_payload = synchronized_owned_payload
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                responses = list(
                    executor.map(
                        lambda _: app_request(
                            "POST",
                            "/auth/delete",
                            token=first_session["accessToken"],
                            payload=payload,
                        ),
                        range(2),
                    )
                )
        finally:
            main_module._principal_owned_payload = original_owned_payload

        require(
            all(status == 200 for status, _ in responses),
            "duplicate account deletion requests must succeed",
        )
        bodies = [body for _, body in responses]
        require(
            all(body.get("status") == "softDeleted" for body in bodies),
            "deletion response must report softDeleted",
        )
        require(
            all(
                (body.get("deletion") or {}).get("accessState")
                == "suspended_restorable"
                for body in bodies
            ),
            "deletion must report suspended_restorable access state",
        )
        require(
            all(int((body.get("deletion") or {}).get("authEpoch") or 0) == 1 for body in bodies),
            "duplicate deletion must retain one advanced auth epoch",
        )
        require(
            all(
                (body.get("deletion") or {}).get("providerCapabilityState") == "revoked"
                for body in bodies
            ),
            "deletion must revoke provider capability",
        )

        rights = [body.get("rights") or {} for body in bodies]
        request_ids = {str(item.get("requestId") or "") for item in rights}
        require(
            len(request_ids) == 1 and "" not in request_ids,
            "duplicate command must use one rights request",
        )
        request_id = next(iter(request_ids))
        access_revocations = [body.get("accessRevocation") or {} for body in bodies]
        require(
            {str(item.get("eventType") or "") for item in access_revocations}
            == {"RightsAccessRevoked"},
            "deletion must return RightsAccessRevoked",
        )
        require(
            {str(item.get("status") or "") for item in access_revocations} == {"pending"},
            "access revocation must remain pending for the sidecar consumer",
        )
        require(
            {int(item.get("authEpoch") or 0) for item in access_revocations} == {1},
            "access revocation must carry the suspended account epoch",
        )
        require(
            {str(item.get("providerCapabilityState") or "") for item in access_revocations}
            == {"revoked"},
            "access revocation must carry provider capability revocation",
        )
        require(
            {str(item.get("outcome") or "") for item in access_revocations}
            == {"recorded", "deduplicated"},
            "duplicate command must write one outbox event",
        )
        require(
            all(
                (body.get("sessionRevocation") or {}).get("scope") == "allDevices"
                for body in bodies
            ),
            "account deletion must revoke all device sessions",
        )

        account = store.get_user(user_id) or {}
        require(account.get("deletionState") == "softDeleted", "account must be soft-deleted")
        require(account.get("accessState") == "suspended_restorable", "account access must suspend")
        require(int(account.get("authEpoch") or 0) == 1, "account auth epoch must advance")
        require(
            account.get("providerCapabilityState") == "revoked",
            "account provider capability must be revoked",
        )
        outbox = store.list_rights_access_revocation_outbox(request_id)
        require(len(outbox) == 1, "duplicate command must persist exactly one outbox event")
        require(outbox[0].get("eventType") == "RightsAccessRevoked", "outbox event type mismatch")
        require(int(outbox[0].get("authEpoch") or 0) == 1, "outbox auth epoch mismatch")
        require(
            outbox[0].get("providerCapabilityState") == "revoked",
            "outbox provider capability state mismatch",
        )

        old_access_status, _ = app_request(
            "POST",
            "/auth/logout",
            token=first_session["accessToken"],
            payload={"scope": "session"},
        )
        require(old_access_status == 401, "old access token must be rejected after suspension")
        old_refresh_status, old_refresh_body = app_request(
            "POST",
            "/auth/refresh",
            payload={"refreshToken": second_session["refreshToken"]},
        )
        require(old_refresh_status == 401, "old refresh token must be rejected after suspension")
        refresh_detail = old_refresh_body.get("detail") or {}
        require(
            refresh_detail.get("code") == "account_session_revoked",
            "suspended refresh must report account_session_revoked",
        )

        return {
            "accessState": account["accessState"],
            "authEpoch": account["authEpoch"],
            "providerCapabilityState": account["providerCapabilityState"],
            "accessRevocationEvent": outbox[0]["eventType"],
            "duplicateCommandOutboxCount": len(outbox),
            "oldAccessRejected": True,
            "oldRefreshRejected": True,
        }
    finally:
        main_module.store = previous_store
        store.close_pool()


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Run the access-first suspension smoke inside a deployed API container "
            "against a disposable Postgres database."
        )
    )
    return parser.parse_args()


def main():
    parse_args()
    assert_deployed_container_context()
    assert_deployed_readiness()
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    admin_dsn = database_dsn(base_dsn, "postgres")
    database_name = f"dj_access_first_smoke_{uuid.uuid4().hex[:12]}"
    temporary_dsn = database_dsn(base_dsn, database_name)

    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=temporary_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="access-first-suspend-g2",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        applied = migrator.apply()
        verified = migrator.verify()
        require(verified.get("status") == "ready", "temporary schema is not ready")
        require(
            verified.get("expectedHead") == MIGRATION_VERSION,
            "temporary schema must include migration 0008",
        )
        require(
            MIGRATION_VERSION in (applied.get("appliedVersions") or []),
            "temporary database must apply migration 0008",
        )
        result = {
            "status": "passed",
            "schemaVersion": 1,
            "deployedReadiness": True,
            "deployedContainer": True,
            "temporaryDatabase": True,
            "productionBusinessDataMutated": False,
            "migrationHead": verified.get("expectedHead"),
            "appliedMigrationCount": len(applied.get("appliedVersions") or []),
            **exercise_access_first_suspend(temporary_dsn),
        }
        serialized = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if OUTPUT_PATH:
            output = Path(OUTPUT_PATH)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(serialized, encoding="utf-8")
            output.chmod(0o600)
        print(serialized, end="")
    finally:
        drop_database(admin_dsn, database_name)


if __name__ == "__main__":
    main()
