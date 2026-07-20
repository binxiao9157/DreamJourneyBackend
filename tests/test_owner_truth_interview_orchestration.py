from __future__ import annotations

import unittest
from uuid import uuid4

from app.domain.owner_truth.interview_orchestration import (
    InterviewAction,
    InterviewBoundary,
    InterviewFatigue,
    InterviewOrchestrationInput,
    InterviewOrchestrator,
    InterviewSessionState,
)


def make_input(**overrides: object) -> InterviewOrchestrationInput:
    values: dict[str, object] = {
        "authority_epoch": 3,
        "candidate_batch_turn_count": 1,
        "deepening_turn_count": 0,
        "is_sensitive": False,
        "needs_clarification": False,
        "owner_subject_id": "owner-interview",
        "session_state": InterviewSessionState.ACTIVE,
        "thread_id": str(uuid4()),
        "topic_id": "topic-startup-story",
        "topic_incomplete": True,
        "user_boundary": InterviewBoundary.NONE,
        "user_changed_topic": False,
        "vault_id": "vault-interview",
    }
    values.update(overrides)
    return InterviewOrchestrationInput(**values)


class OwnerTruthInterviewOrchestrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.orchestrator = InterviewOrchestrator()

    def test_incomplete_safe_story_can_deepen_with_a_bounded_follow_up(self) -> None:
        decision = self.orchestrator.decide(make_input())

        self.assertEqual(decision.action, InterviewAction.DEEPEN)
        self.assertEqual(decision.reason_code, "highValueIncompleteStory")
        self.assertEqual(decision.max_followups_remaining, 4)
        self.assertFalse(decision.review_batch_due)
        self.assertEqual(decision.next_session_state, InterviewSessionState.ACTIVE)

    def test_three_safe_deepening_turns_continue_but_fourth_requires_summary(self) -> None:
        at_three = self.orchestrator.decide(make_input(deepening_turn_count=3))
        at_four = self.orchestrator.decide(make_input(deepening_turn_count=4))

        self.assertEqual(at_three.action, InterviewAction.DEEPEN)
        self.assertEqual(at_three.max_followups_remaining, 1)
        self.assertEqual(at_four.action, InterviewAction.SUMMARIZE)
        self.assertEqual(at_four.reason_code, "followupBudgetReached")
        self.assertEqual(at_four.max_followups_remaining, 0)

    def test_clarification_has_priority_over_deepening_before_the_follow_up_budget_is_exhausted(self) -> None:
        decision = self.orchestrator.decide(
            make_input(needs_clarification=True, deepening_turn_count=1)
        )

        self.assertEqual(decision.action, InterviewAction.CLARIFY)
        self.assertEqual(decision.reason_code, "materialAmbiguity")

    def test_user_boundaries_and_sensitive_content_fail_closed_to_pause(self) -> None:
        do_not_ask = self.orchestrator.decide(
            make_input(user_boundary=InterviewBoundary.DO_NOT_ASK)
        )
        sensitive = self.orchestrator.decide(make_input(is_sensitive=True))
        cooldown = self.orchestrator.decide(
            make_input(user_boundary=InterviewBoundary.COOLDOWN)
        )

        self.assertEqual(do_not_ask.action, InterviewAction.PAUSE)
        self.assertEqual(do_not_ask.reason_code, "userDoNotAsk")
        self.assertEqual(sensitive.action, InterviewAction.PAUSE)
        self.assertEqual(sensitive.reason_code, "sensitiveOrUnsafe")
        self.assertEqual(cooldown.action, InterviewAction.PAUSE)
        self.assertEqual(cooldown.reason_code, "userCooldown")
        self.assertEqual(do_not_ask.next_session_state, InterviewSessionState.PAUSED)

    def test_skip_once_listens_without_a_new_main_question_and_consumes_only_the_current_boundary(self) -> None:
        decision = self.orchestrator.decide(
            make_input(user_boundary=InterviewBoundary.SKIP_ONCE)
        )

        self.assertEqual(decision.action, InterviewAction.LISTEN)
        self.assertEqual(decision.reason_code, "userSkippedCurrentOpportunity")
        self.assertTrue(decision.consumes_one_shot_boundary)
        self.assertEqual(decision.next_session_state, InterviewSessionState.ACTIVE)

    def test_topic_switch_pauses_the_old_thread_without_creating_a_new_thread_or_candidate(self) -> None:
        decision = self.orchestrator.decide(make_input(user_changed_topic=True))

        self.assertEqual(decision.action, InterviewAction.PAUSE)
        self.assertEqual(decision.reason_code, "topicChanged")
        self.assertFalse(decision.review_batch_due)
        self.assertEqual(decision.next_session_state, InterviewSessionState.PAUSED)

    def test_candidate_batch_is_only_a_review_hint_after_five_turns_or_session_exit(self) -> None:
        below_threshold = self.orchestrator.decide(
            make_input(candidate_batch_turn_count=4, topic_incomplete=False)
        )
        at_threshold = self.orchestrator.decide(
            make_input(candidate_batch_turn_count=5, topic_incomplete=False)
        )
        on_exit = self.orchestrator.decide(
            make_input(candidate_batch_turn_count=1, session_state=InterviewSessionState.ENDING)
        )

        self.assertFalse(below_threshold.review_batch_due)
        self.assertTrue(at_threshold.review_batch_due)
        self.assertTrue(on_exit.review_batch_due)
        self.assertEqual(at_threshold.action, InterviewAction.LISTEN)
        self.assertEqual(on_exit.action, InterviewAction.PAUSE)

    def test_guarded_fatigue_prefers_summary_after_the_minimum_safe_depth(self) -> None:
        decision = self.orchestrator.decide(
            make_input(
                deepening_turn_count=2,
                fatigue=InterviewFatigue.GUARDED,
                topic_incomplete=True,
            )
        )

        self.assertEqual(decision.action, InterviewAction.SUMMARIZE)
        self.assertEqual(decision.reason_code, "fatiguePrefersSummary")

    def test_value_free_summary_never_returns_owner_or_thread_identifiers(self) -> None:
        state = make_input()
        summary = self.orchestrator.decide(state).value_free_summary()

        rendered = str(summary)
        self.assertNotIn(state.thread_id, rendered)
        self.assertNotIn(state.vault_id, rendered)
        self.assertNotIn(state.owner_subject_id, rendered)
        self.assertNotIn(state.topic_id, rendered)

    def test_invalid_or_terminal_session_cannot_emit_an_interview_question(self) -> None:
        ended = self.orchestrator.decide(
            make_input(session_state=InterviewSessionState.ENDED)
        )
        malformed_epoch = self.orchestrator.decide(
            make_input(authority_epoch=0, session_state=InterviewSessionState.INVALID)
        )

        self.assertEqual(ended.action, InterviewAction.PAUSE)
        self.assertEqual(ended.reason_code, "sessionNotActive")
        self.assertEqual(malformed_epoch.action, InterviewAction.PAUSE)
        self.assertEqual(malformed_epoch.reason_code, "sessionNotActive")


if __name__ == "__main__":
    unittest.main()
