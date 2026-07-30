#!/usr/bin/env python3
"""Exercise the read-only legacy inbox bridge in a disposable Postgres DB.

The bridge is intentionally only an internal migration boundary.  This smoke
uses synthetic identity records to prove that an already verified active
bridge can be resolved, while suspended/deleted accounts and mutable bridge
coordinates fail closed.  It never creates a public mailbox entry, a family
grant, a session, a notification, or a Provider effect.
"""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.async_effects.legacy_identity_inbox_bridge import (
    LegacyInboxAccountResolutionError,
)
from app.core.config import settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.services.postgres_store import PostgresStore


LEGACY_USER_ID = "user-legacy-inbox-smoke"
SUBJECT_ID = "subject-legacy-inbox-smoke"
VAULT_ID = "vault-legacy-inbox-smoke"
MUTATED_VAULT_ID = "vault-legacy-inbox-mutated"
IDENTITY_BINDING_ID = "binding-legacy-inbox-smoke"
IDENTITY_CHALLENGE_ID = "challenge-legacy-inbox-smoke"
IDENTITY_PROOF_ID = "proof-legacy-inbox-smoke"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


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


def account_payload(*, access_state: str = "active", deletion_state: str = "active") -> str:
    return json.dumps(
        {
            "accessState": access_state,
            "authEpoch": 7,
            "deletionState": deletion_state,
        },
        sort_keys=True,
    )


def seed_verified_bridge(dsn: str) -> None:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO users (id, phone, nickname, payload)
                VALUES (%s, %s, %s, %s::jsonb)
                """,
                (LEGACY_USER_ID, "+8613800138000", "bridge smoke", account_payload()),
            )
            cursor.execute(
                "INSERT INTO subjects (id, status) VALUES (%s, 'active')",
                (SUBJECT_ID,),
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.vaults (vault_id, owner_subject_id, authority_epoch, status)
                VALUES (%s, %s, 7, 'active')
                """,
                (VAULT_ID, SUBJECT_ID),
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.vaults (vault_id, owner_subject_id, authority_epoch, status)
                VALUES (%s, %s, 7, 'active')
                """,
                (MUTATED_VAULT_ID, SUBJECT_ID),
            )
            cursor.execute(
                """
                INSERT INTO identity_hash_key_versions (version, key_fingerprint, status)
                VALUES ('v1', %s, 'active')
                """,
                (digest("legacy-inbox-bridge-smoke-key"),),
            )
            cursor.execute(
                """
                INSERT INTO identity_bindings (
                    id, subject_id, identity_type, target_hash_key_version,
                    target_hash, provider_mode, status, verified_at
                ) VALUES (%s, %s, 'phone', 'v1', %s, 'synthetic', 'active', NOW())
                """,
                (IDENTITY_BINDING_ID, SUBJECT_ID, digest("legacy-inbox-bridge-smoke-phone")),
            )
            cursor.execute(
                """
                INSERT INTO auth_challenges (
                    id, identity_type, target_hash_key_version, target_hash, code_hash,
                    provider_mode, purpose, status, attempts, max_attempts,
                    internal_verification_enabled, expires_at
                ) VALUES (%s, 'phone', 'v1', %s, %s, 'synthetic', 'login', 'consumed', 1, 3, true,
                          NOW() + INTERVAL '1 hour')
                """,
                (
                    IDENTITY_CHALLENGE_ID,
                    digest("legacy-inbox-bridge-smoke-phone"),
                    digest("legacy-inbox-bridge-smoke-code"),
                ),
            )
            cursor.execute(
                """
                INSERT INTO identity_proofs (
                    id, challenge_id, binding_id, subject_id, provider_mode, verified_at
                ) VALUES (%s, %s, %s, %s, 'synthetic', NOW())
                """,
                (IDENTITY_PROOF_ID, IDENTITY_CHALLENGE_ID, IDENTITY_BINDING_ID, SUBJECT_ID),
            )
            cursor.execute(
                """
                INSERT INTO legacy_identity_aliases (
                    legacy_account_user_id, legacy_alias_hash, subject_id, vault_id,
                    claim_state, identity_proof_id, reason_code, claimed_at
                ) VALUES (%s, %s, %s, %s, 'verified', %s, 'smokeVerifiedBridge', NOW())
                """,
                (
                    LEGACY_USER_ID,
                    digest("legacy-inbox-bridge-smoke-alias"),
                    SUBJECT_ID,
                    VAULT_ID,
                    IDENTITY_PROOF_ID,
                ),
            )


def resolve(store: PostgresStore):
    with store.request_unit_of_work(
        correlation_id="legacy-inbox-bridge-smoke-resolve",
        command_id="legacyInboxBridgeResolve",
    ):
        return store.async_effect_legacy_inbox_account_resolver().resolve_active(SUBJECT_ID)


def assert_fails_closed(store: PostgresStore, *, expected_reason: str) -> None:
    try:
        resolve(store)
    except LegacyInboxAccountResolutionError as exc:
        require(expected_reason in str(exc), f"expected {expected_reason} failure reason")
    else:
        raise AssertionError("inactive legacy inbox bridge must fail closed")


def assert_immutable_coordinates(dsn: str) -> None:
    try:
        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE legacy_identity_aliases
                    SET vault_id = %s, row_version = row_version + 1
                    WHERE subject_id = %s
                    """,
                    (MUTATED_VAULT_ID, SUBJECT_ID),
                )
        raise AssertionError("legacy bridge accepted an immutable coordinate mutation")
    except psycopg.Error:
        pass


