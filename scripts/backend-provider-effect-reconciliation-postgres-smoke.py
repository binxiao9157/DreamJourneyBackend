#!/usr/bin/env python3
"""Exercise default-off provider-effect reconciliation in a disposable database.

The smoke persists only deterministic identifiers and hashes.  It never calls
an external Provider, starts a worker, or enables a production runtime flag.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import os
import sys
import uuid
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget
from app.async_effects.provider_effect_repository import ProviderEffectPersistenceSummary
from app.async_effects.provider_effects import (
    ProviderEffectConflict,
    ProviderEffectIntent,
    ProviderEffectQueryOutcome,
    ProviderEffectReconciliation,
    ProviderEffectReceipt,
    ProviderEffectState,
)
from app.core.config import settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.services.postgres_store import PostgresStore


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def payload_hash(value: str) -> str:
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


def expect_rejected(dsn: str, operation, message: str) -> None:
    rejected = False
    try:
        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                operation(cursor)
    except Exception:
        rejected = True
    require(rejected, message)


def build_effect_intent(*, resource_id: str = "voice-profile-provider-smoke") -> AsyncEffectIntent:
    return AsyncEffectIntent(
        operation_type="provider.effect.reconcile.smoke",
        target=AsyncEffectTarget(
            owner_subject_id="owner-provider-smoke",
            vault_id="vault-provider-smoke",
            resource_type="voiceProfile",
            resource_id=resource_id,
            resource_version=1,
            purpose="voiceClone",
            authority_epoch=0,
        ),
        payload_hash=payload_hash(f"provider-effect-parent:{resource_id}"),
    )


def build_provider_intent(
    effect_intent: AsyncEffectIntent,
    *,
    request_material: str = "canonical-provider-request",
) -> ProviderEffectIntent:
    return ProviderEffectIntent(
        effect_intent=effect_intent,
        provider="volcengineVoiceClone",
        capability="voiceCloneTraining",
        request_hash=payload_hash(request_material),
    )


def receipt(
    intent: ProviderEffectIntent,
    state: ProviderEffectState,
    *,
    reason_code: str,
    observation_origin: str,
    attempt: int = 1,
) -> ProviderEffectReceipt:
    return ProviderEffectReceipt(
        intent=intent,
        state=state,
        reason_code=reason_code,
        observation_origin=observation_origin,
        attempt=attempt,
    )


def provider_counts(dsn: str, effect_id: str) -> tuple[int, int]:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM async_effects.provider_effects WHERE effect_id = %s",
                (effect_id,),
            )
            effect_count = int(cursor.fetchone()[0])
            cursor.execute(
                "SELECT COUNT(*) FROM async_effects.provider_receipts WHERE effect_id = %s",
                (effect_id,),
            )
            receipt_count = int(cursor.fetchone()[0])
    return effect_count, receipt_count


def provider_recorded_state(dsn: str, effect_id: str) -> str:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT state FROM async_effects.provider_effects WHERE effect_id = %s",
                (effect_id,),
            )
            row = cursor.fetchone()
    require(row is not None, "provider effect must be durable")
    return str(row[0])


def record_provider(
    store: PostgresStore,
    provider_receipt: ProviderEffectReceipt,
    *,
    command_id: str,
) -> ProviderEffectPersistenceSummary:
    with store.request_unit_of_work(
        correlation_id=f"provider-effect-smoke:{command_id}",
        command_id=command_id,
    ):
        store.effect_kernel_repository().accept(provider_receipt.intent.effect_intent)
        return store.provider_effect_repository().record(provider_receipt)


def reconcile_provider(
    store: PostgresStore,
    reconciliation: ProviderEffectReconciliation,
    *,
    command_id: str,
) -> ProviderEffectPersistenceSummary:
    with store.request_unit_of_work(
        correlation_id=f"provider-reconcile-smoke:{command_id}",
        command_id=command_id,
    ):
        return store.provider_effect_repository().reconcile(reconciliation)


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_provider_effect_smoke_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)

    store: PostgresStore | None = None
    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="provider-effect-reconcile-g2",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        applied = migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")
        require(applied["appliedVersions"][-1] == "0025", "reconciliation migration must apply")

        store = PostgresStore(dsn=test_dsn, pool_min_size=1, pool_max_size=4)
        store.open_pool(wait=True)

        effect_intent = build_effect_intent()
        provider_intent = build_provider_intent(effect_intent)
        accepted = receipt(
            provider_intent,
            ProviderEffectState.ACCEPTED,
            reason_code="providerAccepted",
            observation_origin="providerSubmission",
        )

        def accept_once(index: int) -> str:
            return record_provider(store, accepted, command_id=f"provider-effect-accept-{index}").outcome

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = set(executor.map(accept_once, (1, 2)))
        require(outcomes == {"accepted", "deduplicated"}, "same request must be idempotent")
        require(provider_counts(test_dsn, provider_intent.provider_effect_id) == (1, 1), "one effect and receipt")

        changed_request = build_provider_intent(effect_intent, request_material="changed-provider-request")
        rejected = False
        try:
            record_provider(
                store,
                receipt(
                    changed_request,
                    ProviderEffectState.ACCEPTED,
                    reason_code="providerAccepted",
                    observation_origin="providerSubmission",
                ),
                command_id="provider-effect-changed-request",
            )
        except ProviderEffectConflict:
            rejected = True
        require(rejected, "changed request hash must not reuse the stable provider effect")
        require(provider_counts(test_dsn, provider_intent.provider_effect_id) == (1, 1), "conflict must not append")

        unknown = receipt(
            provider_intent,
            ProviderEffectState.UNKNOWN,
            reason_code="providerTimeout",
            observation_origin="timeoutObservation",
            attempt=2,
        )
        timed_out = record_provider(store, unknown, command_id="provider-effect-timeout")
        require(timed_out.effect_state is ProviderEffectState.UNKNOWN, "timeout must persist unknown")
        require(timed_out.effective_state is ProviderEffectState.UNKNOWN, "unknown has no terminal projection")
        require(timed_out.reconciliation_status == "pendingReconcile", "timeout awaits reconciliation")
        require(provider_recorded_state(test_dsn, provider_intent.provider_effect_id) == "unknown", "base fact stays unknown")

        replay = record_provider(store, accepted, command_id="provider-effect-accepted-replay")
        require(replay.outcome == "deduplicated", "accepted replay after timeout must not resend")
        require(provider_counts(test_dsn, provider_intent.provider_effect_id) == (1, 2), "replay cannot add evidence")

        expect_rejected(
            test_dsn,
            lambda cursor: cursor.execute(
                "UPDATE async_effects.provider_effects SET state = 'completed' WHERE effect_id = %s",
                (provider_intent.provider_effect_id,),
            ),
            "unknown provider fact must remain terminal",
        )

        completed = ProviderEffectReconciliation(
            prior_unknown=unknown,
            outcome=ProviderEffectQueryOutcome.COMPLETED,
            query_receipt_hash=payload_hash("provider-query-completed"),
        )
        reconciled = reconcile_provider(store, completed, command_id="provider-effect-query-completed")
        require(reconciled.effect_state is ProviderEffectState.UNKNOWN, "reconciliation cannot overwrite unknown")
        require(reconciled.effective_state is ProviderEffectState.COMPLETED, "query projects completion")
        require(reconciled.reconciliation_status == "reconciledCompleted", "completion projection expected")
        duplicate = reconcile_provider(store, completed, command_id="provider-effect-query-completed-replay")
        require(duplicate.outcome == "deduplicated", "same query evidence must dedupe")
        require(provider_counts(test_dsn, provider_intent.provider_effect_id) == (1, 3), "query replay cannot add receipt")

        conflicting_failure = ProviderEffectReconciliation(
            prior_unknown=unknown,
            outcome=ProviderEffectQueryOutcome.FAILED,
            query_receipt_hash=payload_hash("provider-query-late-failed"),
        )
        conflict = reconcile_provider(store, conflicting_failure, command_id="provider-effect-query-late-failed")
        require(conflict.effect_state is ProviderEffectState.UNKNOWN, "base fact remains unknown after conflict")
        require(conflict.effective_state is ProviderEffectState.UNKNOWN, "conflicting late evidence fails closed")
        require(conflict.reconciliation_status == "reconciliationConflict", "conflict must be explicit")
        require(conflict.requires_manual_review, "conflicting evidence needs manual review")
        require(not conflict.reissue_allowed, "reconciliation never silently resends")

        rollback_effect = build_provider_intent(build_effect_intent(resource_id="voice-profile-provider-rollback"))
        try:
            with store.request_unit_of_work(
                correlation_id="provider-effect-smoke:rollback",
                command_id="provider-effect-rollback",
            ):
                store.effect_kernel_repository().accept(rollback_effect.effect_intent)
                store.provider_effect_repository().record(
                    receipt(
                        rollback_effect,
                        ProviderEffectState.UNKNOWN,
                        reason_code="providerTimeout",
                        observation_origin="timeoutObservation",
                    )
                )
                raise RuntimeError("force rollback")
        except RuntimeError:
            pass
        require(provider_counts(test_dsn, rollback_effect.provider_effect_id) == (0, 0), "rollback leaves no evidence")

        print(
            "Provider-effect reconciliation Postgres smoke passed "
            "(provider calls disabled; append-only receipt projection verified)."
        )
    finally:
        if store is not None:
            store.close_pool()
        drop_database(admin_dsn, database_name)


if __name__ == "__main__":
    main()
