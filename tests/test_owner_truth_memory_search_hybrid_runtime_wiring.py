from __future__ import annotations

from dataclasses import replace
import unittest

import app.main as main_module
from app.services.owner_truth_memory_search_hybrid import (
    OwnerTruthEmbeddingModel,
)


class OwnerTruthMemorySearchHybridRuntimeWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self._settings = main_module.settings

    def tearDown(self) -> None:
        main_module.settings = self._settings

    def test_complete_server_configuration_reaches_request_search_service(self) -> None:
        main_module.settings = replace(
            self._settings,
            owner_truth_memory_search_embedding_provider="httpJson",
            owner_truth_memory_search_embedding_http_json_url="https://embedding.test/v1/embeddings",
            owner_truth_memory_search_embedding_http_json_api_key="test-secret-not-rendered",
            owner_truth_memory_search_embedding_egress_approved=True,
        )

        service = main_module._owner_truth_memory_search_read_service()
        ranker = main_module._owner_truth_memory_search_hybrid_ranker()

        self.assertIsNotNone(ranker)
        self.assertIsNotNone(service._hybrid_ranker)
        assert ranker is not None
        self.assertEqual(ranker._provider.model, OwnerTruthEmbeddingModel("bge-m3", "v1", 1024))

    def test_missing_egress_approval_keeps_semantic_ranking_disabled(self) -> None:
        main_module.settings = replace(
            self._settings,
            owner_truth_memory_search_embedding_provider="httpJson",
            owner_truth_memory_search_embedding_http_json_url="https://embedding.test/v1/embeddings",
            owner_truth_memory_search_embedding_http_json_api_key="test-secret-not-rendered",
            owner_truth_memory_search_embedding_egress_approved=False,
        )

        service = main_module._owner_truth_memory_search_read_service()

        self.assertIsNone(main_module._owner_truth_memory_search_hybrid_ranker())
        self.assertIsNone(service._hybrid_ranker)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
