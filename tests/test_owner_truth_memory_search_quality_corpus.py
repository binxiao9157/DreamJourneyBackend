from __future__ import annotations

from pathlib import Path
import unittest

from app.services.owner_truth_memory_search_hybrid import (
    OwnerTruthEmbeddingModel,
    OwnerTruthQueryEmbedding,
)
from app.services.owner_truth_memory_search_quality_corpus import (
    OWNER_TRUTH_MEMORY_SEARCH_QUALITY_EVALUATION_SCHEMA_VERSION,
    evaluate_embedding_quality_corpus,
    load_owner_truth_memory_search_quality_corpus,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures/owner_truth/memory_search_quality_zh_v1.json"
)


class _PerfectSyntheticProvider:
    """Algorithm test double only; it is never model-quality evidence."""

    def __init__(self, cases) -> None:
        self.model = OwnerTruthEmbeddingModel("quality-test", "v1", len(cases))
        self._document_index = {case.fact_text: index for index, case in enumerate(cases)}
        self._query_index = {case.query: index for index, case in enumerate(cases)}

    def embed_documents(self, *, texts):
        return tuple(self._vector(self._document_index[text]) for text in texts)

    def embed_query(self, *, query):
        return OwnerTruthQueryEmbedding(model=self.model, values=self._vector(self._query_index[query]))

    def embed_queries(self, *, queries):
        return tuple(self.embed_query(query=query) for query in queries)

    def _vector(self, index: int) -> tuple[float, ...]:
        return tuple(1.0 if offset == index else 0.0 for offset in range(self.model.dimensions))


class OwnerTruthMemorySearchQualityCorpusTests(unittest.TestCase):
    def test_embedding_evaluation_keeps_dev_and_holdout_results_separate(self) -> None:
        cases = load_owner_truth_memory_search_quality_corpus(FIXTURE_PATH)
        result = evaluate_embedding_quality_corpus(
            provider=_PerfectSyntheticProvider(cases),
            cases=cases,
            batch_size=17,
        )
        summary = result.value_free_summary()

        self.assertEqual(
            summary["schemaVersion"], OWNER_TRUTH_MEMORY_SEARCH_QUALITY_EVALUATION_SCHEMA_VERSION
        )
        self.assertEqual(summary["caseCount"], 200)
        self.assertEqual(summary["splitMetrics"]["dev"]["caseCount"], 140)
        self.assertEqual(summary["splitMetrics"]["holdout"]["caseCount"], 60)
        self.assertEqual(summary["splitMetrics"]["holdout"]["top10Recall"], 1.0)
        self.assertEqual(summary["overallMetrics"]["top10Recall"], 1.0)
        self.assertTrue(summary["acceptance"]["passed"])
        self.assertEqual(summary["failedTop10CaseCount"], 0)
        self.assertEqual(summary["failedTop10CaseIds"], [])
        self.assertEqual(len(summary["categoryMetrics"]), 20)
        self.assertNotIn("晨光大学", str(summary))
        self.assertNotIn("菌菇面", str(summary))

    def test_embedding_evaluation_fails_closed_and_lists_only_synthetic_case_ids(self) -> None:
        cases = load_owner_truth_memory_search_quality_corpus(FIXTURE_PATH)

        class PoorProvider(_PerfectSyntheticProvider):
            def embed_query(self, *, query):
                del query
                return OwnerTruthQueryEmbedding(model=self.model, values=self._vector(0))

        result = evaluate_embedding_quality_corpus(
            provider=PoorProvider(cases),
            cases=cases,
            batch_size=19,
        )
        summary = result.value_free_summary()

        self.assertFalse(result.acceptance_passed)
        self.assertFalse(summary["acceptance"]["passed"])
        self.assertLess(summary["overallMetrics"]["top10Recall"], 0.95)
        self.assertGreater(summary["failedTop10CaseCount"], 0)
        self.assertTrue(
            all(identifier.startswith("Q") for identifier in summary["failedTop10CaseIds"])
        )
        self.assertNotIn("晨光大学", str(summary))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
