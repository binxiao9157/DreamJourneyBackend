import unittest
import uuid
from typing import Optional

from app.domain.owner_truth.conversation import (
    AppendInterviewMessageCommand,
    ConversationMessageAuthor,
    ConversationMessageKind,
    InterviewBoundary,
    InterviewSessionState,
    OwnerTruthConversationAccessDenied,
    OwnerTruthInterviewSessionStateConflict,
    OwnerTruthConversationVersionConflict,
    SetInterviewBoundaryCommand,
    StartInterviewSessionCommand,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_conversation import (
    InMemoryOwnerTruthConversationRepository,
    OwnerTruthConversationService,
)


class OwnerTruthConversationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryOwnerTruthConversationRepository()
        self.service = OwnerTruthConversationService(self.repository)
        self.context = OwnerTruthCommandContext(
            vault_id="interview-vault-a",
            owner_subject_id="interview-owner-a",
            actor_subject_id="interview-owner-a",
            policy_version="owner-truth-v1",
        )
        self.thread_id = str(uuid.uuid4())
        self.session_id = str(uuid.uuid4())
        self.message_id = str(uuid.uuid4())

    def start(self, *, command_id: str = "start-interview-1") -> StartInterviewSessionCommand:
        return StartInterviewSessionCommand(
            command_id=command_id,
            thread_id=self.thread_id,
            session_id=self.session_id,
            expected_thread_version=0,
            entry_mode="naturalInput",
        )

    def append(
        self,
        *,
        command_id: str = "append-interview-1",
        message_id: Optional[str] = None,
        expected_thread_version: int = 1,
        expected_session_version: int = 1,
        text: str = "我想从第一次创业失败的经历讲起。",
    ) -> AppendInterviewMessageCommand:
        return AppendInterviewMessageCommand(
            command_id=command_id,
            thread_id=self.thread_id,
            session_id=self.session_id,
            message_id=message_id or self.message_id,
            expected_thread_version=expected_thread_version,
            expected_session_version=expected_session_version,
            author=ConversationMessageAuthor.OWNER,
            kind=ConversationMessageKind.NARRATIVE,
            text=text,
        )

    def test_start_replays_without_creating_a_second_thread_or_session(self) -> None:
        command = self.start()

        created = self.service.start_session(command=command, context=self.context)
        replayed = self.service.start_session(command=command, context=self.context)

        self.assertEqual(created.outcome, "created")
        self.assertEqual(replayed.outcome, "deduplicated")
        self.assertEqual(created.thread_id, replayed.thread_id)
        self.assertEqual(created.session_id, replayed.session_id)
        snapshot = self.repository.snapshot(vault_id=self.context.vault_id)
        self.assertEqual(len(snapshot["threads"]), 1)
        self.assertEqual(len(snapshot["sessions"]), 1)
        self.assertEqual(snapshot["authorityEffects"], ())

    def test_message_append_is_owner_scoped_idempotent_and_does_not_promote_memory(self) -> None:
        self.service.start_session(command=self.start(), context=self.context)
        command = self.append()

        created = self.service.append_message(command=command, context=self.context)
        replayed = self.service.append_message(command=command, context=self.context)

        self.assertEqual(created.outcome, "created")
        self.assertEqual(replayed.outcome, "deduplicated")
        self.assertEqual(created.message_id, self.message_id)
        snapshot = self.repository.snapshot(vault_id=self.context.vault_id)
        self.assertEqual(len(snapshot["messages"]), 1)
        self.assertEqual(snapshot["messages"][0]["text"], command.text)
        self.assertEqual(snapshot["authorityEffects"], ())
        self.assertEqual(snapshot["candidateCount"], 0)
        self.assertEqual(snapshot["memoryVersionCount"], 0)

    def test_stale_versions_are_rejected_without_appending_a_message(self) -> None:
        self.service.start_session(command=self.start(), context=self.context)
        self.service.append_message(command=self.append(), context=self.context)

        with self.assertRaises(OwnerTruthConversationVersionConflict):
            self.service.append_message(
                command=self.append(
                    command_id="append-interview-stale",
                    message_id=str(uuid.uuid4()),
                ),
                context=self.context,
            )

        snapshot = self.repository.snapshot(vault_id=self.context.vault_id)
        self.assertEqual(len(snapshot["messages"]), 1)

    def test_cross_owner_context_cannot_read_or_append_to_the_session(self) -> None:
        self.service.start_session(command=self.start(), context=self.context)
        other_context = OwnerTruthCommandContext(
            vault_id=self.context.vault_id,
            owner_subject_id="interview-owner-b",
            actor_subject_id="interview-owner-b",
            policy_version="owner-truth-v1",
        )

        with self.assertRaises(OwnerTruthConversationAccessDenied):
            self.service.read_session(session_id=self.session_id, context=other_context)
        with self.assertRaises(OwnerTruthConversationAccessDenied):
            self.service.append_message(command=self.append(), context=other_context)

    def test_thread_authority_read_is_owner_scoped_and_value_free(self) -> None:
        self.service.start_session(command=self.start(), context=self.context)

        snapshot = self.service.read_thread_authority(
            thread_id=self.thread_id,
            context=self.context,
        )

        self.assertEqual(snapshot.thread_id, self.thread_id)
        self.assertEqual(snapshot.vault_id, self.context.vault_id)
        self.assertEqual(snapshot.owner_subject_id, self.context.owner_subject_id)
        self.assertEqual(snapshot.authority_epoch, 0)

        other_context = OwnerTruthCommandContext(
            vault_id=self.context.vault_id,
            owner_subject_id="interview-owner-b",
            actor_subject_id="interview-owner-b",
            policy_version="owner-truth-v1",
        )
        with self.assertRaises(OwnerTruthConversationAccessDenied):
            self.service.read_thread_authority(thread_id=self.thread_id, context=other_context)
        with self.assertRaises(OwnerTruthConversationAccessDenied):
            self.service.read_thread_authority(thread_id="not-a-uuid", context=self.context)

    def test_do_not_ask_pauses_the_session_and_persists_the_boundary(self) -> None:
        self.service.start_session(command=self.start(), context=self.context)
        command = SetInterviewBoundaryCommand(
            command_id="boundary-do-not-ask-1",
            thread_id=self.thread_id,
            session_id=self.session_id,
            expected_session_version=1,
            boundary=InterviewBoundary.DO_NOT_ASK,
        )

        created = self.service.set_boundary(command=command, context=self.context)
        replayed = self.service.set_boundary(command=command, context=self.context)

        self.assertEqual(created.outcome, "created")
        self.assertEqual(replayed.outcome, "deduplicated")
        session = self.service.read_session(session_id=self.session_id, context=self.context)
        self.assertEqual(session.state, InterviewSessionState.PAUSED)
        self.assertEqual(session.boundary, InterviewBoundary.DO_NOT_ASK)
        with self.assertRaises(OwnerTruthInterviewSessionStateConflict):
            self.service.append_message(
                command=self.append(
                    command_id="append-after-do-not-ask",
                    message_id=str(uuid.uuid4()),
                    expected_session_version=2,
                ),
                context=self.context,
            )


if __name__ == "__main__":
    unittest.main()
