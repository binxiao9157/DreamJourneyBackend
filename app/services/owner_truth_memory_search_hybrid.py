"""Optional, owner-scoped PostgreSQL hybrid retrieval for formal memory.

The Owner Truth search projection remains the source of every returned
citation.  This module only ranks *current*, already-authorized
``SearchDocument`` rows.  It deliberately has no default embedding provider:
connecting a provider is an explicit product/privacy decision, rather than a
side effect of a normal text question.

When a provider is injected, the PostgreSQL repository combines a conservative
lexical branch with pgvector cosine distance through reciprocal-rank fusion.
The final hydration step rechecks the current in-memory projection's
checkpoint, content hash and version before a hit is returned.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
import re
from typing import Any, Mapping, Protocol

from app.domain.owner_truth.search_documents import (
    OwnerTruthMemorySearchHit,
    OwnerTruthMemorySearchQueryPlan,
    OwnerTruthMemorySearchReadError,
    OwnerTruthSearchDocumentProjection,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext


OWNER_TRUTH_MEMORY_SEARCH_HYBRID_RETRIEVAL_MODE = "postgresHybridRrf"
OWNER_TRUTH_MEMORY_SEARCH_HYBRID_SCHEMA_VERSION = "owner-truth-memory-search-hybrid-v1"
OWNER_TRUTH_MEMORY_SEARCH_HYBRID_RRF_K = 60
_MODEL_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")
_MODEL_VERSION = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")


class OwnerTruthMemorySearchHybridUnavailable(OwnerTruthMemorySearchReadError):
    """The optional semantic branch is not configured or not ready.

    This is intentionally distinct from an authorization or fact-integrity
    failure.  Callers may use the deterministic text branch after this error,
    but may not manufacture a semantic result.
    """


@dataclass(frozen=True)
class OwnerTruthEmbeddingModel:
    """One locked embedding model/dimension/version contract."""

    model_id: str
    model_version: str
    dimensions: int

    def __post_init__(self) -> None:
        if not isinstance(self.model_id, str) or _MODEL_ID.fullmatch(self.model_id) is None:
            raise OwnerTruthMemorySearchHybridUnavailable("embedding model_id is invalid")
        if (
            not isinstance(self.model_version, str)
            or _MODEL_VERSION.fullmatch(self.model_version) is None
        ):
            raise OwnerTruthMemorySearchHybridUnavailable("embedding model_version is invalid")
        if (
            not isinstance(self.dimensions, int)
            or isinstance(self.dimensions, bool)
            or not 1 <= self.dimensions <= 4_096
        ):
            raise OwnerTruthMemorySearchHybridUnavailable("embedding dimensions are invalid")


@dataclass(frozen=True)
class OwnerTruthQueryEmbedding:
    """A numeric query vector emitted by the explicitly selected provider."""

    model: OwnerTruthEmbeddingModel
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.model, OwnerTruthEmbeddingModel):
            raise OwnerTruthMemorySearchHybridUnavailable("embedding model is required")
        values = tuple(self.values)
        if len(values) != self.model.dimensions:
            raise OwnerTruthMemorySearchHybridUnavailable(
                "query embedding dimensions do not match the locked model"
            )
        if any(
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not isfinite(float(value))
            for value in values
        ):
            raise OwnerTruthMemorySearchHybridUnavailable("query embedding contains invalid values")
        object.__setattr__(self, "values", tuple(float(value) for value in values))

    def postgres_literal(self) -> str:
        """Render a pgvector parameter value, never SQL source text."""

        return "[" + ",".join(format(value, ".12g") for value in self.values) + "]"


@dataclass(frozen=True)
class OwnerTruthHybridSearchCandidate:
    """A private candidate row returned by PostgreSQL before final hydration."""

    memory_version_id: str
    content_hash: str
    lexical_rank: int | None
    vector_rank: int | None
    rrf_score: float

    def __post_init__(self) -> None:
        if not isinstance(self.memory_version_id, str) or not self.memory_version_id.strip():
            raise OwnerTruthMemorySearchHybridUnavailable("hybrid hit has no memory version")
        if not isinstance(self.content_hash, str) or not self.content_hash.strip():
            raise OwnerTruthMemorySearchHybridUnavailable("hybrid hit has no content hash")
        for field, value in (("lexical_rank", self.lexical_rank), ("vector_rank", self.vector_rank)):
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 1
            ):
                raise OwnerTruthMemorySearchHybridUnavailable(f"hybrid hit {field} is invalid")
        if self.lexical_rank is None and self.vector_rank is None:
            raise OwnerTruthMemorySearchHybridUnavailable("hybrid hit has no ranking branch")
        if not isinstance(self.rrf_score, (int, float)) or not isfinite(float(self.rrf_score)):
            raise OwnerTruthMemorySearchHybridUnavailable("hybrid hit rrf_score is invalid")


class OwnerTruthMemorySearchEmbeddingProvider(Protocol):
    """Privacy-reviewed provider port; implementations stay outside authority."""

    @property
    def model(self) -> OwnerTruthEmbeddingModel:
        ...

    def embed_query(self, *, query: str) -> OwnerTruthQueryEmbedding:
        ...


class OwnerTruthMemorySearchHybridRepository(Protocol):
    """Private pgvector/lexical retrieval port bound to the active UoW."""

    def search_hybrid(
        self,
        *,
        context: OwnerTruthCommandContext,
        query_plan: OwnerTruthMemorySearchQueryPlan,
        query_embedding: OwnerTruthQueryEmbedding,
    ) -> tuple[OwnerTruthHybridSearchCandidate, ...]:
        ...


@dataclass(frozen=True)
class OwnerTruthMemorySearchHybridExecution:
    """A fully rehydrated hybrid result that is safe for the read service."""

    hits: tuple[OwnerTruthMemorySearchHit, ...]
    model: OwnerTruthEmbeddingModel


class OwnerTruthMemorySearchHybridRanker:
    """Run the optional semantic lane without weakening the fact boundary."""

    def __init__(self, provider: OwnerTruthMemorySearchEmbeddingProvider) -> None:
        self._provider = provider

    def search(
        self,
        *,
        repository: OwnerTruthMemorySearchHybridRepository,
        context: OwnerTruthCommandContext,
        projection: OwnerTruthSearchDocumentProjection,
        query_plan: OwnerTruthMemorySearchQueryPlan,
    ) -> OwnerTruthMemorySearchHybridExecution:
        if context.vault_id != projection.vault_id or context.owner_subject_id != projection.owner_subject_id:
            raise OwnerTruthMemorySearchReadError("hybrid query crosses the Owner Truth scope")
        if (
            projection.vault_id != query_plan.vault_id
            or projection.owner_subject_id != query_plan.owner_subject_id
            or projection.authority_epoch != query_plan.authority_epoch
            or projection.checkpoint != query_plan.projection_checkpoint
        ):
            raise OwnerTruthMemorySearchReadError("hybrid QueryPlan is stale")
        embedding = self._provider.embed_query(query=query_plan.normalized_query)
        if embedding.model != self._provider.model:
            raise OwnerTruthMemorySearchHybridUnavailable(
                "embedding provider returned a different locked model"
            )
        candidates = repository.search_hybrid(
            context=context,
            query_plan=query_plan,
            query_embedding=embedding,
        )
        documents = {
            document.memory_version_id: document for document in projection.documents
        }
        ranked: list[tuple[float, str, OwnerTruthHybridSearchCandidate]] = []
        seen: set[str] = set()
        for candidate in candidates:
            document = documents.get(candidate.memory_version_id)
            # A stale or replaced index row is not a lower-quality hit: it is
            # excluded altogether and will be rebuilt before the next query.
            if (
                document is None
                or document.content_hash != candidate.content_hash
                or candidate.memory_version_id in seen
            ):
                continue
            seen.add(candidate.memory_version_id)
            ranked.append((float(candidate.rrf_score), candidate.memory_version_id, candidate))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        hits = tuple(
            OwnerTruthMemorySearchHit(
                document=documents[candidate.memory_version_id],
                rank=index,
                match_kind=OWNER_TRUTH_MEMORY_SEARCH_HYBRID_RETRIEVAL_MODE,
                match_count=int(candidate.lexical_rank is not None)
                + int(candidate.vector_rank is not None),
            )
            for index, (_, _, candidate) in enumerate(ranked[: query_plan.limit], start=1)
        )
        return OwnerTruthMemorySearchHybridExecution(hits=hits, model=embedding.model)


def owner_truth_postgres_hybrid_search_sql() -> str:
    """Return parameterized RRF SQL for the pgvector repository.

    All dynamic values are positional parameters.  The database query checks
    the same Vault, authority epoch, checkpoint, content hash and embedding
    model contract; callers still rehydrate against the fresh projection.
    """

    return """
