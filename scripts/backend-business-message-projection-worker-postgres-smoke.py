#!/usr/bin/env python3
"""Exercise the default-off business-message projection worker in disposable Postgres.

Only synthetic completed receipts enter this smoke.  It proves typed request
input, one active worker lease, terminal evidence and dead-letter handling
without writing ``mailbox_letters``, exposing an inbox, or calling a provider.
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

from app.async_effects.business_message_projection_effects import BusinessMessageProjectionRequest
from app.async_effects.business_message_projection_enqueue import (
    BusinessMessageProjectionEnqueueCoordinator,
)
from app.async_effects.business_message_projection_repository import InboxAccountSnapshot
from app.async_effects.business_message_projection_worker import (
    BusinessMessageProjectionWorkerRuntime,
)
from app.async_effects.consumer_repository import AsyncEffectSyntheticConsumerCommand
from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget
from app.async_effects.message_notification_effects import (
    BusinessCompletionMessageSource,
    InAppMessageKind,
)
from app.core.config import Settings, settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.services.postgres_store import PostgresStore


LEGACY_USER_ID = "user-message-worker-smoke"
SUBJECT_ID = "owner-message-worker-smoke"
VAULT_ID = "vault-message-worker-smoke"
IDENTITY_BINDING_ID = "binding-message-worker-smoke"
IDENTITY_CHALLENGE_ID = "challenge-message-worker-smoke"
IDENTITY_PROOF_ID = "proof-message-worker-smoke"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def account_payload(
    *,
    auth_epoch: int = 4,
    access_state: str = "active",
    deletion_state: str = "active",
) -> str:
    return json.dumps(
        {
            "accessState": access_state,
            "authEpoch": auth_epoch,
            "deletionState": deletion_state,
        },
        sort_keys=True,
    )


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


def seed_verified_owner_inbox_bridge(dsn: str) -> None:
    """Seed only the read-only identity bridge required by the worker guard."""

    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO users (id, phone, nickname, payload)
                VALUES (%s, %s, %s, %s::jsonb)
                """,
                (LEGACY_USER_ID, "+8613800138111", "worker smoke", account_payload()),
            )
            cursor.execute("INSERT INTO subjects (id, status) VALUES (%s, 'active')", (SUBJECT_ID,))
            cursor.execute(
                """
                INSERT INTO owner_truth.vaults (vault_id, owner_subject_id, authority_epoch, status)
                VALUES (%s, %s, 4, 'active')
                """,
                (VAULT_ID, SUBJECT_ID),
            )
            cursor.execute(
                """
                INSERT INTO identity_hash_key_versions (version, key_fingerprint, status)
                VALUES ('v1', %s, 'active')
                """,
                (digest("message-worker-smoke-key"),),
            )
            cursor.execute(
                """
                INSERT INTO identity_bindings (
                    id, subject_id, identity_type, target_hash_key_version,
                    target_hash, provider_mode, status, verified_at
                ) VALUES (%s, %s, 'phone', 'v1', %s, 'synthetic', 'active', NOW())
                """,
                (IDENTITY_BINDING_ID, SUBJECT_ID, digest("message-worker-smoke-phone")),
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
                    digest("message-worker-smoke-phone"),
                    digest("message-worker-smoke-code"),
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
                    digest("message-worker-smoke-alias"),
                    SUBJECT_ID,
                    VAULT_ID,
                    IDENTITY_PROOF_ID,
                ),
            )


def completed_source(store: PostgresStore, *, label: str) -> BusinessCompletionMessageSource:
    intent = AsyncEffectIntent(
        operation_type="asyncEffect.synthetic.businessMessageProjectionWorker.smoke",
        target=AsyncEffectTarget(
            owner_subject_id=SUBJECT_ID,
            vault_id=VAULT_ID,
            resource_type="timeLetter",
            resource_id=f"letter-message-worker-{label}",
            resource_version=2,
            purpose="timeLetterDelivery",
            authority_epoch=4,
        ),
        payload_hash=digest(f"business-message-worker-source:{label}"),
    )
    with store.request_unit_of_work(
        correlation_id=f"business-message-worker-source-accept:{intent.operation_id}",
        command_id=f"businessMessageWorkerSourceAccept:{label}",
    ):
        store.effect_kernel_repository().accept(intent)
    with store.request_unit_of_work(
        correlation_id=f"business-message-worker-source-complete:{intent.operation_id}",
        command_id=f"businessMessageWorkerSourceComplete:{label}",
    ):
        completion = store.async_effect_consumer_repository().consume(
            AsyncEffectSyntheticConsumerCommand(
                intent=intent,
                consumer_name="smoke.businessMessageProjectionWorker",
                business_target_key=intent.business_target_key,
                outcome="completed",
                reason_code="smokeCompleted",
                result_ref_hash=digest(f"business-message-worker-result:{label}"),
            )
        )
    return BusinessCompletionMessageSource(
        intent=intent,
        completion=completion,
        message_kind=InAppMessageKind.TIME_LETTER,
    )


def enqueue(
    store: PostgresStore,
    *,
    source: BusinessCompletionMessageSource,
    max_attempts: int = 3,
) -> BusinessMessageProjectionRequest:
    request = BusinessMessageProjectionRequest(
        source=source,
        inbox_account=InboxAccountSnapshot(
            inbox_subject_id=str(source.inbox_subject_id),
            inbox_vault_id=str(source.inbox_vault_id),
            account_epoch=4,
        ),
        max_attempts=max_attempts,
    )
    with store.request_unit_of_work(
        correlation_id=f"business-message-worker-enqueue:{request.effect_intent.operation_id}",
        command_id=f"businessMessageWorkerEnqueue:{request.message_id}",
    ):
        accepted = BusinessMessageProjectionEnqueueCoordinator(store).accept(request)
    require(accepted.effect.outcome == "accepted", "first typed job must accept")
    require(accepted.input.outcome == "recorded", "first typed worker input must record")
    return request


