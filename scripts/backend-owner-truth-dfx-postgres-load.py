#!/usr/bin/env python3
"""Run bounded Owner Truth DFX load against a disposable pgvector database.

The script exercises production Postgres repositories and workers with only
synthetic data.  It creates a uniquely named database, applies the complete
migration chain, records aggregate/value-free evidence, and drops that
database on exit.  The default deterministic providers prove server and
database performance only; they are never reported as real-model evidence.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from hashlib import sha256
from math import ceil
import json
import os
from pathlib import Path
import platform
import resource
import sys
from threading import Lock
from time import monotonic, perf_counter, sleep
from typing import Any, Callable, Mapping, Sequence
import uuid

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.async_effects.business_message_projection_worker import (
    BusinessMessageProjectionWorkerRuntime,
)
from app.async_effects.owner_truth_candidate_extraction_worker import (
    DeterministicOwnerTruthCandidateExtractor,
    OwnerTruthCandidateExtractionWorkerRuntime,
)
from app.async_effects.owner_truth_memory_projection_worker import (
    OwnerTruthMemoryProjectionWorkerRuntime,
)
from app.async_effects.owner_truth_memory_search_embedding_worker import (
    OwnerTruthMemorySearchEmbeddingWorkerRuntime,
)
from app.core.config import Settings, settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.domain.owner_truth.candidate_decisions import CandidateReviewAction
from app.domain.owner_truth.contracts import MemoryKind
from app.domain.owner_truth.conversation import (
    AcknowledgeInterviewReviewBatchCommand,
    AppendInterviewMessageCommand,
    ConversationMessageAuthor,
    ConversationMessageKind,
    CreateInterviewReviewBatchCommand,
    PauseInterviewForTopicSwitchCommand,
    StartInterviewSessionCommand,
)
from app.domain.owner_truth.interview_candidate_proposal import (
    AdmitInterviewReviewBatchForCandidateProposalCommand,
)
from app.domain.owner_truth.memory_changeset_group import (
    OwnerTruthMemoryChangeSetGroupCommand,
    OwnerTruthMemoryChangeSetGroupDependency,
    OwnerTruthMemoryChangeSetGroupSelection,
)
from app.domain.owner_truth.ontology import (
    OWNER_TRUTH_SCHEMA_VERSION_V5,
    enrich_memory_payload_v5,
)
from app.domain.owner_truth.source_commands import (
    OwnerTruthCommandAuthorizationCapture,
    OwnerTruthCommandContext,
)
from app.services.formal_memory_conversation_snapshot import (
    FormalMemoryConversationSnapshotService,
)
from app.services.owner_truth_dfx_load import (
    ScheduledLoadConfig,
    nearest_rank_percentile,
    run_scheduled_load,
)
from app.services.owner_truth_interview_candidate_proposal import (
    OwnerTruthInterviewCandidateProposalService,
)
from app.services.owner_truth_memory_changeset_group_review import (
    OwnerTruthMemoryChangeSetGroupReviewService,
)
from app.services.owner_truth_memory_projection import OwnerTruthMemoryProjectionService
from app.services.owner_truth_memory_search_embedding_runtime import (
    OWNER_TRUTH_EMBEDDING_MIGRATED_MODEL,
)
from app.services.owner_truth_memory_search_hybrid import (
    OwnerTruthMemorySearchHybridRanker,
    OwnerTruthQueryEmbedding,
)
from app.services.owner_truth_memory_search_projection import (
    OwnerTruthMemorySearchDocumentProjectionService,
)
from app.services.owner_truth_memory_search_read import OwnerTruthMemorySearchReadService
from app.services.owner_truth_conversation import OwnerTruthConversationService
from app.services.postgres_store import PostgresStore


@dataclass(frozen=True)
class LoadProfile:
    name: str
    online_users: int
    duration_seconds: float
    retrieval_qps: float
    receipt_qps: float
    snapshot_qps: float
    review_qps: float
    extraction_concurrency: int
    search_owner_count: int
    facts_per_search_owner: int


PROFILES = {
    "smoke": LoadProfile(
        name="smoke",
        online_users=10,
        duration_seconds=2.0,
        retrieval_qps=5.0,
        receipt_qps=2.0,
        snapshot_qps=5.0,
        review_qps=1.0,
        extraction_concurrency=1,
        search_owner_count=2,
        facts_per_search_owner=20,
    ),
    "baseline": LoadProfile(
        name="baseline",
        online_users=100,
        duration_seconds=10.0,
        retrieval_qps=20.0,
        receipt_qps=5.0,
        snapshot_qps=10.0,
        review_qps=3.0,
        extraction_concurrency=3,
        search_owner_count=10,
        facts_per_search_owner=100,
    ),
    "capacity": LoadProfile(
        name="capacity",
        online_users=100,
        duration_seconds=60.0,
        retrieval_qps=20.0,
        receipt_qps=5.0,
        snapshot_qps=10.0,
        review_qps=3.0,
        extraction_concurrency=3,
        search_owner_count=300,
        facts_per_search_owner=5_000,
    ),
}


@dataclass(frozen=True)
class ConversationFixture:
    context: OwnerTruthCommandContext
    thread_id: str
    session_id: str


@dataclass(frozen=True)
class ReviewFixture:
    context: OwnerTruthCommandContext
    command: OwnerTruthMemoryChangeSetGroupCommand


@dataclass(frozen=True)
class CandidateExtractionFixture:
    context: OwnerTruthCommandContext
    review_batch_id: str
    source_id: str
    started_at: float


class SyntheticEmbeddingProvider:
    """Deterministic DB-only provider; never constitutes model evidence."""

    model = OWNER_TRUTH_EMBEDDING_MIGRATED_MODEL

    def embed_query(self, *, query: str) -> OwnerTruthQueryEmbedding:
        return OwnerTruthQueryEmbedding(model=self.model, values=self._vector(query))

    def embed_documents(self, *, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return tuple(self._vector(text) for text in texts)

    @classmethod
    def _vector(cls, text: str) -> tuple[float, ...]:
        values = [0.0] * cls.model.dimensions
        digest = sha256(str(text).encode("utf-8")).digest()
        values[digest[0] % 32] = 1.0
        values[32 + (digest[1] % 32)] = 0.5
        return tuple(values)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


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
            cursor.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database_name))
            )


def invoke_conversation(
    store: PostgresStore,
    *,
    command_id: str,
    operation: Callable[[OwnerTruthConversationService], Any],
) -> Any:
    with store.request_unit_of_work(
        correlation_id=f"owner-truth-dfx-{canonical_hash(command_id)[:24]}",
        command_id=command_id,
    ):
        return operation(
            OwnerTruthConversationService(store.owner_truth_conversation_repository())
        )


def seed_search_owner(
    dsn: str,
    *,
    owner_index: int,
    fact_count: int,
) -> OwnerTruthCommandContext:
    owner_subject_id = f"dfx-owner-{owner_index}-{uuid.uuid4().hex[:8]}"
    vault_id = f"dfx-vault-{owner_index}-{uuid.uuid4().hex[:8]}"
    context = OwnerTruthCommandContext(
        vault_id=vault_id,
        owner_subject_id=owner_subject_id,
        actor_subject_id=owner_subject_id,
    )
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO owner_truth.vaults (vault_id, owner_subject_id) VALUES (%s, %s)",
                (vault_id, owner_subject_id),
            )
            for fact_index in range(fact_count):
                source_id = str(uuid.uuid4())
                memory_id = str(uuid.uuid4())
                version_id = str(uuid.uuid4())
                marker = f"DFX-{owner_index:03d}-{fact_index:05d}"
                content = enrich_memory_payload_v5(
                    kind=MemoryKind.KNOWLEDGE,
                    payload={
                        "statement": f"{marker} 是一条合成的性能测试事实。",
                        "knowledgeType": "personal_profile",
                        "domains": ["测试"],
                        "factType": "other",
                        "predicate": "states",
                        "object": {"label": marker, "category": "other"},
                    },
                    provenance={"mode": "selfReport"},
                    memory_subject_id=owner_subject_id,
                    claim_subject_id=owner_subject_id,
                )
                content_hash = canonical_hash(content)
                payload = {
                    "content": content,
                    "contentSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION_V5,
                    "evidenceRefs": [{"sourceId": source_id, "sourceVersion": 1}],
                }
                cursor.execute(
                    """
                    INSERT INTO owner_truth.sources (
                        id, vault_id, owner_subject_id, source_kind, content_hash,
                        policy_version, authority_epoch
                    ) VALUES (%s, %s, %s, 'text', %s, 'owner-truth-v1', 0)
                    """,
                    (source_id, vault_id, owner_subject_id, canonical_hash({"marker": marker})),
                )
                cursor.execute(
                    """
                    INSERT INTO owner_truth.memories (
                        id, vault_id, owner_subject_id, source_id, source_version,
                        memory_kind, perspective_type, epistemic_status, sensitivity,
                        status, policy_version, content_hash, authority_epoch
                    ) VALUES (%s, %s, %s, %s, 1, 'knowledge', 'firstPerson',
                        'recalled', 'standard', 'active', 'owner-truth-v1', %s, 0)
                    """,
                    (memory_id, vault_id, owner_subject_id, source_id, content_hash),
                )
                cursor.execute(
                    """
                    INSERT INTO owner_truth.memory_versions (
                        id, vault_id, memory_id, version_number, is_current,
                        schema_version, content_hash, payload, source_id, source_version
                    ) VALUES (%s, %s, %s, 1, TRUE, %s, %s, %s, %s, 1)
                    """,
                    (
                        version_id,
                        vault_id,
                        memory_id,
                        OWNER_TRUTH_SCHEMA_VERSION_V5,
                        content_hash,
                        Jsonb(payload),
                        source_id,
                    ),
                )
        connection.commit()
    return context


def prepare_search_data(
    *,
    dsn: str,
    store: PostgresStore,
    profile: LoadProfile,
    provider: SyntheticEmbeddingProvider,
) -> list[OwnerTruthCommandContext]:
    contexts = [
        seed_search_owner(
            dsn,
            owner_index=index,
            fact_count=profile.facts_per_search_owner,
        )
        for index in range(profile.search_owner_count)
    ]
    for context in contexts:
        OwnerTruthMemoryProjectionService(store).rebuild(context=context)
        result = OwnerTruthMemorySearchDocumentProjectionService(store).rebuild(
            context=context
        )
        require(result.projection is not None, "search projection must be ready")

    worker_settings = replace(
        Settings.from_env(),
        store_backend="postgres",
        async_effect_v1_enabled=True,
        async_effect_worker_enabled=True,
        owner_truth_memory_search_projection_worker_enabled=True,
        owner_truth_memory_search_embedding_worker_enabled=True,
        owner_truth_memory_search_embedding_batch_size=128,
        owner_truth_memory_search_embedding_backfill_scan_limit=512,
    )
    worker = OwnerTruthMemorySearchEmbeddingWorkerRuntime(
        settings=worker_settings,
        store=store,
        provider=provider,
        worker_id="owner-truth-dfx-embedding-worker",
    )
    maximum_runs = max(10, ceil((profile.search_owner_count * profile.facts_per_search_owner) / 64))
    for _ in range(maximum_runs):
        result = worker.run_once()
        if result["status"] == "idle":
            break
        require(result["status"] == "completed", "embedding worker must complete")
    else:
        raise AssertionError("embedding worker did not drain within the bounded run count")
    return contexts


def prepare_conversations(
    *,
    store: PostgresStore,
    count: int,
    entry_mode: str = "naturalInput",
) -> list[ConversationFixture]:
    fixtures: list[ConversationFixture] = []
    for index in range(count):
        owner = f"dfx-conversation-owner-{index}-{uuid.uuid4().hex[:8]}"
        context = OwnerTruthCommandContext(
            vault_id=f"dfx-conversation-vault-{index}-{uuid.uuid4().hex[:8]}",
            owner_subject_id=owner,
            actor_subject_id=owner,
        )
        fixture = ConversationFixture(
            context=context,
            thread_id=str(uuid.uuid4()),
            session_id=str(uuid.uuid4()),
        )
        command_id = f"dfx-start-{index}-{uuid.uuid4()}"
        result = invoke_conversation(
            store,
            command_id=command_id,
            operation=lambda service, fixture=fixture, command_id=command_id: service.start_session(
                command=StartInterviewSessionCommand(
                    command_id=command_id,
                    thread_id=fixture.thread_id,
                    session_id=fixture.session_id,
                    expected_thread_version=0,
                    entry_mode=entry_mode,
                ),
                context=fixture.context,
            ),
        )
        require(result.outcome == "created", "synthetic session must start")
        fixtures.append(fixture)
    return fixtures


def education_content(*, marker: str, owner_subject_id: str) -> dict[str, Any]:
    return enrich_memory_payload_v5(
        kind=MemoryKind.KNOWLEDGE,
        payload={
            "statement": f"{marker} 是一条待审核的合成事实。",
            "knowledgeType": "personal_profile",
            "domains": ["测试"],
            "factType": "other",
            "predicate": "states",
            "object": {"label": marker, "category": "other"},
        },
        provenance={"mode": "selfReport"},
        memory_subject_id=owner_subject_id,
        claim_subject_id=owner_subject_id,
    )


def prepare_review_fixture(
    *,
    dsn: str,
    store: PostgresStore,
    index: int,
) -> ReviewFixture:
    owner = f"dfx-review-owner-{index}-{uuid.uuid4().hex[:8]}"
    context = OwnerTruthCommandContext(
        vault_id=f"dfx-review-vault-{index}-{uuid.uuid4().hex[:8]}",
        owner_subject_id=owner,
        actor_subject_id=owner,
    )
    candidate_ids: list[str] = []
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO users (id, phone, nickname, payload)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    owner,
                    f"dfx-review-phone-{index}-{uuid.uuid4().hex[:8]}",
                    "DFX synthetic review owner",
                    Jsonb(
                        {
                            "accessState": "active",
                            "deletionState": "active",
                            "authEpoch": 0,
                        }
                    ),
                ),
            )
            cursor.execute(
                "INSERT INTO owner_truth.vaults (vault_id, owner_subject_id) VALUES (%s, %s)",
                (context.vault_id, owner),
            )
            for member_index in range(2):
                source_id = str(uuid.uuid4())
                candidate_id = str(uuid.uuid4())
                candidate_ids.append(candidate_id)
                content = education_content(
                    marker=f"DFX-REVIEW-{index:04d}-{member_index}",
                    owner_subject_id=owner,
                )
                statement = str(content["statement"])
                payload = {
                    "schemaVersion": "owner-truth-candidate-proposal-v1",
                    "candidateKind": "knowledge",
                    "perspectiveType": "firstPerson",
                    "epistemicStatus": "recalled",
                    "sensitivity": "standard",
                    "content": content,
                    "contentSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION_V5,
                    "evidenceRefs": [
                        {
                            "sourceId": source_id,
                            "sourceVersion": 1,
                            "span": {"start": 0, "end": len(statement)},
                        }
                    ],
                    "reviewMode": "batch",
                }
                cursor.execute(
                    """
                    INSERT INTO owner_truth.sources (
                        id, vault_id, owner_subject_id, source_kind, content_hash,
                        policy_version, authority_epoch
                    ) VALUES (%s, %s, %s, 'text', %s, %s, 0)
                    """,
                    (
                        source_id,
                        context.vault_id,
                        owner,
                        canonical_hash({"statement": statement}),
                        context.policy_version,
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO owner_truth.memory_candidates (
                        id, vault_id, owner_subject_id, source_id, candidate_kind,
                        perspective_type, epistemic_status, sensitivity,
                        policy_version, authority_epoch, content_hash,
                        payload_schema_version, payload
                    ) VALUES (%s, %s, %s, %s, 'knowledge', 'firstPerson',
                        'recalled', 'standard', %s, 0, %s, %s, %s)
                    """,
                    (
                        candidate_id,
                        context.vault_id,
                        owner,
                        source_id,
                        context.policy_version,
                        canonical_hash(content),
                        OWNER_TRUTH_SCHEMA_VERSION_V5,
                        Jsonb(payload),
                    ),
                )
        connection.commit()

    selections = tuple(
        OwnerTruthMemoryChangeSetGroupSelection(
            candidate_id=candidate_id,
            expected_candidate_version=1,
            action=CandidateReviewAction.ACCEPT,
            corrected_value=None,
            corrected_value_schema_version=None,
            reason_code="ownerReviewedDfx",
        )
        for candidate_id in candidate_ids
    )
    dependencies = (
        OwnerTruthMemoryChangeSetGroupDependency(
            before_candidate_id=candidate_ids[0],
            after_candidate_id=candidate_ids[1],
        ),
    )
    prefix = f"dfx-review-{index}-{uuid.uuid4()}"
    preview = OwnerTruthMemoryChangeSetGroupCommand(
        command_id=f"{prefix}-preview",
        selections=selections,
        dependencies=dependencies,
    )
    proposal = OwnerTruthMemoryChangeSetGroupReviewService(store).preview(
        command=preview,
        context=context,
    )
    return ReviewFixture(
        context=context,
        command=OwnerTruthMemoryChangeSetGroupCommand(
            command_id=f"{prefix}-confirm",
            selections=selections,
            dependencies=dependencies,
            expected_memory_revision=proposal.base_memory_revision,
            expected_group_proposal_id=proposal.proposal_id,
            expected_group_proposal_hash=proposal.proposal_hash,
        ),
    )


