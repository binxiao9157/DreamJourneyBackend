from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import unittest
from uuid import uuid4

from app.domain.owner_truth.candidate_decisions import (
    CandidateReviewAction,
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
from app.domain.owner_truth.memory_projection import OwnerTruthMemoryProjectionAccessDenied
from app.domain.owner_truth.ontology import OWNER_TRUTH_SCHEMA_VERSION
from app.domain.owner_truth.projection_rights import (
    OwnerTruthProjectionRightsRevisionCommand,
    ProjectionRightsState,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.async_effects.repository import InMemoryEffectKernelRepository
from app.services.owner_truth_answer_citation import (
    InMemoryOwnerTruthAnswerCitationRepository,
    OwnerTruthAnswerCitationCommand,
    OwnerTruthAnswerCitationService,
)
from app.services.owner_truth_answer_feedback import (
    InMemoryOwnerTruthAnswerFeedbackRepository,
    OwnerTruthAnswerCitationReadService,
    OwnerTruthAnswerFeedbackCommand,
    OwnerTruthAnswerFeedbackConflict,
    OwnerTruthAnswerFeedbackService,
    answer_citation_read_summary,
    answer_feedback_summary,
)
from app.services.owner_truth_candidate_review import (
    InMemoryOwnerTruthCandidateReviewRepository,
    OwnerTruthCandidateReviewService,
)
from app.services.owner_truth_memory_projection import (
    InMemoryOwnerTruthMemoryProjectionRepository,
    OwnerTruthMemoryProjectionService,
)
from app.services.owner_truth_memory_search_projection import (
    InMemoryOwnerTruthMemorySearchDocumentProjectionRepository,
)
from app.services.owner_truth_projection_rights import (
    InMemoryOwnerTruthProjectionRightsRepository,
    OwnerTruthProjectionRightsService,
)


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class _Store:
    def __init__(self) -> None:
        self.review_repository = InMemoryOwnerTruthCandidateReviewRepository()
        self.rights_repository = InMemoryOwnerTruthProjectionRightsRepository()
        self.effect_repository = InMemoryEffectKernelRepository()
        self.projection_repository = InMemoryOwnerTruthMemoryProjectionRepository(
            self.review_repository,
            rights_repository=self.rights_repository,
        )
        self.search_projection_repository = (
            InMemoryOwnerTruthMemorySearchDocumentProjectionRepository(
                self.projection_repository
            )
        )
        self.answer_repository = InMemoryOwnerTruthAnswerCitationRepository()
        self.feedback_repository = InMemoryOwnerTruthAnswerFeedbackRepository()

    @contextmanager
    def request_unit_of_work(self, *, correlation_id: str, command_id: str):
        yield

    def owner_truth_candidate_review_repository(self):
        return self.review_repository

    def owner_truth_memory_projection_repository(self):
        return self.projection_repository

    def owner_truth_projection_rights_repository(self):
        return self.rights_repository

    def effect_kernel_repository(self):
        return self.effect_repository

    def owner_truth_memory_search_document_projection_repository(self):
        return self.search_projection_repository

    def owner_truth_answer_citation_repository(self):
        return self.answer_repository

    def owner_truth_answer_feedback_repository(self):
        return self.feedback_repository


class OwnerTruthAnswerFeedbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vault_id = "vault-answer-feedback"
        self.owner_id = "subject-answer-feedback"
        self.context = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            actor_subject_id=self.owner_id,
        )
        self.store = _Store()
        self.review_service = OwnerTruthCandidateReviewService(self.store)
        self.projection_service = OwnerTruthMemoryProjectionService(self.store)
        self.rights_service = OwnerTruthProjectionRightsService(self.store)
        self.answer_service = OwnerTruthAnswerCitationService(self.store, enabled=True)
        self.feedback_service = OwnerTruthAnswerFeedbackService(self.store, enabled=True)
        self.citation_read_service = OwnerTruthAnswerCitationReadService(self.store, enabled=True)

    def _candidate(self, *, summary: str) -> OwnerTruthCandidateSnapshot:
        source_id = str(uuid4())
        content = {"summary": summary}
        return OwnerTruthCandidateSnapshot(
            candidate_id=str(uuid4()),
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            source_id=source_id,
            memory_kind=MemoryKind.EXPERIENCE,
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

    def _activate(self, candidate: OwnerTruthCandidateSnapshot, *, command_id: str) -> None:
        self.store.review_repository.seed(candidate)
        self.review_service.decide_and_activate(
            command=OwnerTruthCandidateReviewCommand(
                command_id=command_id,
                candidate_id=candidate.candidate_id,
                expected_candidate_version=candidate.row_version,
                action=CandidateReviewAction.ACCEPT,
                corrected_value=None,
                corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION,
                reason_code="ownerReviewed",
            ),
            context=self.context,
        )

    def _record_answer(self, *, command_id: str, answer_text: str):
        return self.answer_service.record(
            context=self.context,
            command=OwnerTruthAnswerCitationCommand(
                command_id=command_id,
                answer_text=answer_text,
            ),
            context_payload={"intent": "echo_chat", "query": "私密问题不得进入反馈回执"},
        )

    def _record_rights(
        self,
        *,
        expected_revision: int,
        state: ProjectionRightsState,
        suffix: str,
    ):
        return self.rights_service.record(
            context=self.context,
            command=OwnerTruthProjectionRightsRevisionCommand(
                command_id=f"answer-feedback-rights-{suffix}",
                authority_epoch=0,
                expected_revision=expected_revision,
                state=state,
                event_hash=_hash({"event": suffix, "state": state.value}),
            ),
        )

    def test_current_citations_can_record_one_metric_eligible_feedback_receipt(self) -> None:
        candidate = self._candidate(summary="只有当前确认内容可以成为引用依据")
        self._activate(candidate, command_id="answer-feedback-activate-current")
        self.projection_service.rebuild(context=self.context)
        answer = self._record_answer(
            command_id="answer-feedback-answer-current",
            answer_text="我只根据你已确认的记忆回答。",
        )

        citation_read = self.citation_read_service.read(
            context=self.context,
            answer_id=answer.answer_id,
        )
        feedback = self.feedback_service.record(
            context=self.context,
            command=OwnerTruthAnswerFeedbackCommand(
                command_id="answer-feedback-current-001",
                answer_id=answer.answer_id,
                helpful=True,
            ),
        )
        replay = self.feedback_service.record(
            context=self.context,
            command=OwnerTruthAnswerFeedbackCommand(
                command_id="answer-feedback-current-001",
                answer_id=answer.answer_id,
                helpful=True,
            ),
        )

        self.assertEqual(citation_read.citation_count, 1)
        self.assertEqual(citation_read.current_citation_count, 1)
        self.assertTrue(citation_read.citations[0]["current"])
        self.assertEqual(feedback.outcome, "created")
        self.assertTrue(feedback.metric_eligible)
        self.assertEqual(feedback.eligibility_reason, "eligible")
        self.assertEqual(replay.outcome, "deduplicated")
        self.assertEqual(replay.feedback_id, feedback.feedback_id)

        serialized = json.dumps(
            {
                "citationRead": answer_citation_read_summary(citation_read),
                "feedback": answer_feedback_summary(feedback),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        self.assertNotIn("私密问题不得进入反馈回执", serialized)
        self.assertNotIn("我只根据你已确认的记忆回答。", serialized)
        self.assertNotIn(candidate.payload["content"]["summary"], serialized)

        with self.assertRaises(OwnerTruthAnswerFeedbackConflict):
            self.feedback_service.record(
                context=self.context,
                command=OwnerTruthAnswerFeedbackCommand(
                    command_id="answer-feedback-current-second-command",
                    answer_id=answer.answer_id,
                    helpful=False,
                ),
            )

    def test_no_citation_answer_feedback_is_retained_but_never_metric_eligible(self) -> None:
        candidate = self._candidate(summary="未重建投影不得成为回答引用")
        self._activate(candidate, command_id="answer-feedback-activate-fallback")

        answer = self._record_answer(
            command_id="answer-feedback-answer-fallback",
            answer_text="当前没有足够的已确认记忆可以引用。",
        )
        feedback = self.feedback_service.record(
            context=self.context,
            command=OwnerTruthAnswerFeedbackCommand(
                command_id="answer-feedback-fallback-001",
                answer_id=answer.answer_id,
                helpful=True,
            ),
        )

        self.assertEqual(answer.citation_count, 0)
        self.assertFalse(feedback.metric_eligible)
        self.assertEqual(feedback.eligibility_reason, "noCitations")
        self.assertEqual(feedback.citation_count, 0)
        self.assertEqual(feedback.eligible_citation_count, 0)

    def test_not_helpful_current_feedback_never_becomes_a_metric_signal(self) -> None:
        candidate = self._candidate(summary="当前引用仍可收到非帮助性反馈")
        self._activate(candidate, command_id="answer-feedback-activate-not-helpful")
        self.projection_service.rebuild(context=self.context)
        answer = self._record_answer(
            command_id="answer-feedback-answer-not-helpful",
            answer_text="这条回答没有解决我的问题。",
        )

        feedback = self.feedback_service.record(
            context=self.context,
            command=OwnerTruthAnswerFeedbackCommand(
                command_id="answer-feedback-not-helpful-001",
                answer_id=answer.answer_id,
                helpful=False,
            ),
        )

        self.assertFalse(feedback.metric_eligible)
        self.assertEqual(feedback.eligibility_reason, "notHelpful")
        self.assertEqual(feedback.citation_count, 1)
        self.assertEqual(feedback.eligible_citation_count, 1)

    def test_stale_projection_feedback_is_nonmetric_and_citations_report_rebuilding_reason(self) -> None:
        candidate = self._candidate(summary="来源撤回后旧引用不能继续计量")
        self._activate(candidate, command_id="answer-feedback-activate-stale")
        self.projection_service.rebuild(context=self.context)
        answer = self._record_answer(
            command_id="answer-feedback-answer-stale",
            answer_text="旧回答的引用需要再次确认。",
        )
        self.store.review_repository._source_states[(self.vault_id, candidate.source_id)] = "deleted"

        citation_read = self.citation_read_service.read(
            context=self.context,
            answer_id=answer.answer_id,
        )
        feedback = self.feedback_service.record(
            context=self.context,
            command=OwnerTruthAnswerFeedbackCommand(
                command_id="answer-feedback-stale-001",
                answer_id=answer.answer_id,
                helpful=True,
            ),
        )

        self.assertEqual(citation_read.projection_state, "rebuilding")
        self.assertEqual(citation_read.current_citation_count, 0)
        self.assertEqual(citation_read.citations[0]["currentness"], "projectionInputsChanged")
        self.assertFalse(feedback.metric_eligible)
        self.assertEqual(feedback.eligibility_reason, "projectionInputsChanged")

    def test_rights_revision_feedback_retains_the_value_free_stale_reason(self) -> None:
        candidate = self._candidate(summary="权利修订后引用需要重新构建")
        self._activate(candidate, command_id="answer-feedback-activate-rights-revision")
        self.projection_service.rebuild(context=self.context)
        answer = self._record_answer(
            command_id="answer-feedback-answer-rights-revision",
            answer_text="这条回答的引用受当前权利状态保护。",
        )
        self._record_rights(
            expected_revision=0,
            state=ProjectionRightsState.ACTIVE,
            suffix="active-revision-001",
        )

        citation_read = self.citation_read_service.read(
            context=self.context,
            answer_id=answer.answer_id,
        )
        feedback = self.feedback_service.record(
            context=self.context,
            command=OwnerTruthAnswerFeedbackCommand(
                command_id="answer-feedback-rights-revision-001",
                answer_id=answer.answer_id,
                helpful=True,
            ),
        )

        self.assertEqual(citation_read.projection_state, "rebuilding")
        self.assertEqual(citation_read.current_citation_count, 0)
        self.assertEqual(citation_read.citations[0]["currentness"], "rightsRevisionChanged")
        self.assertFalse(feedback.metric_eligible)
        self.assertEqual(feedback.eligibility_reason, "rightsRevisionChanged")

    def test_rights_revocation_feedback_retains_the_value_free_block_reason(self) -> None:
        candidate = self._candidate(summary="权利撤销后引用不得继续计量")
        self._activate(candidate, command_id="answer-feedback-activate-rights-revocation")
        self.projection_service.rebuild(context=self.context)
        answer = self._record_answer(
            command_id="answer-feedback-answer-rights-revocation",
            answer_text="撤销后不应继续使用旧引用。",
        )
        self._record_rights(
            expected_revision=0,
            state=ProjectionRightsState.REVOKED,
            suffix="revoked-001",
        )

        citation_read = self.citation_read_service.read(
            context=self.context,
            answer_id=answer.answer_id,
        )
        feedback = self.feedback_service.record(
            context=self.context,
            command=OwnerTruthAnswerFeedbackCommand(
                command_id="answer-feedback-rights-revocation-001",
                answer_id=answer.answer_id,
                helpful=True,
            ),
        )

        self.assertEqual(citation_read.projection_state, "rebuilding")
        self.assertEqual(citation_read.current_citation_count, 0)
        self.assertEqual(citation_read.citations[0]["currentness"], "rightsRevoked")
        self.assertFalse(feedback.metric_eligible)
        self.assertEqual(feedback.eligibility_reason, "rightsRevoked")

    def test_non_owner_cannot_read_or_feedback_on_an_answer(self) -> None:
        candidate = self._candidate(summary="跨 Owner 不能读取回答反馈证据")
        self._activate(candidate, command_id="answer-feedback-activate-denied")
        self.projection_service.rebuild(context=self.context)
        answer = self._record_answer(
            command_id="answer-feedback-answer-denied",
            answer_text="只对本人开放。",
        )
        outsider = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            actor_subject_id="different-subject",
        )

        with self.assertRaises(OwnerTruthMemoryProjectionAccessDenied):
            self.citation_read_service.read(context=outsider, answer_id=answer.answer_id)
        with self.assertRaises(OwnerTruthMemoryProjectionAccessDenied):
            self.feedback_service.record(
                context=outsider,
                command=OwnerTruthAnswerFeedbackCommand(
                    command_id="answer-feedback-denied-001",
                    answer_id=answer.answer_id,
                    helpful=True,
                ),
            )


if __name__ == "__main__":
    unittest.main()
