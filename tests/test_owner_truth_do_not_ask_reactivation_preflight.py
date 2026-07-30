from __future__ import annotations

import unittest
from uuid import uuid4

from app.domain.owner_truth.conversation import (
    InterviewBoundary,
    SetInterviewBoundaryCommand,
    StartInterviewSessionCommand,
)
from app.domain.owner_truth.do_not_ask_reactivation_detection import (
    DoNotAskReactivationDetector,
    DoNotAskReactivationReasonCode,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_conversation import (
    InMemoryOwnerTruthConversationRepository,
    OwnerTruthConversationAccessDenied,
    OwnerTruthConversationService,
)
from app.services.owner_truth_do_not_ask_reactivation_preflight import (
    OwnerTruthDoNotAskReactivationPreflightService,
    OwnerTruthDoNotAskReactivationPreflightStatus,
)


class OwnerTruthDoNotAskReactivationPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryOwnerTruthConversationRepository()
        self.conversation = OwnerTruthConversationService(self.repository)
        self.service = OwnerTruthDoNotAskReactivationPreflightService(
            conversation_service=self.conversation
        )
        self.context = OwnerTruthCommandContext(
            vault_id="reactivation-vault",
            owner_subject_id="reactivation-owner",
            actor_subject_id="reactivation-owner",
            policy_version="owner-truth-v1",
        )
        self.thread_id = str(uuid4())
        self.session_id = str(uuid4())
        self.conversation.start_session(
            command=StartInterviewSessionCommand(
                command_id="reactivation-start",
                thread_id=self.thread_id,
                session_id=self.session_id,
                expected_thread_version=0,
                entry_mode="naturalInput",
            ),
            context=self.context,
        )

    def _pause_do_not_ask(self) -> None:
        self.conversation.set_boundary(
            command=SetInterviewBoundaryCommand(
                command_id="reactivation-do-not-ask",
                thread_id=self.thread_id,
                session_id=self.session_id,
                expected_session_version=1,
                boundary=InterviewBoundary.DO_NOT_ASK,
            ),
            context=self.context,
        )

    def test_explicit_reactivation_requires_confirmation_without_writing_or_restoring(self) -> None:
        self._pause_do_not_ask()
        text = "我愿意重新聊这个话题。"

        result = self.service.preflight(
            session_id=self.session_id,
            context=self.context,
            user_text=text,
        )

        self.assertEqual(
            result.status,
            OwnerTruthDoNotAskReactivationPreflightStatus.CONFIRMATION_REQUIRED,
        )
        self.assertTrue(result.requires_confirmation)
        self.assertEqual(result.reason_code, "doNotAskRestoreConfirmationRequired")
        rendered = str(result.value_free_summary())
        self.assertNotIn(text, rendered)
        self.assertNotIn(self.session_id, rendered)
        session = self.conversation.read_session(session_id=self.session_id, context=self.context)
        self.assertEqual(session.state.value, "paused")
        self.assertEqual(session.boundary, InterviewBoundary.DO_NOT_ASK)
        self.assertEqual(session.row_version, 2)
        snapshot = self.repository.snapshot(vault_id=self.context.vault_id)
        self.assertEqual(snapshot["candidateCount"], 0)
        self.assertEqual(snapshot["memoryVersionCount"], 0)
        self.assertEqual(snapshot["authorityEffects"], ())

    def test_ambiguous_or_negative_text_does_not_bypass_or_prompt_for_restore(self) -> None:
        self._pause_do_not_ask()
        for text in (
            "我不想重新聊这个话题。",
            "我记得这件事，不过暂时不展开。",
            "换个时间再说吧。",
        ):
            with self.subTest(text=text):
                result = self.service.preflight(
                    session_id=self.session_id,
                    context=self.context,
                    user_text=text,
                )
                self.assertEqual(
                    result.status,
                    OwnerTruthDoNotAskReactivationPreflightStatus.NOT_REQUIRED,
                )
                self.assertEqual(result.reason_code, "noExplicitDoNotAskReactivationCue")

    def test_explicit_reactivation_does_not_prompt_when_the_session_is_not_do_not_ask(self) -> None:
        result = self.service.preflight(
            session_id=self.session_id,
            context=self.context,
            user_text="你可以继续问我这个话题。",
        )

        self.assertEqual(
            result.status,
            OwnerTruthDoNotAskReactivationPreflightStatus.NOT_REQUIRED,
        )
        self.assertEqual(result.reason_code, "sessionDoesNotRequireDoNotAskRestore")

    def test_cross_owner_cannot_probe_a_paused_session(self) -> None:
        self._pause_do_not_ask()
        other_context = OwnerTruthCommandContext(
            vault_id=self.context.vault_id,
            owner_subject_id="reactivation-other-owner",
            actor_subject_id="reactivation-other-owner",
            policy_version="owner-truth-v1",
        )

        with self.assertRaises(OwnerTruthConversationAccessDenied):
            self.service.preflight(
                session_id=self.session_id,
                context=other_context,
                user_text="我愿意重新聊这个话题。",
            )

    def test_detector_requires_explicit_positive_consent(self) -> None:
        detector = DoNotAskReactivationDetector()
        positive = detector.detect("我愿意重新聊这个话题。")
        negative = detector.detect("我不想重新聊这个话题。")

        self.assertTrue(positive.user_reopened_do_not_ask_topic)
        self.assertEqual(
            positive.reason_code,
            DoNotAskReactivationReasonCode.EXPLICIT_REACTIVATION_CUE,
        )
        self.assertFalse(negative.user_reopened_do_not_ask_topic)
        self.assertEqual(
            negative.reason_code,
            DoNotAskReactivationReasonCode.NO_EXPLICIT_REACTIVATION_CUE,
        )


if __name__ == "__main__":
    unittest.main()