WITH lexical_candidates AS (
    SELECT
        document.memory_version_id,
        document.content_hash
    FROM owner_truth.search_documents AS document
    JOIN owner_truth.search_document_checkpoints AS checkpoint
      ON checkpoint.vault_id = document.vault_id
     AND checkpoint.authority_epoch = document.authority_epoch
    JOIN owner_truth.search_document_embeddings AS embedding
      ON embedding.vault_id = document.vault_id
     AND embedding.authority_epoch = document.authority_epoch
     AND embedding.memory_version_id = document.memory_version_id
     AND embedding.content_hash = document.content_hash
     AND embedding.source_projection_checkpoint = checkpoint.source_projection_checkpoint
    WHERE document.vault_id = %s
      AND document.authority_epoch = %s
      AND checkpoint.owner_subject_id = %s
      AND checkpoint.state = 'ready'
      AND checkpoint.source_projection_checkpoint = %s
      AND embedding.embedding_model_id = %s
      AND embedding.embedding_model_version = %s
      AND embedding.embedding_dimensions = %s
      AND (
          document.search_text ILIKE ('%%' || %s || '%%')
          OR document.structured_terms::text ILIKE ('%%' || %s || '%%')
      )
    ORDER BY document.memory_version_id ASC
    LIMIT %s
),
lexical_ranked AS (
    SELECT memory_version_id, content_hash,
           row_number() OVER (ORDER BY memory_version_id ASC) AS lexical_rank
    FROM lexical_candidates
),
vector_candidates AS (
    SELECT
        document.memory_version_id,
        document.content_hash,
        (embedding.embedding::vector(1024)) <=> %s::vector(1024) AS vector_distance
    FROM owner_truth.search_document_embeddings AS embedding
    JOIN owner_truth.search_documents AS document
      ON document.vault_id = embedding.vault_id
     AND document.authority_epoch = embedding.authority_epoch
     AND document.memory_version_id = embedding.memory_version_id
     AND document.content_hash = embedding.content_hash
    JOIN owner_truth.search_document_checkpoints AS checkpoint
      ON checkpoint.vault_id = document.vault_id
     AND checkpoint.authority_epoch = document.authority_epoch
     AND checkpoint.source_projection_checkpoint = embedding.source_projection_checkpoint
    WHERE embedding.vault_id = %s
      AND embedding.authority_epoch = %s
      AND checkpoint.owner_subject_id = %s
      AND checkpoint.state = 'ready'
      AND checkpoint.source_projection_checkpoint = %s
      AND embedding.embedding_model_id = %s
      AND embedding.embedding_model_version = %s
      AND embedding.embedding_dimensions = %s
    ORDER BY vector_distance ASC
    LIMIT %s
),
vector_ranked AS (
    SELECT memory_version_id, content_hash,
           row_number() OVER (ORDER BY vector_distance ASC, memory_version_id ASC) AS vector_rank
    FROM vector_candidates
),
candidate_ids AS (
    SELECT memory_version_id, content_hash FROM lexical_ranked
    UNION
    SELECT memory_version_id, content_hash FROM vector_ranked
),
fused AS (
    SELECT candidate_ids.memory_version_id,
           candidate_ids.content_hash,
           lexical_ranked.lexical_rank,
           vector_ranked.vector_rank,
           COALESCE(1.0 / (%s + lexical_ranked.lexical_rank), 0.0)
           + COALESCE(1.0 / (%s + vector_ranked.vector_rank), 0.0) AS rrf_score
    FROM candidate_ids
    LEFT JOIN lexical_ranked
      ON lexical_ranked.memory_version_id = candidate_ids.memory_version_id
     AND lexical_ranked.content_hash = candidate_ids.content_hash
    LEFT JOIN vector_ranked
      ON vector_ranked.memory_version_id = candidate_ids.memory_version_id
     AND vector_ranked.content_hash = candidate_ids.content_hash
)
SELECT memory_version_id, content_hash, lexical_rank, vector_rank, rrf_score
FROM fused
ORDER BY rrf_score DESC, memory_version_id ASC
LIMIT %s
"""


def owner_truth_postgres_hybrid_search_params(
    *,
    query_plan: OwnerTruthMemorySearchQueryPlan,
    query_embedding: OwnerTruthQueryEmbedding,
) -> tuple[object, ...]:
    """Bind the exact lexical/vector Top-K statement without duplicating order."""

    return (
        query_plan.vault_id,
        query_plan.authority_epoch,
        query_plan.owner_subject_id,
        query_plan.projection_checkpoint,
        query_embedding.model.model_id,
        query_embedding.model.model_version,
        query_embedding.model.dimensions,
        query_plan.normalized_query,
        query_plan.normalized_query,
        query_plan.limit,
        query_embedding.postgres_literal(),
        query_plan.vault_id,
        query_plan.authority_epoch,
        query_plan.owner_subject_id,
        query_plan.projection_checkpoint,
        query_embedding.model.model_id,
        query_embedding.model.model_version,
        query_embedding.model.dimensions,
        query_plan.limit,
        OWNER_TRUTH_MEMORY_SEARCH_HYBRID_RRF_K,
        OWNER_TRUTH_MEMORY_SEARCH_HYBRID_RRF_K,
        query_plan.limit,
    )


class PostgresOwnerTruthMemorySearchHybridRepository:
    """Same-PostgreSQL pgvector execution port; no embedding provider lives here."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def search_hybrid(
        self,
        *,
        context: OwnerTruthCommandContext,
        query_plan: OwnerTruthMemorySearchQueryPlan,
        query_embedding: OwnerTruthQueryEmbedding,
    ) -> tuple[OwnerTruthHybridSearchCandidate, ...]:
        if context.actor_subject_id != context.owner_subject_id:
            raise OwnerTruthMemorySearchReadError("only the Vault Owner may search confirmed memory")
        if (
            context.vault_id != query_plan.vault_id
            or context.owner_subject_id != query_plan.owner_subject_id
        ):
            raise OwnerTruthMemorySearchReadError("hybrid query context does not match its QueryPlan")
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        try:
            with self._connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    owner_truth_postgres_hybrid_search_sql(),
                    owner_truth_postgres_hybrid_search_params(
                        query_plan=query_plan,
                        query_embedding=query_embedding,
                    ),
                )
                rows = cursor.fetchall()
        except Exception as error:  # pragma: no cover - needs real PostgreSQL
            sqlstate = str(getattr(error, "sqlstate", "") or "")
            if sqlstate in {"42P01", "42704", "42883", "0A000"}:
                raise OwnerTruthMemorySearchHybridUnavailable(
                    "pgvector hybrid index is unavailable"
                ) from error
            raise
        return tuple(
            OwnerTruthHybridSearchCandidate(
                memory_version_id=str(row["memory_version_id"]),
                content_hash=str(row["content_hash"]),
                lexical_rank=(
                    int(row["lexical_rank"])
                    if row.get("lexical_rank") is not None
                    else None
                ),
                vector_rank=(
                    int(row["vector_rank"])
                    if row.get("vector_rank") is not None
                    else None
                ),
                rrf_score=float(row["rrf_score"]),
            )
            for row in rows
        )


__all__ = [
    "OWNER_TRUTH_MEMORY_SEARCH_HYBRID_RETRIEVAL_MODE",
    "OWNER_TRUTH_MEMORY_SEARCH_HYBRID_RRF_K",
    "OWNER_TRUTH_MEMORY_SEARCH_HYBRID_SCHEMA_VERSION",
    "OwnerTruthEmbeddingModel",
    "OwnerTruthHybridSearchCandidate",
    "OwnerTruthMemorySearchEmbeddingProvider",
    "OwnerTruthMemorySearchHybridExecution",
    "OwnerTruthMemorySearchHybridRanker",
    "OwnerTruthMemorySearchHybridRepository",
    "OwnerTruthMemorySearchHybridUnavailable",
    "OwnerTruthQueryEmbedding",
    "PostgresOwnerTruthMemorySearchHybridRepository",
    "owner_truth_postgres_hybrid_search_params",
    "owner_truth_postgres_hybrid_search_sql",
]
