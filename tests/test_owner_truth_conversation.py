import unittest
import uuid
from datetime import datetime, timezone
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
    OwnerTruthConversationConflict,
    OwnerTruthInterviewSessionStateConflict,
    OwnerTruthInterviewTurnsPending,
    OwnerTruthConversationVersionConflict,
    PauseInterviewForTopicSwitchWriteRecord,
    SetInterviewBoundaryCommand,
    StartInterviewSessionCommand,
    StartInterviewSessionWriteRecord,
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

    def start(
        self,
        *,
        command_id: str = "start-interview-1",
        entry_mode: str = "naturalInput",
        product_session_id: Optional[str] = None,
    ) -> StartInterviewSessionCommand:
        return StartInterviewSessionCommand(
            command_id=command_id,
            thread_id=self.thread_id,
            session_id=self.session_id,
            expected_thread_version=0,
            entry_mode=entry_mode,
            product_session_id=product_session_id,
        )

    def test_live_session_retains_product_session_binding(self) -> None:
        command = self.start(
            command_id="start-live-bound",
            entry_mode="live",
            product_session_id="echo_live_product_001",
        )

        result = self.service.start_session(command=command, context=self.context)

        self.assertEqual(result.outcome, "created")
        session = self.repository._sessions[(self.context.vault_id, self.session_id)]
        thread = self.repository._threads[(self.context.vault_id, self.thread_id)]
        self.assertEqual(
            session["metadata"]["productSessionId"],
            "echo_live_product_001",
        )
        self.assertEqual(
            thread["metadata"]["productSessionId"],
            "echo_live_product_001",
        )

    def test_product_session_lookup_is_exact_and_legacy_lane_excludes_live(self) -> None:
        first_thread_id = str(uuid.uuid4())
        first_session_id = str(uuid.uuid4())
        second_thread_id = str(uuid.uuid4())
        second_session_id = str(uuid.uuid4())
        self.service.start_session(
            command=StartInterviewSessionCommand(
                command_id="start-live-product-a",
                thread_id=first_thread_id,
                session_id=first_session_id,
                expected_thread_version=0,
                entry_mode="live",
                product_session_id="echo_live_product_a",
            ),
            context=self.context,
        )
        self.service.start_session(
            command=StartInterviewSessionCommand(
                command_id="start-live-product-b",
                thread_id=second_thread_id,
                session_id=second_session_id,
                expected_thread_version=0,
                entry_mode="live",
                product_session_id="echo_live_product_b",
            ),
            context=self.context,
        )

        first = self.service.read_current_session(
            context=self.context,
            product_session_id="echo_live_product_a",
        )
        second = self.service.read_current_session(
            context=self.context,
            product_session_id="echo_live_product_b",
        )

        self.assertEqual(first.session_id if first else None, first_session_id)
        self.assertEqual(second.session_id if second else None, second_session_id)
        self.assertIsNone(self.service.read_current_session(context=self.context))

    def test_active_product_session_cannot_be_started_twice(self) -> None:
        self.service.start_session(
            command=self.start(
                command_id="start-live-product-first",
                entry_mode="live",
                product_session_id="echo_live_product_shared",
            ),
            context=self.context,
        )

        with self.assertRaises(OwnerTruthInterviewSessionStateConflict):
            self.service.start_session(
                command=StartInterviewSessionCommand(
                    command_id="start-live-product-reused",
                    thread_id=str(uuid.uuid4()),
                    session_id=str(uuid.uuid4()),
                    expected_thread_version=0,
                    entry_mode="live",
                    product_session_id="echo_live_product_shared",
                ),
                context=self.context,
            )

    def append(
        self,
        *,
        command_id: str = "append-interview-1",
        message_id: Optional[str] = None,
        expected_thread_version: int = 1,
        expected_session_version: int = 1,
        text: str = "我想从第一次创业失败的经历讲起。",
        capture_mode: str = "naturalInput",
        client_sequence_number: Optional[int] = None,
        captured_at: Optional[str] = None,
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
            capture_mode=capture_mode,
            client_sequence_number=client_sequence_number,
            captured_at=captured_at,
        )

    def end(
        self,
        *,
        command_id: str = "end-interview-1",
        expected_thread_version: int = 1,
        expected_session_version: int = 1,
        last_client_sequence_number: Optional[int] = None,
    ) -> EndInterviewSessionCommand:
        return EndInterviewSessionCommand(
            command_id=command_id,
            thread_id=self.thread_id,
            session_id=self.session_id,
            expected_thread_version=expected_thread_version,
            expected_session_version=expected_session_version,
            last_client_sequence_number=last_client_sequence_number,
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

    def test_lost_live_receipts_replay_after_process_restart_and_new_product_session_isolated(self) -> None:
        product_session_id = "echo-live-restart-proof"
        self.service.start_session(
            command=self.start(
                command_id="start-restart-proof",
                entry_mode="live",
                product_session_id=product_session_id,
            ),
            context=self.context,
        )
        first_turn = self.append(
            command_id="append-restart-proof",
            message_id=self.message_id,
            capture_mode="live",
            client_sequence_number=1,
            captured_at="2026-09-08T12:00:00Z",
        )
        created_turn = self.service.append_message(command=first_turn, context=self.context)

        # A fresh service instance models an app/server process restart while
        # keeping the same persistent repository and durable command receipts.
        recovered_service = OwnerTruthConversationService(self.repository)
        replayed_turn = recovered_service.append_message(
            command=self.append(
                command_id="append-restart-proof",
                message_id=self.message_id,
                expected_thread_version=2,
                expected_session_version=2,
                capture_mode="live",
                client_sequence_number=1,
                captured_at="2026-09-08T12:00:00Z",
            ),
            context=self.context,
        )
        self.assertEqual(replayed_turn.outcome, "deduplicated")
        self.assertEqual(replayed_turn.message_id, created_turn.message_id)
        self.assertEqual(replayed_turn.continuous_client_sequence, 1)

        closed = recovered_service.end_session(
            command=self.end(
                command_id="end-restart-proof",
                expected_thread_version=2,
                expected_session_version=2,
                last_client_sequence_number=1,
            ),
            context=self.context,
        )
        replayed_close = OwnerTruthConversationService(self.repository).end_session(
            command=self.end(
                command_id="end-restart-proof",
                expected_thread_version=3,
                expected_session_version=3,
                last_client_sequence_number=1,
            ),
            context=self.context,
        )
        self.assertEqual(closed.outcome, "created")
        self.assertEqual(replayed_close.outcome, "deduplicated")
        self.assertEqual(replayed_close.continuous_client_sequence, 1)

        new_thread_id = str(uuid.uuid4())
        new_session_id = str(uuid.uuid4())
        fresh = OwnerTruthConversationService(self.repository)
        fresh.start_session(
            command=StartInterviewSessionCommand(
                command_id="start-after-restart-proof",
                thread_id=new_thread_id,
                session_id=new_session_id,
                expected_thread_version=0,
                entry_mode="live",
                product_session_id=product_session_id,
            ),
            context=self.context,
        )
        current = fresh.read_current_session(
            context=self.context,
            product_session_id=product_session_id,
        )
        self.assertEqual(current.session_id if current else None, new_session_id)
        self.assertEqual(current.thread_id if current else None, new_thread_id)
        with self.assertRaises(OwnerTruthInterviewSessionStateConflict):
            fresh.append_message(
                command=self.append(
                    command_id="append-ended-session-after-new-live",
                    message_id=str(uuid.uuid4()),
                    expected_thread_version=3,
                    expected_session_version=3,
                    capture_mode="live",
                    client_sequence_number=2,
                    captured_at="2026-09-08T12:00:02Z",
                ),
                context=self.context,
            )

    def test_live_delivery_watermark_rejects_early_close_then_accepts_late_turn(self) -> None:
        self.service.start_session(
            command=self.start(entry_mode="live", product_session_id="live-product-a"),
            context=self.context,
        )
        second = self.service.append_message(
            command=self.append(
                command_id="append-live-sequence-2",
                message_id=str(uuid.uuid4()),
                client_sequence_number=2,
                captured_at="2026-09-08T10:00:02Z",
                capture_mode="live",
            ),
            context=self.context,
        )
        self.assertEqual(second.client_sequence_number, 2)
        self.assertEqual(second.continuous_client_sequence, 0)
        self.assertEqual(second.delivery_state, "awaitingPriorTurns")

        with self.assertRaises(OwnerTruthInterviewTurnsPending) as raised:
            self.service.end_session(
                command=self.end(
                    command_id="end-live-before-sequence-1",
                    expected_thread_version=2,
                    expected_session_version=2,
                    last_client_sequence_number=2,
                ),
                context=self.context,
            )
        self.assertEqual(raised.exception.requested_sequence, 2)
        self.assertEqual(raised.exception.continuous_sequence, 0)

        first = self.service.append_message(
            command=self.append(
                command_id="append-live-sequence-1",
                message_id=str(uuid.uuid4()),
                expected_thread_version=2,
                expected_session_version=2,
                client_sequence_number=1,
                captured_at="2026-09-08T10:00:01+00:00",
                capture_mode="live",
            ),
            context=self.context,
        )
        self.assertEqual(first.continuous_client_sequence, 2)
        self.assertEqual(first.delivery_state, "contiguous")

        ended = self.service.end_session(
            command=self.end(
                command_id="end-live-after-all-turns",
                expected_thread_version=3,
                expected_session_version=3,
                last_client_sequence_number=2,
            ),
            context=self.context,
        )
        self.assertEqual(ended.continuous_client_sequence, 2)
        self.assertEqual(ended.delivery_state, "closedAfterContiguousDelivery")
        session = self.service.read_session(session_id=self.session_id, context=self.context)
        self.assertEqual(session.product_session_id, "live-product-a")
        self.assertEqual(session.continuous_client_sequence, 2)
        self.assertEqual(session.close_requested_client_sequence, 2)

    def test_live_delivery_watermark_rejects_another_command_claiming_same_turn(self) -> None:
        self.service.start_session(command=self.start(entry_mode="live"), context=self.context)
        self.service.append_message(
            command=self.append(
                command_id="append-live-sequence-1",
                client_sequence_number=1,
                capture_mode="live",
            ),
            context=self.context,
        )

        with self.assertRaises(OwnerTruthConversationConflict):
            self.service.append_message(
                command=self.append(
                    command_id="append-live-sequence-1-reused",
                    message_id=str(uuid.uuid4()),
                    expected_thread_version=2,
                    expected_session_version=2,
                    client_sequence_number=1,
                    capture_mode="live",
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
    def test_live_delivery_status_reads_message_and_operation_receipts(self) -> None:
        captured_at = datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc)

        class CapturingCursor:
            def __init__(self) -> None:
                self.statement = ""
                self.statements: list[tuple[str, tuple[object, ...]]] = []

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback) -> None:
                return None

            def execute(self, statement: str, params: tuple[object, ...]) -> None:
                self.statement = statement
                self.statements.append((statement, params))

            def fetchone(self):
                if "FROM owner_truth.vaults" in self.statement:
                    return {
                        "owner_subject_id": "owner-a",
                        "authority_epoch": 4,
                        "status": "active",
                    }
                if "FROM owner_truth.interview_sessions AS s" in self.statement:
                    return {
                        "id": "session-a",
                        "thread_id": "thread-a",
                        "state": "ended",
                        "boundary": "open",
                        "row_version": 9,
                        "thread_version": 8,
                        "authority_epoch": 4,
                        "continuous_client_sequence": 1,
                        "close_requested_client_sequence": 1,
                    }
                return None

            def fetchall(self):
                if "FROM owner_truth.conversation_messages AS m" in self.statement:
                    return [
                        {
                            "id": "message-a",
                            "client_sequence_number": 1,
                            "author": "owner",
                            "kind": "narrative",
                            "captured_at": captured_at,
                            "content_hash": "content-hash-a",
                            "command_id_hash": "append-command-hash-a",
                        }
                    ]
                if "command_type IN ('startInterviewSession', 'endInterviewSession')" in self.statement:
                    return [
                        {
                            "id": "receipt-start-a",
                            "command_id_hash": "start-command-hash-a",
                            "command_type": "startInterviewSession",
                            "target_thread_id": "thread-a",
                            "target_session_id": "session-a",
                        },
                        {
                            "id": "receipt-end-a",
                            "command_id_hash": "end-command-hash-a",
                            "command_type": "endInterviewSession",
                            "target_thread_id": "thread-a",
                            "target_session_id": "session-a",
                        },
                    ]
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

        status = repository.read_live_delivery_status(
            session_id="session-a",
            product_session_id="echo-live-a",
            from_client_sequence=1,
            limit=20,
            context=context,
        )

        self.assertEqual(status.continuous_client_sequence, 1)
        self.assertEqual(status.close_requested_client_sequence, 1)
        self.assertEqual([item.client_sequence_number for item in status.deliveries], [1])
        self.assertEqual(
            [operation.operation for operation in status.operations],
            ["startInterviewSession", "endInterviewSession"],
        )
        operation_queries = [
            statement
            for statement, _ in cursor.statements
            if "command_type IN ('startInterviewSession', 'endInterviewSession')" in statement
        ]
        self.assertEqual(len(operation_queries), 1)

    def test_pause_for_topic_switch_does_not_require_end_delivery_watermark(self) -> None:
        class CapturingCursor:
            def __init__(self) -> None:
                self.statement = ""

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback) -> None:
                return None

            def execute(self, statement: str, params: tuple[object, ...]) -> None:
                self.statement = statement

            def fetchone(self):
                if "FROM owner_truth.vaults" in self.statement:
                    return {
                        "owner_subject_id": "owner-a",
                        "authority_epoch": 4,
                        "status": "active",
                    }
                if "FROM owner_truth.conversation_command_receipts" in self.statement:
                    return None
                if "FROM owner_truth.interview_sessions AS s" in self.statement:
                    return {
                        "id": "session-a",
                        "owner_subject_id": "owner-a",
                        "current_thread_id": "thread-a",
                        "state": "active",
                        "boundary": "open",
                        "turn_count": 0,
                        "deepening_turn_count": 0,
                        "candidate_batch_turn_count": 0,
                        "pending_review_batch_id": None,
                        "fatigue": "normal",
                        "authority_epoch": 4,
                        "row_version": 1,
                        "continuous_client_sequence": 0,
                        "close_requested_client_sequence": None,
                        "thread_id": "thread-a",
                        "thread_state": "active",
                        "thread_owner_subject_id": "owner-a",
                        "thread_authority_epoch": 4,
                        "thread_row_version": 1,
                        "thread_entry_mode": "live",
                    }
                if "UPDATE owner_truth.conversation_threads" in self.statement:
                    return {"row_version": 2}
                if "UPDATE owner_truth.interview_sessions" in self.statement:
                    return {"row_version": 2, "state": "paused", "boundary": "open"}
                return None

        class CapturingConnection:
            def __init__(self, cursor: CapturingCursor) -> None:
                self.cursor_value = cursor

            def cursor(self, *, row_factory=None):
                return self.cursor_value

        repository = PostgresOwnerTruthConversationRepository(
            CapturingConnection(CapturingCursor())
        )
        result = repository.pause_interview_for_topic_switch(
            PauseInterviewForTopicSwitchWriteRecord(
                receipt_id="receipt-a",
                command_id_hash="command-a",
                payload_hash="payload-a",
                thread_id="thread-a",
                session_id="session-a",
                expected_thread_version=1,
                expected_session_version=1,
                vault_id="vault-a",
                owner_subject_id="owner-a",
                actor_subject_id="owner-a",
                policy_version="owner-truth-v1",
            )
        )

        self.assertEqual(result.state, InterviewSessionState.PAUSED)
        self.assertEqual(result.thread_version, 2)
        self.assertEqual(result.session_version, 2)

    def test_start_adapts_thread_and_session_metadata_for_jsonb(self) -> None:
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
                statement = self.statements[-1][0]
                if "INSERT INTO owner_truth.vaults" in statement:
                    return {
                        "owner_subject_id": "owner-a",
                        "authority_epoch": 4,
                        "status": "active",
                    }
                if "INSERT INTO owner_truth.conversation_threads" in statement:
                    return {"row_version": 1}
                if "INSERT INTO owner_truth.interview_sessions" in statement:
                    return {
                        "row_version": 1,
                        "state": "active",
                        "boundary": "open",
                    }
                return None

        class CapturingConnection:
            def __init__(self, cursor: CapturingCursor) -> None:
                self.cursor_value = cursor

            def cursor(self, *, row_factory=None):
                return self.cursor_value

        cursor = CapturingCursor()
        repository = PostgresOwnerTruthConversationRepository(CapturingConnection(cursor))
        record = StartInterviewSessionWriteRecord(
            receipt_id="receipt-a",
            command_id_hash="command-a",
            payload_hash="payload-a",
            thread_id="thread-a",
            session_id="session-a",
            expected_thread_version=0,
            entry_mode="live",
            vault_id="vault-a",
            owner_subject_id="owner-a",
            actor_subject_id="owner-a",
            policy_version="owner-truth-v1",
            product_session_id="echo-live-a",
        )

        result = repository.start_interview_session(record)

        self.assertEqual(result.outcome, "created")
        jsonb_inserts = [
            params
            for statement, params in cursor.statements
            if "INSERT INTO owner_truth.conversation_threads" in statement
            or "INSERT INTO owner_truth.interview_sessions" in statement
        ]
        self.assertEqual(len(jsonb_inserts), 2)
        for params in jsonb_inserts:
            metadata = params[-1]
            self.assertFalse(isinstance(metadata, dict))
            self.assertEqual(
                getattr(metadata, "obj", metadata),
                {"productSessionId": "echo-live-a"},
            )

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