def _capture_for_context(context: OwnerTruthCommandContext, index: int) -> OwnerTruthCommandContext:
    return OwnerTruthCommandContext(
        vault_id=context.vault_id,
        owner_subject_id=context.owner_subject_id,
        actor_subject_id=context.actor_subject_id,
        policy_version=context.policy_version,
        authorization_capture=OwnerTruthCommandAuthorizationCapture(
            feature="ownerTruthCandidateReview",
            policy_version="release-policy-v1",
            policy_revision=1,
            emergency_revision=0,
            account_generation_hash=canonical_hash({"account": index})[:24],
            decision_id_hash=canonical_hash({"decision": index}),
            audience="owner",
            cohort="closedPilotAdultSelf",
            client_build=1,
            expires_at="2030-01-01T00:00:00+00:00",
        ),
    )


def prepare_candidate_extraction_fixture(
    *,
    store: PostgresStore,
    index: int,
) -> CandidateExtractionFixture:
    fixture = prepare_conversations(store=store, count=1)[0]
    append_command_id = f"dfx-extract-append-{index}-{uuid.uuid4()}"
    appended = invoke_conversation(
        store,
        command_id=append_command_id,
        operation=lambda service: service.append_message(
            command=AppendInterviewMessageCommand(
                command_id=append_command_id,
                thread_id=fixture.thread_id,
                session_id=fixture.session_id,
                message_id=str(uuid.uuid4()),
                expected_thread_version=1,
                expected_session_version=1,
                author=ConversationMessageAuthor.OWNER,
                kind=ConversationMessageKind.NARRATIVE,
                text=f"DFX-EXTRACT-{index:04d} 是一条合成会话事实。",
            ),
            context=fixture.context,
        ),
    )
    require(appended.session_version == 2, "synthetic message must persist")
    started_at = perf_counter()
    pause_command_id = f"dfx-extract-pause-{index}-{uuid.uuid4()}"
    paused = invoke_conversation(
        store,
        command_id=pause_command_id,
        operation=lambda service: service.pause_for_topic_switch(
            command=PauseInterviewForTopicSwitchCommand(
                command_id=pause_command_id,
                thread_id=fixture.thread_id,
                session_id=fixture.session_id,
                expected_thread_version=2,
                expected_session_version=2,
            ),
            context=fixture.context,
        ),
    )
    review_command_id = f"dfx-extract-batch-{index}-{uuid.uuid4()}"
    batch = invoke_conversation(
        store,
        command_id=review_command_id,
        operation=lambda service: service.create_review_batch(
            command=CreateInterviewReviewBatchCommand(
                command_id=review_command_id,
                thread_id=fixture.thread_id,
                session_id=fixture.session_id,
                expected_session_version=paused.session_version,
            ),
            context=fixture.context,
        ),
    )
    acknowledge_command_id = f"dfx-extract-ack-{index}-{uuid.uuid4()}"
    acknowledged = invoke_conversation(
        store,
        command_id=acknowledge_command_id,
        operation=lambda service: service.acknowledge_review_batch(
            command=AcknowledgeInterviewReviewBatchCommand(
                command_id=acknowledge_command_id,
                thread_id=fixture.thread_id,
                session_id=fixture.session_id,
                review_batch_id=batch.review_batch.review_batch_id,
                expected_session_version=batch.session_version,
                expected_review_batch_version=batch.review_batch.row_version,
            ),
            context=fixture.context,
        ),
    )
    require(acknowledged.outcome == "acknowledged", "review batch must acknowledge")
    admitted = OwnerTruthInterviewCandidateProposalService(store).admit_review_batch(
        command=AdmitInterviewReviewBatchForCandidateProposalCommand(
            command_id=f"dfx-extract-admit-{index}-{uuid.uuid4()}",
            review_batch_id=batch.review_batch.review_batch_id,
            expected_review_batch_version=acknowledged.review_batch.row_version,
        ),
        context=_capture_for_context(fixture.context, index),
    )
    require(admitted.outcome == "created", "conversation source must be admitted")
    return CandidateExtractionFixture(
        context=fixture.context,
        review_batch_id=batch.review_batch.review_batch_id,
        source_id=admitted.source_id,
        started_at=started_at,
    )


