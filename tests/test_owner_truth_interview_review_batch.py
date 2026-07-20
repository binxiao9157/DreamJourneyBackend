from __future__ import annotations

import unittest
from uuid import uuid4

from app.domain.owner_truth.conversation import (
    AcknowledgeInterviewReviewBatchCommand,
    AppendInterviewMessageCommand,
    ConversationMessageAuthor,
    ConversationMessageKind,
    CreateInterviewReviewBatchCommand,
    InterviewBoundary,
    InterviewReviewBatchState,
    InterviewReviewBatchTrigger,
    OwnerTruthConversationAccessDenied,
    OwnerTruthConversationConflict,
    SetInterviewBoundaryCommand,
    StartInterviewSessionCommand,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_conversation import (
    InMemoryOwnerTruthConversationRepository,
    OwnerTruthConversationService,
)
from app.services.owner_truth_interview_session_orchestration import (
    InterviewSessionOrchestrationSignals,
    OwnerTruthInterviewSessionOrchestrationService,
)


class OwnerTruthInterviewReviewBatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryOwnerTruthConversationRepository()
        self.service = OwnerTruthConversationService(self.repository)
        self.orchestration = OwnerTruthInterviewSessionOrchestrationService(
            conversation_service=self.service
        )
        self.context = OwnerTruthCommandContext(
            vault_id="review-batch-vault-a",
            owner_subject_id="review-batch-owner-a",
            actor_subject_id="review-batch-owner-a",
            policy_version="owner-truth-v1",
        )
        self.thread_id = str(uuid4())
        self.session_id = str(uuid4())
        started = self.service.start_session(
            command=StartInterviewSessionCommand(
                command_id="review-batch-start",
                thread_id=self.thread_id,
                session_id=self.session_id,
                expected_thread_version=0,
                entry_mode="naturalInput",
            ),
            context=self.context,
        )
        self.thread_version = started.thread_version
        self.session_version = started.session_version

    def _append_owner_message(self, index: int) -> None:
        result = self.service.append_message(
            command=AppendInterviewMessageCommand(
                command_id=f"review-batch-append-{index}",
                thread_id=self.thread_id,
                session_id=self.session_id,
                message_id=str(uuid4()),
                expected_thread_version=self.thread_version,
                expected_session_version=self.session_version,
                author=ConversationMessageAuthor.OWNER,
                kind=ConversationMessageKind.NARRATIVE,
                text=f"第 {index + 1} 条只属于本人的私有叙述。",
            ),
            context=self.context,
        )
        self.thread_version = result.thread_version
        self.session_version = result.session_version

    def _bridge_review_due(self) -> bool:
        return self.orchestration.decide(
            session_id=self.session_id,
            context=self.context,
            signals=InterviewSessionOrchestrationSignals(
                topic_id="topic-review-batch-private-story",
                topic_incomplete=False,
            ),
        ).decision.review_batch_due

    def test_threshold_batch_freezes_a_boundary_and_acknowledgement_preserves_new_turns(self) -> None:
        for index in range(5):
            self._append_owner_message(index)

        self.assertTrue(self._bridge_review_due())
        create = CreateInterviewReviewBatchCommand(
            command_id="review-batch-create-threshold",
            thread_id=self.thread_id,
            session_id=self.session_id,
            expected_session_version=self.session_version,
        )
        created = self.service.create_review_batch(command=create, context=self.context)
        self.session_version = created.session_version

        self.assertEqual(created.outcome, "created")
        self.assertEqual(created.review_batch.trigger, InterviewReviewBatchTrigger.TURN_THRESHOLD)
        self.assertEqual(created.review_batch.state, InterviewReviewBatchState.PENDING_ACKNOWLEDGEMENT)
        self.assertEqual(created.review_batch.captured_candidate_batch_turn_count, 5)
        self.assertEqual(created.review_batch.owner_turn_start_count, 1)
        self.assertEqual(created.review_batch.owner_turn_end_count, 5)
        self.assertEqual(created.review_batch.through_message_sequence, 5)
        self.assertFalse(self._bridge_review_due())

        replayed_create = self.service.create_review_batch(command=create, context=self.context)
        self.assertEqual(replayed_create.outcome, "deduplicated")
        self.assertEqual(replayed_create.review_batch.review_batch_id, created.review_batch.review_batch_id)

        self._append_owner_message(5)
        acknowledged = self.service.acknowledge_review_batch(
            command=AcknowledgeInterviewReviewBatchCommand(
                command_id="review-batch-ack-threshold",
                thread_id=self.thread_id,
                session_id=self.session_id,
                review_batch_id=created.review_batch.review_batch_id,
                expected_session_version=self.session_version,
                expected_review_batch_version=1,
            ),
            context=self.context,
        )
        self.session_version = acknowledged.session_version

        self.assertEqual(acknowledged.outcome, "acknowledged")
        self.assertEqual(acknowledged.review_batch.state, InterviewReviewBatchState.ACKNOWLEDGED)
        self.assertEqual(acknowledged.review_batch.row_version, 2)
        session = self.service.read_session(session_id=self.session_id, context=self.context)
        self.assertIsNone(session.pending_review_batch_id)
        self.assertEqual(session.candidate_batch_turn_count, 1)

        replayed_ack = self.service.acknowledge_review_batch(
            command=AcknowledgeInterviewReviewBatchCommand(
                command_id="review-batch-ack-threshold",
                thread_id=self.thread_id,
                session_id=self.session_id,
                review_batch_id=created.review_batch.review_batch_id,
                expected_session_version=self.session_version - 1,
                expected_review_batch_version=1,
            ),
            context=self.context,
        )
        self.assertEqual(replayed_ack.outcome, "deduplicated")
        self.assertEqual(replayed_ack.review_batch.state, InterviewReviewBatchState.ACKNOWLEDGED)

        listed = self.service.list_review_batches(session_id=self.session_id, context=self.context)
        self.assertEqual(len(listed), 1)
        snapshot = self.repository.snapshot(vault_id=self.context.vault_id)
        self.assertEqual(snapshot["candidateCount"], 0)
        self.assertEqual(snapshot["memoryVersionCount"], 0)
        self.assertEqual(snapshot["authorityEffects"], ())
        self.assertNotIn("第 1 条", str(listed[0]))

    def test_session_exit_can_create_a_small_batch_but_not_before_any_due_condition(self) -> None:
        for index in range(4):
            self._append_owner_message(index)
        with self.assertRaises(OwnerTruthConversationConflict):
            self.service.create_review_batch(
                command=CreateInterviewReviewBatchCommand(
                    command_id="review-batch-not-due",
                    thread_id=self.thread_id,
                    session_id=self.session_id,
                    expected_session_version=self.session_version,
                ),
                context=self.context,
            )

        boundary = self.service.set_boundary(
            command=SetInterviewBoundaryCommand(
                command_id="review-batch-exit",
                thread_id=self.thread_id,
                session_id=self.session_id,
                expected_session_version=self.session_version,
                boundary=InterviewBoundary.DO_NOT_ASK,
            ),
            context=self.context,
        )
        self.session_version = boundary.session_version
        self.assertTrue(self._bridge_review_due())

        created = self.service.create_review_batch(
            command=CreateInterviewReviewBatchCommand(
                command_id="review-batch-create-exit",
                thread_id=self.thread_id,
                session_id=self.session_id,
                expected_session_version=self.session_version,
            ),
            context=self.context,
        )
        self.assertEqual(created.review_batch.trigger, InterviewReviewBatchTrigger.SESSION_EXIT)
        self.assertEqual(created.review_batch.captured_candidate_batch_turn_count, 4)

    def test_cross_owner_cannot_read_or_acknowledge_a_review_batch(self) -> None:
        for index in range(5):
            self._append_owner_message(index)
        created = self.service.create_review_batch(
            command=CreateInterviewReviewBatchCommand(
                command_id="review-batch-cross-owner-create",
                thread_id=self.thread_id,
                session_id=self.session_id,
                expected_session_version=self.session_version,
            ),
            context=self.context,
        )
        other_context = OwnerTruthCommandContext(
            vault_id=self.context.vault_id,
            owner_subject_id="review-batch-owner-b",
            actor_subject_id="review-batch-owner-b",
            policy_version="owner-truth-v1",
        )
        with self.assertRaises(OwnerTruthConversationAccessDenied):
            self.service.list_review_batches(session_id=self.session_id, context=other_context)
        with self.assertRaises(OwnerTruthConversationAccessDenied):
            self.service.acknowledge_review_batch(
                command=AcknowledgeInterviewReviewBatchCommand(
                    command_id="review-batch-cross-owner-ack",
                    thread_id=self.thread_id,
                    session_id=self.session_id,
                    review_batch_id=created.review_batch.review_batch_id,
                    expected_session_version=created.session_version,
                    expected_review_batch_version=1,
                ),
                context=other_context,
            )


if __name__ == "__main__":
    unittest.main()
