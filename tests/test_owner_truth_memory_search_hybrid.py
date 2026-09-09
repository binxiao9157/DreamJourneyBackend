from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import unittest
from uuid import uuid4

from app.domain.owner_truth.memory_projection import (
    OwnerTruthMemoryProjectionInput,
    build_ready_memory_projection,
)
from app.domain.owner_truth.search_documents import (
    build_owner_truth_memory_search_query_plan,
    build_owner_truth_search_document_projection,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_memory_search_hybrid import (
    OWNER_TRUTH_MEMORY_SEARCH_HYBRID_RETRIEVAL_MODE,
    OwnerTruthEmbeddingModel,
    OwnerTruthHybridSearchCandidate,
    OwnerTruthMemorySearchHybridRanker,
    OwnerTruthMemorySearchHybridUnavailable,
    OwnerTruthQueryEmbedding,
    owner_truth_postgres_hybrid_search_params,
    owner_truth_postgres_hybrid_search_sql,
)
from app.services.owner_truth_memory_search_read import OwnerTruthMemorySearchReadService


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class _Provider:
    model = OwnerTruthEmbeddingModel("bge-m3", "v1", 4)

    def embed_query(self, *, query: str) -> OwnerTruthQueryEmbedding:
        if query != "我哪年大学毕业":
            raise OwnerTruthMemorySearchHybridUnavailable("unexpected synthetic query")
        return OwnerTruthQueryEmbedding(model=self.model, values=(0.1, 0.2, 0.3, 0.4))


class _HybridRepository:
    def __init__(self, candidates: tuple[OwnerTruthHybridSearchCandidate, ...]) -> None:
        self.candidates = candidates
        self.requests: list[tuple[object, object, object]] = []

    def search_hybrid(self, *, context, query_plan, query_embedding):
        self.requests.append((context, query_plan, query_embedding))
        return self.candidates


class _ProjectionReader:
    def __init__(self, projection):
        self.projection = projection

    def read(self, *, context):
        del context
        return self.projection


class _Store:
    def __init__(self, projection, hybrid_repository):
        self.reader = _ProjectionReader(projection)
        self.hybrid_repository = hybrid_repository

    @contextmanager
    def request_unit_of_work(self, *, correlation_id, command_id):
        del correlation_id, command_id
        yield

    def owner_truth_memory_search_document_projection_repository(self):
        return self.reader

    def owner_truth_memory_search_hybrid_repository(self):
        return self.hybrid_repository


class OwnerTruthMemorySearchHybridTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = OwnerTruthCommandContext(
            vault_id="vault-hybrid-search",
            owner_subject_id="owner-hybrid-search",
            actor_subject_id="owner-hybrid-search",
        )
        content = {"summary": "我于2016年从北京的一所大学毕业，专业是计算机。"}
        other = {"summary": "我喜欢吃东坡肉。"}
        first = OwnerTruthMemoryProjectionInput(
            memory_id=str(uuid4()), memory_version_id=str(uuid4()),
            vault_id=self.context.vault_id, owner_subject_id=self.context.owner_subject_id,
            authority_epoch=2, version_number=1, source_id=str(uuid4()), source_version=1,
            memory_kind="experience", perspective_type="firstPerson", epistemic_status="recalled",
            sensitivity="standard", content_schema_version="owner-truth-v1",
            content_hash=_hash(content), content=content,
            evidence_refs=({"sourceId": str(uuid4()), "sourceVersion": 1},),
        )
        second = OwnerTruthMemoryProjectionInput(
            memory_id=str(uuid4()), memory_version_id=str(uuid4()),
            vault_id=self.context.vault_id, owner_subject_id=self.context.owner_subject_id,
            authority_epoch=2, version_number=1, source_id=str(uuid4()), source_version=1,
            memory_kind="experience", perspective_type="firstPerson", epistemic_status="recalled",
            sensitivity="standard", content_schema_version="owner-truth-v1",
            content_hash=_hash(other), content=other,
            evidence_refs=({"sourceId": str(uuid4()), "sourceVersion": 1},),
        )
        snapshot = build_ready_memory_projection(
            vault_id=self.context.vault_id, owner_subject_id=self.context.owner_subject_id,
            authority_epoch=2, inputs=(first, second),
        )
        self.projection = build_owner_truth_search_document_projection(memory_projection=snapshot)
        assert self.projection is not None
        self.first = first
        self.second = second

    def test_hybrid_branch_rehydrates_only_current_hash_bound_documents(self) -> None:
        repository = _HybridRepository((
            OwnerTruthHybridSearchCandidate(
                memory_version_id=self.second.memory_version_id,
                content_hash=self.second.content_hash,
                lexical_rank=None, vector_rank=1, rrf_score=0.016,
            ),
            OwnerTruthHybridSearchCandidate(
                memory_version_id=self.first.memory_version_id,
                content_hash=self.first.content_hash,
                lexical_rank=1, vector_rank=2, rrf_score=0.032,
            ),
            OwnerTruthHybridSearchCandidate(
                memory_version_id=str(uuid4()), content_hash="a" * 64,
                lexical_rank=1, vector_rank=None, rrf_score=1.0,
            ),
        ))
        result = OwnerTruthMemorySearchReadService(
            _Store(self.projection, repository),
            hybrid_ranker=OwnerTruthMemorySearchHybridRanker(_Provider()),
        ).read(context=self.context, query="我哪年大学毕业", limit=5)

        self.assertEqual(result.retrieval_mode, OWNER_TRUTH_MEMORY_SEARCH_HYBRID_RETRIEVAL_MODE)
        self.assertTrue(result.semantic_ranking_available)
        self.assertEqual(
            [hit.document.memory_version_id for hit in result.hits],
            [self.first.memory_version_id, self.second.memory_version_id],
        )
        self.assertEqual(result.hits[0].match_kind, "postgresHybridRrf")
        self.assertEqual(len(repository.requests), 1)
        self.assertEqual(
            result.value_free_summary()["queryPlan"]["retrievalMode"],
            OWNER_TRUTH_MEMORY_SEARCH_HYBRID_RETRIEVAL_MODE,
        )

    def test_unavailable_hybrid_never_claims_semantic_ranking(self) -> None:
        repository = _HybridRepository(())

        class _UnavailableProvider(_Provider):
            def embed_query(self, *, query: str):
                raise OwnerTruthMemorySearchHybridUnavailable("provider not approved")

        result = OwnerTruthMemorySearchReadService(
            _Store(self.projection, repository),
            hybrid_ranker=OwnerTruthMemorySearchHybridRanker(_UnavailableProvider()),
        ).read(context=self.context, query="我哪年大学毕业", limit=5)

        self.assertEqual(result.retrieval_mode, "deterministicTextFallback")
        self.assertFalse(result.semantic_ranking_available)
        self.assertEqual(repository.requests, [])

    def test_parameterized_postgres_rrf_contract_has_vector_lexical_and_checkpoint_fences(self) -> None:
        sql = owner_truth_postgres_hybrid_search_sql()
        self.assertIn(
            "(embedding.embedding::vector(1024)) <=> %s::vector(1024)",
            sql,
        )
        self.assertIn("lexical_ranked", sql)
        self.assertIn("lexical_candidates", sql)
        self.assertIn("vector_candidates", sql)
        self.assertIn("vector_ranked", sql)
        self.assertIn("ORDER BY vector_distance ASC\n    LIMIT %s", sql)
        self.assertNotIn(
            "ORDER BY vector_distance ASC, document.memory_version_id ASC",
            sql,
        )
        self.assertIn("rrf_score", sql)
        self.assertIn("document.vault_id = %s", sql)
        self.assertIn("checkpoint.source_projection_checkpoint = %s", sql)
        self.assertIn("embedding.content_hash = document.content_hash", sql)
        self.assertIn("ILIKE ('%%' || %s || '%%')", sql)
        self.assertNotIn("{query", sql)

        params = owner_truth_postgres_hybrid_search_params(
            query_plan=build_owner_truth_memory_search_query_plan(
                projection=self.projection,
                query="我哪年大学毕业",
                limit=5,
            ),
            query_embedding=_Provider().embed_query(query="我哪年大学毕业"),
        )
        self.assertEqual(sql.count("%s"), len(params))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
