#!/usr/bin/env python3
"""Verify bounded Live contract-retry context through disposable PostgreSQL."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import importlib.util
import json
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
from psycopg.types.json import Jsonb

from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget
from app.async_effects.lease_repository import AsyncEffectLeaseLost
from app.async_effects.owner_truth_candidate_extraction_worker import (
    ModelAssistedOwnerTruthSourceExtractor,
    OwnerTruthCandidateExtractionWorkerRuntime,
)
from app.core.config import Settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.services.postgres_store import PostgresStore


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def digest(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True).encode("utf-8")
    ).hexdigest()


def dsn_for_database(base_dsn: str, database_name: str) -> str:
    values = conninfo_to_dict(base_dsn)
    values["dbname"] = database_name
    return make_conninfo(**values)


def create_database(admin_dsn: str, database_name: str) -> None:
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        connection.execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name))
        )


def drop_database(admin_dsn: str, database_name: str) -> None:
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        connection.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (database_name,),
        )
        connection.execute(
            sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database_name))
        )


def intent(resource_id: str) -> AsyncEffectIntent:
    return AsyncEffectIntent(
        operation_type="ownerTruth.source.created",
        target=AsyncEffectTarget(
            owner_subject_id="synthetic-live-retry-owner",
            vault_id="synthetic-live-retry-owner",
            resource_type="source",
            resource_id=resource_id,
            resource_version=1,
            purpose="candidateExtraction",
            authority_epoch=0,
        ),
        payload_hash=digest({"fixture": resource_id}),
        max_attempts=3,
    )


def load_full_chain_helpers():
    path = ROOT_DIR / "scripts/backend-owner-truth-live-candidate-formal-postgres-smoke.py"
    spec = importlib.util.spec_from_file_location("live_formal_helpers", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Live formal smoke helpers are unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def seed_live_source(dsn: str, *, resource_id: str) -> tuple[AsyncEffectIntent, list[dict[str, object]], list[dict[str, object]]]:
    owner_id = f"synthetic-live-lease-owner-{uuid.uuid4().hex[:8]}"
    content_payload = {"text": "用户说最后一次重试的代号是松风七十一号。"}
    content_hash = digest(content_payload)
    turns: list[dict[str, object]] = [
        {
            "index": 1,
            "role": "user",
            "text": content_payload["text"],
            "captureMode": "live",
        }
    ]
    memories: list[dict[str, object]] = [
        {
            "memoryKind": "experience",
            "summary": "本次重试测试代号是松风七十一号。",
            "sourceTurnIndices": [1],
        }
    ]
    value = AsyncEffectIntent(
        operation_type="ownerTruth.source.created",
        target=AsyncEffectTarget(
            owner_subject_id=owner_id,
            vault_id=owner_id,
            resource_type="source",
            resource_id=resource_id,
            resource_version=1,
            purpose="candidateExtraction",
            authority_epoch=0,
        ),
        payload_hash=content_hash,
        max_attempts=3,
    )
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO owner_truth.vaults (vault_id, owner_subject_id) VALUES (%s, %s)",
                (owner_id, owner_id),
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.sources (
                    id, vault_id, owner_subject_id, source_kind, content_hash,
                    policy_version, metadata, content_payload
                ) VALUES (%s, %s, %s, 'conversation', %s, 'owner-truth-v5', %s, %s)
                """,
                (
                    resource_id,
                    owner_id,
                    owner_id,
                    content_hash,
                    Jsonb({
                        "captureMode": "live",
                        "sourcePolicy": "userEvidenceOnly",
                        "conversationTurns": turns,
                    }),
                    Jsonb(content_payload),
                ),
            )
        connection.commit()
    return value, turns, memories


def open_store(dsn: str) -> PostgresStore:
    store = PostgresStore(dsn=dsn, pool_min_size=1, pool_max_size=2)
    store.open_pool(wait=True)
    return store


def accept(store: PostgresStore, value: AsyncEffectIntent) -> None:
    with store.request_unit_of_work(
        correlation_id=f"live-retry-accept-{value.job_id}",
        command_id=f"liveRetryAccept:{value.operation_id}",
    ):
        store.effect_kernel_repository().accept(value)


def claim(store: PostgresStore, worker_id: str):
    with store.request_unit_of_work(
        correlation_id=f"live-retry-claim-{worker_id}",
        command_id=f"liveRetryClaim:{worker_id}",
    ):
        return store.async_effect_lease_repository().claim_next(
            worker_id=worker_id,
            lease_seconds=30,
            supported_job_types=["ownerTruth.source.created"],
        )


