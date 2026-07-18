#!/usr/bin/env python3
"""Prove terminal account purge semantics against a disposable deployed Postgres DB.

The public API is checked for readiness, then all destructive checks run in a
fresh database created from the deployed container's own migration set.  This
keeps production business data untouched while proving the deployed code,
schema, machine-only purge route, and receipt contract together.
"""

import json
import os
import secrets
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

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
from app.services.account_deletion_state import AccountDeletionStateError
from app.services.data_rights_adapter import make_account_delete_request
from app.services.postgres_store import PostgresStore


BASE_URL = os.environ.get("BACKEND_BASE_URL", "").strip().rstrip("/")
OUTPUT_PATH = os.environ.get("OUTPUT_PATH", "").strip()
MIGRATION_VERSION = "0009"


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
            "X-DreamJourney-Client-Build": "9009",
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
        "terminal purge smoke must run inside the deployed API container",
    )


def app_request(method, path, *, token, payload=None):
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "X-DreamJourney-Client-Build": "9009",
        "X-DreamJourney-Runtime-Contract-Version": "2",
    }
    # This smoke swaps main_module.store for a disposable PostgresStore.  Do
    # not use TestClient's context manager, whose shutdown closes that store.
    client = TestClient(main_module.app, raise_server_exceptions=True)
    response = client.request(method, path, headers=headers, json=payload)
    try:
        body = response.json() if response.content else {}
    except ValueError:
        body = {}
    return response.status_code, body


def seeded_user(store, *, phone_prefix, label):
    phone = f"{phone_prefix}{secrets.randbelow(100_000_000):08d}"
    user = store.upsert_user(phone=phone, nickname=f"terminal purge smoke {label}")
    return str(user["id"]), phone


def set_retention_holds(store, user_id, holds):
    with store.auth_user_operation(user_id):
        user = store.get_user(user_id)
        require(user is not None, "retention hold target must exist")
        user["retentionHolds"] = holds
        updated = store._fetchone(
            "UPDATE users SET payload = %s, updated_at = NOW() "
            "WHERE id = %s RETURNING payload",
            (user, user_id),
            commit=True,
        )
    require(updated is not None, "retention hold update must persist")


def create_delete_rights_request(store, *, user_id, phone, command_id):
    request = make_account_delete_request(
        command_id=command_id,
        subject_id=user_id,
        phone=phone,
        lifecycle_marker="0",
        now="2026-01-01T00:00:00+00:00",
    )
    created = store.create_rights_request(request)
    record = created.get("request") or {}
    request_id = str(record.get("id") or "")
    require(request_id, "terminal purge smoke must create a data-rights request")
    return request_id


def call_purge_with_server_clock(token, cutoff, payload_cutoff):
    original_cutoff = main_module._account_purge_server_cutoff
    main_module._account_purge_server_cutoff = lambda: cutoff
    try:
        return app_request(
            "POST",
            "/auth/purge-expired-deletions",
            token=token,
            payload={"cutoff": payload_cutoff, "trace": "terminalPurgeSmoke"},
        )
    finally:
        main_module._account_purge_server_cutoff = original_cutoff


def assert_append_only_receipt(store, subject_hash):
    mutation_rejected = False
    try:
        with store.request_unit_of_work(
            correlation_id=f"terminal-purge-receipt-mutation-{uuid.uuid4().hex}",
            command_id="verifyAccountPurgeReceiptAppendOnly",
        ) as unit_of_work:
            with unit_of_work.connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE account_purge_receipts SET restore_count = 99 "
                    "WHERE subject_hash = %s",
                    (subject_hash,),
                )
    except Exception:
        mutation_rejected = True
    require(mutation_rejected, "terminal purge receipt must reject mutation")


