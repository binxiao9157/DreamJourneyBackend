import unittest
from hashlib import sha256

from app.async_effects.consumer_repository import InMemoryAsyncEffectConsumerRepository
from app.async_effects.repository import InMemoryEffectKernelRepository
from app.services.echo_delayed_reply_effects import (
    ECHO_DELAYED_REPLY_SCHEMA_VERSION,
    EchoDelayedReplyCompletion,
    EchoDelayedReplyContractError,
    EchoDelayedReplyGeneratedAnswer,
    build_echo_delayed_reply_plan,
)
from app.services.in_memory_store import InMemoryStore


def _reply(**overrides):
    item = {
        "id": "delayed-echo-001",
        "ownerSubjectId": "owner-001",
        "vaultId": "vault-001",
        "conversationId": "conversation-001",
        "requestId": "request-001",
        "replyGeneration": 3,
        "contextHash": sha256(b"echo-context-v4").hexdigest(),
        "contextVersion": "echo-context-v4",
        "policyVersion": "echo-policy-v4",
        "authorityEpoch": 9,
        "rowVersion": 4,
        "deliverAt": "2026-07-20T09:00:00Z",
        "contextExpiresAt": "2026-07-20T10:00:00Z",
        "authorityState": "active",
        "deliveryState": "scheduled",
        "deliveryProtocolVersion": ECHO_DELAYED_REPLY_SCHEMA_VERSION,
        "rawTranscript": "This must never enter a V4 effect summary.",
    }
    item.update(overrides)
    return item


def _answer():
    return EchoDelayedReplyGeneratedAnswer(
        answer_text="This private answer must never enter the generic receipt.",
        citation_receipt_hash=sha256(b"citation-receipt").hexdigest(),
        provider_result_hash=sha256(b"provider-result").hexdigest(),
    )


class EchoDelayedReplyEffectsTests(unittest.TestCase):
    def test_due_plan_has_one_stable_owner_target_and_value_free_summary(self):
        plan = build_echo_delayed_reply_plan(
            _reply(),
            now_iso="2026-07-20T09:00:01Z",
        )

        self.assertTrue(plan.due)
        self.assertEqual(len(plan.effect_intents), 1)
        intent = plan.effect_intents[0]
        self.assertEqual(intent.target.resource_type, "echoDelayedReply")
        self.assertEqual(intent.target.resource_version, 3)
        self.assertEqual(intent.target.purpose, "echoDelayedReply")
        self.assertEqual(intent.target.resource_id, plan.snapshot.stable_target_key)
        summary = str(plan.value_free_summary())
        self.assertNotIn("rawTranscript", summary)
        self.assertNotIn("This must never", summary)
        self.assertEqual(plan.value_free_summary()["schemaVersion"], ECHO_DELAYED_REPLY_SCHEMA_VERSION)

    def test_stable_target_uses_vault_conversation_request_and_context_not_visible_timing(self):
        first = build_echo_delayed_reply_plan(_reply(), now_iso="2026-07-20T09:00:01Z")
        retry = build_echo_delayed_reply_plan(
            _reply(deliverAt="2026-07-20T09:02:00Z"),
            now_iso="2026-07-20T09:02:01Z",
        )

        self.assertEqual(first.snapshot.stable_target_key, retry.snapshot.stable_target_key)
        self.assertEqual(first.effect_intents[0].stable_key, retry.effect_intents[0].stable_key)

    def test_not_due_plan_emits_no_effect(self):
        plan = build_echo_delayed_reply_plan(
            _reply(deliverAt="2026-07-20T09:01:00Z"),
            now_iso="2026-07-20T09:00:59Z",
        )

        self.assertFalse(plan.due)
        self.assertEqual(plan.effect_intents, ())

    def test_completed_completion_has_one_deduplicated_business_receipt(self):
        plan = build_echo_delayed_reply_plan(_reply(), now_iso="2026-07-20T09:00:01Z")
        completion = EchoDelayedReplyCompletion(
            snapshot=plan.snapshot,
            outcome="completed",
            reason_code="answerInboxPersisted",
            answer=_answer(),
        )
        kernel = InMemoryEffectKernelRepository()
        consumer = InMemoryAsyncEffectConsumerRepository()
        kernel.accept(plan.effect_intents[0])

        first = consumer.consume(completion.consumer_command)
        replayed = consumer.consume(completion.consumer_command)

        self.assertEqual(first.outcome, "accepted")
        self.assertEqual(replayed.outcome, "deduplicated")
        self.assertEqual(first.business_outcome, "completed")
        self.assertEqual(completion.answer_id, "echo-delayed-v1-" + completion.answer_id.removeprefix("echo-delayed-v1-"))
        summary = str(completion.value_free_summary())
        self.assertNotIn(_answer().answer_text, summary)
        self.assertNotIn("citation-receipt", summary)

    def test_completed_requires_answer_and_terminal_failures_cannot_carry_one(self):
        plan = build_echo_delayed_reply_plan(_reply(), now_iso="2026-07-20T09:00:01Z")

        with self.assertRaisesRegex(EchoDelayedReplyContractError, "requires an Answer"):
            EchoDelayedReplyCompletion(
                snapshot=plan.snapshot,
                outcome="completed",
                reason_code="answerInboxPersisted",
            )
        with self.assertRaisesRegex(EchoDelayedReplyContractError, "cannot retain an Answer"):
            EchoDelayedReplyCompletion(
                snapshot=plan.snapshot,
                outcome="failed",
                reason_code="providerFailed",
                answer=_answer(),
            )

    def test_missing_v4_evidence_fails_closed(self):
        for item in (
            _reply(deliveryProtocolVersion=None),
            _reply(contextHash=None),
            _reply(conversationId=None),
            _reply(requestId=None),
            _reply(replyGeneration=0),
            _reply(rowVersion=0),
        ):
            with self.subTest(item=item):
                with self.assertRaises(EchoDelayedReplyContractError):
                    build_echo_delayed_reply_plan(item, now_iso="2026-07-20T09:00:01Z")

    def test_legacy_dispatcher_skips_v4_reply_envelopes(self):
        store = InMemoryStore()
        store.add_echo_delayed_reply(
            "owner-001",
            _reply(userId="owner-001", deliverAt="2026-07-20T08:00:00Z"),
        )

        dispatched = store.mark_due_echo_delayed_replies_for_dispatch(
            cutoff_iso="2026-07-20T09:00:01Z",
            dispatched_at_iso="2026-07-20T09:00:01Z",
        )

        self.assertEqual(dispatched, [])
        self.assertEqual(store.list_echo_delayed_replies("owner-001")[0]["deliveryState"], "scheduled")


if __name__ == "__main__":
    unittest.main()