def table_count(dsn: str, table: str) -> int:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(*table.split("."))))
            return int(cursor.fetchone()[0])


def exercise(dsn: str) -> None:
    seed_verified_bridge(dsn)
    store = PostgresStore(dsn=dsn, pool_min_size=1, pool_max_size=2)
    store.open_pool(wait=True)
    try:
        resolved = resolve(store)
        require(resolved.snapshot.inbox_subject_id == SUBJECT_ID, "subject must resolve")
        require(resolved.snapshot.inbox_vault_id == VAULT_ID, "vault must resolve")
        require(resolved.snapshot.account_epoch == 7, "account epoch must resolve")
        summary = json.dumps(resolved.value_free_summary(), sort_keys=True)
        for raw_value in (LEGACY_USER_ID, SUBJECT_ID, VAULT_ID):
            require(raw_value not in summary, "value-free resolver summary leaked an identifier")

        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE users SET payload = %s::jsonb WHERE id = %s",
                    (account_payload(access_state="suspended_restorable"), LEGACY_USER_ID),
                )
        assert_fails_closed(store, expected_reason="accountAccessNotActive")

        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE users SET payload = %s::jsonb WHERE id = %s",
                    (account_payload(deletion_state="softDeleted"), LEGACY_USER_ID),
                )
        assert_fails_closed(store, expected_reason="accountDeletionNotActive")
        assert_immutable_coordinates(dsn)
    finally:
        store.close_pool()

    require(table_count(dsn, "legacy_identity_aliases") == 1, "bridge must remain one row")
    require(table_count(dsn, "mailbox_letters") == 0, "bridge must not write public mailbox")
    require(
        table_count(dsn, "async_effects.business_message_projections") == 0,
        "bridge must not write business message projections",
    )


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_legacy_inbox_bridge_{uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    created = False
    try:
        create_database(admin_dsn, database_name)
        created = True
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="legacy-identity-inbox-bridge-g2",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        migrator.apply()
        require(migrator.verify()["status"] == "ready", "temporary schema must verify")
        exercise(test_dsn)
        print(
            "Legacy identity inbox bridge Postgres smoke passed "
            "(read-only bridge only; no mailbox, worker, notification, session, or Provider effect)."
        )
    finally:
        if created:
            drop_database(admin_dsn, database_name)


if __name__ == "__main__":
    main()