def exercise_terminal_purge(dsn):
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
        machine_token = main_module._configured_backend_api_token()
        require(machine_token, "BACKEND_API_TOKEN is required for the machine-only purge route")

        due_user_id, due_phone = seeded_user(store, phone_prefix="191", label="due")
        held_user_id, held_phone = seeded_user(store, phone_prefix="192", label="held")
        restore_user_id, restore_phone = seeded_user(
            store,
            phone_prefix="193",
            label="restore",
        )
        due_request_id = create_delete_rights_request(
            store,
            user_id=due_user_id,
            phone=due_phone,
            command_id="terminal-purge-smoke-due",
        )
        deleted_at = "2026-01-01T00:00:00+00:00"
        due = store.soft_delete_user(
            due_user_id,
            phone=due_phone,
            requested_at_iso=deleted_at,
            deletion_request_id=due_request_id,
        )
        held = store.soft_delete_user(
            held_user_id,
            phone=held_phone,
            requested_at_iso=deleted_at,
            deletion_request_id="rr_terminal_held",
        )
        restore_pending = store.soft_delete_user(
            restore_user_id,
            phone=restore_phone,
            requested_at_iso=deleted_at,
            deletion_request_id="rr_terminal_restore_one",
        )
        require(due and held and restore_pending, "test accounts must soft-delete")
        set_retention_holds(
            store,
            held_user_id,
            [{"holdId": "hold_terminal_smoke", "state": "active"}],
        )

        restored = store.restore_user(
            restore_user_id,
            phone=restore_phone,
            restored_at_iso="2026-01-31T00:00:00+00:00",
        )
        require(restored is not None, "restore at the exact deadline must succeed")
        store.soft_delete_user(
            restore_user_id,
            phone=restore_phone,
            requested_at_iso="2026-02-01T00:00:00+00:00",
            deletion_request_id="rr_terminal_restore_two",
        )
        require(
            store.restore_user(
                restore_user_id,
                phone=restore_phone,
                restored_at_iso="2026-02-02T00:00:00+00:00",
            )
            is None,
            "restore count must be enforced from persisted account state",
        )

        before_due_status, before_due = call_purge_with_server_clock(
            machine_token,
            "2026-01-15T00:00:00+00:00",
            "2099-01-01T00:00:00+00:00",
        )
        require(before_due_status == 200, "machine purge route must accept the configured token")
        require(before_due.get("cutoff") == "2026-01-15T00:00:00+00:00", "server clock must override request payload")
        require(before_due.get("cutoffSource") == "serverClock", "purge must disclose server clock source")
        require(int(before_due.get("purgedCount") or 0) == 0, "early purge must do nothing")
        require("items" not in before_due, "purge response must not expose tombstones")

        due_status, due_response = call_purge_with_server_clock(
            machine_token,
            "2026-02-01T00:00:00+00:00",
            "2000-01-01T00:00:00+00:00",
        )
        require(due_status == 200, "due purge scan must succeed")
        require(int(due_response.get("purgedCount") or 0) == 1, "only the due, unheld account may purge")
        require("items" not in due_response, "purge response must not expose tombstones")
        require(due_phone not in json.dumps(due_response), "purge response must not expose phone")
        require(due_user_id not in json.dumps(due_response), "purge response must not expose user id")

        due_tombstone = store.get_user(due_user_id) or {}
        due_receipt = store.get_account_purge_receipt(due_user_id)
        held_tombstone = store.get_user(held_user_id) or {}
        require(due_tombstone.get("deletionState") == "purged", "due account must reach terminal state")
        require(due_tombstone.get("phone") == "", "purged tombstone must clear phone")
        require(held_tombstone.get("deletionState") == "softDeleted", "active retention hold must block purge")
        require(due_receipt is not None, "terminal purge must persist a receipt")
        serialized_receipt = json.dumps(due_receipt, sort_keys=True)
        require(due_phone not in serialized_receipt, "receipt must not retain phone")
        require(due_user_id not in serialized_receipt, "receipt must not retain raw user id")
        assert_append_only_receipt(store, due_receipt["subjectHash"])

        cleanup_summary = store.summarize_rights_request(due_request_id) or {}
        cleanup_executions = {
            (str(item.get("moduleId") or ""), str(item.get("resourceType") or "")): str(
                item.get("outcome") or ""
            )
            for item in cleanup_summary.get("executions") or []
        }
        cleanup_receipts = {
            str(item.get("moduleId") or ""): str(item.get("outcome") or "")
            for item in cleanup_summary.get("receipts") or []
        }
        serialized_cleanup = json.dumps(cleanup_summary, sort_keys=True)
        require(
            cleanup_executions.get(("archive", "archiveMetadata")) == "completed",
            "terminal purge must record local archive cleanup",
        )
        require(
            cleanup_executions.get(("providerVoice", "voiceCloneAsset")) == "pending",
            "provider voice cleanup must remain pending without a provider adapter",
        )
        require(
            cleanup_executions.get(("backupRetention", "backupCopy")) == "pending",
            "backup retention cleanup must remain pending for the external operator",
        )
        require(
            cleanup_receipts.get("objectStorage") == "unsupported",
            "object storage must not be reported as application-cleaned",
        )
        require(due_phone not in serialized_cleanup, "cleanup receipts must not retain phone")
        require(due_user_id not in serialized_cleanup, "cleanup receipts must not retain raw user id")

        reactivation_blocked = False
        try:
            store.upsert_user(phone=due_phone, nickname="must not revive")
        except AccountDeletionStateError as error:
            reactivation_blocked = error.code == "accountLifecycleUpsertBlocked"
        require(reactivation_blocked, "generic upsert must not revive a purged account")

        set_retention_holds(
            store,
            held_user_id,
            [{"holdId": "hold_terminal_smoke", "state": "released"}],
        )
        released_status, released_response = call_purge_with_server_clock(
            machine_token,
            "2026-02-01T00:00:00+00:00",
            "2099-01-01T00:00:00+00:00",
        )
        require(released_status == 200, "released hold purge scan must succeed")
        require(int(released_response.get("purgedCount") or 0) == 1, "released hold must permit due purge")
        require(
            (store.get_user(held_user_id) or {}).get("deletionState") == "purged",
            "released hold account must reach terminal state",
        )

        repeated_status, repeated_response = call_purge_with_server_clock(
            machine_token,
            "2026-02-01T00:00:00+00:00",
            "2099-01-01T00:00:00+00:00",
        )
        require(repeated_status == 200, "repeat purge scan must succeed")
        require(int(repeated_response.get("purgedCount") or 0) == 0, "terminal purge must be idempotent")

        return {
            "serverClockEnforced": True,
            "exactDeadlineRestoreAllowed": True,
            "restoreLimitPersisted": True,
            "retentionHoldBlocked": True,
            "releasedHoldPurged": True,
            "terminalReceiptRedacted": True,
            "terminalReceiptAppendOnly": True,
            "moduleCleanupReceiptsRecorded": True,
            "externalCleanupBoundaryPreserved": True,
            "purgedAccountCannotReactivate": True,
            "repeatPurgeNoop": True,
        }
    finally:
        main_module.store = previous_store
        store.close_pool()


def main():
    assert_deployed_container_context()
    assert_deployed_readiness()
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    admin_dsn = database_dsn(base_dsn, "postgres")
    database_name = f"dj_terminal_purge_smoke_{uuid.uuid4().hex[:12]}"
    temporary_dsn = database_dsn(base_dsn, database_name)

    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=temporary_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="account-terminal-purge-g2",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        applied = migrator.apply()
        verified = migrator.verify()
        require(verified.get("status") == "ready", "temporary schema is not ready")
        require(
            verified.get("expectedHead") == MIGRATION_VERSION,
            "temporary schema must include migration 0009",
        )
        require(
            MIGRATION_VERSION in (applied.get("appliedVersions") or []),
            "temporary database must apply migration 0009",
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
            **exercise_terminal_purge(temporary_dsn),
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
