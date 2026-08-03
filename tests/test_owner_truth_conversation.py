import unittest
import uuid
from typing import Optional

from app.domain.owner_truth.conversation import (
    AppendInterviewMessageCommand,
    ConversationMessageAuthor,
    ConversationMessageKind,
    ConversationThreadState,
    EndInterviewSessionCommand,
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
    PostgresOwnerTruthConversationRepository,
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

    def end(
        self,
        *,
        command_id: str = "end-interview-1",
        expected_thread_version: int = 1,
        expected_session_version: int = 1,
    ) -> EndInterviewSessionCommand:
        return EndInterviewSessionCommand(
            command_id=command_id,
            thread_id=self.thread_id,
            session_id=self.session_id,
            expected_thread_version=expected_thread_version,
            expected_session_version=expected_session_version,
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
        self.assertEqual(snapshot.state, ConversationThreadState.ACTIVE)
        self.assertEqual(snapshot.session_id, self.session_id)
        self.assertEqual(snapshot.session_state, InterviewSessionState.ACTIVE)
        self.assertEqual(snapshot.session_boundary, InterviewBoundary.OPEN)
        self.assertTrue(snapshot.is_recommendation_eligible)

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

    def test_recommendation_authority_list_returns_only_active_open_session(self) -> None:
        self.service.start_session(command=self.start(), context=self.context)

        eligible = self.service.list_recommendation_eligible_thread_authorities(
            context=self.context,
        )
        self.assertEqual([item.thread_id for item in eligible], [self.thread_id])
        self.assertTrue(eligible[0].is_recommendation_eligible)

        self.service.set_boundary(
            command=SetInterviewBoundaryCommand(
                command_id="boundary-list-cooldown-1",
                thread_id=self.thread_id,
                session_id=self.session_id,
                expected_session_version=1,
                boundary=InterviewBoundary.COOLDOWN,
            ),
            context=self.context,
        )
        self.assertEqual(
            self.service.list_recommendation_eligible_thread_authorities(context=self.context),
            (),
        )
        cooldown_candidates = self.service.list_recommendation_candidate_thread_authorities(
            context=self.context,
        )
        self.assertEqual([item.thread_id for item in cooldown_candidates], [self.thread_id])
        self.assertTrue(cooldown_candidates[0].is_elapsed_cooldown_candidate)

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
        authority = self.service.read_thread_authority(
            thread_id=self.thread_id,
            context=self.context,
        )
        self.assertEqual(authority.state, ConversationThreadState.ACTIVE)
        self.assertEqual(authority.session_state, InterviewSessionState.PAUSED)
        self.assertEqual(authority.session_boundary, InterviewBoundary.DO_NOT_ASK)
        self.assertFalse(authority.is_recommendation_eligible)
        with self.assertRaises(OwnerTruthInterviewSessionStateConflict):
            self.service.append_message(
                command=self.append(
                    command_id="append-after-do-not-ask",
                    message_id=str(uuid.uuid4()),
                    expected_session_version=2,
                ),
                context=self.context,
            )

    def test_explicit_end_is_idempotent_and_fences_future_turns(self) -> None:
        self.service.start_session(command=self.start(), context=self.context)
        self.service.append_message(command=self.append(), context=self.context)
        command = self.end(
            expected_thread_version=2,
            expected_session_version=2,
        )

        ended = self.service.end_session(command=command, context=self.context)
        replayed = self.service.end_session(command=command, context=self.context)

        self.assertEqual(ended.outcome, "created")
        self.assertEqual(replayed.outcome, "deduplicated")
        self.assertEqual(ended.thread_version, 3)
        self.assertEqual(ended.session_version, 3)
        session = self.service.read_session(session_id=self.session_id, context=self.context)
        self.assertEqual(session.state, InterviewSessionState.ENDED)
        self.assertEqual(session.thread_version, 3)
        self.assertEqual(session.row_version, 3)
        authority = self.service.read_thread_authority(
            thread_id=self.thread_id,
            context=self.context,
        )
        self.assertEqual(authority.state, ConversationThreadState.ENDED)
        self.assertEqual(authority.session_state, InterviewSessionState.ENDED)
        self.assertFalse(authority.is_recommendation_eligible)
        self.assertIsNone(self.service.read_current_session(context=self.context))
        with self.assertRaises(OwnerTruthInterviewSessionStateConflict):
            self.service.append_message(
                command=self.append(
                    command_id="append-after-explicit-end",
                    message_id=str(uuid.uuid4()),
                    expected_thread_version=3,
                    expected_session_version=3,
                ),
                context=self.context,
            )

    def test_explicit_end_allows_paused_session_but_preserves_boundary(self) -> None:
        self.service.start_session(command=self.start(), context=self.context)
        self.service.set_boundary(
            command=SetInterviewBoundaryCommand(
                command_id="pause-before-explicit-end",
                thread_id=self.thread_id,
                session_id=self.session_id,
                expected_session_version=1,
                boundary=InterviewBoundary.DO_NOT_ASK,
            ),
            context=self.context,
        )

        ended = self.service.end_session(
            command=self.end(
                command_id="end-paused-interview",
                expected_thread_version=1,
                expected_session_version=2,
            ),
            context=self.context,
        )

        self.assertEqual(ended.state, InterviewSessionState.ENDED)
        self.assertEqual(ended.boundary, InterviewBoundary.DO_NOT_ASK)
        with self.assertRaises(OwnerTruthInterviewSessionStateConflict):
            self.service.end_session(
                command=self.end(
                    command_id="end-paused-interview-again",
                    expected_thread_version=2,
                    expected_session_version=3,
                ),
                context=self.context,
            )


class PostgresOwnerTruthConversationRepositoryTests(unittest.TestCase):
    def test_pending_review_query_binds_batch_to_current_session_thread(self) -> None:
        class CapturingCursor:
            def __init__(self) -> None:
                self.statements: list[tuple[str, tuple[object, ...]]] = []

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback) -> None:
                return None

            def execute(self, statement: str, params: tuple[object, ...]) -> None:
                self.statements.append((statement, params))

            def fetchone(self):
                return {
                    "owner_subject_id": "owner-a",
                    "authority_epoch": 4,
                    "status": "active",
                }

            def fetchall(self):
                return []

        class CapturingConnection:
            def __init__(self, cursor: CapturingCursor) -> None:
                self.cursor_value = cursor

            def cursor(self, *, row_factory=None):
                return self.cursor_value

        cursor = CapturingCursor()
        repository = PostgresOwnerTruthConversationRepository(CapturingConnection(cursor))
        context = OwnerTruthCommandContext(
            vault_id="vault-a",
            owner_subject_id="owner-a",
            actor_subject_id="owner-a",
            policy_version="owner-truth-v1",
        )

        self.assertEqual(repository.list_pending_interview_review_batches(context=context), ())

        pending_query = next(
            statement
            for statement, _ in cursor.statements
            if "FROM owner_truth.interview_review_batches AS b" in statement
        )
        self.assertIn("AND s.current_thread_id = b.thread_id", pending_query)
        self.assertNotIn("AND s.thread_id = b.thread_id", pending_query)


if __name__ == "__main__":
    unittest.main()
