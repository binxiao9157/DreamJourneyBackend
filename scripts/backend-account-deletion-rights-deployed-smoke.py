#!/usr/bin/env python3
"""Exercise the account-deletion rights lifecycle against a disposable Postgres DB.

The deployed API is checked first for readiness. The destructive lifecycle then
runs through the deployed code's FastAPI routes against a freshly migrated,
temporary database. This avoids leaving append-only deletion receipts in the
production business database while still proving the production migration and
route contracts together.
"""

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
from app.services.data_rights_adapter import make_account_delete_request
from app.services.postgres_store import PostgresStore


BASE_URL = os.environ.get("BACKEND_BASE_URL", "").strip().rstrip("/")
OUTPUT_PATH = os.environ.get("OUTPUT_PATH", "").strip()


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
            "X-DreamJourney-Client-Build": "9007",
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
    ready = deployed_json("/ready")
    require(ready.get("status") == "ready", "deployed API is not ready")
    components = {
        str(item.get("component") or ""): str(item.get("status") or "")
        for item in ready.get("components") or []
        if isinstance(item, dict)
    }
    require(components.get("database") == "ready", "deployed database is not ready")
    require(components.get("schema") == "ready", "deployed schema is not ready")

    runtime = deployed_json("/config/runtime")
    auth = runtime.get("auth") or {}
    route_authentication = auth.get("routeAuthentication") or {}
    require(
        route_authentication.get("mode") == "enforce",
        "deployed route authentication must enforce",
    )


def assert_deployed_container_context():
    require(
        os.environ.get("DREAMJOURNEY_DEPLOYED_CONTAINER_SMOKE") == "1",
        "DREAMJOURNEY_DEPLOYED_CONTAINER_SMOKE=1 is required",
    )
    require(
        any(path.exists() for path in (Path("/.dockerenv"), Path("/run/.containerenv"))),
        "rights lifecycle smoke must run inside the deployed API container",
    )


def app_request(method, path, *, token, payload=None, raise_server_exceptions=True):
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "X-DreamJourney-Client-Build": "9007",
        "X-DreamJourney-Runtime-Contract-Version": "2",
    }
    # Do not use a TestClient context manager here. Its shutdown hook closes
    # main_module.store, while this smoke deliberately shares one temporary
    # PostgresStore across multiple route requests and concurrent workers.
    client = TestClient(
        main_module.app,
        raise_server_exceptions=raise_server_exceptions,
    )
    response = client.request(method, path, headers=headers, json=payload)
    try:
        body = response.json() if response.content else {}
    except ValueError:
        body = {}
    return response.status_code, body, {key.lower(): value for key, value in response.headers.items()}


def seeded_user(store, *, phone_prefix, label):
    phone = f"{phone_prefix}{secrets.randbelow(100_000_000):08d}"
    user = store.upsert_user(phone=phone, nickname=f"rights smoke {label}")
    return str(user["id"]), phone


def issue_access_token(store, user_id):
    return AuthSessionService(
        store,
        access_ttl_seconds=300,
        refresh_ttl_seconds=900,
    ).issue(user_id)["accessToken"]


def delete_payload(phone, command_id, *, scope=None, asserted_user_id=None):
    payload = {
        "phone": phone,
        "commandId": command_id,
        "firstConfirmation": True,
        "secondConfirmation": True,
    }
    if scope is not None:
        payload["rightsScope"] = scope
    if asserted_user_id is not None:
        payload["userId"] = asserted_user_id
    return payload


def require_detail_code(body, expected, message):
    detail = body.get("detail") if isinstance(body, dict) else None
    actual = detail.get("code") if isinstance(detail, dict) else None
    require(actual == expected, f"{message} (expected={expected}, actual={actual})")


class RollbackProbe(RuntimeError):
    pass


