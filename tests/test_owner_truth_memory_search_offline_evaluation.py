from __future__ import annotations

import json
import unittest
from uuid import uuid4

from app.domain.owner_truth.memory_projection import (
    OwnerTruthMemoryProjectionInput,
    build_ready_memory_projection,
)
from app.domain.owner_truth.search_documents import build_owner_truth_search_document_projection
from app.services.owner_truth_memory_search_offline_evaluation import (
    OWNER_TRUTH_MEMORY_SEARCH_EVALUATION_METRIC_ALLOWLIST,
    OWNER_TRUTH_MEMORY_SEARCH_FORBIDDEN_ENGAGEMENT_METRICS,
    OWNER_TRUTH_MEMORY_SEARCH_OFFLINE_EVALUATION_SCHEMA_VERSION,
    OwnerTruthMemorySearchEvaluationCase,
    OwnerTruthMemorySearchEvaluationQuery,
    OwnerTruthMemorySearchOfflineEvaluationError,
    OwnerTruthMemorySearchOfflineEvaluator,
)


OWNER = "owner-memory-search-evaluation"
VAULT = "vault-memory-search-evaluation"


def _memory(*, content: dict[str, object], source_id: str | None = None) -> OwnerTruthMemoryProjectionInput:
    memory_id = str(uuid4())
    memory_version_id = str(uuid4())
    source = source_id or str(uuid4())
    return OwnerTruthMemoryProjectionInput(
        memory_id=memory_id,
        memory_version_id=memory_version_id,
        vault_id=VAULT,
        owner_subject_id=OWNER,
        authority_epoch=2,
        version_number=1,
        source_id=source,
        source_version=1,
        memory_kind="knowledge",
        perspective_type="firstPerson",
        epistemic_status="recalled",
        sensitivity="standard",
        content_schema_version="owner-truth-v1",
        content_hash=f"hash-{memory_id}",
        content=content,
        evidence_refs=({"sourceId": source, "sourceVersion": 1},),
    )


class OwnerTruthMemorySearchOfflineEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evaluator = OwnerTruthMemorySearchOfflineEvaluator()
        self.focus_memory = _memory(
            content={
                "claim": "synthetic-private-marker: 北辰计划帮助我整理职业转折。",
                "tags": ["北辰计划", "职业转折"],
            }
        )
        self.other_memory = _memory(
            content={
                "claim": "synthetic-private-marker: 旅行经历让我学会放慢节奏。",
                "tags": ["旅行经历"],
            }
        )
        snapshot = build_ready_memory_projection(
            vault_id=VAULT,
            owner_subject_id=OWNER,
            authority_epoch=2,
            inputs=(self.focus_memory, self.other_memory),
        )
        self.projection = build_owner_truth_search_document_projection(memory_projection=snapshot)
        assert self.projection is not None

    def test_synthetic_gold_case_checks_expected_rank_and_value_free_output(self) -> None:
        case = OwnerTruthMemorySearchEvaluationCase(
            case_id="lexical-gold-v1",
            projection=self.projection,
            queries=(
                OwnerTruthMemorySearchEvaluationQuery(
                    query_id="focus-topic",
                    query="北辰计划",
                    limit=2,
                    expected_memory_version_ids=(self.focus_memory.memory_version_id,),
                ),
                OwnerTruthMemorySearchEvaluationQuery(
                    query_id="no-match",
                    query="不存在的检索词",
                    limit=2,
                    expected_memory_version_ids=(),
                ),
            ),
            private_markers=(
                "synthetic-private-marker",
                "北辰计划",
                "职业转折",
                "不存在的检索词",
            ),
        )

        result = self.evaluator.evaluate_case(case)
        report = self.evaluator.evaluate((case,))

        self.assertTrue(result.passed)
        self.assertEqual(result.metrics["queryCount"], 2)
        self.assertEqual(result.metrics["expectedCitationCount"], 1)
        self.assertEqual(result.metrics["matchedExpectedCitationCount"], 1)
        self.assertEqual(result.metrics["rankOneExpectedCitationCount"], 1)
        self.assertEqual(result.metrics["missingExpectedCitationCount"], 0)
        self.assertEqual(result.metrics["unexpectedCitationCount"], 0)
        self.assertEqual(result.metrics["privateInputLeakageCount"], 0)
        self.assertTrue(report.passed)
        summary = json.dumps(report.value_free_summary(), ensure_ascii=False)
        self.assertNotIn("北辰计划", summary)
        self.assertNotIn("synthetic-private-marker", summary)
        self.assertEqual(
            report.value_free_summary()["schemaVersion"],
            OWNER_TRUTH_MEMORY_SEARCH_OFFLINE_EVALUATION_SCHEMA_VERSION,
        )

    def test_wrong_gold_expectation_is_reported_as_a_retrieval_regression(self) -> None:
        case = OwnerTruthMemorySearchEvaluationCase(
            case_id="wrong-gold-v1",
            projection=self.projection,
            queries=(
                OwnerTruthMemorySearchEvaluationQuery(
                    query_id="focus-topic",
                    query="北辰计划",
                    limit=1,
                    expected_memory_version_ids=(self.other_memory.memory_version_id,),
                ),
            ),
            private_markers=("北辰计划",),
        )

        result = self.evaluator.evaluate_case(case)

        self.assertFalse(result.passed)
        self.assertIn("expectedCitationMismatch:focus-topic", result.violation_codes)
        self.assertEqual(result.metrics["policyViolationCount"], 1)
        self.assertEqual(result.metrics["missingExpectedCitationCount"], 1)
        self.assertEqual(result.metrics["unexpectedCitationCount"], 1)

    def test_case_rejects_gold_citations_outside_the_owner_projection(self) -> None:
        with self.assertRaises(OwnerTruthMemorySearchOfflineEvaluationError):
            OwnerTruthMemorySearchEvaluationCase(
                case_id="cross-scope-gold",
                projection=self.projection,
                queries=(
                    OwnerTruthMemorySearchEvaluationQuery(
                        query_id="invalid",
                        query="北辰计划",
                        limit=1,
                        expected_memory_version_ids=(str(uuid4()),),
                    ),
                ),
                private_markers=("北辰计划",),
            )

    def test_metric_boundary_rejects_engagement_optimization_vocabulary(self) -> None:
        self.assertTrue(OWNER_TRUTH_MEMORY_SEARCH_EVALUATION_METRIC_ALLOWLIST)
        self.assertFalse(
            OWNER_TRUTH_MEMORY_SEARCH_EVALUATION_METRIC_ALLOWLIST
            & OWNER_TRUTH_MEMORY_SEARCH_FORBIDDEN_ENGAGEMENT_METRICS
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
