from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import unittest

from app.services.owner_truth_memory_changeset_quality import (
    OWNER_TRUTH_MEMORY_CHANGESET_QUALITY_RESULT_SCHEMA_VERSION,
    evaluate_owner_truth_memory_changeset_quality,
    load_owner_truth_memory_changeset_quality_corpus,
)


FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures/owner_truth/memory_changeset_quality_zh_v1.json"
)


class OwnerTruthMemoryChangeSetQualityTests(unittest.TestCase):
    def test_corpus_expands_to_independent_dev_and_holdout_denominators(self) -> None:
        cases = load_owner_truth_memory_changeset_quality_corpus(FIXTURE)

        self.assertEqual(len(cases), 200)
        self.assertEqual(sum(case.split == "dev" for case in cases), 140)
        self.assertEqual(sum(case.split == "holdout" for case in cases), 60)
        self.assertEqual(len({case.case_id for case in cases}), 200)
        self.assertEqual(len({case.category for case in cases}), 20)
        self.assertTrue(all("person-owner" in str(case.current_memories[0].content) for case in cases))

    def test_actual_changeset_authority_passes_operation_and_temporal_gate(self) -> None:
        report = evaluate_owner_truth_memory_changeset_quality(
            load_owner_truth_memory_changeset_quality_corpus(FIXTURE)
        )

        self.assertEqual(
            report["schemaVersion"],
            OWNER_TRUTH_MEMORY_CHANGESET_QUALITY_RESULT_SCHEMA_VERSION,
        )
        self.assertEqual(report["caseCount"], 200)
        self.assertEqual(report["overallMetrics"]["accuracy"], 1.0)
        self.assertEqual(report["splitMetrics"]["holdout"]["accuracy"], 1.0)
        self.assertEqual(report["temporalMetrics"]["caseCount"], 80)
        self.assertEqual(report["temporalMetrics"]["accuracy"], 1.0)
        self.assertEqual(report["falseMergeCount"], 0)
        self.assertEqual(report["falseConflictCount"], 0)
        self.assertTrue(report["acceptance"]["passed"])
        self.assertNotIn("菌菇面", str(report))

    def test_wrong_expectation_is_reported_by_case_id_without_fact_content(self) -> None:
        cases = load_owner_truth_memory_changeset_quality_corpus(FIXTURE)
        corrupted = (replace(cases[0], expected_operation="add"), *cases[1:])

        report = evaluate_owner_truth_memory_changeset_quality(corrupted)

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["failedCaseIds"], ["C001"])
        self.assertNotIn("菌菇面", str(report))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
