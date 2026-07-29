from __future__ import annotations

from contextlib import contextmanager
import unittest
from uuid import uuid4

from app.domain.owner_truth.conversation import (
    AppendInterviewMessageCommand,
    ConversationMessageAuthor,
    ConversationMessageKind,
    InterviewBoundary,
    InterviewReviewBatchTrigger,
    OwnerTruthConversationAccessDenied,
    SetInterviewBoundaryCommand,
    StartInterviewSessionCommand,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_conversation import (
    InMemoryOwnerTruthConversationRepository,
    OwnerTruthConversationService,
)
from app.services.owner_truth_interview_review_batch_automation import (
    OwnerTruthInterviewReviewBatchAutomationService,
    review_batch_automation_summary,
)


class _Store:
    def __init__(self) -> None:
        self.conversation_repository = InMemoryOwnerTruthConversationRepository()

    @contextmanager
    def request_unit_of_work(self, *, correlation_id: str, command_id: str):
        del correlation_id, command_id
        yield

    def owner_truth_conversation_repository(self):
        return self.conversation_repository


class OwnerTruthInterviewReviewBatchAutomationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = OwnerTruthCommandContext(
            vault_id="review-batch-automation-vault",
            owner_subject_id="review-batch-automation-owner",
            actor_subject_id="review-batch-automation-owner",
        )
        self.store = _Store()
        self.conversation = OwnerTruthConversationService(self.store.conversation_repository)
        self.automation = OwnerTruthInterviewReviewBatchAutomationService(
            self.store,
            enabled=True,
        )
        self.thread_id = str(uuid4())
        self.session_id = str(uuid4())
        started = self.conversation.start_session(
            command=StartInterviewSessionCommand(
                command_id="review-batch-automation-start",
                thread_id=self.thread_id,
                session_id=self.session_id,
                expected_thread_version=0,
                entry_mode="naturalInput",
            ),
            context=self.context,
        )
        self.thread_version = started.thread_version
        self.session_version = started.session_version

    def _append(self, index: int):
        result = self.conversation.append_message(
            command=AppendInterviewMessageCommand(
                command_id=f"review-batch-automation-append-{index}",
                thread_id=self.thread_id,
                session_id=self.session_id,
                message_id=str(uuid4()),
                expected_thread_version=self.thread_version,
                expected_session_version=self.session_version,
                author=ConversationMessageAuthor.OWNER,
                kind=ConversationMessageKind.NARRATIVE,
                text=f"私有第 {index + 1} 段叙述不应出现在自动化摘要。",
            ),
            context=self.context,
        )
        self.thread_version = result.thread_version
        self.session_version = result.session_version
        return result

    def _ensure(self, *, command_id: str):
        return self.automation.ensure_after_transition(
            session_id=self.session_id,
            transition_command_id=command_id,
            context=self.context,
        )

    def test_fifth_owner_turn_creates_one_pending_review_batch(self) -> None:
        for index in range(5):
            appended = self._append(index)

        result = self._ensure(command_id="review-batch-automation-append-4")
        summary = review_batch_automation_summary(result)

        self.assertEqual(result.state, "created")
        self.assertTrue(result.review_batch_created)
        self.assertEqual(result.session_version, 7)
        self.assertEqual(result.review_batch.trigger, InterviewReviewBatchTrigger.TURN_THRESHOLD)
        self.assertEqual(result.review_batch.captured_candidate_batch_turn_count, 5)
        self.assertEqual(summary["state"], "created")
        self.assertTrue(summary["reviewBatchCreated"])
        self.assertEqual(summary["sessionVersion"], 7)
        self.assertEqual(summary["reviewBatch"]["trigger"], "turnThreshold")
        self.assertNotIn("私有第", str(summary))
        self.assertNotIn(self.session_id, str(summary))
        self.assertNotIn(self.thread_id, str(summary))
        snapshot = self.store.conversation_repository.snapshot(vault_id=self.context.vault_id)
        self.assertEqual(snapshot["candidateCount"], 0)
        self.assertEqual(snapshot["memoryVersionCount"], 0)
        self.assertEqual(snapshot["authorityEffects"], ())
        self.assertEqual(appended.message_sequence, 5)

    def test_exit_with_pending_turns_creates_session_exit_batch(self) -> None:
        for index in range(4):
            self._append(index)
        paused = self.conversation.set_boundary(
            command=SetInterviewBoundaryCommand(
                command_id="review-batch-automation-exit",
                thread_id=self.thread_id,
                session_id=self.session_id,
                expected_session_version=self.session_version,
                boundary=InterviewBoundary.DO_NOT_ASK,
            ),
            context=self.context,
        )
        self.session_version = paused.session_version

        result = self._ensure(command_id="review-batch-automation-exit")

        self.assertEqual(result.state, "created")
        self.assertEqual(result.review_batch.trigger, InterviewReviewBatchTrigger.SESSION_EXIT)
        self.assertEqual(result.review_batch.captured_candidate_batch_turn_count, 4)
        self.assertEqual(result.session_version, 7)

    def test_replay_or_pending_batch_never_creates_a_second_batch(self) -> None:
        for index in range(5):
            self._append(index)

        created = self._ensure(command_id="review-batch-automation-replay")
        replay = self._ensure(command_id="review-batch-automation-replay")
        batches = self.conversation.list_review_batches(
            session_id=self.session_id,
            context=self.context,
        )

        self.assertEqual(created.state, "created")
        self.assertEqual(replay.state, "alreadyPending")
        self.assertEqual(replay.session_version, created.session_version)
        self.assertEqual(replay.review_batch.review_batch_id, created.review_batch.review_batch_id)
        self.assertEqual(len(batches), 1)

    def test_not_due_and_cross_owner_fail_closed_without_creating_batch(self) -> None:
        self._append(0)

        not_due = self._ensure(command_id="review-batch-automation-not-due")
        self.assertEqual(not_due.state, "notDue")
        self.assertFalse(not_due.review_batch_created)
        self.assertEqual(not_due.session_version, 2)

        other_context = OwnerTruthCommandContext(
            vault_id=self.context.vault_id,
            owner_subject_id="review-batch-automation-other",
            actor_subject_id="review-batch-automation-other",
        )
        with self.assertRaises(OwnerTruthConversationAccessDenied):
            self.automation.ensure_after_transition(
                session_id=self.session_id,
                transition_command_id="review-batch-automation-cross-owner",
                context=other_context,
            )
        self.assertEqual(
            self.conversation.list_review_batches(
                session_id=self.session_id,
                context=self.context,
            ),
            (),
        )


if __name__ == "__main__":
    unittest.main()