def release(store: PostgresStore, lease, error_code: str) -> None:
    with store.request_unit_of_work(
        correlation_id=f"live-retry-release-{lease.attempt}",
        command_id=f"liveRetryRelease:{lease.operation_id}:{lease.attempt}",
    ):
        store.async_effect_lease_repository().release_retryable(
            lease,
            retry_seconds=0,
            error_code=error_code,
        )


def load_context(store: PostgresStore, lease):
    with store.request_unit_of_work(
        correlation_id=f"live-retry-context-{lease.attempt}",
        command_id=f"liveRetryContext:{lease.operation_id}:{lease.attempt}",
    ):
        return store.async_effect_lease_repository().load_contract_retry_context(lease)


class ExpiringAfterProviderSourceExtractor(ModelAssistedOwnerTruthSourceExtractor):
    def __init__(self, *, dsn: str, delegate: ModelAssistedOwnerTruthSourceExtractor) -> None:
        super().__init__(
            settings=Settings(owner_truth_live_memory_organization_enabled=True),
            organizer=delegate._organizer,
            live_extractor=delegate._live_extractor,
        )
        self._dsn = dsn

    def extract(self, **kwargs):
        command = super().extract(**kwargs)
        intent_value = kwargs["intent"]
        with psycopg.connect(self._dsn) as connection:
            connection.execute(
                "UPDATE async_effects.jobs SET lease_until = NOW() - INTERVAL '1 second' "
                "WHERE job_id = %s",
                (intent_value.job_id,),
            )
            connection.commit()
        return command


