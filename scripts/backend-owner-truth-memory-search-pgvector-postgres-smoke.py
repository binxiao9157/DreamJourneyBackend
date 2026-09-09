#!/usr/bin/env python3
"""Exercise the derived pgvector search lane in disposable PostgreSQL.

This smoke uses synthetic facts and a deterministic in-process vector provider
to prove database, migration, worker retry/restart, checkpoint invalidation,
HNSW planning, and hybrid retrieval behavior. It deliberately does not claim
real-model quality evidence and never touches the configured application DB.
"""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Sequence
import uuid

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.async_effects.owner_truth_memory_search_embedding_worker import (
    OwnerTruthMemorySearchEmbeddingWorkerRuntime,
)
from app.core.config import Settings, settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.domain.owner_truth.search_documents import (
    build_owner_truth_memory_search_query_plan,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_memory_projection import OwnerTruthMemoryProjectionService
from app.services.owner_truth_memory_search_embedding_runtime import (
    OWNER_TRUTH_EMBEDDING_MIGRATED_MODEL,
    build_configured_embedding_provider,
    embedding_runtime_readiness,
)
from app.services.owner_truth_memory_search_hybrid import (
    OwnerTruthMemorySearchHybridRanker,
    OwnerTruthQueryEmbedding,
    owner_truth_postgres_hybrid_search_params,
    owner_truth_postgres_hybrid_search_sql,
)
from app.services.owner_truth_memory_search_projection import (
    OwnerTruthMemorySearchDocumentProjectionService,
)
from app.services.owner_truth_memory_search_read import OwnerTruthMemorySearchReadService
from app.services.postgres_store import PostgresStore


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def canonical_hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


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


class SyntheticEmbeddingProvider:
    """Deterministic DB-only test double; never represents model evidence."""

    model = OWNER_TRUTH_EMBEDDING_MIGRATED_MODEL

    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail
        self.calls = 0

    def embed_query(self, *, query: str) -> OwnerTruthQueryEmbedding:
        return OwnerTruthQueryEmbedding(model=self.model, values=self._vector(query))

    def embed_documents(self, *, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        self.calls += 1
        if self._fail:
            raise RuntimeError("synthetic provider outage")
        return tuple(self._vector(text) for text in texts)

    @classmethod
    def _vector(cls, text: str) -> tuple[float, ...]:
        values = [0.0] * cls.model.dimensions
        normalized = str(text).casefold()
        if any(marker in normalized for marker in ("学校", "大学", "毕业", "硕士")):
            values[0] = 1.0
        elif any(marker in normalized for marker in ("职业", "工作", "产品")):
            values[1] = 1.0
        else:
            values[2] = 1.0
        return tuple(values)


def seed_formal_memory(
    dsn: str,
    *,
    vault_id: str,
    owner_subject_id: str,
    content: dict[str, Any],
    create_vault: bool,
) -> tuple[str, str]:
    source_id = str(uuid.uuid4())
    memory_id = str(uuid.uuid4())
    memory_version_id = str(uuid.uuid4())
    content_hash = canonical_hash(content)
    payload = {
        "content": content,
        "contentSchemaVersion": "owner-truth-v1",
        "evidenceRefs": [{"sourceId": source_id, "sourceVersion": 1}],
    }
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            if create_vault:
                cursor.execute(
                    "INSERT INTO owner_truth.vaults (vault_id, owner_subject_id) VALUES (%s, %s)",
                    (vault_id, owner_subject_id),
                )
            cursor.execute(
                """
                INSERT INTO owner_truth.sources (
                    id, vault_id, owner_subject_id, source_kind, content_hash,
                    policy_version, authority_epoch
                ) VALUES (%s, %s, %s, 'text', %s, 'owner-truth-v1', 0)
                """,
                (source_id, vault_id, owner_subject_id, canonical_hash({"synthetic": content})),
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
                ) VALUES (%s, %s, %s, 1, TRUE, 'owner-truth-v1', %s, %s, %s, 1)
                """,
                (memory_version_id, vault_id, memory_id, content_hash, Jsonb(payload), source_id),
            )
        connection.commit()
    return memory_id, memory_version_id


def revise_formal_memory(
    dsn: str,
    *,
    vault_id: str,
    owner_subject_id: str,
    memory_id: str,
    content: dict[str, Any],
) -> str:
    source_id = str(uuid.uuid4())
    memory_version_id = str(uuid.uuid4())
    content_hash = canonical_hash(content)
    payload = {
        "content": content,
        "contentSchemaVersion": "owner-truth-v1",
        "evidenceRefs": [{"sourceId": source_id, "sourceVersion": 1}],
    }
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO owner_truth.sources (
                    id, vault_id, owner_subject_id, source_kind, content_hash,
                    policy_version, authority_epoch
                ) VALUES (%s, %s, %s, 'text', %s, 'owner-truth-v1', 0)
                """,
                (source_id, vault_id, owner_subject_id, canonical_hash({"synthetic": content})),
            )
            cursor.execute(
                "SELECT id FROM owner_truth.memory_versions "
                "WHERE vault_id = %s AND memory_id = %s AND is_current = TRUE "
                "FOR UPDATE",
                (vault_id, memory_id),
            )
            prior_version = cursor.fetchone()
            require(prior_version is not None, "formal memory revision requires a current version")
            cursor.execute(
                "UPDATE owner_truth.memory_versions SET is_current = FALSE "
                "WHERE vault_id = %s AND memory_id = %s AND is_current = TRUE",
                (vault_id, memory_id),
            )
            require(cursor.rowcount == 1, "exactly one prior formal version must be current")
            cursor.execute(
                """
                INSERT INTO owner_truth.memory_versions (
                    id, vault_id, memory_id, version_number, is_current,
                    schema_version, content_hash, payload, source_id, source_version,
                    supersedes_version_id
                ) VALUES (%s, %s, %s, 2, TRUE, 'owner-truth-v1', %s, %s, %s, 1, %s)
                """,
                (
                    memory_version_id,
                    vault_id,
                    memory_id,
                    content_hash,
                    Jsonb(payload),
                    source_id,
                    prior_version[0],
                ),
            )
            cursor.execute(
                """
                UPDATE owner_truth.memories
                SET source_id = %s, source_version = 1, content_hash = %s,
                    row_version = row_version + 1, updated_at = NOW()
                WHERE vault_id = %s AND id = %s AND status = 'active'
                """,
                (source_id, content_hash, vault_id, memory_id),
            )
            require(cursor.rowcount == 1, "formal memory revision must update one chain")
            cursor.execute(
                """
                INSERT INTO owner_truth.memory_revisions (vault_id, revision)
                VALUES (%s, 1)
                ON CONFLICT (vault_id) DO UPDATE
                SET revision = owner_truth.memory_revisions.revision + 1, updated_at = NOW()
                """,
                (vault_id,),
            )
        connection.commit()
    return memory_version_id


def force_retry_available(dsn: str, *, worker_id: str) -> None:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE owner_truth.search_document_embedding_jobs
                SET available_at = NOW()
                WHERE state = 'retry' AND last_error_code = 'embeddingProviderUnavailable'
                """
            )
            require(cursor.rowcount >= 1, f"{worker_id} must persist at least one retry job")
        connection.commit()


def plan_uses_hnsw(dsn: str, *, query_plan: Any, query_embedding: Any) -> bool:
    explain_sql = "EXPLAIN (COSTS FALSE, FORMAT JSON) " + owner_truth_postgres_hybrid_search_sql()
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL enable_seqscan = off")
            cursor.execute(
                explain_sql,
                owner_truth_postgres_hybrid_search_params(
                    query_plan=query_plan,
                    query_embedding=query_embedding,
                ),
            )
            plan = cursor.fetchone()[0]
    return "owner_truth_search_document_embeddings_bge_m3_hnsw" in json.dumps(plan)


def current_embedding_counts(dsn: str, *, vault_id: str) -> dict[str, int]:
    with psycopg.connect(dsn) as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM owner_truth.search_documents
                     WHERE vault_id = %s) AS documents,
                    (SELECT COUNT(*) FROM owner_truth.search_document_embeddings
                     WHERE vault_id = %s) AS embeddings,
                    (SELECT COUNT(*) FROM owner_truth.search_document_embedding_jobs
                     WHERE vault_id = %s AND state = 'ready') AS ready_jobs
                """,
                (vault_id, vault_id, vault_id),
            )
            row = cursor.fetchone()
    assert row is not None
    return {key: int(row[key]) for key in ("documents", "embeddings", "ready_jobs")}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()

    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    provider_mode = os.environ.get(
        "OWNER_TRUTH_MEMORY_SEARCH_PGVECTOR_PROVIDER",
        "synthetic",
    ).strip()
    require(
        provider_mode in {"synthetic", "configured"},
        "OWNER_TRUTH_MEMORY_SEARCH_PGVECTOR_PROVIDER must be synthetic or configured",
    )
    runtime_settings = Settings.from_env() if provider_mode == "configured" else Settings()
    if provider_mode == "configured":
        require(
            os.environ.get("OWNER_TRUTH_REAL_EMBEDDING_PG_SMOKE_APPROVED") == "1",
            "real embedding PostgreSQL smoke requires explicit approval",
        )
        readiness = embedding_runtime_readiness(runtime_settings)
        provider = build_configured_embedding_provider(runtime_settings)
        require(
            provider is not None,
            f"configured embedding provider is not ready: {readiness.reason}",
        )
    else:
        provider = SyntheticEmbeddingProvider()
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_pgvector_smoke_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    store: PostgresStore | None = None

    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="owner-truth-pgvector-pipeline-smoke",
            lock_timeout_ms=1_000,
            statement_timeout_ms=30_000,
        )
        migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")

        with psycopg.connect(test_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
                require(cursor.fetchone() is not None, "pgvector extension must be installed")

        store = PostgresStore(dsn=test_dsn, pool_min_size=1, pool_max_size=4)
        store.open_pool(wait=True)
        owner_subject_id = f"terra-a-owner-{uuid.uuid4().hex[:10]}"
        vault_id = f"terra-a-vault-{uuid.uuid4().hex[:10]}"
        school_memory_id, old_school_version_id = seed_formal_memory(
            test_dsn,
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            content={"claim": "我于2019年从海州科技大学硕士毕业。", "tags": ["教育"]},
            create_vault=True,
        )
        seed_formal_memory(
            test_dsn,
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            content={"claim": "我现在从事产品设计工作。", "tags": ["职业"]},
            create_vault=False,
        )
        context = OwnerTruthCommandContext(
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            actor_subject_id=owner_subject_id,
        )
        OwnerTruthMemoryProjectionService(store).rebuild(context=context)
        search_rebuild = OwnerTruthMemorySearchDocumentProjectionService(store).rebuild(
            context=context
        )
        require(search_rebuild.projection is not None, "search projection must become ready")

        worker_settings = replace(
            runtime_settings,
            store_backend="postgres",
            owner_truth_memory_search_projection_worker_enabled=True,
            owner_truth_memory_search_embedding_worker_enabled=True,
            owner_truth_memory_search_embedding_batch_size=16,
            owner_truth_memory_search_embedding_backfill_scan_limit=128,
            owner_truth_memory_search_embedding_max_attempts=3,
        )
        failed_provider = SyntheticEmbeddingProvider(fail=True)
        failed = OwnerTruthMemorySearchEmbeddingWorkerRuntime(
            settings=worker_settings,
            store=store,
            provider=failed_provider,
            worker_id="pgvector-smoke-failing-worker",
            retry_seconds=1,
        ).run_once()
        require(failed["status"] == "retryWait", "provider failure must persist retry state")
        force_retry_available(test_dsn, worker_id="pgvector-smoke-failing-worker")

        completed = OwnerTruthMemorySearchEmbeddingWorkerRuntime(
            settings=worker_settings,
            store=store,
            provider=provider,
            worker_id="pgvector-smoke-restarted-worker",
        ).run_once()
        require(completed["status"] == "completed", "a new worker must recover retry jobs")
        counts_before = current_embedding_counts(test_dsn, vault_id=vault_id)
        require(counts_before == {"documents": 2, "embeddings": 2, "ready_jobs": 2},
                "every current search document must have one ready vector")

        search = OwnerTruthMemorySearchReadService(
            store,
            hybrid_ranker=OwnerTruthMemorySearchHybridRanker(provider),
        ).read(context=context, query="我从哪个学校毕业", limit=2)
        require(search.semantic_ranking_available, "hybrid retrieval must be reported honestly")
        require(bool(search.hits), "hybrid retrieval must return a current citation")
        require(search.hits[0].document.memory_version_id == old_school_version_id,
                "synthetic school query must rank the current school fact first")
        assert search.projection is not None and search.query_plan is not None
        query_embedding = provider.embed_query(query=search.query_plan.normalized_query)
        require(
            plan_uses_hnsw(
                test_dsn,
                query_plan=search.query_plan,
                query_embedding=query_embedding,
            ),
            "the production hybrid Top-K SQL must expose the locked HNSW index in EXPLAIN",
        )

        new_school_version_id = revise_formal_memory(
            test_dsn,
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            memory_id=school_memory_id,
            content={"claim": "我于2020年从江南理工大学硕士毕业。", "tags": ["教育"]},
        )
        stale_read = OwnerTruthMemorySearchReadService(
            store,
            hybrid_ranker=OwnerTruthMemorySearchHybridRanker(provider),
        ).read(context=context, query="我从哪个学校毕业", limit=2)
        require(stale_read.state == "rebuilding", "changed formal facts must invalidate stale search")

        OwnerTruthMemoryProjectionService(store).rebuild(context=context)
        rebuilt = OwnerTruthMemorySearchDocumentProjectionService(store).rebuild(context=context)
        require(rebuilt.projection is not None, "revised formal fact must rebuild search projection")
        recovered = OwnerTruthMemorySearchEmbeddingWorkerRuntime(
            settings=worker_settings,
            store=store,
            provider=provider,
            worker_id="pgvector-smoke-rebuild-worker",
        ).run_once()
        require(recovered["status"] == "completed", "rebuild must enqueue the replacement vector")
        counts_after = current_embedding_counts(test_dsn, vault_id=vault_id)
        require(counts_after == {"documents": 2, "embeddings": 2, "ready_jobs": 2},
                "cascade rebuild must retain vectors only for current documents")

        revised_search = OwnerTruthMemorySearchReadService(
            store,
            hybrid_ranker=OwnerTruthMemorySearchHybridRanker(provider),
        ).read(context=context, query="我从哪个学校毕业", limit=2)
        require(
            revised_search.semantic_ranking_available
            and bool(revised_search.hits)
            and revised_search.hits[0].document.memory_version_id == new_school_version_id,
            "hybrid retrieval must cite only the replacement formal-memory version",
        )
        require(
            all(hit.document.memory_version_id != old_school_version_id for hit in revised_search.hits),
            "superseded vectors must never survive projection rebuild",
        )

        report = {
            "schemaVersion": "owner-truth-pgvector-postgres-smoke-result-v1",
            "status": "passed",
            "schemaHead": verified["expectedHead"],
            "providerMode": provider_mode,
            "model": {
                "modelId": provider.model.model_id,
                "modelVersion": provider.model.model_version,
                "dimensions": provider.model.dimensions,
            },
            "syntheticInputOnly": True,
            "realModelEvidence": provider_mode == "configured",
            "retryRecovered": True,
            "hnswPlanned": True,
            "staleReadFailedClosed": True,
            "replacementOnly": True,
            "privateVaultRead": False,
            "credentialRetained": False,
        }
        encoded = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
        if args.result is not None:
            args.result.parent.mkdir(parents=True, exist_ok=True)
            args.result.write_text(encoded + "\n", encoding="utf-8")
        print(encoded)
        return 0
    finally:
        if store is not None:
            store.close_pool()
        try:
            drop_database(admin_dsn, database_name)
        except Exception as error:  # pragma: no cover - cleanup diagnostics only
            print(f"warning: failed to drop temporary database: {type(error).__name__}", file=sys.stderr)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
