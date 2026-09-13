#!/usr/bin/env python3
"""Validate the real DeepSeek Source-to-read loop in disposable Postgres.

The script is explicit opt-in, uses synthetic text only, never reads an
application Vault, and drops its temporary database on exit. Output contains
only counts, states, contract identifiers and timings.
"""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import sys
from time import perf_counter
import uuid

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.async_effects.owner_truth_candidate_extraction_worker import (
    OwnerTruthCandidateExtractionWorkerRuntime,
)
from app.async_effects.owner_truth_memory_projection_worker import (
    OwnerTruthMemoryProjectionWorkerRuntime,
)
from app.core.config import Settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.domain.owner_truth.candidate_decisions import (
    CandidateReviewAction,
    OwnerTruthCandidateReviewCommand,
)
from app.domain.owner_truth.source_commands import CreateTextSourceCommand, OwnerTruthCommandContext
from app.services.formal_memory_conversation_snapshot import (
    FormalMemoryConversationSnapshotService,
    bind_provider_role_text,
)
from app.services.owner_truth_candidate_review import OwnerTruthCandidateReviewService
from app.services.owner_truth_memory_search_read import OwnerTruthMemorySearchReadService
from app.services.owner_truth_source import OwnerTruthSourceAsyncEffectCommandService
from app.services.postgres_store import PostgresStore


APPROVAL_ENV = "OWNER_TRUTH_REAL_MODEL_POSTGRES_VALIDATION_APPROVED"
SCHEMA_VERSION = "owner-truth-real-deepseek-postgres-smoke-v1"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def dsn_for_database(base_dsn: str, database_name: str) -> str:
    values = conninfo_to_dict(base_dsn)
    values["dbname"] = database_name
    return make_conninfo(**values)


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


def create_live_source(
    store: PostgresStore,
    *,
    context: OwnerTruthCommandContext,
    command_id: str,
    user_text: str,
    assistant_text: str,
) -> object:
    return OwnerTruthSourceAsyncEffectCommandService(store).create_text_source(
        command=CreateTextSourceCommand(
            command_id=command_id,
            source_id=str(uuid.uuid4()),
            expected_version=0,
            text=user_text,
            metadata={
                "origin": "syntheticRealProviderValidation",
                "captureMode": "live",
                "sourcePolicy": "userEvidenceOnly",
                "conversationTurns": [
                    {"index": 1, "role": "assistant", "text": assistant_text, "captureMode": "live"},
                    {"index": 2, "role": "user", "text": user_text, "captureMode": "live"},
                ],
            },
        ),
        context=context,
    )


