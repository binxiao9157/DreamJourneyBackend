from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from app.domain.owner_truth.knowledge_recommendations import (
    ConfirmedMemoryDimensionEvidence,
    KnowledgeDimension,
    KnowledgeDimensionProjector,
    RecommendationCandidate,
    RecommendationEvidenceKind,
    RecommendationSelector,
    RecommendationSlot,
)


NOW = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
OWNER = "owner-knowledge-a"
VAULT = "vault-knowledge-a"


def evidence(
    *,
    memory_version_id: str = "memory-version-a",
    source_id: str = "source-a",
    dimension: KnowledgeDimension = KnowledgeDimension.KEY_DECISIONS,
    covered_facets: tuple[str, ...] = ("choice",),
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


def candidate(
    *,
    candidate_id: str,
    slot: RecommendationSlot,
    thread_id: str,
    target_dimension: KnowledgeDimension,
    missing_facet: str,
    **overrides: object,
) -> RecommendationCandidate:
    values: dict[str, object] = {
        "candidate_id": candidate_id,
        "owner_subject_id": OWNER,
        "vault_id": VAULT,
        "slot": slot,
        "thread_id": thread_id,
        "target_dimension": target_dimension,
        "missing_facet": missing_facet,
        "question_template_id": f"question-{candidate_id}",
        "evidence_kind": RecommendationEvidenceKind.CONFIRMED_MEMORY,
        "evidence_refs": ("memory-version-a",),
        "reason_code": f"reason-{candidate_id}",
    }
    values.update(overrides)
    return RecommendationCandidate(**values)


class KnowledgeDimensionProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.projector = KnowledgeDimensionProjector()

    def test_projection_has_exactly_six_stable_dimensions_and_only_confirmed_evidence_counts(self) -> None:
        projection = self.projector.project(
            owner_subject_id=OWNER,
            vault_id=VAULT,
            evidence=(
                evidence(covered_facets=("choice", "reason")),
                evidence(
                    memory_version_id="memory-version-pending",
                    source_id="source-pending",
                    is_current_confirmed=False,
                ),
                evidence(
                    memory_version_id="memory-version-ai",
                    source_id="source-ai",
                    is_ai_inference_only=True,
                ),
                evidence(
                    memory_version_id="memory-version-other-vault",
                    source_id="source-other-vault",
                    vault_id="vault-knowledge-b",
                ),
            ),
        )

        self.assertEqual(tuple(item.dimension for item in projection.coverage), tuple(KnowledgeDimension))
        decisions = projection.for_dimension(KnowledgeDimension.KEY_DECISIONS)
        self.assertEqual(decisions.memory_version_ids, ("memory-version-a",))
        self.assertEqual(decisions.covered_facets, ("choice", "reason"))
        self.assertEqual(decisions.missing_facets, ("outcome",))
        self.assertEqual(projection.excluded_evidence_count, 3)
        self.assertEqual(
            projection.for_dimension(KnowledgeDimension.VALUES).missing_facet_count,
            2,
        )

    def test_projection_rejects_unknown_facets_instead_of_counting_unversioned_data(self) -> None:
        with self.assertRaisesRegex(Exception, "unsupported"):
            evidence(covered_facets=("unknownFacet",))


class RecommendationSelectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.projector = KnowledgeDimensionProjector()
        self.selector = RecommendationSelector()
        self.coverage = self.projector.project(
            owner_subject_id=OWNER,
            vault_id=VAULT,
            evidence=(
                evidence(
                    dimension=KnowledgeDimension.KEY_DECISIONS,
                    covered_facets=("choice", "reason", "outcome"),
                ),
                evidence(
                    memory_version_id="memory-version-life-stage",
                    source_id="source-life-stage",
                    dimension=KnowledgeDimension.LIFE_STAGE,
                    covered_facets=("timeContext",),
                ),
            ),
        )

    def select(self, candidates: tuple[RecommendationCandidate, ...], **overrides: object):
        values: dict[str, object] = {
            "owner_subject_id": OWNER,
            "vault_id": VAULT,
            "coverage": self.coverage,
            "candidates": candidates,
            "now": NOW,
        }
        values.update(overrides)
        return self.selector.select(**values)

    def test_selects_one_continuity_and_one_breadth_candidate_with_different_thread_facets(self) -> None:
        result = self.select(
            (
                candidate(
                    candidate_id="continuity-intent",
                    slot=RecommendationSlot.CONTINUITY,
                    thread_id="thread-startup",
                    target_dimension=KnowledgeDimension.VALUES,
                    missing_facet="reflection",
                    explicit_intent_priority=10,
                ),
                candidate(
                    candidate_id="continuity-lower",
                    slot=RecommendationSlot.CONTINUITY,
                    thread_id="thread-old-job",
                    target_dimension=KnowledgeDimension.PROFESSIONAL_EXPERIENCE,
                    missing_facet="judgment",
                    continuity_score=99,
                ),
                candidate(
                    candidate_id="breadth-values",
                    slot=RecommendationSlot.BREADTH,
                    thread_id="thread-values",
                    target_dimension=KnowledgeDimension.VALUES,
                    missing_facet="priority",
                    importance_score=5,
                ),
            )
        )

        self.assertEqual([item.slot for item in result.selected], [RecommendationSlot.CONTINUITY, RecommendationSlot.BREADTH])
        self.assertEqual(result.selected[0].candidate_id, "continuity-intent")
        self.assertEqual(result.selected[1].candidate_id, "breadth-values")
        self.assertEqual(result.selected[0].policy_version, "m0-knowledge-dimension-v1")
        self.assertEqual(result.selected[1].evidence_refs, ("memory-version-a",))

    def test_breadth_prefers_dimension_with_more_confirmed_coverage_gaps(self) -> None:
        result = self.select(
            (
                candidate(
                    candidate_id="breadth-life-stage",
                    slot=RecommendationSlot.BREADTH,
                    thread_id="thread-life-stage",
                    target_dimension=KnowledgeDimension.LIFE_STAGE,
                    missing_facet="experience",
                    importance_score=100,
                ),
                candidate(
                    candidate_id="breadth-values",
                    slot=RecommendationSlot.BREADTH,
                    thread_id="thread-values",
                    target_dimension=KnowledgeDimension.VALUES,
                    missing_facet="priority",
                    importance_score=1,
                ),
            )
        )

        self.assertEqual(len(result.selected), 1)
        self.assertEqual(result.selected[0].candidate_id, "breadth-values")

    def test_candidate_rejects_a_facet_from_another_dimension(self) -> None:
        with self.assertRaisesRegex(Exception, "target_dimension"):
            candidate(
                candidate_id="wrong-facet",
                slot=RecommendationSlot.CONTINUITY,
                thread_id="thread-wrong-facet",
                target_dimension=KnowledgeDimension.KEY_DECISIONS,
                missing_facet="reflection",
            )

    def test_scope_mismatch_and_non_gap_breadth_are_filtered_before_ranking(self) -> None:
        result = self.select(
            (
                candidate(
                    candidate_id="wrong-vault",
                    slot=RecommendationSlot.CONTINUITY,
                    thread_id="thread-wrong-vault",
                    target_dimension=KnowledgeDimension.KEY_DECISIONS,
                    missing_facet="reason",
                    vault_id="vault-knowledge-b",
                ),
                candidate(
                    candidate_id="already-covered-breadth",
                    slot=RecommendationSlot.BREADTH,
                    thread_id="thread-covered",
                    target_dimension=KnowledgeDimension.KEY_DECISIONS,
                    missing_facet="choice",
                ),
            )
        )

        self.assertEqual(result.selected, ())
        self.assertEqual(
            {item.candidate_id: item.reason_code for item in result.filtered},
            {
                "wrong-vault": "candidateScopeMismatch",
                "already-covered-breadth": "facetAlreadyCovered",
            },
        )

    def test_safety_and_user_control_exclusions_are_hard_filters_before_ranking(self) -> None:
        result = self.select(
            (
                candidate(
                    candidate_id="blocked-do-not-ask",
                    slot=RecommendationSlot.CONTINUITY,
                    thread_id="thread-do-not-ask",
                    target_dimension=KnowledgeDimension.KEY_DECISIONS,
                    missing_facet="reason",
                    explicit_intent_priority=999,
                    is_do_not_ask=True,
                ),
                candidate(
                    candidate_id="blocked-sensitive",
                    slot=RecommendationSlot.BREADTH,
                    thread_id="thread-sensitive",
                    target_dimension=KnowledgeDimension.VALUES,
                    missing_facet="priority",
                    is_sensitive=True,
                ),
                candidate(
                    candidate_id="blocked-ai",
                    slot=RecommendationSlot.BREADTH,
                    thread_id="thread-ai",
                    target_dimension=KnowledgeDimension.LIFE_STAGE,
                    missing_facet="experience",
                    is_ai_inference_only=True,
                ),
                candidate(
                    candidate_id="blocked-skip",
                    slot=RecommendationSlot.CONTINUITY,
                    thread_id="thread-skipped",
                    target_dimension=KnowledgeDimension.KEY_DECISIONS,
                    missing_facet="outcome",
                    consecutive_skip_count=2,
                ),
                candidate(
                    candidate_id="expired",
                    slot=RecommendationSlot.BREADTH,
                    thread_id="thread-expired",
                    target_dimension=KnowledgeDimension.VALUES,
                    missing_facet="reflection",
                    expires_at=NOW - timedelta(seconds=1),
                ),
            )
        )

        self.assertEqual(result.selected, ())
        self.assertEqual(
            {item.candidate_id: item.reason_code for item in result.filtered},
            {
                "blocked-do-not-ask": "doNotAsk",
                "blocked-sensitive": "sensitiveWithoutRecentConsent",
                "blocked-ai": "aiInferenceOnly",
                "blocked-skip": "repeatedSkipWithoutReopen",
                "expired": "candidateExpired",
            },
        )

    def test_duplicate_thread_facet_cannot_fill_both_recommendation_slots(self) -> None:
        result = self.select(
            (
                candidate(
                    candidate_id="continuity",
                    slot=RecommendationSlot.CONTINUITY,
                    thread_id="thread-startup",
                    target_dimension=KnowledgeDimension.VALUES,
                    missing_facet="priority",
                ),
                candidate(
                    candidate_id="breadth-duplicate",
                    slot=RecommendationSlot.BREADTH,
                    thread_id="thread-startup",
                    target_dimension=KnowledgeDimension.VALUES,
                    missing_facet="priority",
                ),
            )
        )

        self.assertEqual([item.candidate_id for item in result.selected], ["continuity"])
        self.assertEqual(
            [(item.candidate_id, item.reason_code) for item in result.filtered],
            [("breadth-duplicate", "duplicateThreadFacet")],
        )

    def test_crisis_safety_override_allows_no_recommendation(self) -> None:
        result = self.select(
            (
                candidate(
                    candidate_id="otherwise-safe",
                    slot=RecommendationSlot.CONTINUITY,
                    thread_id="thread-safe",
                    target_dimension=KnowledgeDimension.KEY_DECISIONS,
                    missing_facet="reason",
                ),
            ),
            crisis_active=True,
        )

        self.assertEqual(result.selected, ())
        self.assertEqual(result.filtered[0].reason_code, "crisisSafetyOverride")

    def test_selection_summary_does_not_copy_question_text_or_memory_content(self) -> None:
        result = self.select(
            (
                candidate(
                    candidate_id="safe-continuity",
                    slot=RecommendationSlot.CONTINUITY,
                    thread_id="thread-safe",
                    target_dimension=KnowledgeDimension.KEY_DECISIONS,
                    missing_facet="reason",
                ),
            )
        )

        summary = result.value_free_summary()
        rendered = str(summary)
        self.assertNotIn("今天想聊", rendered)
        self.assertNotIn("我小时候", rendered)
        self.assertEqual(summary["schemaVersion"], "owner-truth-recommendation-selection-v1")


if __name__ == "__main__":
    unittest.main()
