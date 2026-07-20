from __future__ import annotations

import unittest
from uuid import uuid4

from app.domain.owner_truth.conversation import (
    AppendInterviewMessageCommand,
    ConversationMessageAuthor,
    ConversationMessageKind,
    InterviewBoundary,
    InterviewFatigue,
    InterviewPacingEvent,
    OwnerTruthConversationConflict,
    RecordInterviewPacingCommand,
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


class OwnerTruthInterviewPacingStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryOwnerTruthConversationRepository()
        self.service = OwnerTruthConversationService(self.repository)
        self.context = OwnerTruthCommandContext(
            vault_id="pacing-vault-a",
            owner_subject_id="pacing-owner-a",
            actor_subject_id="pacing-owner-a",
            policy_version="owner-truth-v1",
        )
        self.thread_id = str(uuid4())
        self.session_id = str(uuid4())
        started = self.service.start_session(
            command=StartInterviewSessionCommand(
                command_id="pacing-start",
                thread_id=self.thread_id,
                session_id=self.session_id,
                expected_thread_version=0,
                entry_mode="naturalInput",
            ),
            context=self.context,
        )
        self.thread_version = started.thread_version
        self.session_version = started.session_version

    def _append_owner_message(self) -> None:
        result = self.service.append_message(
            command=AppendInterviewMessageCommand(
                command_id="pacing-owner-message",
                thread_id=self.thread_id,
                session_id=self.session_id,
                message_id=str(uuid4()),
                expected_thread_version=self.thread_version,
                expected_session_version=self.session_version,
                author=ConversationMessageAuthor.OWNER,
                kind=ConversationMessageKind.NARRATIVE,
                text="仅用于验证节奏状态的私有输入。",
            ),
            context=self.context,
        )
        self.thread_version = result.thread_version
        self.session_version = result.session_version

    def _record(self, *, command_id: str, event: InterviewPacingEvent):
        result = self.service.record_pacing(
            command=RecordInterviewPacingCommand(
                command_id=command_id,
                thread_id=self.thread_id,
                session_id=self.session_id,
                expected_session_version=self.session_version,
                event=event,
            ),
            context=self.context,
        )
        self.session_version = result.session_version
        return result

    def test_pacing_snapshot_persists_owner_turn_batch_deepening_and_fatigue(self) -> None:
        self._append_owner_message()
        self._record(
            command_id="pacing-deepening",
            event=InterviewPacingEvent.DEEPENING_COMPLETED,
        )
        self._record(
            command_id="pacing-fatigue-guarded",
            event=InterviewPacingEvent.FATIGUE_GUARDED,
        )

        snapshot = self.service.read_session(session_id=self.session_id, context=self.context)

        self.assertEqual(snapshot.turn_count, 1)
        self.assertEqual(snapshot.candidate_batch_turn_count, 1)
        self.assertEqual(snapshot.deepening_turn_count, 1)
        self.assertEqual(snapshot.fatigue, InterviewFatigue.GUARDED)
        bridged = OwnerTruthInterviewSessionOrchestrationService(
            conversation_service=self.service
        ).decide(
            session_id=self.session_id,
            context=self.context,
            signals=InterviewSessionOrchestrationSignals(
                topic_id="topic-pacing-private-story",
                topic_incomplete=True,
            ),
        )
        self.assertEqual(bridged.persisted_deepening_turn_count, 1)
        self.assertEqual(bridged.persisted_candidate_batch_turn_count, 1)
        self.assertEqual(bridged.persisted_fatigue, InterviewFatigue.GUARDED)

    def test_skip_once_consumption_is_explicit_idempotent_and_never_promotes_authority(self) -> None:
        boundary = self.service.set_boundary(
            command=SetInterviewBoundaryCommand(
                command_id="pacing-skip-once",
                thread_id=self.thread_id,
                session_id=self.session_id,
                expected_session_version=self.session_version,
                boundary=InterviewBoundary.SKIP_ONCE,
            ),
            context=self.context,
        )
        self.session_version = boundary.session_version

        with self.assertRaises(OwnerTruthConversationConflict):
            self.service.record_pacing(
                command=RecordInterviewPacingCommand(
                    command_id="pacing-deepening-while-skip",
                    thread_id=self.thread_id,
                    session_id=self.session_id,
                    expected_session_version=self.session_version,
                    event=InterviewPacingEvent.DEEPENING_COMPLETED,
                ),
                context=self.context,
            )

        created = self._record(
            command_id="pacing-consume-skip",
            event=InterviewPacingEvent.SKIP_ONCE_CONSUMED,
        )
        replayed = self.service.record_pacing(
            command=RecordInterviewPacingCommand(
                command_id="pacing-consume-skip",
                thread_id=self.thread_id,
                session_id=self.session_id,
                expected_session_version=boundary.session_version,
                event=InterviewPacingEvent.SKIP_ONCE_CONSUMED,
            ),
            context=self.context,
        )

        self.assertEqual(created.outcome, "created")
        self.assertEqual(replayed.outcome, "deduplicated")
        snapshot = self.service.read_session(session_id=self.session_id, context=self.context)
        self.assertEqual(snapshot.boundary, InterviewBoundary.OPEN)
        self.assertEqual(self.repository.snapshot(vault_id=self.context.vault_id)["authorityEffects"], ())
        self.assertEqual(self.repository.snapshot(vault_id=self.context.vault_id)["candidateCount"], 0)
        self.assertEqual(self.repository.snapshot(vault_id=self.context.vault_id)["memoryVersionCount"], 0)


if __name__ == "__main__":
    unittest.main()
