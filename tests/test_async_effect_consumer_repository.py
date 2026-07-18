from __future__ import annotations

from hashlib import sha256
import unittest

from app.async_effects.consumer_repository import (
    AsyncEffectConsumerAdmissionDenied,
    AsyncEffectConsumerIncomplete,
    AsyncEffectSyntheticConsumerCommand,
    InMemoryAsyncEffectConsumerRepository,
)
from app.async_effects.contracts import AsyncEffectConflict, AsyncEffectIntent, AsyncEffectTarget


def digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class AsyncEffectConsumerRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryAsyncEffectConsumerRepository()
        self.intent = AsyncEffectIntent(
            operation_type="asyncEffect.synthetic.consumer",
            target=AsyncEffectTarget(
                owner_subject_id="owner-consumer-test",
                vault_id="vault-consumer-test",
                resource_type="syntheticEffect",
                resource_id="consumer-test-1",
                resource_version=1,
                purpose="consumerFoundation",
                authority_epoch=0,
            ),
            payload_hash=digest("consumer-metadata-only"),
        )

    def command(self, *, target: str = "target-1", outcome: str = "completed"):
        return AsyncEffectSyntheticConsumerCommand(
            intent=self.intent,
            consumer_name="synthetic.consumer",
            business_target_key=digest(target),
            outcome=outcome,
            reason_code="syntheticCompleted" if outcome == "completed" else "syntheticSkipped",
            result_ref_hash=digest(f"result:{target}:{outcome}"),
        )

    def test_same_consumer_event_returns_the_original_completion_receipt(self):
        created = self.repository.consume(self.command())
        replayed = self.repository.consume(self.command())

        self.assertEqual(created.outcome, "accepted")
        self.assertEqual(replayed.outcome, "deduplicated")
        self.assertEqual(created.inbox_id, replayed.inbox_id)
        self.assertEqual(created.business_receipt_id, replayed.business_receipt_id)
        self.assertEqual(created.inbox_state, "completed")

    def test_same_consumer_event_cannot_complete_a_different_business_target(self):
        self.repository.consume(self.command(target="target-1"))

        with self.assertRaises(AsyncEffectConflict):
            self.repository.consume(self.command(target="target-2"))

    def test_changed_completion_meaning_is_rejected(self):
        self.repository.consume(self.command(outcome="completed"))

        with self.assertRaises(AsyncEffectConflict):
            self.repository.consume(self.command(outcome="skipped"))

    def test_non_synthetic_operation_is_rejected_before_any_consumer_write(self):
        real_intent = AsyncEffectIntent(
            operation_type="timeLetter.delivery",
            target=self.intent.target,
            payload_hash=digest("real-operation-metadata"),
        )

        with self.assertRaises(AsyncEffectConsumerAdmissionDenied):
            AsyncEffectSyntheticConsumerCommand(
                intent=real_intent,
                consumer_name="synthetic.consumer",
                business_target_key=digest("target"),
                outcome="completed",
                reason_code="syntheticCompleted",
                result_ref_hash=digest("result"),
            )

    def test_incomplete_inbox_fails_closed_instead_of_replaying_without_receipt(self):
        command = self.command()
        self.repository._inbox[(command.consumer_name, command.intent.outbox_event_id)] = {
            "inboxId": command.inbox_id,
            "operationId": command.intent.operation_id,
            "eventId": command.intent.outbox_event_id,
            "payloadHash": command.intent.payload_hash,
            "consumerName": command.consumer_name,
            "state": "processing",
        }

        with self.assertRaises(AsyncEffectConsumerIncomplete):
            self.repository.consume(command)


if __name__ == "__main__":
    unittest.main()
