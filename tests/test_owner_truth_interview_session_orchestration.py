from __future__ import annotations

import unittest
from uuid import uuid4

from app.domain.owner_truth.conversation import (
    AppendInterviewMessageCommand,
    ConversationMessageAuthor,
    ConversationMessageKind,
    InterviewBoundary as ConversationInterviewBoundary,
    OwnerTruthConversationAccessDenied,
    SetInterviewBoundaryCommand,
    StartInterviewSessionCommand,
)
from app.domain.owner_truth.interview_orchestration import InterviewAction
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_conversation import (
    InMemoryOwnerTruthConversationRepository,
    OwnerTruthConversationService,
)
from app.services.owner_truth_interview_session_orchestration import (
    InterviewSessionOrchestrationSignals,
    OwnerTruthInterviewSessionOrchestrationService,
)


class OwnerTruthInterviewSessionOrchestrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryOwnerTruthConversationRepository()
        self.conversation = OwnerTruthConversationService(self.repository)
        self.orchestration = OwnerTruthInterviewSessionOrchestrationService(
            conversation_service=self.conversation
        )
        self.context = OwnerTruthCommandContext(
            vault_id="orchestration-vault-a",
            owner_subject_id="orchestration-owner-a",
            actor_subject_id="orchestration-owner-a",
            policy_version="owner-truth-v1",
        )
        self.thread_id = str(uuid4())
        self.session_id = str(uuid4())
        self.thread_version = 1
        self.session_version = 1

    def _start(self) -> None:
        result = self.conversation.start_session(
            command=StartInterviewSessionCommand(
                command_id="orchestration-start",
                thread_id=self.thread_id,
                session_id=self.session_id,
                expected_thread_version=0,
                entry_mode="naturalInput",
            ),
            context=self.context,
        )
        self.thread_version = result.thread_version
        self.session_version = result.session_version

    def _append_owner_message(self, *, index: int, text: str = "一段只属于本人的私有叙述") -> None:
        result = self.conversation.append_message(
            command=AppendInterviewMessageCommand(
                command_id=f"orchestration-append-{index}",
                thread_id=self.thread_id,
                session_id=self.session_id,
                message_id=str(uuid4()),
                expected_thread_version=self.thread_version,
                expected_session_version=self.session_version,
                author=ConversationMessageAuthor.OWNER,
                kind=ConversationMessageKind.NARRATIVE,
                text=text,
            ),
            context=self.context,
        )
        self.thread_version = result.thread_version
        self.session_version = result.session_version

    def _signals(self, **overrides: object) -> InterviewSessionOrchestrationSignals:
        values: dict[str, object] = {
            "topic_id": "topic-private-story",
            "topic_incomplete": False,
        }
        values.update(overrides)
        return InterviewSessionOrchestrationSignals(**values)

    def test_persisted_owner_turn_count_drives_review_batch_without_reading_message_text(self) -> None:
        self._start()
        private_text = "不应进入策略摘要的私有原文"
        for index in range(5):
            self._append_owner_message(index=index, text=private_text)

        result = self.orchestration.decide(
            session_id=self.session_id,
            context=self.context,
            signals=self._signals(),
        )

        self.assertEqual(result.decision.action, InterviewAction.LISTEN)
        self.assertTrue(result.decision.review_batch_due)
        self.assertEqual(result.persisted_owner_turn_count, 5)
        rendered = str(result.value_free_summary())
        self.assertNotIn(private_text, rendered)
        self.assertNotIn(self.thread_id, rendered)
        self.assertNotIn(self.session_id, rendered)

    def test_persisted_skip_once_is_a_listen_only_decision_without_mutating_the_session(self) -> None:
        self._start()
        boundary = self.conversation.set_boundary(
            command=SetInterviewBoundaryCommand(
                command_id="orchestration-skip-once",
                thread_id=self.thread_id,
                session_id=self.session_id,
                expected_session_version=self.session_version,
                boundary=ConversationInterviewBoundary.SKIP_ONCE,
            ),
            context=self.context,
        )
        self.session_version = boundary.session_version

        result = self.orchestration.decide(
            session_id=self.session_id,
            context=self.context,
            signals=self._signals(topic_incomplete=True),
        )

        self.assertEqual(result.decision.action, InterviewAction.LISTEN)
        self.assertTrue(result.decision.consumes_one_shot_boundary)
        persisted = self.conversation.read_session(session_id=self.session_id, context=self.context)
        self.assertEqual(persisted.row_version, self.session_version)
        self.assertEqual(persisted.boundary, ConversationInterviewBoundary.SKIP_ONCE)

    def test_persisted_do_not_ask_fails_closed_even_when_transient_signals_are_safe(self) -> None:
        self._start()
        boundary = self.conversation.set_boundary(
            command=SetInterviewBoundaryCommand(
                command_id="orchestration-do-not-ask",
                thread_id=self.thread_id,
                session_id=self.session_id,
                expected_session_version=self.session_version,
                boundary=ConversationInterviewBoundary.DO_NOT_ASK,
            ),
            context=self.context,
        )
        self.session_version = boundary.session_version

        result = self.orchestration.decide(
            session_id=self.session_id,
            context=self.context,
            signals=self._signals(topic_incomplete=True),
        )

        self.assertEqual(result.decision.action, InterviewAction.PAUSE)
        self.assertEqual(result.persisted_boundary, ConversationInterviewBoundary.DO_NOT_ASK)

    def test_cross_owner_cannot_use_the_bridge_and_no_authority_record_is_created(self) -> None:
        self._start()
        other_context = OwnerTruthCommandContext(
            vault_id=self.context.vault_id,
            owner_subject_id="orchestration-owner-b",
            actor_subject_id="orchestration-owner-b",
            policy_version="owner-truth-v1",
        )

        with self.assertRaises(OwnerTruthConversationAccessDenied):
            self.orchestration.decide(
                session_id=self.session_id,
                context=other_context,
                signals=self._signals(),
            )

        snapshot = self.repository.snapshot(vault_id=self.context.vault_id)
        self.assertEqual(snapshot["candidateCount"], 0)
        self.assertEqual(snapshot["memoryVersionCount"], 0)
        self.assertEqual(snapshot["authorityEffects"], ())


if __name__ == "__main__":
    unittest.main()
