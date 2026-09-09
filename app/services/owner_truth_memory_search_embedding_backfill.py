"""Durable, checkpoint-fenced backfill for Owner Truth SearchDocument vectors.

The formal-memory write and its Outbox remain the only authority-changing
transaction.  This module runs later as a derived worker: it claims current
SearchDocument jobs in PostgreSQL, calls the configured embedding provider
outside a database transaction, then rechecks scope/hash/checkpoint before
persisting a vector.  A correction therefore cannot be overwritten by an old
provider response, and a provider outage becomes a bounded retry rather than a
partial formal-memory write.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Mapping, Sequence

from app.services.owner_truth_memory_search_hybrid import (
    OwnerTruthEmbeddingModel,
    OwnerTruthMemorySearchHybridUnavailable,
    OwnerTruthQueryEmbedding,
)


OWNER_TRUTH_MEMORY_SEARCH_EMBEDDING_BACKFILL_SCHEMA_VERSION = (
    "owner-truth-memory-search-embedding-backfill-v1"
)


class OwnerTruthMemorySearchEmbeddingBackfillError(
    OwnerTruthMemorySearchHybridUnavailable
):
    """A derived embedding task cannot be safely queued, claimed, or written."""


@dataclass(frozen=True)
class OwnerTruthMemorySearchEmbeddingTask:
    vault_id: str
    owner_subject_id: str
    authority_epoch: int
    memory_version_id: str
    content_hash: str
    source_projection_checkpoint: str
    model: OwnerTruthEmbeddingModel
    search_text: str
    attempt: int

    def __post_init__(self) -> None:
        for field in (
            "vault_id",
            "owner_subject_id",
            "memory_version_id",
            "content_hash",
            "source_projection_checkpoint",
            "search_text",
        ):
            if not isinstance(getattr(self, field), str) or not getattr(self, field).strip():
                raise OwnerTruthMemorySearchEmbeddingBackfillError(
                    f"embedding task {field} is required"
                )
        if (
            not isinstance(self.authority_epoch, int)
            or isinstance(self.authority_epoch, bool)
            or self.authority_epoch < 0
        ):
            raise OwnerTruthMemorySearchEmbeddingBackfillError(
                "embedding task authority_epoch is invalid"
            )
        if (
            not isinstance(self.attempt, int)
            or isinstance(self.attempt, bool)
            or self.attempt < 1
        ):
            raise OwnerTruthMemorySearchEmbeddingBackfillError(
                "embedding task attempt is invalid"
            )


@dataclass(frozen=True)
class OwnerTruthMemorySearchEmbeddingCompletion:
    ready_count: int
    stale_count: int


class PostgresOwnerTruthMemorySearchEmbeddingRepository:
    """Private task/embedding persistence bound to an active PostgreSQL UoW."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def enqueue_missing(
        self,
        *,
        model: OwnerTruthEmbeddingModel,
        limit: int,
    ) -> int:
        """Queue changed/current documents without provider I/O.

        A matching ready task is not mutated.  Any hash, checkpoint or model
        dimension change resets only that derived job to ``queued``.
        """

        bounded_limit = _bounded_limit(limit)
        with self._cursor() as cursor:
            cursor.execute(
                """
                WITH current_documents AS (
                    SELECT
                        document.vault_id,
                        document.authority_epoch,
                        document.memory_version_id,
                        checkpoint.owner_subject_id,
                        document.content_hash,
                        checkpoint.source_projection_checkpoint
                    FROM owner_truth.search_documents AS document
                    JOIN owner_truth.search_document_checkpoints AS checkpoint
                      ON checkpoint.vault_id = document.vault_id
                     AND checkpoint.authority_epoch = document.authority_epoch
                    JOIN owner_truth.vaults AS vault
                      ON vault.vault_id = document.vault_id
                    LEFT JOIN owner_truth.search_document_embedding_jobs AS job
                      ON job.vault_id = document.vault_id
                     AND job.authority_epoch = document.authority_epoch
                     AND job.memory_version_id = document.memory_version_id
                     AND job.embedding_model_id = %s
                     AND job.embedding_model_version = %s
                    WHERE checkpoint.state = 'ready'
                      AND vault.status = 'active'
                      AND vault.owner_subject_id = checkpoint.owner_subject_id
                      AND vault.authority_epoch = document.authority_epoch
                      AND (
                          job.memory_version_id IS NULL
                          OR job.content_hash IS DISTINCT FROM document.content_hash
                          OR job.source_projection_checkpoint
                             IS DISTINCT FROM checkpoint.source_projection_checkpoint
                          OR job.embedding_dimensions IS DISTINCT FROM %s
                      )
                    ORDER BY document.vault_id, document.authority_epoch,
                        document.memory_version_id
                    LIMIT %s
                )
                INSERT INTO owner_truth.search_document_embedding_jobs (
                    vault_id, authority_epoch, memory_version_id, owner_subject_id,
                    content_hash, source_projection_checkpoint,
                    embedding_model_id, embedding_model_version, embedding_dimensions,
                    state, attempts, available_at, updated_at
                )
                SELECT
                    vault_id, authority_epoch, memory_version_id, owner_subject_id,
                    content_hash, source_projection_checkpoint,
                    %s, %s, %s,
                    'queued', 0, NOW(), NOW()
                FROM current_documents
                ON CONFLICT (
                    vault_id, authority_epoch, memory_version_id,
                    embedding_model_id, embedding_model_version
                ) DO UPDATE SET
                    owner_subject_id = EXCLUDED.owner_subject_id,
                    content_hash = EXCLUDED.content_hash,
                    source_projection_checkpoint = EXCLUDED.source_projection_checkpoint,
                    embedding_dimensions = EXCLUDED.embedding_dimensions,
                    state = 'queued',
                    attempts = 0,
                    available_at = NOW(),
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    last_error_code = NULL,
                    updated_at = NOW()
                """,
                (
                    model.model_id,
                    model.model_version,
                    model.dimensions,
                    bounded_limit,
                    model.model_id,
                    model.model_version,
                    model.dimensions,
                ),
            )
            return max(0, int(cursor.rowcount or 0))

    def claim_batch(
        self,
        *,
        worker_id: str,
        model: OwnerTruthEmbeddingModel,
        limit: int,
        lease_seconds: int,
    ) -> tuple[OwnerTruthMemorySearchEmbeddingTask, ...]:
        """Lease only current active-Vault jobs; stale rows never reach a provider."""

        worker_id = _required_text(worker_id, field="embedding worker_id")
        bounded_limit = _bounded_limit(limit)
        bounded_lease_seconds = max(1, min(900, int(lease_seconds)))
        with self._cursor() as cursor:
            cursor.execute(
                """
                WITH candidates AS (
                    SELECT
                        job.vault_id,
                        job.authority_epoch,
                        job.memory_version_id,
                        job.embedding_model_id,
                        job.embedding_model_version
                    FROM owner_truth.search_document_embedding_jobs AS job
                    JOIN owner_truth.search_documents AS document
                      ON document.vault_id = job.vault_id
                     AND document.authority_epoch = job.authority_epoch
                     AND document.memory_version_id = job.memory_version_id
                    JOIN owner_truth.search_document_checkpoints AS checkpoint
                      ON checkpoint.vault_id = job.vault_id
                     AND checkpoint.authority_epoch = job.authority_epoch
                    JOIN owner_truth.vaults AS vault
                      ON vault.vault_id = job.vault_id
                    WHERE job.embedding_model_id = %s
                      AND job.embedding_model_version = %s
                      AND job.embedding_dimensions = %s
                      AND job.state IN ('queued', 'retry', 'leased')
                      AND job.available_at <= NOW()
                      AND (job.state <> 'leased' OR job.lease_expires_at <= NOW())
                      AND vault.status = 'active'
                      AND vault.owner_subject_id = job.owner_subject_id
                      AND vault.authority_epoch = job.authority_epoch
                      AND checkpoint.state = 'ready'
                      AND checkpoint.owner_subject_id = job.owner_subject_id
                      AND checkpoint.source_projection_checkpoint
                          = job.source_projection_checkpoint
                      AND document.content_hash = job.content_hash
                    ORDER BY job.available_at ASC, job.updated_at ASC,
                        job.memory_version_id ASC
                    FOR UPDATE OF job SKIP LOCKED
                    LIMIT %s
                ), claimed AS (
                    UPDATE owner_truth.search_document_embedding_jobs AS job
                    SET state = 'leased',
                        attempts = job.attempts + 1,
                        lease_owner = %s,
                        lease_expires_at = NOW() + make_interval(secs => %s),
                        updated_at = NOW()
                    FROM candidates
                    WHERE job.vault_id = candidates.vault_id
                      AND job.authority_epoch = candidates.authority_epoch
                      AND job.memory_version_id = candidates.memory_version_id
                      AND job.embedding_model_id = candidates.embedding_model_id
                      AND job.embedding_model_version = candidates.embedding_model_version
                    RETURNING job.*
                )
                SELECT
                    claimed.vault_id,
                    claimed.owner_subject_id,
                    claimed.authority_epoch,
                    claimed.memory_version_id,
                    claimed.content_hash,
                    claimed.source_projection_checkpoint,
                    claimed.embedding_model_id,
                    claimed.embedding_model_version,
                    claimed.embedding_dimensions,
                    claimed.attempts,
                    document.search_text
                FROM claimed
                JOIN owner_truth.search_documents AS document
                  ON document.vault_id = claimed.vault_id
                 AND document.authority_epoch = claimed.authority_epoch
                 AND document.memory_version_id = claimed.memory_version_id
                ORDER BY claimed.memory_version_id ASC
                """,
                (
                    model.model_id,
                    model.model_version,
                    model.dimensions,
                    bounded_limit,
                    worker_id,
                    bounded_lease_seconds,
                ),
            )
            return tuple(self._task_from_row(row) for row in cursor.fetchall())

    def complete_batch(
        self,
        *,
        worker_id: str,
        tasks: Sequence[OwnerTruthMemorySearchEmbeddingTask],
        vectors: Sequence[Sequence[float]],
    ) -> OwnerTruthMemorySearchEmbeddingCompletion:
        """Persist only still-current vectors and mark replaced jobs stale."""

        worker_id = _required_text(worker_id, field="embedding worker_id")
        if len(tasks) != len(vectors):
            raise OwnerTruthMemorySearchEmbeddingBackfillError(
                "embedding completion task/vector count does not match"
            )
        ready_count = 0
        stale_count = 0
        with self._cursor() as cursor:
            for task, vector in zip(tasks, vectors):
                embedding = OwnerTruthQueryEmbedding(model=task.model, values=tuple(vector))
                cursor.execute(
                    """
                    SELECT job.memory_version_id
                    FROM owner_truth.search_document_embedding_jobs AS job
                    JOIN owner_truth.search_documents AS document
                      ON document.vault_id = job.vault_id
                     AND document.authority_epoch = job.authority_epoch
                     AND document.memory_version_id = job.memory_version_id
                    JOIN owner_truth.search_document_checkpoints AS checkpoint
                      ON checkpoint.vault_id = job.vault_id
                     AND checkpoint.authority_epoch = job.authority_epoch
                    JOIN owner_truth.vaults AS vault
                      ON vault.vault_id = job.vault_id
                    WHERE job.vault_id = %s
                      AND job.authority_epoch = %s
                      AND job.memory_version_id = %s
                      AND job.embedding_model_id = %s
                      AND job.embedding_model_version = %s
                      AND job.state = 'leased'
                      AND job.lease_owner = %s
                      AND job.content_hash = %s
                      AND job.source_projection_checkpoint = %s
                      AND job.embedding_dimensions = %s
                      AND document.content_hash = job.content_hash
                      AND checkpoint.state = 'ready'
                      AND checkpoint.owner_subject_id = job.owner_subject_id
                      AND checkpoint.source_projection_checkpoint
                          = job.source_projection_checkpoint
                      AND vault.status = 'active'
                      AND vault.owner_subject_id = job.owner_subject_id
                      AND vault.authority_epoch = job.authority_epoch
                    FOR UPDATE OF job
                    """,
                    (
                        task.vault_id,
                        task.authority_epoch,
                        task.memory_version_id,
                        task.model.model_id,
                        task.model.model_version,
                        worker_id,
                        task.content_hash,
                        task.source_projection_checkpoint,
                        task.model.dimensions,
                    ),
                )
                if cursor.fetchone() is None:
                    cursor.execute(
                        """
                        UPDATE owner_truth.search_document_embedding_jobs
                        SET state = 'stale', lease_owner = NULL, lease_expires_at = NULL,
                            updated_at = NOW()
                        WHERE vault_id = %s
                          AND authority_epoch = %s
                          AND memory_version_id = %s
                          AND embedding_model_id = %s
                          AND embedding_model_version = %s
                          AND state = 'leased'
                          AND lease_owner = %s
                        """,
                        (
                            task.vault_id,
                            task.authority_epoch,
                            task.memory_version_id,
                            task.model.model_id,
                            task.model.model_version,
                            worker_id,
                        ),
                    )
                    stale_count += 1
                    continue
                cursor.execute(
                    """
                    INSERT INTO owner_truth.search_document_embeddings (
                        vault_id, authority_epoch, memory_version_id, content_hash,
                        source_projection_checkpoint, embedding_model_id,
                        embedding_model_version, embedding_dimensions, embedding
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector)
                    ON CONFLICT (
                        vault_id, authority_epoch, memory_version_id,
                        embedding_model_id, embedding_model_version
                    ) DO UPDATE SET
                        content_hash = EXCLUDED.content_hash,
                        source_projection_checkpoint = EXCLUDED.source_projection_checkpoint,
                        embedding_dimensions = EXCLUDED.embedding_dimensions,
                        embedding = EXCLUDED.embedding,
                        created_at = NOW()
                    """,
                    (
                        task.vault_id,
                        task.authority_epoch,
                        task.memory_version_id,
                        task.content_hash,
                        task.source_projection_checkpoint,
                        task.model.model_id,
                        task.model.model_version,
                        task.model.dimensions,
                        embedding.postgres_literal(),
                    ),
                )
                cursor.execute(
                    """
                    UPDATE owner_truth.search_document_embedding_jobs
                    SET state = 'ready', lease_owner = NULL, lease_expires_at = NULL,
                        last_error_code = NULL, updated_at = NOW()
                    WHERE vault_id = %s
                      AND authority_epoch = %s
                      AND memory_version_id = %s
                      AND embedding_model_id = %s
                      AND embedding_model_version = %s
                      AND state = 'leased'
                      AND lease_owner = %s
                    """,
                    (
                        task.vault_id,
                        task.authority_epoch,
                        task.memory_version_id,
                        task.model.model_id,
                        task.model.model_version,
                        worker_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise OwnerTruthMemorySearchEmbeddingBackfillError(
                        "embedding task lease was lost during completion"
                    )
                ready_count += 1
        return OwnerTruthMemorySearchEmbeddingCompletion(
            ready_count=ready_count,
            stale_count=stale_count,
        )

    def retry_batch(
        self,
        *,
        worker_id: str,
        tasks: Sequence[OwnerTruthMemorySearchEmbeddingTask],
        retry_seconds: int,
        error_code: str,
        max_attempts: int,
    ) -> tuple[int, int]:
        """Release provider failures as bounded retry or terminal derived failure."""

        worker_id = _required_text(worker_id, field="embedding worker_id")
        error_code = _required_text(error_code, field="embedding error_code")[:128]
        retry_seconds = max(1, min(3_600, int(retry_seconds)))
        max_attempts = max(1, min(100, int(max_attempts)))
        retry_count = 0
        failed_count = 0
        with self._cursor() as cursor:
            for task in tasks:
                terminal = task.attempt >= max_attempts
                cursor.execute(
                    """
                    UPDATE owner_truth.search_document_embedding_jobs
                    SET state = %s,
                        available_at = CASE
                            WHEN %s THEN available_at
                            ELSE NOW() + make_interval(secs => %s)
                        END,
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        last_error_code = %s,
                        updated_at = NOW()
                    WHERE vault_id = %s
                      AND authority_epoch = %s
                      AND memory_version_id = %s
                      AND embedding_model_id = %s
                      AND embedding_model_version = %s
                      AND state = 'leased'
                      AND lease_owner = %s
                    """,
                    (
                        "failed" if terminal else "retry",
                        terminal,
                        retry_seconds,
                        error_code,
                        task.vault_id,
                        task.authority_epoch,
                        task.memory_version_id,
                        task.model.model_id,
                        task.model.model_version,
                        worker_id,
                    ),
                )
                if cursor.rowcount:
                    if terminal:
                        failed_count += 1
                    else:
                        retry_count += 1
        return retry_count, failed_count

    def readiness_summary(self, *, model: OwnerTruthEmbeddingModel) -> dict[str, int]:
        """Return counts only; no search text, source IDs, or user values."""

        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT state, COUNT(*) AS count
                FROM owner_truth.search_document_embedding_jobs
                WHERE embedding_model_id = %s
                  AND embedding_model_version = %s
                  AND embedding_dimensions = %s
                GROUP BY state
                """,
                (model.model_id, model.model_version, model.dimensions),
            )
            counts = {str(row["state"]): int(row["count"]) for row in cursor.fetchall()}
        return {
            "queued": counts.get("queued", 0),
            "leased": counts.get("leased", 0),
            "retry": counts.get("retry", 0),
            "ready": counts.get("ready", 0),
            "failed": counts.get("failed", 0),
            "stale": counts.get("stale", 0),
        }

    @staticmethod
    def _task_from_row(row: Mapping[str, Any]) -> OwnerTruthMemorySearchEmbeddingTask:
        return OwnerTruthMemorySearchEmbeddingTask(
            vault_id=str(row["vault_id"]),
            owner_subject_id=str(row["owner_subject_id"]),
            authority_epoch=int(row["authority_epoch"]),
            memory_version_id=str(row["memory_version_id"]),
            content_hash=str(row["content_hash"]),
            source_projection_checkpoint=str(row["source_projection_checkpoint"]),
            model=OwnerTruthEmbeddingModel(
                str(row["embedding_model_id"]),
                str(row["embedding_model_version"]),
                int(row["embedding_dimensions"]),
            ),
            search_text=str(row["search_text"]),
            attempt=int(row["attempts"]),
        )

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)


def _required_text(value: object, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise OwnerTruthMemorySearchEmbeddingBackfillError(f"{field} is required")
    return text


def _bounded_limit(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise OwnerTruthMemorySearchEmbeddingBackfillError("embedding batch limit is invalid")
    return max(1, min(128, value))


__all__ = [
    "OWNER_TRUTH_MEMORY_SEARCH_EMBEDDING_BACKFILL_SCHEMA_VERSION",
    "OwnerTruthMemorySearchEmbeddingBackfillError",
    "OwnerTruthMemorySearchEmbeddingCompletion",
    "OwnerTruthMemorySearchEmbeddingTask",
    "PostgresOwnerTruthMemorySearchEmbeddingRepository",
]
