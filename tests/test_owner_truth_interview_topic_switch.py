from __future__ import annotations

import unittest
from uuid import uuid4

from app.domain.owner_truth.conversation import (
    AppendInterviewMessageCommand,
    ConversationMessageAuthor,
    ConversationMessageKind,
    InterviewSessionState,
    OwnerTruthConversationAccessDenied,
    OwnerTruthInterviewSessionStateConflict,
    OwnerTruthConversationVersionConflict,
    PauseInterviewForTopicSwitchCommand,
    StartInterviewSessionCommand,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_conversation import (
    InMemoryOwnerTruthConversationRepository,
    OwnerTruthConversationService,
)


class OwnerTruthInterviewTopicSwitchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryOwnerTruthConversationRepository()
        self.service = OwnerTruthConversationService(self.repository)
        self.context = OwnerTruthCommandContext(
            vault_id="topic-switch-vault-a",
            owner_subject_id="topic-switch-owner-a",
            actor_subject_id="topic-switch-owner-a",
            policy_version="owner-truth-v1",
        )
        self.thread_id = str(uuid4())
        self.session_id = str(uuid4())
        started = self.service.start_session(
            command=StartInterviewSessionCommand(
                command_id="topic-switch-start",
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
        appended = self.service.append_message(
            command=AppendInterviewMessageCommand(
                command_id="topic-switch-append",
                thread_id=self.thread_id,
                session_id=self.session_id,
                message_id=str(uuid4()),
                expected_thread_version=self.thread_version,
                expected_session_version=self.session_version,
                author=ConversationMessageAuthor.OWNER,
                kind=ConversationMessageKind.NARRATIVE,
                text="这条私有叙述只用于验证换话题的线程边界。",
            ),
            context=self.context,
        )
        self.thread_version = appended.thread_version
        self.session_version = appended.session_version

    def _pause(self, *, command_id: str = "topic-switch-pause") -> PauseInterviewForTopicSwitchCommand:
        return PauseInterviewForTopicSwitchCommand(
            command_id=command_id,
            thread_id=self.thread_id,
            session_id=self.session_id,
            expected_thread_version=self.thread_version,
            expected_session_version=self.session_version,
        )

    def test_topic_switch_pauses_old_thread_then_allows_an_explicit_new_session(self) -> None:
        self._append_owner_message()
        command = self._pause()

        paused = self.service.pause_for_topic_switch(command=command, context=self.context)
        replayed = self.service.pause_for_topic_switch(command=command, context=self.context)

        self.assertEqual(paused.outcome, "created")
        self.assertEqual(replayed.outcome, "deduplicated")
        self.assertEqual(paused.state, InterviewSessionState.PAUSED)
        self.assertEqual(paused.thread_version, 3)
        self.assertEqual(paused.session_version, 3)

        snapshot = self.repository.snapshot(vault_id=self.context.vault_id)
        old_thread = next(item for item in snapshot["threads"] if item["id"] == self.thread_id)
        old_session = next(item for item in snapshot["sessions"] if item["id"] == self.session_id)
        self.assertEqual(old_thread["state"], "paused")
        self.assertEqual(old_thread["rowVersion"], 3)
        self.assertEqual(old_session["state"], InterviewSessionState.PAUSED.value)
        self.assertEqual(old_session["rowVersion"], 3)

        with self.assertRaises(OwnerTruthInterviewSessionStateConflict):
            self.service.append_message(
                command=AppendInterviewMessageCommand(
                    command_id="topic-switch-append-old-thread",
                    thread_id=self.thread_id,
                    session_id=self.session_id,
                    message_id=str(uuid4()),
                    expected_thread_version=3,
                    expected_session_version=3,
                    author=ConversationMessageAuthor.OWNER,
                    kind=ConversationMessageKind.NARRATIVE,
                    text="暂停的旧线程不能再接收这条内容。",
                ),
                context=self.context,
            )

        new_thread_id = str(uuid4())
        new_session_id = str(uuid4())
        new_session = self.service.start_session(
            command=StartInterviewSessionCommand(
                command_id="topic-switch-start-new-thread",
                thread_id=new_thread_id,
                session_id=new_session_id,
                expected_thread_version=0,
                entry_mode="naturalInput",
            ),
            context=self.context,
        )
        self.assertEqual(new_session.state, InterviewSessionState.ACTIVE)

        snapshot = self.repository.snapshot(vault_id=self.context.vault_id)
        self.assertEqual(len(snapshot["threads"]), 2)
        self.assertEqual(len(snapshot["sessions"]), 2)
        self.assertEqual(
            [item["state"] for item in snapshot["sessions"]].count(InterviewSessionState.ACTIVE.value),
            1,
        )
        self.assertEqual(snapshot["authorityEffects"], ())
        self.assertEqual(snapshot["candidateCount"], 0)
        self.assertEqual(snapshot["memoryVersionCount"], 0)

    def test_topic_switch_requires_current_owner_and_both_current_versions(self) -> None:
        self._append_owner_message()
        with self.assertRaises(OwnerTruthConversationVersionConflict):
            self.service.pause_for_topic_switch(
                command=PauseInterviewForTopicSwitchCommand(
                    command_id="topic-switch-stale-thread",
                    thread_id=self.thread_id,
                    session_id=self.session_id,
                    expected_thread_version=1,
                    expected_session_version=self.session_version,
                ),
                context=self.context,
            )

        other_context = OwnerTruthCommandContext(
            vault_id=self.context.vault_id,
            owner_subject_id="topic-switch-owner-b",
            actor_subject_id="topic-switch-owner-b",
            policy_version="owner-truth-v1",
        )
        with self.assertRaises(OwnerTruthConversationAccessDenied):
            self.service.pause_for_topic_switch(
                command=self._pause(command_id="topic-switch-cross-owner"),
                context=other_context,
            )

        session = self.service.read_session(session_id=self.session_id, context=self.context)
        self.assertEqual(session.state, InterviewSessionState.ACTIVE)
        self.assertEqual(session.thread_version, self.thread_version)
        self.assertEqual(session.row_version, self.session_version)


if __name__ == "__main__":
    unittest.main()
