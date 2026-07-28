from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import unittest

from app.domain.owner_truth.knowledge_recommendations import (
    ConfirmedMemoryDimensionEvidence,
    KnowledgeDimension,
    KnowledgeDimensionProjector,
    RecommendationCandidate,
    RecommendationDecision,
    RecommendationEvidenceKind,
    RecommendationSelection,
    RecommendationSlot,
)
from app.services.owner_truth_recommendation_offline_evaluation import (
    OWNER_TRUTH_RECOMMENDATION_EVALUATION_METRIC_ALLOWLIST,
    OWNER_TRUTH_RECOMMENDATION_FORBIDDEN_ENGAGEMENT_METRICS,
    OWNER_TRUTH_RECOMMENDATION_OFFLINE_EVALUATION_SCHEMA_VERSION,
    OwnerTruthRecommendationEvaluationCase,
    OwnerTruthRecommendationOfflineEvaluator,
    RecommendationEvaluationCategory,
)


NOW = datetime(2026, 7, 29, 9, 0, tzinfo=timezone.utc)
OWNER = "owner-offline-eval"
VAULT = "vault-offline-eval"


def _evidence(
    *,
    memory_version_id: str,
    source_id: str,
    dimension: KnowledgeDimension,
    covered_facets: tuple[str, ...],
    **overrides: object,
) -> ConfirmedMemoryDimensionEvidence:
    values: dict[str, object] = {
        "memory_version_id": memory_version_id,
        "source_id": source_id,
        "vault_id": VAULT,
        "owner_subject_id": OWNER,
        "dimension": dimension,
        "covered_facets": covered_facets,
    }
    values.update(overrides)
    return ConfirmedMemoryDimensionEvidence(**values)


def _coverage():
    return KnowledgeDimensionProjector().project(
        owner_subject_id=OWNER,
        vault_id=VAULT,
        evidence=(
            _evidence(
                memory_version_id="memory-decision",
                source_id="source-decision",
                dimension=KnowledgeDimension.KEY_DECISIONS,
                covered_facets=("choice", "reason"),
            ),
            _evidence(
                memory_version_id="memory-values",
                source_id="source-values",
                dimension=KnowledgeDimension.VALUES,
                covered_facets=("priority",),
            ),
        ),
    )


def _candidate(
    candidate_id: str,
    *,
    slot: RecommendationSlot,
    target_dimension: KnowledgeDimension,
    missing_facet: str,
    evidence_refs: tuple[str, ...],
    **overrides: object,
) -> RecommendationCandidate:
    values: dict[str, object] = {
        "candidate_id": candidate_id,
        "owner_subject_id": OWNER,
        "vault_id": VAULT,
        "slot": slot,
        "thread_id": "thread-offline-eval",
        "target_dimension": target_dimension,
        "missing_facet": missing_facet,
        "question_template_id": f"template-{candidate_id}",
        "evidence_kind": RecommendationEvidenceKind.CONFIRMED_MEMORY,
        "evidence_refs": evidence_refs,
        "reason_code": f"reason-{candidate_id}",
    }
    values.update(overrides)
    return RecommendationCandidate(**values)


class OwnerTruthRecommendationOfflineEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evaluator = OwnerTruthRecommendationOfflineEvaluator()
        self.coverage = _coverage()

    def _quality_case(self) -> OwnerTruthRecommendationEvaluationCase:
        continuity = _candidate(
            "quality-continuity",
            slot=RecommendationSlot.CONTINUITY,
            target_dimension=KnowledgeDimension.KEY_DECISIONS,
            missing_facet="outcome",
            evidence_refs=("memory-decision",),
            explicit_intent_priority=3,
        )
        breadth = _candidate(
            "quality-breadth",
            slot=RecommendationSlot.BREADTH,
            target_dimension=KnowledgeDimension.VALUES,
            missing_facet="reflection",
            evidence_refs=("memory-values",),
            importance_score=4,
        )
        return OwnerTruthRecommendationEvaluationCase(
            case_id="quality-two-safe-distinct-slots",
            category=RecommendationEvaluationCategory.QUALITY,
            owner_subject_id=OWNER,
            vault_id=VAULT,
            coverage=self.coverage,
            candidates=(continuity, breadth),
            now=NOW,
            expected_selected_candidate_ids=("quality-continuity", "quality-breadth"),
            expected_filtered_reason_codes={},
        )

    def _safety_case(self) -> OwnerTruthRecommendationEvaluationCase:
        safe = _candidate(
            "safety-safe-breadth",
            slot=RecommendationSlot.BREADTH,
            target_dimension=KnowledgeDimension.VALUES,
            missing_facet="reflection",
            evidence_refs=("memory-values",),
        )
        cross_vault = _candidate(
            "safety-cross-vault",
            slot=RecommendationSlot.BREADTH,
            target_dimension=KnowledgeDimension.KEY_DECISIONS,
            missing_facet="outcome",
            evidence_refs=("memory-decision",),
            vault_id="vault-other",
        )
        ai_only = _candidate(
            "safety-ai-only",
            slot=RecommendationSlot.BREADTH,
            target_dimension=KnowledgeDimension.KEY_DECISIONS,
            missing_facet="outcome",
            evidence_refs=("memory-decision",),
            is_ai_inference_only=True,
        )
        sensitive = _candidate(
            "safety-sensitive",
            slot=RecommendationSlot.BREADTH,
            target_dimension=KnowledgeDimension.KEY_DECISIONS,
            missing_facet="outcome",
            evidence_refs=("memory-decision",),
            is_sensitive=True,
        )
        do_not_ask = _candidate(
            "safety-do-not-ask",
            slot=RecommendationSlot.CONTINUITY,
            target_dimension=KnowledgeDimension.KEY_DECISIONS,
            missing_facet="outcome",
            evidence_refs=("memory-decision",),
            is_do_not_ask=True,
        )
        return OwnerTruthRecommendationEvaluationCase(
            case_id="safety-cross-vault-sensitive-ai-do-not-ask",
            category=RecommendationEvaluationCategory.SAFETY_RED_TEAM,
            owner_subject_id=OWNER,
            vault_id=VAULT,
            coverage=self.coverage,
            candidates=(safe, cross_vault, ai_only, sensitive, do_not_ask),
            now=NOW,
            expected_selected_candidate_ids=("safety-safe-breadth",),
            expected_filtered_reason_codes={
                "safety-cross-vault": "candidateScopeMismatch",
                "safety-ai-only": "aiInferenceOnly",
                "safety-sensitive": "sensitiveWithoutRecentConsent",
                "safety-do-not-ask": "doNotAsk",
            },
        )

    def _repetition_case(self) -> OwnerTruthRecommendationEvaluationCase:
        accepted = _candidate(
            "repeat-accepted",
            slot=RecommendationSlot.CONTINUITY,
            target_dimension=KnowledgeDimension.KEY_DECISIONS,
            missing_facet="outcome",
            evidence_refs=("memory-decision",),
        )
        replaced = _candidate(
            "repeat-replaced",
            slot=RecommendationSlot.BREADTH,
            target_dimension=KnowledgeDimension.VALUES,
            missing_facet="reflection",
            evidence_refs=("memory-values",),
        )
        skipped = _candidate(
            "repeat-skipped",
            slot=RecommendationSlot.BREADTH,
            target_dimension=KnowledgeDimension.KEY_DECISIONS,
            missing_facet="outcome",
            evidence_refs=("memory-decision",),
            consecutive_skip_count=2,
        )
        reopened = _candidate(
            "repeat-reopened",
            slot=RecommendationSlot.BREADTH,
            target_dimension=KnowledgeDimension.VALUES,
            missing_facet="reflection",
            evidence_refs=("memory-values",),
            consecutive_skip_count=2,
            was_reopened_by_user=True,
        )
        return OwnerTruthRecommendationEvaluationCase(
            case_id="repetition-accepted-replaced-skip-reopen",
            category=RecommendationEvaluationCategory.REPETITION_BASELINE,
            owner_subject_id=OWNER,
            vault_id=VAULT,
            coverage=self.coverage,
            candidates=(accepted, replaced, skipped, reopened),
            now=NOW,
            expected_selected_candidate_ids=("repeat-reopened",),
            expected_filtered_reason_codes={
                "repeat-accepted": "acceptedAlready",
                "repeat-replaced": "userRequestedReplacement",
                "repeat-skipped": "repeatedSkipWithoutReopen",
            },
            accepted_candidate_ids=("repeat-accepted",),
            excluded_candidate_ids=("repeat-replaced",),
        )

    def _crisis_case(self) -> OwnerTruthRecommendationEvaluationCase:
        continuity = _candidate(
            "crisis-continuity",
            slot=RecommendationSlot.CONTINUITY,
            target_dimension=KnowledgeDimension.KEY_DECISIONS,
            missing_facet="outcome",
            evidence_refs=("memory-decision",),
        )
        breadth = _candidate(
            "crisis-breadth",
            slot=RecommendationSlot.BREADTH,
            target_dimension=KnowledgeDimension.VALUES,
            missing_facet="reflection",
            evidence_refs=("memory-values",),
        )
        return OwnerTruthRecommendationEvaluationCase(
            case_id="safety-crisis-empty-selection",
            category=RecommendationEvaluationCategory.SAFETY_RED_TEAM,
            owner_subject_id=OWNER,
            vault_id=VAULT,
            coverage=self.coverage,
            candidates=(continuity, breadth),
            now=NOW,
            crisis_active=True,
            expected_selected_candidate_ids=(),
            expected_filtered_reason_codes={
                "crisis-continuity": "crisisSafetyOverride",
                "crisis-breadth": "crisisSafetyOverride",
            },
        )

    def test_synthetic_corpus_passes_quality_safety_and_repetition_baselines(self) -> None:
        report = self.evaluator.evaluate_corpus(
            (
                self._quality_case(),
                self._safety_case(),
                self._repetition_case(),
                self._crisis_case(),
            )
        )

        self.assertTrue(report.passed)
        self.assertEqual(len(report.results), 4)
        self.assertEqual(
            report.value_free_summary(),
            {
                "caseCount": 4,
                "failedCaseIds": [],
                "passed": True,
                "schemaVersion": OWNER_TRUTH_RECOMMENDATION_OFFLINE_EVALUATION_SCHEMA_VERSION,
                "syntheticOnly": True,
            },
        )
        quality = report.results[0]
        self.assertEqual(quality.metrics["selectedCount"], 2)
        self.assertEqual(quality.metrics["duplicateQuestionCount"], 0)
        self.assertEqual(
            set(quality.metrics), OWNER_TRUTH_RECOMMENDATION_EVALUATION_METRIC_ALLOWLIST
        )
        self.assertFalse(
            set(quality.metrics).intersection(
                OWNER_TRUTH_RECOMMENDATION_FORBIDDEN_ENGAGEMENT_METRICS
            )
        )
        self.assertNotIn("private", json.dumps(quality.value_free_summary()))

    def test_independent_result_check_rejects_a_manually_injected_ai_only_selection(self) -> None:
        unsafe = _candidate(
            "manual-ai-only",
            slot=RecommendationSlot.CONTINUITY,
            target_dimension=KnowledgeDimension.KEY_DECISIONS,
            missing_facet="outcome",
            evidence_refs=("memory-decision",),
            is_ai_inference_only=True,
        )
        case = OwnerTruthRecommendationEvaluationCase(
            case_id="manual-selection-safety-oracle",
            category=RecommendationEvaluationCategory.SAFETY_RED_TEAM,
            owner_subject_id=OWNER,
            vault_id=VAULT,
            coverage=self.coverage,
            candidates=(unsafe,),
            now=NOW,
            expected_selected_candidate_ids=("manual-ai-only",),
            expected_filtered_reason_codes={},
        )
        injected = RecommendationSelection(
            owner_subject_id=OWNER,
            vault_id=VAULT,
            policy_version="m0-knowledge-dimension-v1",
            selected=(
                RecommendationDecision(
                    slot=unsafe.slot,
                    candidate_id=unsafe.candidate_id,
                    thread_id=unsafe.thread_id,
                    target_dimension=unsafe.target_dimension,
                    missing_facet=unsafe.missing_facet,
                    question_template_id=unsafe.question_template_id,
                    evidence_refs=unsafe.evidence_refs,
                    reason_code=unsafe.reason_code,
                    policy_version="m0-knowledge-dimension-v1",
                ),
            ),
            filtered=(),
        )

        result = self.evaluator.evaluate_selection(case=case, selection=injected)

        self.assertFalse(result.passed)
        self.assertIn("selectedBlockedCandidate", result.violation_codes)
        self.assertEqual(result.metrics["policyViolationCount"], 1)

    def test_corpus_manifest_matches_the_synthetic_case_inventory(self) -> None:
        fixture_path = (
            Path(__file__).parent
            / "fixtures"
            / "owner_truth"
            / "recommendation_offline_evaluation_v1.json"
        )
        manifest = json.loads(fixture_path.read_text(encoding="utf-8"))

        self.assertEqual(
            manifest["schemaVersion"],
            OWNER_TRUTH_RECOMMENDATION_OFFLINE_EVALUATION_SCHEMA_VERSION,
        )
        self.assertTrue(manifest["syntheticOnly"])
        self.assertEqual(
            set(manifest["metricAllowlist"]),
            OWNER_TRUTH_RECOMMENDATION_EVALUATION_METRIC_ALLOWLIST,
        )
        self.assertEqual(
            set(manifest["forbiddenEngagementMetrics"]),
            OWNER_TRUTH_RECOMMENDATION_FORBIDDEN_ENGAGEMENT_METRICS,
        )
        self.assertEqual(
            {item["caseId"] for item in manifest["cases"]},
            {
                "quality-two-safe-distinct-slots",
                "safety-cross-vault-sensitive-ai-do-not-ask",
                "repetition-accepted-replaced-skip-reopen",
                "safety-crisis-empty-selection",
            },
        )
        self.assertTrue(all("rawOwnerText" not in item for item in manifest["cases"]))


if __name__ == "__main__":
    unittest.main()
