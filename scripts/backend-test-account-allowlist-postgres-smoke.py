#!/usr/bin/env python3
"""Exercise the controlled test-account login lane in a disposable database."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import uuid

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.core.config import Settings
from app.services.auth_sessions import AuthSessionService
from app.services.identity_bindings import (
    IdentityBindingService,
    IdentityChallengeConfigurationError,
    UnavailableIdentityChallengeAdapter,
)
from app.services.postgres_store import PostgresStore
from app.services.realtime_voice_proxy import RealtimeVoiceSessionBroker
from app.services.test_account_allowlist import (
    TEST_ACCOUNT_PROVIDER_MODE,
    TestAccountAllowlistService,
)


HMAC_KEY = "postgres-test-account-smoke-key-" + ("x" * 40)
TARGET = "10000000001"
NORMALIZED_TARGET = "8610000000001"
TARGET_PREFIX = "8610000000"


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


def main() -> None:
    base_dsn = str(os.environ.get("DATABASE_URL") or "").strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_test_account_smoke_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    store = None

    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="test-account-allowlist-smoke",
            lock_timeout_ms=1000,
            statement_timeout_ms=30000,
        )
        applied = migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")
        require("0089" in applied["appliedVersions"], "migration 0089 must apply")
        require("0090" in applied["appliedVersions"], "migration 0090 must apply")
        require("0093" in applied["appliedVersions"], "migration 0093 must apply")
        require("0095" in applied["appliedVersions"], "migration 0095 must apply")

        store = PostgresStore(
            test_dsn,
            pool_min_size=1,
            pool_max_size=4,
        )
        store.open_pool()
        allowlist = TestAccountAllowlistService(
            store,
            hmac_key=HMAC_KEY,
            hmac_key_version="v1",
            enabled=True,
            allowed_phone_prefixes=(TARGET_PREFIX,),
            environment="smoke",
        )
        created = allowlist.create(
            target=TARGET,
            label="Postgres iPhone QA",
            actor_id="backend-service-v1",
        )["testAccount"]
        account_id = created["accountId"]
        code = created["verificationCode"]
        require(created["loginTarget"] == TARGET, "login target must be iOS-ready")

        with psycopg.connect(test_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT target_hash, code_hash, target_hint, expires_at "
                    "FROM test_account_allowlist WHERE id = %s",
                    (account_id,),
                )
                persisted = json.dumps(cursor.fetchone(), ensure_ascii=False)
        require(NORMALIZED_TARGET not in persisted, "raw target must not persist")
        require(code not in persisted, "plaintext verification code must not persist")
        require(created["expiresAt"] is None, "test account must be permanent")
        require(created["validity"] == "permanent", "validity must be permanent")

        sessions = AuthSessionService(
            store,
            access_ttl_seconds=900,
            refresh_ttl_seconds=86400,
            authorization_snapshot_resolver=(
                allowlist.authorization_snapshot_for_subject
            ),
        )
        identity = IdentityBindingService(
            store,
            hmac_key=HMAC_KEY,
            hmac_key_version="v1",
            adapter=UnavailableIdentityChallengeAdapter(),
            challenge_ttl_seconds=300,
            max_attempts=3,
            retry_after_seconds=1,
            auth_session_service=sessions,
            test_account_allowlist_service=allowlist,
            environment="smoke",
        )
        started_at = datetime.now(timezone.utc)
        first_challenge = identity.create_challenge(
            identity_type="phone",
            target=TARGET,
            purpose="login",
            now=started_at,
        )
        first_challenge_id = first_challenge["challenge"]["challengeId"]
        require(
            store.get_auth_challenge(first_challenge_id)["providerMode"]
            == TEST_ACCOUNT_PROVIDER_MODE,
            "test target must use the isolated allowlist provider mode",
        )
        first_login = identity.verify_challenge(
            first_challenge_id,
            code,
            nickname="Test Account",
            now=started_at + timedelta(seconds=1),
        )
        subject_id = first_login["subject"]["subjectId"]
        access_token = first_login["auth"]["accessToken"]
        require(bool(subject_id), "first login must auto-create an account")
        require(
            sessions.resolve_access_token(access_token) is not None,
            "issued access token must resolve",
        )
        require(
            first_login["auth"]["authorizationSnapshot"]["revision"] == 1,
            "initial test account session must carry the empty authorization revision",
        )

        realtime = RealtimeVoiceSessionBroker(
            Settings(
                store_backend="postgres",
                database_url=test_dsn,
                public_base_url="https://api.example.test/dreamjourney-api",
                volcengine_app_id="postgres-smoke-app-id",
                volcengine_app_key="postgres-smoke-app-key",
                volcengine_app_token="postgres-smoke-access-token",
                volcengine_realtime_resource_id="volc.speech.dialog",
                realtime_voice_proxy_enabled=True,
            ),
            store,
        )
        realtime_config = realtime.issue_runtime_config(
            user_id=subject_id,
            auth_session_id=first_login["auth"]["sessionId"],
        )
        require(
            realtime_config["status"] == "ready",
            "typed Subject login must issue a realtime voice ticket",
        )
        with psycopg.connect(test_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT ticket.user_id, subject.status
                    FROM realtime_voice_session_tickets AS ticket
                    JOIN subjects AS subject ON subject.id = ticket.user_id
                    WHERE ticket.ticket_hash = %s
                    """,
                    (realtime.ticket_hash(realtime_config["proxy"]["sessionToken"]),),
                )
                persisted_ticket = cursor.fetchone()
        require(
            persisted_ticket == (subject_id, "active"),
            "realtime ticket must be owned by the verified Subject",
        )
        realtime_lease = realtime.consume(realtime_config["proxy"]["sessionToken"])
        require(
            realtime_lease is not None,
            "typed Subject realtime ticket must be consumable",
        )
        require(
            realtime.is_lease_authorized(realtime_lease),
            "typed Subject realtime lease must remain authorized",
        )
        realtime.release(realtime_lease, reason="postgresSmokeComplete")

        updated_authorization = allowlist.update_authorization(
            account_id,
            test_role="ownerTest",
            feature_entitlements=["profileSettings", "familyManagement"],
            scenario_bindings={"relationshipId": "rel_smoke_001"},
            expected_entitlement_revision=1,
            actor_id="backend-service-v1",
        )["testAccount"]
        require(
            updated_authorization["entitlementRevision"] == 2,
            "authorization update must advance the revision",
        )
        require(
            sessions.resolve_access_token(access_token) is None,
            "authorization revision change must invalidate the old access token",
        )
        sessions.revoke_all_for_user(
            subject_id,
            reason="testAccountAuthorizationChanged",
        )

        second_challenge = identity.create_challenge(
            identity_type="phone",
            target=TARGET,
            purpose="login",
            now=started_at + timedelta(seconds=2),
        )
        second_login = identity.verify_challenge(
            second_challenge["challenge"]["challengeId"],
            code,
            nickname="Test Account",
            now=started_at + timedelta(seconds=3),
        )
        require(
            second_login["subject"]["subjectId"] == subject_id,
            "repeat login must resolve the same subject",
        )
        require(
            second_login["auth"]["authorizationSnapshot"]["revision"] == 2,
            "reauthenticated session must carry the current authorization revision",
        )
        require(
            store.get_test_account_allowlist(account_id)["useCount"] == 2,
            "successful logins must be counted",
        )

        disabled = allowlist.disable(
            account_id,
            actor_id="backend-service-v1",
        )
        require(disabled["testAccount"]["status"] == "disabled", "disable must persist")
        sessions.revoke_all_for_user(subject_id, reason="testAccountDisabled")
        require(
            sessions.resolve_access_token(second_login["auth"]["accessToken"]) is None,
            "disabling a bound account must support immediate session revocation",
        )
        try:
            identity.create_challenge(
                identity_type="phone",
                target=TARGET,
                purpose="login",
            )
        except IdentityChallengeConfigurationError:
            pass
        else:
            raise AssertionError("disabled test account must fail closed")

        print(
            json.dumps(
                {
                    "status": "passed",
                    "migrationHead": verified["expectedHead"],
                    "subjectReused": True,
                    "sessionRevoked": True,
                    "authorizationRevision": 2,
                    "defaultEntitlementsEmpty": True,
                    "realtimeVoiceSubjectAuthority": True,
                    "rawCredentialsPersisted": False,
                },
                sort_keys=True,
            )
        )
    finally:
        if store is not None:
            store.close_pool()
        drop_database(admin_dsn, database_name)


if __name__ == "__main__":
    main()
