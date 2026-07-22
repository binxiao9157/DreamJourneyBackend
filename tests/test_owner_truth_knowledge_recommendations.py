from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest
from uuid import UUID

from app.domain.owner_truth.conversation import (
    ConversationThreadState,
    InterviewBoundary,
    InterviewSessionState,
    OwnerTruthConversationThreadAuthoritySnapshot,
)
from app.domain.owner_truth.knowledge_recommendations import (
    ConfirmedMemoryDimensionEvidence,
    KnowledgeDimension,
    KnowledgeDimensionProjector,
    RecommendationCandidate,
    RecommendationEvidenceKind,
    RecommendationSelector,
    RecommendationSlot,
    ServerPlannedContinuationCue,
    ServerPlannedRecommendationCandidateProjector,
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


class ServerPlannedRecommendationCandidateProjectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.projector = KnowledgeDimensionProjector()
        self.planner = ServerPlannedRecommendationCandidateProjector()
        self.thread_id = str(UUID("00000000-0000-4000-8000-000000000101"))
        self.session_id = str(UUID("00000000-0000-4000-8000-000000000102"))
        self.coverage = self.projector.project(
            owner_subject_id=OWNER,
            vault_id=VAULT,
            evidence=(
                evidence(
                    memory_version_id="memory-version-decision",
                    source_id="source-decision",
                    dimension=KnowledgeDimension.KEY_DECISIONS,
                    covered_facets=("choice", "reason"),
                ),
                evidence(
                    memory_version_id="memory-version-values",
                    source_id="source-values",
                    dimension=KnowledgeDimension.VALUES,
                    covered_facets=("priority",),
                ),
            ),
        )

    def _authority(
        self,
        **overrides: object,
    ) -> OwnerTruthConversationThreadAuthoritySnapshot:
        values: dict[str, object] = {
            "thread_id": self.thread_id,
            "vault_id": VAULT,
            "owner_subject_id": OWNER,
            "authority_epoch": 7,
            "state": ConversationThreadState.ACTIVE,
            "session_id": self.session_id,
            "session_state": InterviewSessionState.ACTIVE,
            "session_boundary": InterviewBoundary.OPEN,
        }
        values.update(overrides)
        return OwnerTruthConversationThreadAuthoritySnapshot(**values)

    def _project(
        self,
        thread_authorities: tuple[OwnerTruthConversationThreadAuthoritySnapshot, ...],
        **overrides: object,
    ) -> tuple[RecommendationCandidate, ...]:
        values: dict[str, object] = {
            "owner_subject_id": OWNER,
            "vault_id": VAULT,
            "authority_epoch": 7,
            "checkpoint": "coverage-checkpoint-a",
            "coverage": self.coverage,
            "thread_authorities": thread_authorities,
        }
        values.update(overrides)
        return self.planner.project(**values)

    def test_projects_only_one_breadth_candidate_from_current_confirmed_gap(self) -> None:
        rows = self._project((self._authority(),))

        self.assertEqual(len(rows), 1)
        candidate = rows[0]
        self.assertEqual(candidate.slot, RecommendationSlot.BREADTH)
        self.assertEqual(candidate.question_template_id, "broadenConfirmedGap")
        self.assertEqual(candidate.reason_code, "confirmedDimensionGap")
        self.assertEqual(candidate.thread_id, self.thread_id)
        self.assertTrue(candidate.evidence_refs)
        self.assertNotIn("claim", str(candidate))
        self.assertNotIn("今天", str(candidate))

    def test_projects_explicit_saved_continuation_without_inferring_topic_text(self) -> None:
        cue = ServerPlannedContinuationCue(
            cue_id=str(UUID("00000000-0000-4000-8000-000000000103")),
            owner_subject_id=OWNER,
            vault_id=VAULT,
            authority_epoch=7,
            thread_id=self.thread_id,
            session_id=self.session_id,
            expected_session_version=1,
            memory_version_id="memory-version-decision",
            target_dimension=KnowledgeDimension.KEY_DECISIONS,
            missing_facet="outcome",
        )

        rows = self._project((self._authority(),), continuity_cues=(cue,))

        self.assertEqual([item.slot for item in rows], [RecommendationSlot.CONTINUITY, RecommendationSlot.BREADTH])
        continuity = rows[0]
        self.assertEqual(continuity.evidence_kind, RecommendationEvidenceKind.SAVED_CONTINUATION)
        self.assertEqual(continuity.evidence_refs, ("memory-version-decision",))
        self.assertEqual(continuity.question_template_id, "continueSavedOwnerCue")
        self.assertEqual(continuity.reason_code, "explicitOwnerSavedContinuation")
        self.assertNotIn("claim", str(continuity))

    def test_candidate_identifier_changes_when_authority_or_coverage_checkpoint_changes(self) -> None:
        baseline = self._project((self._authority(),))[0]
        changed_epoch = self._project((self._authority(authority_epoch=8),), authority_epoch=8)[0]
        changed_checkpoint = self._project((self._authority(),), checkpoint="coverage-checkpoint-b")[0]

        self.assertNotEqual(baseline.candidate_id, changed_epoch.candidate_id)
        self.assertNotEqual(baseline.candidate_id, changed_checkpoint.candidate_id)

    def test_returns_empty_when_no_active_open_thread_or_confirmed_gap_exists(self) -> None:
        paused = self._authority(
            state=ConversationThreadState.PAUSED,
            session_state=InterviewSessionState.PAUSED,
            session_boundary=InterviewBoundary.COOLDOWN,
        )
        self.assertEqual(self._project((),), ())
        with self.assertRaisesRegex(Exception, "active open"):
            self._project((paused,))

        complete_coverage = self.projector.project(
            owner_subject_id=OWNER,
            vault_id=VAULT,
            evidence=(
                evidence(
                    memory_version_id="memory-version-complete",
                    source_id="source-complete",
                    dimension=KnowledgeDimension.KEY_DECISIONS,
                    covered_facets=("choice", "reason", "outcome"),
                ),
            ),
        )
        self.assertEqual(
            self._project((self._authority(),), coverage=complete_coverage),
            (),
        )

    def test_rejects_multiple_eligible_threads_or_scope_drift(self) -> None:
        other_thread = self._authority(
            thread_id=str(UUID("00000000-0000-4000-8000-000000000103")),
            session_id=str(UUID("00000000-0000-4000-8000-000000000104")),
        )
        with self.assertRaisesRegex(Exception, "multiple active open"):
            self._project((self._authority(), other_thread))
        with self.assertRaisesRegex(Exception, "scope"):
            self._project((self._authority(vault_id="vault-other"),))


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
