#!/usr/bin/env python3
"""Exercise read-only Provider-query operations reporting in disposable Postgres.

The smoke records only synthetic, value-free Provider receipt evidence.  It
never supplies a credential, calls a Provider, replays a job, or writes a
reconciliation result.  The final snapshot proves the operations baseline is
strictly read-only relative to Provider-effect rows.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import os
from pathlib import Path
import sys
import uuid

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget
from app.async_effects.provider_effects import (
    ProviderEffectIntent,
    ProviderEffectReceipt,
    ProviderEffectState,
)
from app.async_effects.provider_query_operations import build_provider_query_operations_evidence
from app.core.config import settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.services.postgres_store import PostgresStore


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


def build_intent(*, suffix: str) -> AsyncEffectIntent:
    return AsyncEffectIntent(
        operation_type="provider.effect.query.operations.smoke",
        target=AsyncEffectTarget(
            owner_subject_id="owner-query-operations-smoke",
            vault_id="vault-query-operations-smoke",
            resource_type="voiceProfile",
            resource_id=f"query-operations-{suffix}",
            resource_version=1,
            purpose="voiceClone",
            authority_epoch=0,
        ),
        payload_hash=digest(f"query-operations-payload:{suffix}"),
    )


def record_unknown(
    store: PostgresStore,
    *,
    suffix: str,
    provider: str,
    capability: str,
) -> ProviderEffectIntent:
    effect_intent = build_intent(suffix=suffix)
    provider_intent = ProviderEffectIntent(
        effect_intent=effect_intent,
        provider=provider,
        capability=capability,
        request_hash=digest(f"query-operations-request:{suffix}"),
    )
    receipt = ProviderEffectReceipt(
        intent=provider_intent,
        state=ProviderEffectState.UNKNOWN,
        reason_code="providerTimeout",
        observation_origin="timeoutObservation",
    )
    with store.request_unit_of_work(
        correlation_id=f"provider-query-operations:{suffix}",
        command_id=f"providerQueryOperations:{suffix}",
    ):
        store.effect_kernel_repository().accept(effect_intent)
        store.provider_effect_repository().record(receipt)
    return provider_intent


def provider_snapshot(dsn: str) -> tuple[int, int, int]:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM async_effects.provider_effects")
            effect_count = int(cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(*) FROM async_effects.provider_receipts")
            receipt_count = int(cursor.fetchone()[0])
            cursor.execute(
                "SELECT COUNT(*) FROM async_effects.provider_effect_reconciliation_projection "
                "WHERE effective_state = 'unknown'"
            )
            unknown_count = int(cursor.fetchone()[0])
    return effect_count, receipt_count, unknown_count


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_provider_query_ops_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    store: PostgresStore | None = None
    created = False
    try:
        create_database(admin_dsn, database_name)
        created = True
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="provider-query-operations-g2",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        applied = migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")
        require(applied["appliedVersions"][-1] == "0028", "current migration head must apply")

        store = PostgresStore(dsn=test_dsn, pool_min_size=1, pool_max_size=4)
        store.open_pool(wait=True)
        record_unknown(
            store,
            suffix="voice",
            provider="volcengineVoiceClone",
            capability="voiceCloneTraining",
        )
        record_unknown(
            store,
            suffix="analysis",
            provider="deepseekTextOnly",
            capability="archiveImageAnalysis",
        )
        before = provider_snapshot(test_dsn)
        with store.request_unit_of_work(
            correlation_id="provider-query-operations-observe",
            command_id="providerQueryOperationsObserve",
        ):
            backlog = store.provider_effect_repository().reconciliation_backlog()
        observed_at = datetime.now(timezone.utc)
        evidence = build_provider_query_operations_evidence(
            backlog_entries=backlog,
            observed_at=observed_at,
            expires_at=observed_at + timedelta(minutes=5),
        ).value_free_summary()
        after = provider_snapshot(test_dsn)

        require(before == (2, 2, 2), "synthetic unknown effects must persist before observation")
        require(after == before, "operations baseline must not mutate provider-effect evidence")
        require(evidence["unknownEffectCount"] == 2, "all unresolved effects must be counted")
        require(evidence["pendingReconciliationCount"] == 2, "unknown effects await manual query review")
        require(evidence["providerQueryExecutionEnabled"] is False, "baseline must not query a Provider")
        require(evidence["automaticReconciliationEnabled"] is False, "baseline must not reconcile")
        require(evidence["replayEnabled"] is False, "baseline must not replay")
        require(evidence["externalProviderQueryGateOpen"] is True, "catalog must retain the external query gate")
        serialized = str(evidence)
        for forbidden in (
            "owner-query-operations-smoke",
            "vault-query-operations-smoke",
            "query-operations-voice",
            "query-operations-analysis",
        ):
            require(forbidden not in serialized, "operations evidence must remain value-free")
        print("Provider-query operations Postgres smoke passed (read-only; Provider calls remain disabled).")
    finally:
        if store is not None:
            store.close_pool()
        if created:
            drop_database(admin_dsn, database_name)


if __name__ == "__main__":
    main()
