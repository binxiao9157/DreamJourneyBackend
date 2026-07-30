from __future__ import annotations

from hashlib import sha256
import json
import unittest
from uuid import uuid4

from app.domain.owner_truth.candidate_decisions import (
    OwnerTruthCandidateReviewCommand,
    OwnerTruthCandidateSnapshot,
)
from app.domain.owner_truth.contracts import (
    CandidateDecision,
    EpistemicStatus,
    MemoryKind,
    PerspectiveType,
    SensitivityLevel,
)
from app.domain.owner_truth.conversation import StartInterviewSessionCommand
from app.domain.owner_truth.ontology import OWNER_TRUTH_SCHEMA_VERSION
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.in_memory_store import InMemoryStore
from app.services.owner_truth_candidate_review import OwnerTruthCandidateReviewService
from app.services.owner_truth_conversation import OwnerTruthConversationService
from app.services.owner_truth_knowledge_dimension_confirmation import (
    OwnerTruthKnowledgeDimensionConfirmationCommand,
    OwnerTruthKnowledgeDimensionConfirmationService,
)
from app.services.owner_truth_knowledge_recommendation_feedback import (
    OwnerTruthKnowledgeRecommendationFeedbackAccessDenied,
    OwnerTruthKnowledgeRecommendationFeedbackCommand,
    OwnerTruthKnowledgeRecommendationFeedbackConflict,
    OwnerTruthKnowledgeRecommendationFeedbackService,
    OwnerTruthKnowledgeRecommendationFeedbackStale,
    OwnerTruthKnowledgeRecommendationFeedbackUnavailable,
    RecommendationFeedbackAction,
    RecommendationFeedbackReason,
    knowledge_recommendation_feedback_summary,
)
from app.services.owner_truth_knowledge_recommendation_read import (
    OwnerTruthKnowledgeRecommendationReadService,
)
from app.services.owner_truth_memory_projection import OwnerTruthMemoryProjectionService


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class OwnerTruthKnowledgeRecommendationFeedbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryStore()
        self.vault_id = "vault-recommendation-feedback"
        self.owner_id = "owner-recommendation-feedback"
        self.context = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            actor_subject_id=self.owner_id,
        )

    def _activate_memory(self, *, content: dict[str, object], command_id: str) -> tuple[str, str]:
        source_id = str(uuid4())
        candidate = OwnerTruthCandidateSnapshot(
            candidate_id=str(uuid4()),
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            source_id=source_id,
            memory_kind=MemoryKind.KNOWLEDGE,
            perspective_type=PerspectiveType.FIRST_PERSON,
            epistemic_status=EpistemicStatus.RECALLED,
            sensitivity=SensitivityLevel.STANDARD,
            decision=CandidateDecision.PENDING,
            policy_version=OWNER_TRUTH_SCHEMA_VERSION,
            authority_epoch=0,
            row_version=1,
            content_hash=_hash(content),
            content_schema_version=OWNER_TRUTH_SCHEMA_VERSION,
            payload={
                "content": content,
                "contentSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION,
                "evidenceRefs": [{"sourceId": source_id, "sourceVersion": 1}],
                "reviewMode": "single",
                "schemaVersion": "owner-truth-candidate-proposal-v1",
            },
        )
        self.store.owner_truth_candidate_review_repository().seed(candidate)
        OwnerTruthCandidateReviewService(self.store).decide_and_activate(
            command=OwnerTruthCandidateReviewCommand(
                command_id=command_id,
                candidate_id=candidate.candidate_id,
                expected_candidate_version=1,
                action="accept",
                corrected_value=None,
                corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION,
                reason_code="ownerReviewed",
            ),
            context=self.context,
        )
        OwnerTruthMemoryProjectionService(self.store).rebuild(context=self.context)
        snapshot = self.store.owner_truth_memory_projection_repository().read(context=self.context)
        entry = next(
            item
            for item in snapshot["entries"]
            if item["citation"]["contentHash"] == _hash(content)
        )
        return str(entry["citation"]["memoryVersionId"]), str(entry["citation"]["contentHash"])

    def _confirm(
        self,
        *,
        memory_version_id: str,
        content_hash: str,
        dimension: str,
        facets: tuple[str, ...],
        command_id: str,
    ) -> None:
        OwnerTruthKnowledgeDimensionConfirmationService(self.store, enabled=True).confirm(
            context=self.context,
            memory_version_id=memory_version_id,
            command=OwnerTruthKnowledgeDimensionConfirmationCommand(
                command_id=command_id,
                expected_content_hash=content_hash,
                dimension=dimension,
                covered_facets=facets,
            ),
        )

    def _seed_thread(self) -> None:
        with self.store.request_unit_of_work(
            correlation_id=f"recommendation-feedback-thread:{self.vault_id}",
            command_id="recommendation-feedback-thread",
        ):
            OwnerTruthConversationService(
                self.store.owner_truth_conversation_repository()
            ).start_session(
                command=StartInterviewSessionCommand(
                    command_id="recommendation-feedback-thread",
                    thread_id=str(uuid4()),
                    session_id=str(uuid4()),
                    expected_thread_version=0,
                    entry_mode="recommendation",
                ),
                context=self.context,
            )

    def _prepare_plan(self) -> None:
        decision_id, decision_hash = self._activate_memory(
            content={"claim": "private decision content"},
            command_id="recommendation-feedback-memory-decision",
        )
        values_id, values_hash = self._activate_memory(
            content={"claim": "private values content"},
            command_id="recommendation-feedback-memory-values",
        )
        life_id, life_hash = self._activate_memory(
            content={"claim": "private life content"},
            command_id="recommendation-feedback-memory-life",
        )
        self._confirm(
            memory_version_id=decision_id,
            content_hash=decision_hash,
            dimension="keyDecisions",
            facets=("choice", "reason"),
            command_id="recommendation-feedback-confirm-decision",
        )
        self._confirm(
            memory_version_id=values_id,
            content_hash=values_hash,
            dimension="values",
            facets=("priority",),
            command_id="recommendation-feedback-confirm-values",
        )
        self._confirm(
            memory_version_id=life_id,
            content_hash=life_hash,
            dimension="lifeStage",
            facets=("timeContext",),
            command_id="recommendation-feedback-confirm-life",
        )
        self._seed_thread()

    def _plan_breadth(self):
        result = OwnerTruthKnowledgeRecommendationReadService(self.store).plan(
            context=self.context
        )
        self.assertEqual(result.state.value, "ready")
        assert result.selection is not None
        rows = [item for item in result.selection.selected if item.slot.value == "breadth"]
        self.assertEqual(len(rows), 1)
        return rows[0], result

    def test_replace_is_server_revalidated_replay_safe_and_selects_an_alternative(self) -> None:
        self._prepare_plan()
        decision, _ = self._plan_breadth()
        command = OwnerTruthKnowledgeRecommendationFeedbackCommand(
            command_id="recommendation-feedback-replace",
            expected_candidate_id=decision.candidate_id,
            feedback_action=RecommendationFeedbackAction.REPLACE,
            feedback_reason=RecommendationFeedbackReason.QUESTION_WORDING,
            expected_session_version=1,
        )

        disabled = OwnerTruthKnowledgeRecommendationFeedbackService(self.store, enabled=False)
        with self.assertRaises(OwnerTruthKnowledgeRecommendationFeedbackUnavailable):
            disabled.submit(context=self.context, command=command)

        service = OwnerTruthKnowledgeRecommendationFeedbackService(self.store, enabled=True)
        with self.assertRaises(OwnerTruthKnowledgeRecommendationFeedbackStale):
            service.submit(
                context=self.context,
                command=OwnerTruthKnowledgeRecommendationFeedbackCommand(
                    command_id="recommendation-feedback-forged",
                    expected_candidate_id="server-plan-breadth-forged",
                    feedback_action="replace",
                    feedback_reason="questionWording",
                    expected_session_version=1,
                ),
            )
        created = service.submit(context=self.context, command=command)
        replayed = service.submit(context=self.context, command=command)

        self.assertEqual(created.outcome, "created")
        self.assertEqual(replayed.outcome, "deduplicated")
        self.assertEqual(created.feedback_scope.value, "candidate")
        self.assertEqual(created.reason_code, "userRequestedReplacement")
        summary = knowledge_recommendation_feedback_summary(created)
        rendered = json.dumps(summary, ensure_ascii=False)
        self.assertNotIn("private decision content", rendered)
        self.assertNotIn("private values content", rendered)
        self.assertNotIn("questionText", summary)

        alternative, after = self._plan_breadth()
        self.assertNotEqual(alternative.candidate_id, decision.candidate_id)
        assert after.selection is not None
        self.assertIn(
            (decision.candidate_id, "userRequestedReplacement"),
            [(item.candidate_id, item.reason_code) for item in after.selection.filtered],
        )
        with self.assertRaises(OwnerTruthKnowledgeRecommendationFeedbackConflict):
            service.submit(
                context=self.context,
                command=OwnerTruthKnowledgeRecommendationFeedbackCommand(
                    command_id="recommendation-feedback-replace",
                    expected_candidate_id=alternative.candidate_id,
                    feedback_action="replace",
                    feedback_reason="questionWording",
                    expected_session_version=1,
                ),
            )

    def test_not_interested_lowers_dimension_without_becoming_do_not_ask(self) -> None:
        self._prepare_plan()
        decision, _ = self._plan_breadth()
        service = OwnerTruthKnowledgeRecommendationFeedbackService(self.store, enabled=True)
        created = service.submit(
            context=self.context,
            command=OwnerTruthKnowledgeRecommendationFeedbackCommand(
                command_id="recommendation-feedback-topic-preference",
                expected_candidate_id=decision.candidate_id,
                feedback_action="notInterested",
                feedback_reason="topicPreference",
                expected_session_version=1,
            ),
        )

        self.assertEqual(created.feedback_scope.value, "dimension")
        self.assertEqual(created.reason_code, "topicNotInterested")
        policy = self.store.owner_truth_knowledge_recommendation_feedback_repository().current_policy(
            context=self.context,
            authority_epoch=0,
        )
        self.assertEqual(policy.dimension_penalty_counts[decision.target_dimension], 1)
        self.assertEqual(policy.replaced_candidate_ids, frozenset())
        next_decision, _ = self._plan_breadth()
        self.assertNotEqual(next_decision.target_dimension, decision.target_dimension)

    def test_recommendation_type_feedback_only_lowers_the_policy_template(self) -> None:
        self._prepare_plan()
        decision, _ = self._plan_breadth()
        created = OwnerTruthKnowledgeRecommendationFeedbackService(
            self.store,
            enabled=True,
        ).submit(
            context=self.context,
            command=OwnerTruthKnowledgeRecommendationFeedbackCommand(
                command_id="recommendation-feedback-template-preference",
                expected_candidate_id=decision.candidate_id,
                feedback_action="notInterested",
                feedback_reason="recommendationType",
                expected_session_version=1,
            ),
        )

        self.assertEqual(created.feedback_scope.value, "questionTemplate")
        self.assertEqual(created.reason_code, "recommendationTypeNotInterested")
        policy = self.store.owner_truth_knowledge_recommendation_feedback_repository().current_policy(
            context=self.context,
            authority_epoch=0,
        )
        self.assertEqual(
            policy.question_template_penalty_counts[decision.question_template_id],
            1,
        )
        self.assertEqual(policy.dimension_penalty_counts, {})
        self.assertEqual(policy.replaced_candidate_ids, frozenset())

    def test_feedback_rejects_foreign_actor_and_second_command_for_same_candidate(self) -> None:
        self._prepare_plan()
        decision, _ = self._plan_breadth()
        service = OwnerTruthKnowledgeRecommendationFeedbackService(self.store, enabled=True)
        command = OwnerTruthKnowledgeRecommendationFeedbackCommand(
            command_id="recommendation-feedback-candidate-ownership",
            expected_candidate_id=decision.candidate_id,
            feedback_action="replace",
            feedback_reason="questionWording",
            expected_session_version=1,
        )
        foreign_context = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            actor_subject_id="other-owner",
        )
        with self.assertRaises(OwnerTruthKnowledgeRecommendationFeedbackAccessDenied):
            service.submit(context=foreign_context, command=command)

        service.submit(context=self.context, command=command)
        with self.assertRaises(OwnerTruthKnowledgeRecommendationFeedbackConflict):
            service.submit(
                context=self.context,
                command=OwnerTruthKnowledgeRecommendationFeedbackCommand(
                    command_id="recommendation-feedback-second-command",
                    expected_candidate_id=decision.candidate_id,
                    feedback_action="notInterested",
                    feedback_reason="topicPreference",
                    expected_session_version=1,
                ),
            )

    def test_command_rejects_invalid_pairs_and_reserves_timing_for_guided_presentation(self) -> None:
        with self.assertRaisesRegex(Exception, "replace feedback"):
            OwnerTruthKnowledgeRecommendationFeedbackCommand(
                command_id="recommendation-feedback-invalid-replace",
                expected_candidate_id="server-plan-breadth-a",
                feedback_action="replace",
                feedback_reason="topicPreference",
                expected_session_version=1,
            )
        with self.assertRaisesRegex(Exception, "notInterested feedback"):
            OwnerTruthKnowledgeRecommendationFeedbackCommand(
                command_id="recommendation-feedback-invalid-interest",
                expected_candidate_id="server-plan-breadth-a",
                feedback_action="notInterested",
                feedback_reason="questionWording",
                expected_session_version=1,
            )
        timing = OwnerTruthKnowledgeRecommendationFeedbackCommand(
            command_id="recommendation-feedback-generic-timing",
            expected_candidate_id="server-plan-breadth-a",
            feedback_action="defer",
            feedback_reason="timing",
            expected_session_version=1,
        )
        with self.assertRaisesRegex(
            OwnerTruthKnowledgeRecommendationFeedbackUnavailable,
            "guided recommendation presentation",
        ):
            OwnerTruthKnowledgeRecommendationFeedbackService(
                self.store,
                enabled=True,
            ).submit(context=self.context, command=timing)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