def main() -> int:
    if os.environ.get(APPROVAL_ENV) != "1":
        print(f"BLOCKED: set {APPROVAL_ENV}=1 for synthetic real-provider validation.", file=sys.stderr)
        return 3
    base = Settings.from_env()
    require(bool(base.deepseek_api_key), "DeepSeek configuration is incomplete")
    base_dsn = os.environ.get("DATABASE_URL", base.database_url).strip()
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_real_deepseek_smoke_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    store: PostgresStore | None = None
    started = perf_counter()
    try:
        create_database(admin_dsn, database_name)
        migration = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="real-deepseek-postgres-smoke",
            lock_timeout_ms=1_000,
            statement_timeout_ms=30_000,
        )
        migration.apply()
        verified = migration.verify()
        require(verified["status"] == "ready", "migration head must verify")
        store = PostgresStore(dsn=test_dsn, pool_min_size=1, pool_max_size=3)
        store.open_pool(wait=True)
        runtime = replace(
            base,
            database_url=test_dsn,
            async_effect_v1_enabled=True,
            async_effect_worker_enabled=True,
            owner_truth_candidate_extraction_worker_enabled=True,
            owner_truth_live_memory_organization_enabled=True,
            owner_truth_text_memory_organization_enabled=True,
            owner_truth_memory_projection_worker_enabled=True,
            owner_truth_memory_search_projection_worker_enabled=True,
        )
        context = OwnerTruthCommandContext(
            vault_id="synthetic-real-provider-owner",
            owner_subject_id="synthetic-real-provider-owner",
            actor_subject_id="synthetic-real-provider-owner",
        )
        new_fact = create_live_source(
            store,
            context=context,
            command_id="synthetic-real-live-new-fact",
            user_text="我于2016年从晨光大学计算机专业毕业。",
            assistant_text="请只讲一条用于隔离验证的合成经历。",
        )
        require(new_fact.effect.outcome == "accepted", "new synthetic Source effect must be accepted")
        worker = OwnerTruthCandidateExtractionWorkerRuntime(
            settings=runtime,
            store=store,
            worker_id="synthetic-real-deepseek-worker",
            retry_seconds=1,
        )
        extraction = worker.run_once()
        safe_extraction = {
            key: extraction.get(key)
            for key in (
                "status",
                "reason",
                "attempt",
                "jobState",
                "failureCode",
                "failureStage",
                "failureType",
                "providerStatus",
                "retryable",
                "candidateCount",
                "candidateOutcome",
            )
            if extraction.get(key) is not None
        }
        if extraction.get("status") != "completed":
            print(
                json.dumps(
                    {
                        "schemaVersion": SCHEMA_VERSION,
                        "status": "failed",
                        "stage": "realOrganizer",
                        "safeExtraction": safe_extraction,
                        "privateVaultRead": False,
                        "responseContentRetained": False,
                    },
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
        require(extraction.get("status") == "completed", "real organizer must complete")
        require(int(extraction.get("candidateCount") or 0) > 0, "real organizer must persist a candidate")
        review = OwnerTruthCandidateReviewService(store)
        pending = review.list_pending(context=context)
        require(bool(pending), "real organizer candidate must enter review")
        with psycopg.connect(test_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) FROM owner_truth.memory_versions WHERE vault_id = %s",
                    (context.vault_id,),
                )
                require(
                    int(cursor.fetchone()[0]) == 0,
                    "candidate must not become formal before synthetic review",
                )
        activated = 0
        while True:
            refreshed_pending = review.list_pending(context=context)
            if not refreshed_pending:
                break
            require(activated < 8, "synthetic review exceeded the bounded candidate count")
            candidate = refreshed_pending[0]
            proposed_change_set = dict(candidate.proposed_change_set or {})
            require(
                bool(proposed_change_set.get("changeSetId"))
                and bool(proposed_change_set.get("proposalHash"))
                and type(proposed_change_set.get("baseMemoryRevision")) is int,
                "synthetic V5 Candidate must expose a ChangeSet preview",
            )
            result = review.decide_and_activate(
                command=OwnerTruthCandidateReviewCommand(
                    command_id=f"synthetic-review-{candidate.candidate_id}",
                    candidate_id=candidate.candidate_id,
                    expected_candidate_version=candidate.candidate_row_version,
                    action=CandidateReviewAction.ACCEPT,
                    corrected_value=None,
                    corrected_value_schema_version=candidate.content_schema_version,
                    reason_code="syntheticIsolatedValidation",
                    expected_memory_revision=proposed_change_set["baseMemoryRevision"],
                    expected_change_set_id=proposed_change_set["changeSetId"],
                    expected_proposal_hash=proposed_change_set["proposalHash"],
                ),
                context=context,
            )
            require(result.memory_activation.outcome == "created", "synthetic review must activate")
            activated += 1
        projection_worker = OwnerTruthMemoryProjectionWorkerRuntime(
            settings=runtime,
            store=store,
            worker_id="synthetic-real-projection-worker",
            retry_seconds=1,
        )
        try:
            with store.request_unit_of_work(
                correlation_id="synthetic-real-direct-projection",
                command_id="syntheticRealDirectProjection",
            ):
                direct_projection = store.owner_truth_memory_projection_repository().rebuild(
                    context=context
                )
                direct_search_projection = (
                    store.owner_truth_memory_search_document_projection_repository().rebuild(
                        context=context
                    )
                )
                require(
                    direct_search_projection.projection.checkpoint
                    == direct_projection.snapshot["checkpoint"],
                    "direct search projection checkpoint must match formal projection",
                )
        except Exception as error:
            print(
                json.dumps(
                    {
                        "schemaVersion": SCHEMA_VERSION,
                        "status": "failed",
                        "stage": "directDerivedProjection",
                        "errorType": type(error).__name__,
                        "safeMessage": str(error),
                        "privateVaultRead": False,
                        "responseContentRetained": False,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            raise
        projection_runs = 0
        for _ in range(20):
            result = projection_worker.run_once()
            if result.get("status") == "idle":
                break
            if result.get("status") != "completed":
                print(
                    json.dumps(
                        {
                            "schemaVersion": SCHEMA_VERSION,
                            "status": "failed",
                            "stage": "derivedProjection",
                            "safeProjection": {
                                key: result.get(key)
                                for key in (
                                    "status",
                                    "reason",
                                    "attempt",
                                    "jobState",
                                    "projectionState",
                                    "failureCode",
                                )
                                if result.get(key) is not None
                            },
                            "privateVaultRead": False,
                            "responseContentRetained": False,
                        },
                        sort_keys=True,
                    ),
                    file=sys.stderr,
                )
            require(result.get("status") == "completed", "projection rebuild must complete")
            projection_runs += 1
        snapshot = FormalMemoryConversationSnapshotService(store).build(context=context)
        bound = bind_provider_role_text(
            snapshot,
            system_role="只依据已审核的合成正式记忆回答。",
            speaking_style="自然、客观。",
        )
        require("晨光大学" in bound["providerRoleText"], "Live role must contain the synthetic fact")
        search = OwnerTruthMemorySearchReadService(store).read(
            context=context,
            query="晨光大学",
            limit=8,
        )
        require(search.state == "ready" and bool(search.hits), "search must find the reviewed synthetic fact")

        no_change = create_live_source(
            store,
            context=context,
            command_id="synthetic-real-live-query-only",
            user_text="我是哪所大学毕业的？",
            assistant_text="请提出你的问题。",
        )
        require(no_change.effect.outcome == "accepted", "query-only Source effect must be accepted")
        no_change_result = worker.run_once()
        require(
            no_change_result.get("status") == "completed"
            and no_change_result.get("reason") == "candidateExtractionCompletedNoChange"
            and int(no_change_result.get("candidateCount") or 0) == 0,
            "pure query must complete with no change",
        )
        after_query = FormalMemoryConversationSnapshotService(store).build(context=context)
        require(
            after_query["memoryRevision"] == snapshot["memoryRevision"],
            "unreviewed query must not change formal revision",
        )
        payload = {
            "schemaVersion": SCHEMA_VERSION,
            "status": "passed",
            "executionMode": "realProviderSyntheticInputDisposablePostgres",
            "schemaHead": verified["expectedHead"],
            "candidateCount": len(pending),
            "activatedMemoryCount": activated,
            "projectionRunCount": projection_runs,
            "searchHitCount": len(search.hits),
            "liveFactCount": snapshot["coverage"]["includedFactCount"],
            "noChangeCandidateCount": 0,
            "privateVaultRead": False,
            "responseContentRetained": False,
            "credentialRetained": False,
            "elapsedMs": round((perf_counter() - started) * 1_000),
        }
        print(json.dumps(payload, sort_keys=True))
        return 0
    finally:
        if store is not None:
            store.close_pool()
        try:
            drop_database(admin_dsn, database_name)
        except Exception as error:
            print(f"warning: temporary database cleanup failed: {type(error).__name__}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
