"""Current facts stay searchable while the derived vector index catches up."""
from dataclasses import replace
import unittest
from unittest.mock import patch
from tests import test_owner_truth_memory_search_hybrid as fixture
from tests import test_owner_truth_context_authority_api as api
from app.services.owner_truth_memory_search_read import OwnerTruthMemorySearchReadService
from app.services.owner_truth_memory_search_hybrid import (
    OwnerTruthMemorySearchHybridRanker, OwnerTruthHybridSearchCandidate,
    OwnerTruthQueryEmbedding, OwnerTruthEmbeddingModel,
)


class HybridLexicalContinuityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.OwnerTruthMemorySearchHybridTests()
        self.fixture.setUp()

    def read(self, candidates=(), limit=5, projection=None):
        f = self.fixture
        return OwnerTruthMemorySearchReadService(
            fixture._Store(projection or f.projection, fixture._HybridRepository(candidates)),
            hybrid_ranker=OwnerTruthMemorySearchHybridRanker(fixture._Provider()),
        ).read(context=f.context, query="我哪年大学毕业", limit=limit)

    def candidate(self, memory, rank=1, content_hash=None):
        return OwnerTruthHybridSearchCandidate(
            memory_version_id=memory.memory_version_id,
            content_hash=content_hash or memory.content_hash,
            lexical_rank=None, vector_rank=rank, rrf_score=1 / (60 + rank))

    def test_empty_vectors_do_not_erase_current_lexical_match(self):
        result = self.read()
        self.assertEqual([h.document.memory_version_id for h in result.hits], [self.fixture.first.memory_version_id])
        self.assertEqual(result.retrieval_mode, "deterministicTextFallback")
        self.assertFalse(result.semantic_ranking_available)

    def test_partial_vectors_keep_new_lexical_fact(self):
        result = self.read((self.candidate(self.fixture.second),))
        self.assertEqual({h.document.memory_version_id for h in result.hits},
                         {self.fixture.first.memory_version_id, self.fixture.second.memory_version_id})
        self.assertTrue(result.semantic_ranking_available)
        self.assertEqual([h.rank for h in result.hits], [1, 2])

    def test_stale_vector_hash_cannot_erase_or_revive_fact(self):
        result = self.read((self.candidate(self.fixture.second, content_hash="0" * 64),))
        self.assertEqual([h.document.memory_version_id for h in result.hits], [self.fixture.first.memory_version_id])
        self.assertFalse(result.semantic_ranking_available)

    def test_overlap_deduplicated_and_limit_respected(self):
        result = self.read((self.candidate(self.fixture.first), self.candidate(self.fixture.second, 2)), limit=1)
        self.assertEqual([h.document.memory_version_id for h in result.hits], [self.fixture.first.memory_version_id])
        self.assertEqual(result.hits[0].rank, 1)

    def test_absent_current_projection_never_uses_old_vector(self):
        projection = replace(self.fixture.projection, documents=())
        result = self.read((self.candidate(self.fixture.first),), projection=projection)
        self.assertEqual(result.hits, ())


class EchoHybridGapRouteTests(unittest.TestCase):
    def setUp(self):
        self.fixture = api.OwnerTruthContextAuthorityAPITests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def test_current_fact_reaches_answer_request_before_vectors_ready(self):
        f = self.fixture
        owner, headers = f._login("13800139891")
        fact = f._seed_confirmed_memory(owner)
        f._enable_authenticated_owner_v4(owner)
        payload = f._payload(owner)
        payload['query'] = '闭环试点回响'

        class Provider:
            model = OwnerTruthEmbeddingModel('bge-m3', 'v1', 4)

            def embed_query(self, *, query):
                return OwnerTruthQueryEmbedding(model=self.model, values=(0.1, 0.2, 0.3, 0.4))

        repository = fixture._HybridRepository(())
        with patch.object(api.main_module.store, 'owner_truth_memory_search_hybrid_repository', return_value=repository, create=True), patch.object(api.main_module, '_owner_truth_memory_search_hybrid_ranker', return_value=OwnerTruthMemorySearchHybridRanker(Provider())), patch.object(api.main_module.DeepSeekEchoAnswerProxy, 'request_answer', return_value='这段你确认的回忆已经在这里了。') as provider:
            response = api.client.post('/echo/answers', headers=headers, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        provider.assert_called_once()
        self.assertIn(fact.content['summary'], provider.call_args.kwargs['generation_context'])
        answer = response.json()['answer']
        self.assertEqual(answer['memoryGrounding']['outcome'], 'grounded')
        self.assertEqual(answer['citations'][0]['contentHash'], fact.content_hash)
