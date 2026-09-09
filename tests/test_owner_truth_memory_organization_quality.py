from __future__ import annotations

from pathlib import Path
import unittest

from app.services.owner_truth_memory_organization_quality import (
    OwnerTruthOrganizationQualityCaseResult,
    organization_quality_summary,
    score_organization_quality_case,
    provider_error_case_result,
)
from app.services.owner_truth_memory_search_quality_corpus import (
    load_owner_truth_memory_search_quality_corpus,
)


ROOT_DIR = Path(__file__).resolve().parents[1]
CORPUS = ROOT_DIR / "tests/fixtures/owner_truth/memory_search_quality_zh_v1.json"


class OwnerTruthMemoryOrganizationQualityTests(unittest.TestCase):
    def test_provider_error_descriptor_is_value_free(self) -> None:
        case = load_owner_truth_memory_search_quality_corpus(CORPUS)[0]

        result = provider_error_case_result(case, code="typed_schema")
        descriptor = result.value_free_descriptor()

        self.assertTrue(descriptor["providerError"])
        self.assertEqual(descriptor["providerErrorCode"], "typed_schema")
        self.assertNotIn("message", descriptor)

    def test_supported_candidate_counts_anchor_and_owner_stated_evidence(self) -> None:
        case = load_owner_truth_memory_search_quality_corpus(CORPUS)[0]
        result = score_organization_quality_case(
            case=case,
            organization={
                "memories": [
                    {
                        "memoryKind": "experience",
                        "content": {
                            "event": "我在晨光大学完成了本科阶段的学习。",
                            "facets": {
                                "places": [
                                    {
                                        "value": "晨光大学",
                                        "evidenceMode": "ownerStated",
                                    }
                                ]
                            },
                        },
                    }
                ]
            },
        )

        self.assertEqual(result.candidate_count, 1)
        self.assertEqual(result.supported_candidate_count, 1)
        self.assertEqual(result.evidence_claim_count, 2)
        self.assertEqual(result.supported_evidence_claim_count, 2)
        self.assertTrue(result.anchor_recalled)

    def test_new_number_and_unsupported_facet_fail_closed(self) -> None:
        case = load_owner_truth_memory_search_quality_corpus(CORPUS)[0]
        result = score_organization_quality_case(
            case=case,
            organization={
                "memories": [
                    {
                        "memoryKind": "experience",
                        "content": {
                            "event": "我在2020年从晨光大学毕业。",
                            "facets": {
                                "places": [
                                    {
                                        "value": "北辰大学",
                                        "evidenceMode": "ownerStated",
                                    }
                                ]
                            },
                        },
                    }
                ]
            },
        )

        self.assertEqual(result.supported_candidate_count, 0)
        self.assertEqual(result.supported_evidence_claim_count, 0)
        summary = organization_quality_summary(
            [result],
            expected_case_count=1,
            model_id="synthetic-test",
            prompt_version="test-v1",
            required_splits=(result.split,),
        )
        self.assertEqual(summary["status"], "failed")
        self.assertFalse(summary["acceptance"]["passed"])
        self.assertFalse(summary["responseContentRetained"])

    def test_question_or_unconfirmed_hypothesis_candidate_is_false_admission(self) -> None:
        case = next(
            item
            for item in load_owner_truth_memory_search_quality_corpus(CORPUS)
            if item.category == "questionAndHypothesis"
        )
        result = score_organization_quality_case(
            case=case,
            organization={
                "memories": [
                    {
                        "memoryKind": "knowledge",
                        "content": {
                            "statement": case.fact_text,
                            "facets": {},
                        },
                    }
                ]
            },
        )

        self.assertFalse(result.expected_candidate)
        self.assertEqual(result.unexpected_candidate_count, 1)
        self.assertFalse(result.expectation_met)
        summary = organization_quality_summary(
            [result],
            expected_case_count=1,
            model_id="synthetic-test",
            prompt_version="test-v1",
            required_splits=(result.split,),
        )
        self.assertEqual(summary["metrics"]["falseAdmissionRate"], 1.0)
        self.assertEqual(summary["falseAdmissionCaseIds"], [case.case_id])
        self.assertEqual(summary["status"], "failed")

    def test_empty_question_case_passes_no_candidate_expectation(self) -> None:
        corpus = load_owner_truth_memory_search_quality_corpus(CORPUS)
        positive_case = corpus[0]
        negative_case = next(
            item for item in corpus if item.category == "questionAndHypothesis"
        )
        positive_result = score_organization_quality_case(
            case=positive_case,
            organization={
                "memories": [
                    {
                        "memoryKind": "experience",
                        "content": {
                            "event": positive_case.fact_text,
                            "facets": {},
                        },
                    }
                ]
            },
        )
        negative_result = score_organization_quality_case(
            case=negative_case,
            organization={"memories": []},
        )

        self.assertTrue(negative_result.expectation_met)
        summary = organization_quality_summary(
            [positive_result, negative_result],
            expected_case_count=2,
            model_id="synthetic-test",
            prompt_version="test-v1",
            required_splits=(positive_result.split,),
        )
        self.assertEqual(summary["metrics"]["falseAdmissionRate"], 0.0)
        self.assertEqual(summary["metrics"]["missRate"], 0.0)
        self.assertTrue(summary["splitMetrics"][positive_result.split]["passed"])
        self.assertIn(positive_case.category, summary["categoryMetrics"])
        self.assertIn(negative_case.category, summary["categoryMetrics"])
        self.assertEqual(summary["status"], "passed")

    def test_holdout_failure_cannot_hide_inside_passing_overall_rate(self) -> None:
        dev_results = [
            OwnerTruthOrganizationQualityCaseResult(
                case_id=f"Q{index:03d}",
                split="dev",
                category="school",
                candidate_count=1,
                supported_candidate_count=1,
                evidence_claim_count=1,
                supported_evidence_claim_count=1,
                anchor_recalled=True,
            )
            for index in range(1, 20)
        ]
        holdout_result = OwnerTruthOrganizationQualityCaseResult(
            case_id="Q200",
            split="holdout",
            category="school",
            candidate_count=1,
            supported_candidate_count=1,
            evidence_claim_count=1,
            supported_evidence_claim_count=1,
            anchor_recalled=False,
        )

        summary = organization_quality_summary(
            [*dev_results, holdout_result],
            expected_case_count=20,
            model_id="synthetic-test",
            prompt_version="test-v1",
        )

        self.assertLessEqual(summary["metrics"]["missRate"], 0.05)
        self.assertTrue(summary["splitMetrics"]["dev"]["passed"])
        self.assertFalse(summary["splitMetrics"]["holdout"]["passed"])
        self.assertEqual(summary["status"], "failed")


if __name__ == "__main__":
    unittest.main()
