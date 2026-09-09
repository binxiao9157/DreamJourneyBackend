from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.in_memory_store import InMemoryStore
from app.services.owner_truth_echo_conversation_context import (
    OWNER_TRUTH_ECHO_CONVERSATION_MAX_TOTAL_CHARS,
    OWNER_TRUTH_ECHO_CONVERSATION_MAX_TURNS,
    OwnerTruthEchoConversationContextAccessDenied,
    OwnerTruthEchoConversationContextService,
)


class OwnerTruthEchoConversationContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryStore()
        self.context = OwnerTruthCommandContext(
            vault_id="echo-context-vault",
            owner_subject_id="echo-context-owner",
            actor_subject_id="echo-context-owner",
        )
        self.service = OwnerTruthEchoConversationContextService(self.store)

    def test_product_session_context_replays_completed_turns_and_deduplicates_request(self) -> None:
        initial = self.service.read_recent(
            context=self.context,
            product_session_id="text-session-a",
        )
        self.assertEqual(initial.state, "empty")
        recorded = self.service.record_answered_exchange(
            context=self.context,
            product_session_id="text-session-a",
            request_id="request-a",
            user_text="我在 A 大学读书。",
            assistant_text="我记住了这句话在本场对话里的语境。",
        )
        self.assertEqual(recorded.state, "ready")
        self.assertEqual(
            recorded.prompt_turns,
            [
                {"role": "user", "text": "我在 A 大学读书。"},
                {"role": "assistant", "text": "我记住了这句话在本场对话里的语境。"},
            ],
        )

        replayed = self.service.record_answered_exchange(
            context=self.context,
            product_session_id="text-session-a",
            request_id="request-a",
            user_text="不应重复的用户问题",
            assistant_text="不应重复的回答",
        )
        self.assertEqual(replayed.state, "deduplicated")
        self.assertEqual(replayed.turns, recorded.turns)
        self.assertEqual(replayed.revision, recorded.revision)

    def test_context_is_bounded_and_isolated_by_product_session_and_owner(self) -> None:
        for index in range(5):
            self.service.record_answered_exchange(
                context=self.context,
                product_session_id="text-session-a",
                request_id=f"request-{index}",
                user_text=f"用户问题 {index} " + "字" * 700,
                assistant_text=f"助手回答 {index} " + "字" * 700,
            )
        bounded = self.service.read_recent(
            context=self.context,
            product_session_id="text-session-a",
        )
        self.assertLessEqual(len(bounded.turns), OWNER_TRUTH_ECHO_CONVERSATION_MAX_TURNS)
        self.assertLessEqual(
            sum(len(turn.text) for turn in bounded.turns),
            OWNER_TRUTH_ECHO_CONVERSATION_MAX_TOTAL_CHARS,
        )
        self.assertEqual(
            self.service.read_recent(
                context=self.context,
                product_session_id="text-session-b",
            ).turns,
            (),
        )
        denied = OwnerTruthCommandContext(
            vault_id=self.context.vault_id,
            owner_subject_id=self.context.owner_subject_id,
            actor_subject_id="another-owner",
        )
        with self.assertRaises(OwnerTruthEchoConversationContextAccessDenied):
            self.service.read_recent(
                context=denied,
                product_session_id="text-session-a",
            )

    def test_expired_context_is_not_reused_as_live_conversation_history(self) -> None:
        self.service.record_answered_exchange(
            context=self.context,
            product_session_id="text-session-a",
            request_id="request-expiring",
            user_text="昨天的上下文",
            assistant_text="昨天的回答",
        )
        repository = self.store.owner_truth_echo_conversation_context_repository()
        repository._records[(self.context.vault_id, self.context.owner_subject_id, "text-session-a")][
            "expiresAt"
        ] = datetime.now(timezone.utc) - timedelta(seconds=1)

        expired = self.service.read_recent(
            context=self.context,
            product_session_id="text-session-a",
        )
        self.assertEqual(expired.state, "expired")
        self.assertEqual(expired.turns, ())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
