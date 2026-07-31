from __future__ import annotations

import unittest
from uuid import uuid4

from app.domain.owner_truth.conversation import (
    InterviewBoundary,
    SetInterviewBoundaryCommand,
    StartInterviewSessionCommand,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_conversation import (
    InMemoryOwnerTruthConversationRepository,
    OwnerTruthConversationAccessDenied,
    OwnerTruthConversationService,
)
from app.services.owner_truth_topic_shift_preflight import (
    OwnerTruthTopicShiftPreflightService,
    OwnerTruthTopicShiftPreflightStatus,
)


class OwnerTruthTopicShiftPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryOwnerTruthConversationRepository()
        self.conversation = OwnerTruthConversationService(self.repository)
        self.service = OwnerTruthTopicShiftPreflightService(
            conversation_service=self.conversation
        )
        self.context = OwnerTruthCommandContext(
            vault_id="topic-shift-preflight-vault",
            owner_subject_id="topic-shift-preflight-owner",
            actor_subject_id="topic-shift-preflight-owner",
            policy_version="owner-truth-v1",
        )
        self.thread_id = str(uuid4())
        self.session_id = str(uuid4())
        self.conversation.start_session(
            command=StartInterviewSessionCommand(
                command_id="topic-shift-preflight-start",
                thread_id=self.thread_id,
                session_id=self.session_id,
                expected_thread_version=0,
                entry_mode="naturalInput",
            ),
            context=self.context,
        )

    def test_explicit_topic_change_requires_switch_without_writing(self) -> None:
        text = "我们换个话题吧，聊聊我第一份工作的经历。"

        result = self.service.preflight(
            session_id=self.session_id,
            context=self.context,
            user_text=text,
        )

        self.assertEqual(
            result.status,
            OwnerTruthTopicShiftPreflightStatus.TOPIC_SWITCH_REQUIRED,
        )
        self.assertTrue(result.requires_topic_switch)
        self.assertEqual(result.reason_code, "explicitTopicSwitchRequired")
        rendered = str(result.value_free_summary())
        self.assertNotIn(text, rendered)
        self.assertNotIn(self.session_id, rendered)
        session = self.conversation.read_session(session_id=self.session_id, context=self.context)
        self.assertEqual(session.state.value, "active")
        self.assertEqual(session.row_version, 1)
        snapshot = self.repository.snapshot(vault_id=self.context.vault_id)
        self.assertEqual(snapshot["candidateCount"], 0)
        self.assertEqual(snapshot["memoryVersionCount"], 0)
        self.assertEqual(snapshot["authorityEffects"], ())

    def test_continuation_or_ambiguous_text_does_not_require_a_switch(self) -> None:
        for text in (
            "我继续讲刚才院子里听故事的经历。",
            "换个时间我再把这段故事讲完。",
        ):
            with self.subTest(text=text):
                result = self.service.preflight(
                    session_id=self.session_id,
                    context=self.context,
                    user_text=text,
                )
                self.assertEqual(
                    result.status,
                    OwnerTruthTopicShiftPreflightStatus.NOT_REQUIRED,
                )
                self.assertEqual(result.reason_code, "noExplicitTopicChangeCue")

    def test_paused_session_does_not_reopen_or_replace_existing_boundary(self) -> None:
        self.conversation.set_boundary(
            command=SetInterviewBoundaryCommand(
                command_id="topic-shift-preflight-cooldown",
                thread_id=self.thread_id,
                session_id=self.session_id,
                expected_session_version=1,
                boundary=InterviewBoundary.COOLDOWN,
            ),
            context=self.context,
        )

        result = self.service.preflight(
            session_id=self.session_id,
            context=self.context,
            user_text="先不聊这个了，我想说说外婆年轻时的故事。",
        )

        self.assertEqual(
            result.status,
            OwnerTruthTopicShiftPreflightStatus.NOT_REQUIRED,
        )
        self.assertEqual(result.reason_code, "sessionDoesNotRequireTopicSwitch")
        session = self.conversation.read_session(session_id=self.session_id, context=self.context)
        self.assertEqual(session.state.value, "paused")
        self.assertEqual(session.boundary, InterviewBoundary.COOLDOWN)
        self.assertEqual(session.row_version, 2)

    def test_cross_owner_cannot_probe_a_session(self) -> None:
        other_context = OwnerTruthCommandContext(
            vault_id=self.context.vault_id,
            owner_subject_id="topic-shift-preflight-other-owner",
            actor_subject_id="topic-shift-preflight-other-owner",
            policy_version="owner-truth-v1",
        )

        with self.assertRaises(OwnerTruthConversationAccessDenied):
            self.service.preflight(
                session_id=self.session_id,
                context=other_context,
                user_text="我们换个话题吧。",
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