def worker(*, store: PostgresStore, enabled: bool) -> BusinessMessageProjectionWorkerRuntime:
    return BusinessMessageProjectionWorkerRuntime(
        settings=Settings(
            store_backend="postgres",
            database_url=store.dsn or "",
            async_effect_v1_enabled=True,
            async_effect_worker_enabled=True,
            business_message_projection_worker_enabled=enabled,
        ),
        store=store,
        worker_id="business-message-projection-postgres-smoke",
        lease_seconds=10,
        retry_seconds=1,
    )


def table_count(dsn: str, table: str) -> int:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(*table.split("."))))
            return int(cursor.fetchone()[0])


def column_value(dsn: str, *, table: str, column: str, where_column: str, where_value: str) -> str:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("SELECT {} FROM {} WHERE {} = %s").format(
                    sql.Identifier(column),
                    sql.Identifier(*table.split(".")),
                    sql.Identifier(where_column),
                ),
                (where_value,),
            )
            row = cursor.fetchone()
    require(row is not None, f"{table} row is missing")
    return str(row[0])


class _FailingProjectionRepository:
    def record(self, *_args, **_kwargs):
        raise RuntimeError("business message worker smoke projection failure")


def exercise(dsn: str) -> None:
    seed_verified_owner_inbox_bridge(dsn)
    store = PostgresStore(dsn=dsn, pool_min_size=1, pool_max_size=4)
    store.open_pool(wait=True)
    try:
        require(store.readiness_probe().get("status") == "ready", "temporary schema must be ready")

        success_request = enqueue(store, source=completed_source(store, label="success"))
        disabled = worker(store=store, enabled=False).run_once()
        require(disabled["status"] == "blocked", "default-off worker must not claim a job")
        require(
            disabled["reason"] == "businessMessageProjectionWorkerDisabled",
            "default-off worker must explain its closed state",
        )

        completed = worker(store=store, enabled=True).run_once()
        require(completed["status"] == "completed", "enabled worker must consume the typed job")
        require(
            completed["messageProjectionOutcome"] == "recorded",
            "first worker projection must be durable",
        )
        require(
            column_value(
                dsn,
                table="async_effects.jobs",
                column="state",
                where_column="job_id",
                where_value=success_request.effect_intent.job_id,
            )
            == "succeeded",
            "successful worker job must be terminal",
        )
        require(
            table_count(dsn, "async_effects.business_message_projection_requests") == 1,
            "immutable worker input must persist once",
        )
        require(
            table_count(dsn, "async_effects.business_message_projections") == 1,
            "one private metadata shadow must persist",
        )
        require(table_count(dsn, "mailbox_letters") == 0, "worker must not write the public mailbox")

        failed_request = enqueue(
            store,
            source=completed_source(store, label="failed"),
            max_attempts=1,
        )
        original_projection_repository = store.async_effect_business_message_projection_repository
        store.async_effect_business_message_projection_repository = lambda: _FailingProjectionRepository()
        try:
            failed = worker(store=store, enabled=True).run_once()
        finally:
            store.async_effect_business_message_projection_repository = original_projection_repository
        require(failed["status"] == "failed", "terminal worker failure must not stay leased")
        require(
            failed["reason"] == "businessMessageProjectionRetriesExhausted",
            "terminal worker failure must retain the typed reason",
        )
        require(
            column_value(
                dsn,
                table="async_effects.jobs",
                column="state",
                where_column="job_id",
                where_value=failed_request.effect_intent.job_id,
            )
            == "failed",
            "failed worker job must be terminal",
        )
        require(
            table_count(dsn, "async_effects.dead_letters") == 1,
            "terminal worker failure must admit exactly one dead letter",
        )
        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE users SET payload = %s::jsonb WHERE id = %s",
                    (account_payload(auth_epoch=5), LEGACY_USER_ID),
                )
        rotated_request = enqueue(store, source=completed_source(store, label="rotated"))
        rotated = worker(store=store, enabled=True).run_once()
        require(rotated["status"] == "blocked", "rotated inbox snapshot must not project")
        require(
            rotated["reason"] == "businessMessageProjectionInboxSnapshotMismatch",
            "rotated inbox snapshot must retain a typed blocked reason",
        )
        require(
            column_value(
                dsn,
                table="async_effects.jobs",
                column="state",
                where_column="job_id",
                where_value=rotated_request.effect_intent.job_id,
            )
            == "blocked",
            "rotated inbox snapshot must terminally block its job",
        )
        require(
            table_count(dsn, "async_effects.business_message_projections") == 1,
            "a rejected live inbox must not add another private projection",
        )
        require(table_count(dsn, "mailbox_letters") == 0, "failure must not write the public mailbox")
    finally:
        store.close_pool()


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_business_message_worker_{uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    created = False
    try:
        create_database(admin_dsn, database_name)
        created = True
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="business-message-projection-worker-p0-s2",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        migrator.apply()
        require(migrator.verify()["status"] == "ready", "temporary schema must verify")
        exercise(test_dsn)
        print(
            "Business-message projection worker Postgres smoke passed "
            "(default-off internal shadow only; mailbox, notification and Provider remain unchanged)."
        )
    finally:
        if created:
            drop_database(admin_dsn, database_name)


if __name__ == "__main__":
    main()