def main() -> int:
    base_dsn = os.environ.get("DATABASE_URL", Settings.from_env().database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_live_retry_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    store: PostgresStore | None = None
    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="live-contract-retry-postgres-smoke",
            lock_timeout_ms=1_000,
            statement_timeout_ms=30_000,
        )
        migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")

        value = intent(str(uuid.uuid4()))
        store = open_store(test_dsn)
        accept(store, value)
        first = claim(store, "live-retry-worker-a")
        require(first is not None and first.attempt == 1, "attempt 1 was not claimed")
        release(
            store,
            first,
            "candidateExtraction.live.organizationDecode.invalidJson",
        )
        store.close_pool()

        store = open_store(test_dsn)
        second = claim(store, "live-retry-worker-b")
        require(second is not None and second.attempt == 2, "attempt 2 was not restored")
        second_context = load_context(store, second)
        require(
            second_context is not None
            and second_context.attempt == 1
            and second_context.stage == "organizationDecode"
            and second_context.reason == "invalidJson",
            "attempt 1 contract context was not restored",
        )
        release(
            store,
            second,
            "candidateExtraction.live.organizationRequest.transport",
        )
        store.close_pool()

        store = open_store(test_dsn)
        third = claim(store, "live-retry-worker-c")
        require(third is not None and third.attempt == 3, "attempt 3 was not restored")
        third_context = load_context(store, third)
        require(
            third_context == second_context,
            "transient attempt lost or changed the original contract context",
        )
        with psycopg.connect(test_dsn) as connection:
            connection.execute(
                "UPDATE async_effects.jobs SET lease_until = NOW() - INTERVAL '1 second' "
                "WHERE job_id = %s",
                (third.job_id,),
            )
            connection.commit()
        stale_rejected = False
        try:
            load_context(store, third)
        except AsyncEffectLeaseLost:
            stale_rejected = True
        require(stale_rejected, "expired lease was treated as missing context")

        with psycopg.connect(test_dsn) as connection:
            connection.execute(
                "UPDATE async_effects.jobs SET state = 'failed', terminal_at = NOW(), "
                "lease_owner = NULL, lease_until = NULL WHERE job_id = %s",
                (third.job_id,),
            )
            connection.commit()

        rejected_later_history_codes = [
            "candidateExtraction.live.supportValidate.factOmitted",
            "candidateExtraction.live.organizationRequest.authorizationRejected",
            "candidateExtraction.live.organizationRequest.arbitrary",
        ]
        for index, rejected_code in enumerate(rejected_later_history_codes):
            rejected_value = intent(str(uuid.uuid4()))
            accept(store, rejected_value)
            rejected_first = claim(store, f"live-retry-rejected-{index}-a")
            require(rejected_first is not None, "rejected-history attempt 1 was not claimed")
            release(
                store,
                rejected_first,
                "candidateExtraction.live.organizationDecode.invalidJson",
            )
            rejected_second = claim(store, f"live-retry-rejected-{index}-b")
            require(rejected_second is not None, "rejected-history attempt 2 was not claimed")
            release(store, rejected_second, rejected_code)
            rejected_third = claim(store, f"live-retry-rejected-{index}-c")
            require(rejected_third is not None, "rejected-history attempt 3 was not claimed")
            require(
                load_context(store, rejected_third) is None,
                f"unapproved later failure restored contract context: {rejected_code}",
            )
            with psycopg.connect(test_dsn) as connection:
                connection.execute(
                    "UPDATE async_effects.jobs SET state = 'failed', terminal_at = NOW(), "
                    "lease_owner = NULL, lease_until = NULL WHERE job_id = %s",
                    (rejected_third.job_id,),
                )
                connection.commit()

        live_source_id = str(uuid.uuid4())
        live_value, live_turns, live_memories = seed_live_source(
            test_dsn,
            resource_id=live_source_id,
        )
        accept(store, live_value)
        live_first = claim(store, "live-retry-provider-worker-a")
        require(
            live_first is not None and live_first.attempt == 1,
            "provider scenario attempt 1 was not claimed",
        )
        release(
            store,
            live_first,
            "candidateExtraction.live.organizationDecode.invalidJson",
        )
        live_second = claim(store, "live-retry-provider-worker-b")
        require(
            live_second is not None and live_second.attempt == 2,
            "provider scenario attempt 2 was not claimed",
        )
        release(
            store,
            live_second,
            "candidateExtraction.live.organizationRequest.transport",
        )

        helpers = load_full_chain_helpers()
        runtime = replace(
            Settings.from_env(),
            database_url=test_dsn,
            deepseek_api_key="synthetic-live-retry-key",
            async_effect_v1_enabled=True,
            async_effect_worker_enabled=True,
            owner_truth_candidate_extraction_worker_enabled=True,
            owner_truth_live_memory_organization_enabled=True,
        )
        live_memories[0]["facets"] = helpers.facets()
        delegate, provider_requests = helpers.controlled_extractor(
            runtime,
            turns=live_turns,
            memories=live_memories,
            store=store,
        )
        expiring_extractor = ExpiringAfterProviderSourceExtractor(
            dsn=test_dsn,
            delegate=delegate,
        )
        third_worker = OwnerTruthCandidateExtractionWorkerRuntime(
            settings=runtime,
            store=store,
            worker_id="live-retry-provider-worker-c",
            lease_seconds=120,
            heartbeat_interval_seconds=60,
            extractor=expiring_extractor,
        )
        provider_result = third_worker.run_once()
        require(
            provider_result.get("status") == "lost",
            "provider result was committed after its lease expired: "
            f"{json.dumps(provider_result, sort_keys=True)}",
        )
        require(
            len(provider_requests) == 2,
            "the final allowed attempt must make exactly two provider requests",
        )

        exhausted_worker = OwnerTruthCandidateExtractionWorkerRuntime(
            settings=runtime,
            store=store,
            worker_id="live-retry-provider-worker-d",
            lease_seconds=30,
            extractor=expiring_extractor,
        )
        exhausted_result = exhausted_worker.run_once()
        require(
            exhausted_result.get("status") == "failed"
            and exhausted_result.get("failureCode")
            == "candidateExtraction.live.workerExecution.budgetExhausted",
            "expired final attempt did not terminate at the persisted budget boundary",
        )
        require(
            len(provider_requests) == 2,
            "budget-exhausted reclaim made an additional provider request",
        )
        with psycopg.connect(test_dsn) as connection:
            row = connection.execute(
                "SELECT state, attempt FROM async_effects.jobs WHERE job_id = %s",
                (live_value.job_id,),
            ).fetchone()
            candidate_count = connection.execute(
                "SELECT COUNT(*) FROM owner_truth.memory_candidates WHERE source_id = %s",
                (live_source_id,),
            ).fetchone()[0]
        require(
            row is not None and row[0] == "failed" and int(row[1]) == 4,
            "budget-exhausted reclaim did not persist the terminal job state",
        )
        require(candidate_count == 0, "lease loss committed a partial Candidate")

        print(json.dumps({
            "schemaVersion": "owner-truth-live-contract-retry-postgres-smoke-v1",
            "status": "passed",
            "schemaHead": verified["expectedHead"],
            "sameJobRestored": True,
            "attemptOneFeedbackPreservedAcrossTransient": True,
            "unapprovedLaterHistoriesRejected": rejected_later_history_codes,
            "staleLeaseRejected": True,
            "providerReturnedBeforeLeaseLoss": True,
            "reclaimProviderRequestCount": len(provider_requests),
            "budgetExhaustedWithoutCandidateCommit": True,
        }, sort_keys=True))
        return 0
    finally:
        if store is not None:
            store.close_pool()
        try:
            drop_database(admin_dsn, database_name)
        except Exception as error:
            print(
                f"warning: temporary database cleanup failed: {type(error).__name__}",
                file=sys.stderr,
            )


if __name__ == "__main__":
    raise SystemExit(main())