def exercise_lifecycle(dsn):
    store = PostgresStore(
        dsn=dsn,
        pool_min_size=1,
        pool_max_size=6,
        pool_timeout_seconds=4.0,
    )
    store.open_pool(wait=True)
    previous_store = main_module.store
    restarted_store = None
    main_module.store = store
    try:
        suffix = secrets.token_hex(8)

        concurrent_user_id, concurrent_phone = seeded_user(
            store, phone_prefix="193", label="concurrent"
        )
        concurrent_token = issue_access_token(store, concurrent_user_id)
        command_id = f"rights-concurrent-{suffix}"
        concurrent_payload = delete_payload(concurrent_phone, command_id)

        original_owned_payload = main_module._principal_owned_payload
        authorization_barrier = Barrier(2)

        def synchronized_owned_payload(*args, **kwargs):
            result = original_owned_payload(*args, **kwargs)
            try:
                authorization_barrier.wait(timeout=10)
            except BrokenBarrierError as error:
                raise AssertionError(
                    "concurrent deletion requests did not both complete authorization"
                ) from error
            return result

        main_module._principal_owned_payload = synchronized_owned_payload
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                concurrent_results = list(
                    executor.map(
                        lambda _: app_request(
                            "POST",
                            "/auth/delete",
                            token=concurrent_token,
                            payload=concurrent_payload,
                        ),
                        range(2),
                    )
                )
        finally:
            main_module._principal_owned_payload = original_owned_payload
        require(
            all(status == 200 for status, _, _ in concurrent_results),
            "concurrent same-command deletion must succeed",
        )
        rights_results = [body.get("rights") or {} for _, body, _ in concurrent_results]
        request_ids = {str(item.get("requestId") or "") for item in rights_results}
        outcomes = {str(item.get("outcome") or "") for item in rights_results}
        require(len(request_ids) == 1 and "" not in request_ids, "same command must share one rights request")
        require(outcomes == {"recorded", "deduplicated"}, "same command must record then deduplicate")
        require(
            all(item.get("status") == "completed" for item in rights_results),
            "concurrent deletion rights request must complete",
        )
        require(
            all(int(item.get("executionCount") or 0) == 1 for item in rights_results)
            and all(int(item.get("receiptCount") or 0) == 1 for item in rights_results),
            "same command must keep one execution and one receipt",
        )
        request_id = next(iter(request_ids))
        summary = store.summarize_rights_request(request_id)
        require(summary is not None, "rights summary must persist")
        require(len(summary.get("executions") or []) == 1, "one persisted execution")
        require(len(summary.get("receipts") or []) == 1, "one persisted receipt")
        serialized = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        require(concurrent_phone not in serialized, "rights summary leaked phone")
        require(command_id not in serialized, "rights summary leaked command id")
        machine_token = main_module._configured_backend_api_token()
        require(machine_token, "deployed container must configure BACKEND_API_TOKEN")
        evidence_status, evidence_body, evidence_headers = app_request(
            "GET",
            f"/ops/data-rights/requests/{request_id}/evidence",
            token=machine_token,
        )
        require(evidence_status == 200, "rights evidence report must be readable by machine principal")
        require(
            evidence_headers.get("cache-control") == "no-store",
            "rights evidence report must disable caching",
        )
        require(
            str((evidence_body.get("accessRevocation") or {}).get("status") or "")
            == "revoked",
            "rights evidence report must separate recorded access revocation",
        )
        require(
            str((evidence_body.get("physicalCleanup") or {}).get("status") or "")
            == "completed",
            "rights evidence report must require the terminal cleanup receipt",
        )
        evidence_serialized = json.dumps(evidence_body, ensure_ascii=False, sort_keys=True)
        require(concurrent_phone not in evidence_serialized, "rights evidence report leaked phone")
        require(command_id not in evidence_serialized, "rights evidence report leaked command id")

        conflict_user_id, conflict_phone = seeded_user(
            store, phone_prefix="194", label="conflict"
        )
        conflict_token = issue_access_token(store, conflict_user_id)
        conflict_command = f"rights-conflict-{suffix}"
        seeded_request = make_account_delete_request(
            command_id=conflict_command,
            subject_id=conflict_user_id,
            phone=conflict_phone,
            lifecycle_marker="0",
            scope=["account", "archive"],
        )
        seeded = store.create_rights_request(seeded_request)
        require(seeded.get("outcome") == "created", "conflict fixture must be created")
        status, body, _ = app_request(
            "POST",
            "/auth/delete",
            token=conflict_token,
            payload=delete_payload(
                conflict_phone,
                conflict_command,
                scope=["account", "voice"],
            ),
        )
        require(status == 409, "changed scope must conflict")
        require_detail_code(body, "rightsCommandConflict", "changed scope conflict contract")
        require(
            str((store.get_user(conflict_user_id) or {}).get("deletionState") or "active")
            == "active",
            "conflicting request must not soft-delete the account",
        )
        conflict_summary = store.summarize_rights_request(seeded_request.request_id)
        require(conflict_summary is not None, "conflict fixture request must remain")
        require(
            conflict_summary["request"].get("status") == "requested"
            and not conflict_summary.get("executions")
            and not conflict_summary.get("receipts"),
            "conflicting request must not append execution or receipt",
        )
        require(
            AuthSessionService(
                store,
                access_ttl_seconds=300,
                refresh_ttl_seconds=900,
            ).resolve_access_token(conflict_token)
            is not None,
            "conflicting request must not revoke the owner session",
        )

        rollback_user_id, rollback_phone = seeded_user(
            store, phone_prefix="195", label="rollback"
        )
        rollback_request = make_account_delete_request(
            command_id=f"rights-rollback-{suffix}",
            subject_id=rollback_user_id,
            phone=rollback_phone,
            lifecycle_marker="0",
        )
        rollback_token = issue_access_token(store, rollback_user_id)
        before_rollback_metrics = store.uow_metrics()
        original_completion = main_module._record_account_delete_rights_completion

        def fail_completion(*args, **kwargs):
            raise RollbackProbe("intentional failure after account-delete side effects")

        main_module._record_account_delete_rights_completion = fail_completion
        try:
            status, _, _ = app_request(
                "POST",
                "/auth/delete",
                token=rollback_token,
                payload=delete_payload(
                    rollback_phone,
                    f"rights-rollback-{suffix}",
                ),
                raise_server_exceptions=False,
            )
        finally:
            main_module._record_account_delete_rights_completion = original_completion
        require(status == 500, "injected receipt failure must surface as server error")
        require(
            str((store.get_user(rollback_user_id) or {}).get("deletionState") or "active")
            == "active",
            "transaction rollback must keep the account active",
        )
        require(
            store.summarize_rights_request(rollback_request.request_id) is None,
            "transaction rollback must remove the rights request",
        )
        require(
            AuthSessionService(
                store,
                access_ttl_seconds=300,
                refresh_ttl_seconds=900,
            ).resolve_access_token(rollback_token)
            is not None,
            "transaction rollback must keep the original session active",
        )
        after_rollback_metrics = store.uow_metrics()
        require(
            int(after_rollback_metrics.get("rolledBack") or 0)
            >= int(before_rollback_metrics.get("rolledBack") or 0) + 1,
            "injected route failure must roll back the database unit of work",
        )

        owner_id, owner_phone = seeded_user(store, phone_prefix="196", label="owner")
        attacker_id, _ = seeded_user(store, phone_prefix="197", label="attacker")
        owner_token = issue_access_token(store, owner_id)
        attacker_token = issue_access_token(store, attacker_id)
        cross_account_command = f"rights-cross-account-{suffix}"
        cross_account_request = make_account_delete_request(
            command_id=cross_account_command,
            subject_id=owner_id,
            phone=owner_phone,
            lifecycle_marker="0",
        )
        status, _, authorization_headers = app_request(
            "POST",
            "/auth/delete",
            token=attacker_token,
            payload=delete_payload(
                owner_phone,
                cross_account_command,
                asserted_user_id=owner_id,
            ),
        )
        require(status == 403, "cross-account deletion must be denied")
        require(
            authorization_headers.get("x-dreamjourney-authorization-decision")
            in {"deny", "denyClaimMismatch"},
            "cross-account deletion must report an authorization denial",
        )
        require(
            str((store.get_user(owner_id) or {}).get("deletionState") or "active")
            == "active",
            "cross-account denial must not mutate the owner account",
        )
        require(
            store.summarize_rights_request(cross_account_request.request_id) is None,
            "cross-account denial must not create a rights request",
        )
        auth_service = AuthSessionService(
            store,
            access_ttl_seconds=300,
            refresh_ttl_seconds=900,
        )
        require(
            auth_service.resolve_access_token(owner_token) is not None
            and auth_service.resolve_access_token(attacker_token) is not None,
            "cross-account denial must not revoke either session",
        )

        store.close_pool()
        restarted_store = PostgresStore(
            dsn=dsn,
            pool_min_size=1,
            pool_max_size=2,
            pool_timeout_seconds=4.0,
        )
        restarted_store.open_pool(wait=True)
        main_module.store = restarted_store
        restarted_summary = restarted_store.summarize_rights_request(request_id)
        require(
            restarted_summary is not None,
            "rights summary must survive store reconstruction",
        )
        require(
            len(restarted_summary.get("executions") or []) == 1
            and len(restarted_summary.get("receipts") or []) == 1,
            "store reconstruction must preserve execution and receipt counts",
        )

        return {
            "concurrentSameCommandDeduplicated": True,
            "rightsEvidenceProjectionVerified": True,
            "commandConflictRejected": True,
            "rollbackRestoredActiveState": True,
            "crossAccountDenied": True,
            "storeReconstructionPersistence": True,
        }
    finally:
        main_module.store = previous_store
        if restarted_store is not None:
            restarted_store.close_pool()
        else:
            store.close_pool()


def main():
    assert_deployed_container_context()
    assert_deployed_readiness()
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    admin_dsn = database_dsn(base_dsn, "postgres")
    database_name = f"dj_rights_smoke_{uuid.uuid4().hex[:12]}"
    temporary_dsn = database_dsn(base_dsn, database_name)

    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=temporary_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="rights-lifecycle-g2",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        applied = migrator.apply()
        verified = migrator.verify()
        require(verified.get("status") == "ready", "temporary schema is not ready")
        lifecycle = exercise_lifecycle(temporary_dsn)
        result = {
            "status": "passed",
            "schemaVersion": 1,
            "deployedReadiness": True,
            "deployedContainer": True,
            "temporaryDatabase": True,
            "productionBusinessDataMutated": False,
            "migrationHead": verified.get("expectedHead"),
            "appliedMigrationCount": len(applied.get("appliedVersions") or []),
            **lifecycle,
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