def latency_summary(
    *,
    name: str,
    latencies: Sequence[float],
    budget_ms: float,
    failure_count: int = 0,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    successful = [float(value) for value in latencies]
    percentile_values = (
        {
            "p50": round(nearest_rank_percentile(successful, 50), 3),
            "p95": round(nearest_rank_percentile(successful, 95), 3),
            "p99": round(nearest_rank_percentile(successful, 99), 3),
            "max": round(max(successful), 3),
        }
        if successful
        else None
    )
    passed = (
        failure_count == 0
        and percentile_values is not None
        and percentile_values["p95"] <= budget_ms
    )
    return {
        "schemaVersion": "owner-truth-dfx-operation-v1",
        "name": name,
        "status": "passed" if passed else "failed",
        "completedSamples": len(successful) + failure_count,
        "successCount": len(successful),
        "failureCount": failure_count,
        "timeoutCount": 0,
        "failureCodes": {},
        "latencyMs": percentile_values,
        "latencyBudgetMs": budget_ms,
        "metadata": dict(metadata or {}),
    }


def database_observation(dsn: str, *, store: PostgresStore) -> dict[str, Any]:
    with psycopg.connect(dsn) as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT current_setting('server_version') AS postgres_version,
                       pg_database_size(current_database()) AS database_bytes,
                       (SELECT extversion FROM pg_extension WHERE extname = 'vector') AS vector_version,
                       (SELECT COUNT(*) FROM pg_stat_activity
                        WHERE datname = current_database()) AS database_connections,
                       (SELECT COUNT(*) FROM async_effects.jobs
                        WHERE state IN ('pending', 'leased', 'retryWait')) AS async_backlog,
                       (SELECT COUNT(*) FROM owner_truth.search_document_embedding_jobs
                        WHERE state IN ('queued', 'leased', 'retry')) AS embedding_backlog
                """
            )
            row = cursor.fetchone()
            cursor.execute(
                """
                SELECT job_type, COUNT(*) AS job_count
                FROM async_effects.jobs
                WHERE state IN ('pending', 'leased', 'retryWait')
                GROUP BY job_type
                ORDER BY job_type
                """
            )
            async_backlog_by_job_type = {
                str(item["job_type"]): int(item["job_count"])
                for item in cursor.fetchall()
            }
    assert row is not None
    return {
        "postgresVersion": str(row["postgres_version"]),
        "pgvectorVersion": str(row["vector_version"]),
        "databaseBytes": int(row["database_bytes"]),
        "databaseConnections": int(row["database_connections"]),
        "asyncBacklog": int(row["async_backlog"]),
        "asyncBacklogByJobType": async_backlog_by_job_type,
        "embeddingBacklog": int(row["embedding_backlog"]),
        "pool": store.uow_metrics()["pool"],
    }


def drain_worker_until_idle(
    worker: Any,
    *,
    accepted_statuses: set[str],
    maximum_runs: int,
    description: str,
) -> dict[str, int]:
    completed = 0
    for _ in range(maximum_runs):
        result = worker.run_once()
        status = str(result.get("status") or "")
        if status == "idle":
            return {"completedRuns": completed, "idleObserved": 1}
        require(status in accepted_statuses, f"{description} must complete")
        completed += 1
    raise AssertionError(f"{description} did not drain within the bounded run count")


def async_backlog_growth(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> dict[str, int]:
    before_counts = dict(before.get("asyncBacklogByJobType") or {})
    after_counts = dict(after.get("asyncBacklogByJobType") or {})
    return {
        job_type: int(after_counts.get(job_type, 0)) - int(before_counts.get(job_type, 0))
        for job_type in sorted(set(before_counts) | set(after_counts))
    }


def process_observation() -> dict[str, Any]:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "processUserCpuSeconds": round(float(usage.ru_utime), 3),
        "processSystemCpuSeconds": round(float(usage.ru_stime), 3),
        "processMaxRssPlatformUnits": int(usage.ru_maxrss),
    }


def run_candidate_extraction(
    *,
    dsn: str,
    store: PostgresStore,
    profile: LoadProfile,
) -> dict[str, Any]:
    sample_count = max(1, int(profile.extraction_concurrency))
    fixtures = [
        prepare_candidate_extraction_fixture(store=store, index=index)
        for index in range(sample_count)
    ]
    worker_settings = replace(
        Settings.from_env(),
        store_backend="postgres",
        async_effect_v1_enabled=True,
        async_effect_worker_enabled=True,
        owner_truth_candidate_extraction_worker_enabled=True,
    )

    def run_worker(index: int) -> dict[str, Any]:
        return OwnerTruthCandidateExtractionWorkerRuntime(
            settings=worker_settings,
            store=store,
            worker_id=f"owner-truth-dfx-candidate-worker-{index}",
            extractor=DeterministicOwnerTruthCandidateExtractor(),
        ).run_once()

    with ThreadPoolExecutor(max_workers=profile.extraction_concurrency) as pool:
        results = list(pool.map(run_worker, range(sample_count)))
    failures = sum(1 for result in results if result.get("status") != "completed")
    latencies: list[float] = []
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            for fixture in fixtures:
                cursor.execute(
                    "SELECT COUNT(*) FROM owner_truth.memory_candidates WHERE source_id = %s",
                    (fixture.source_id,),
                )
                if int(cursor.fetchone()[0]) < 1:
                    failures += 1
                    continue
                latencies.append((perf_counter() - fixture.started_at) * 1_000.0)
    return latency_summary(
        name="shortSessionToCandidateVisible",
        latencies=latencies,
        budget_ms=30_000,
        failure_count=failures,
        metadata={
            "evidenceClass": "postgresWorkerDeterministicExtractor",
            "realModel": False,
            "extractionConcurrency": profile.extraction_concurrency,
        },
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--profile", choices=tuple(PROFILES), default="smoke")
    result.add_argument("--result", type=Path)
    return result


def main() -> int:
    args = parser().parse_args()
    if os.environ.get("OWNER_TRUTH_DFX_POSTGRES_APPROVED") != "1":
        print(
            "BLOCKED: OWNER_TRUTH_DFX_POSTGRES_APPROVED=1 is required for a disposable database run.",
            file=sys.stderr,
        )
        return 3
    profile = PROFILES[args.profile]
    if profile.name == "capacity" and os.environ.get("OWNER_TRUTH_DFX_CAPACITY_ACK") != "YES":
        print(
            "BLOCKED: OWNER_TRUTH_DFX_CAPACITY_ACK=YES is required for the 1.5M-fact profile.",
            file=sys.stderr,
        )
        return 3

    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_terra_a_dfx_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    store: PostgresStore | None = None

    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id=f"owner-truth-dfx-{profile.name}",
            lock_timeout_ms=1_000,
            statement_timeout_ms=120_000,
        )
        migration_result = migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")

        store = PostgresStore(
            dsn=test_dsn,
            pool_min_size=2,
            pool_max_size=32,
            pool_timeout_seconds=5,
        )
        store.open_pool(wait=True)
        provider = SyntheticEmbeddingProvider()
        search_contexts = prepare_search_data(
            dsn=test_dsn,
            store=store,
            profile=profile,
            provider=provider,
        )

        receipt_samples = max(
            profile.online_users,
            ceil(profile.receipt_qps * profile.duration_seconds),
        )
        conversation_fixtures = prepare_conversations(
            store=store,
            count=receipt_samples,
        )
        review_samples = ceil(profile.review_qps * profile.duration_seconds)
        review_fixtures = [
            prepare_review_fixture(dsn=test_dsn, store=store, index=index)
            for index in range(review_samples)
        ]
        before_db = database_observation(test_dsn, store=store)
        before_process = process_observation()

        receipt_metric = run_scheduled_load(
            name="reliableConversationReceipt",
            config=ScheduledLoadConfig(
                target_qps=profile.receipt_qps,
                duration_seconds=profile.duration_seconds,
                max_workers=max(2, ceil(profile.receipt_qps * 2)),
                latency_budget_ms=300,
            ),
            operation=lambda index: invoke_conversation(
                store,
                command_id=f"dfx-receipt-{index}-{uuid.uuid4()}",
                operation=lambda service: service.append_message(
                    command=AppendInterviewMessageCommand(
                        command_id=f"dfx-receipt-command-{index}-{uuid.uuid4()}",
                        thread_id=conversation_fixtures[index].thread_id,
                        session_id=conversation_fixtures[index].session_id,
                        message_id=str(uuid.uuid4()),
                        expected_thread_version=1,
                        expected_session_version=1,
                        author=ConversationMessageAuthor.OWNER,
                        kind=ConversationMessageKind.NARRATIVE,
                        text=f"DFX-RECEIPT-{index:05d} synthetic owner turn.",
                    ),
                    context=conversation_fixtures[index].context,
                ),
            ),
            metadata={
                "evidenceClass": "productionPostgresRepository",
                "durabilityBoundary": "transactionCommitted",
            },
        )

        ranker = OwnerTruthMemorySearchHybridRanker(provider)
        retrieval_metric = run_scheduled_load(
            name="authorizedInternalHybridRetrieval",
            config=ScheduledLoadConfig(
                target_qps=profile.retrieval_qps,
                duration_seconds=profile.duration_seconds,
                max_workers=max(4, ceil(profile.retrieval_qps * 2)),
                latency_budget_ms=250,
            ),
            operation=lambda index: OwnerTruthMemorySearchReadService(
                store,
                hybrid_ranker=ranker,
            ).read(
                context=search_contexts[index % len(search_contexts)],
                query=(
                    f"DFX-{index % len(search_contexts):03d}-"
                    f"{index % profile.facts_per_search_owner:05d}"
                ),
                limit=8,
            ),
            metadata={
                "evidenceClass": "productionPostgresHybridWithSyntheticEmbedding",
                "realModel": False,
                "cacheMode": "warmProjectionMixedQuery",
            },
        )

        snapshot_metric = run_scheduled_load(
            name="livePreGeneratedSnapshotRead",
            config=ScheduledLoadConfig(
                target_qps=profile.snapshot_qps,
                duration_seconds=profile.duration_seconds,
                max_workers=max(2, ceil(profile.snapshot_qps * 2)),
                latency_budget_ms=200,
            ),
            operation=lambda index: FormalMemoryConversationSnapshotService(store).build(
                context=search_contexts[index % len(search_contexts)]
            ),
            metadata={
                "evidenceClass": "productionPostgresProjectionRead",
                "cacheMode": "warmProjection",
                "supplierStartupExcluded": True,
            },
        )

        review_transaction_latencies: list[float] = []
        visibility_latencies: list[float] = []
        review_failures = 0
        visibility_failures = 0
        review_lock = Lock()
        worker_settings = replace(
            Settings.from_env(),
            store_backend="postgres",
            async_effect_v1_enabled=True,
            async_effect_worker_enabled=True,
            owner_truth_memory_projection_worker_enabled=True,
            owner_truth_memory_search_projection_worker_enabled=True,
            owner_truth_memory_search_embedding_worker_enabled=True,
            owner_truth_memory_search_embedding_batch_size=16,
            owner_truth_memory_search_embedding_backfill_scan_limit=128,
            business_message_projection_worker_enabled=True,
        )

        def confirm_and_publish_review(index: int) -> None:
            nonlocal review_failures, visibility_failures
            fixture = review_fixtures[index]
            review_started = perf_counter()
            try:
                result = OwnerTruthMemoryChangeSetGroupReviewService(store).confirm(
                    command=fixture.command,
                    context=fixture.context,
                )
                version_ids = tuple(
                    member.memory_version_id
                    for member in result.members
                    if member.memory_version_id is not None
                )
                require(len(version_ids) == 2, "atomic review must activate both facts")
            except Exception:
                with review_lock:
                    review_failures += 1
                    visibility_failures += 1
                raise
            review_completed = perf_counter()
            with review_lock:
                review_transaction_latencies.append(
                    (review_completed - review_started) * 1_000.0
                )

            try:
                projection_worker = OwnerTruthMemoryProjectionWorkerRuntime(
                    settings=worker_settings,
                    store=store,
                    worker_id=f"owner-truth-dfx-review-projection-worker-{index}",
                    retry_seconds=1,
                )
                business_message_worker = BusinessMessageProjectionWorkerRuntime(
                    settings=worker_settings,
                    store=store,
                    worker_id=f"owner-truth-dfx-review-message-worker-{index}",
                    retry_seconds=1,
                )
                embedding_worker = OwnerTruthMemorySearchEmbeddingWorkerRuntime(
                    settings=worker_settings,
                    store=store,
                    provider=provider,
                    worker_id=f"owner-truth-dfx-review-embedding-worker-{index}",
                )
                deadline = review_completed + 5.0
                while True:
                    projection_result = projection_worker.run_once()
                    require(
                        projection_result["status"] in {"completed", "idle"},
                        "review projection worker must complete",
                    )
                    message_result = business_message_worker.run_once()
                    require(
                        message_result["status"] in {"completed", "idle"},
                        "review message projection worker must complete",
                    )
                    embedding_result = embedding_worker.run_once()
                    require(
                        embedding_result["status"] in {"completed", "idle"},
                        "review vectors must complete",
                    )
                    search = OwnerTruthMemorySearchReadService(
                        store,
                        hybrid_ranker=ranker,
                    ).read(
                        context=fixture.context,
                        query=f"DFX-REVIEW-{index:04d}",
                        limit=8,
                    )
                    returned = {hit.document.memory_version_id for hit in search.hits}
                    if search.semantic_ranking_available and returned.intersection(version_ids):
                        with review_lock:
                            visibility_latencies.append(
                                (perf_counter() - review_completed) * 1_000.0
                            )
                        return
                    if perf_counter() >= deadline:
                        raise TimeoutError("formal memory did not become searchable in budget")
                    sleep(0.02)
            except Exception:
                with review_lock:
                    visibility_failures += 1
                raise

        review_workflow_metric = run_scheduled_load(
            name="formalReviewToSearchWorkflow",
            config=ScheduledLoadConfig(
                target_qps=profile.review_qps,
                duration_seconds=profile.duration_seconds,
                max_workers=max(2, ceil(profile.review_qps * 2)),
                latency_budget_ms=5_500,
            ),
            operation=confirm_and_publish_review,
            metadata={
                "evidenceClass": "productionPostgresReviewProjectionAndWorker",
                "membersPerTransaction": 2,
                "realModel": False,
            },
        )
        review_metric = latency_summary(
            name="atomicFormalFactReviewTransaction",
            latencies=review_transaction_latencies,
            budget_ms=500,
            failure_count=review_failures,
            metadata={
                "evidenceClass": "productionPostgresAtomicGroupReview",
                "membersPerTransaction": 2,
                "asyncProjectionExcluded": True,
                "scheduledWorkflowFailureCodes": review_workflow_metric["failureCodes"],
            },
        )
        visibility_metric = latency_summary(
            name="formalCommitToSearchVisibility",
            latencies=visibility_latencies,
            budget_ms=5_000,
            failure_count=visibility_failures,
            metadata={
                "evidenceClass": "productionPostgresProjectionAndWorker",
                "realModel": False,
                "supersededIndexAccepted": False,
                "measuredFromEachCommit": True,
                "scheduledWorkflowFailureCodes": review_workflow_metric["failureCodes"],
            },
        )

        extraction_metric = run_candidate_extraction(
            dsn=test_dsn,
            store=store,
            profile=profile,
        )
        drain_limit = max(100, review_samples * 4 + 10)
        worker_drain = {
            "memoryProjection": drain_worker_until_idle(
                OwnerTruthMemoryProjectionWorkerRuntime(
                    settings=worker_settings,
                    store=store,
                    worker_id="owner-truth-dfx-final-projection-drain",
                    retry_seconds=1,
                ),
                accepted_statuses={"completed"},
                maximum_runs=drain_limit,
                description="memory projection worker",
            ),
            "searchEmbedding": drain_worker_until_idle(
                OwnerTruthMemorySearchEmbeddingWorkerRuntime(
                    settings=worker_settings,
                    store=store,
                    provider=provider,
                    worker_id="owner-truth-dfx-final-embedding-drain",
                ),
                accepted_statuses={"completed"},
                maximum_runs=drain_limit,
                description="search embedding worker",
            ),
            "businessMessageProjection": drain_worker_until_idle(
                BusinessMessageProjectionWorkerRuntime(
                    settings=worker_settings,
                    store=store,
                    worker_id="owner-truth-dfx-final-message-drain",
                    retry_seconds=1,
                ),
                accepted_statuses={"completed"},
                maximum_runs=drain_limit,
                description="business message projection worker",
            ),
        }
        after_db = database_observation(test_dsn, store=store)
        after_process = process_observation()
        queue_growth_by_job_type = async_backlog_growth(before_db, after_db)
        queue_drain_metric = {
            "schemaVersion": "owner-truth-dfx-operation-v1",
            "name": "asyncWorkerQueueDrain",
            "status": (
                "passed"
                if after_db["asyncBacklog"] == 0 and after_db["embeddingBacklog"] == 0
                else "failed"
            ),
            "completedSamples": sum(
                int(value["completedRuns"]) for value in worker_drain.values()
            ),
            "successCount": sum(
                int(value["completedRuns"]) for value in worker_drain.values()
            ),
            "failureCount": 0,
            "timeoutCount": 0,
            "failureCodes": {},
            "latencyMs": None,
            "latencyBudgetMs": None,
            "metadata": {
                "evidenceClass": "productionPostgresWorkers",
                "afterAsyncBacklog": after_db["asyncBacklog"],
                "afterEmbeddingBacklog": after_db["embeddingBacklog"],
                "growthByJobType": queue_growth_by_job_type,
                "workerDrain": worker_drain,
            },
        }
        operations = [
            receipt_metric,
            retrieval_metric,
            snapshot_metric,
            review_metric,
            visibility_metric,
            extraction_metric,
            queue_drain_metric,
        ]
        report = {
            "schemaVersion": "owner-truth-dfx-postgres-load-v1",
            "status": (
                "passed" if all(item["status"] == "passed" for item in operations) else "failed"
            ),
            "evidenceClass": "realPostgresPgvectorNoExternalModel",
            "realDatabase": True,
            "realPgvector": True,
            "realModel": False,
            "profile": {
                "name": profile.name,
                "onlineUsers": profile.online_users,
                "durationSecondsPerScheduledOperation": profile.duration_seconds,
                "retrievalQps": profile.retrieval_qps,
                "receiptQps": profile.receipt_qps,
                "reviewQps": profile.review_qps,
                "extractionConcurrency": profile.extraction_concurrency,
                "searchOwnerCount": profile.search_owner_count,
                "factsPerSearchOwner": profile.facts_per_search_owner,
                "totalSeededSearchFacts": (
                    profile.search_owner_count * profile.facts_per_search_owner
                ),
            },
            "software": {
                "pythonVersion": platform.python_version(),
                "platform": platform.platform(),
                "machine": platform.machine(),
                "postgresVersion": after_db["postgresVersion"],
                "pgvectorVersion": after_db["pgvectorVersion"],
                "migrationHead": verified["expectedHead"],
                "appliedMigrationCount": len(migration_result["appliedVersions"]),
                "embeddingContract": (
                    f"{OWNER_TRUTH_EMBEDDING_MIGRATED_MODEL.model_id}:"
                    f"{OWNER_TRUTH_EMBEDDING_MIGRATED_MODEL.model_version}:"
                    f"{OWNER_TRUTH_EMBEDDING_MIGRATED_MODEL.dimensions}"
                ),
            },
            "hardware": {
                "logicalCpuCount": os.cpu_count(),
                "memoryEvidence": "processMaxRssPlatformUnits",
            },
            "operations": operations,
            "reviewWorkflowSchedule": review_workflow_metric,
            "resources": {
                "before": {"database": before_db, "process": before_process},
                "after": {"database": after_db, "process": after_process},
                "queueGrowth": {
                    "asyncBacklog": after_db["asyncBacklog"] - before_db["asyncBacklog"],
                    "asyncBacklogByJobType": queue_growth_by_job_type,
                    "embeddingBacklog": (
                        after_db["embeddingBacklog"] - before_db["embeddingBacklog"]
                    ),
                },
            },
            "cost": {
                "externalProviderCalls": 0,
                "amount": 0,
                "currency": None,
                "realModelCostMeasured": False,
            },
            "notProvenByThisReport": [
                "externalEmbeddingLatencyOrQuality",
                "DeepSeekLatencyOrQuality",
                "VolcengineLiveFirstAudioOrInterruption",
                "deployedHttpsAuthenticationPath",
                "productionCapacity",
                "backupRpoOrRestoreRto",
            ],
        }
        encoded = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
        if args.result is not None:
            args.result.parent.mkdir(parents=True, exist_ok=True)
            args.result.write_text(encoded + "\n", encoding="utf-8")
        print(encoded)
        return 0 if report["status"] == "passed" else 1
    finally:
        if store is not None:
            store.close_pool()
        try:
            drop_database(admin_dsn, database_name)
        except Exception as error:  # pragma: no cover - cleanup diagnostics only
            print(
                f"warning: failed to drop temporary database: {type(error).__name__}",
                file=sys.stderr,
            )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
