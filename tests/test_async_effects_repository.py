import hashlib
import unittest

from app.async_effects.contracts import AsyncEffectConflict, AsyncEffectIntent, AsyncEffectTarget
from app.async_effects.repository import InMemoryEffectKernelRepository, PostgresEffectKernelRepository


def payload_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class InMemoryEffectKernelRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.repository = InMemoryEffectKernelRepository()
        self.target = AsyncEffectTarget(
            owner_subject_id="owner-effect-1",
            vault_id="vault-effect-1",
            resource_type="echoReply",
            resource_id="reply-effect-1",
            resource_version=3,
            purpose="delayedDelivery",
            authority_epoch=2,
        )

    def intent(self, *, payload: str = "request-v1") -> AsyncEffectIntent:
        return AsyncEffectIntent(
            operation_type="echoReply.deliver",
            target=self.target,
            payload_hash=payload_hash(payload),
        )

    def test_same_intent_returns_original_coordination_ids_without_duplicates(self):
        created = self.repository.accept(self.intent())
        replayed = self.repository.accept(self.intent())

        self.assertEqual(created.outcome, "accepted")
        self.assertEqual(replayed.outcome, "deduplicated")
        self.assertEqual(created.operation_id, replayed.operation_id)
        self.assertEqual(created.outbox_event_id, replayed.outbox_event_id)
        self.assertEqual(created.job_id, replayed.job_id)
        self.assertEqual(created.business_receipt_id, replayed.business_receipt_id)
        self.assertEqual(self.repository.record_count(), 1)

    def test_same_stable_key_with_different_payload_is_a_conflict(self):
        self.repository.accept(self.intent(payload="request-v1"))

        with self.assertRaises(AsyncEffectConflict):
            self.repository.accept(self.intent(payload="request-v2"))

        self.assertEqual(self.repository.record_count(), 1)


class _FakeCursor:
    def __init__(self):
        self.calls = []
        self._next_row = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, statement, params=()):
        self.calls.append((" ".join(statement.split()), params))
        if "INSERT INTO async_effects.operations" in statement:
            self._next_row = {"operation_id": params[0]}
        else:
            self._next_row = None

    def fetchone(self):
        return self._next_row


class _FakeConnection:
    def __init__(self):
        self.cursor_instance = _FakeCursor()

    def cursor(self, **_kwargs):
        return self.cursor_instance


class PostgresEffectKernelRepositoryContractTests(unittest.TestCase):
    def test_initial_insert_uses_hash_only_coordination_rows(self):
        intent = AsyncEffectIntent(
            operation_type="timeLetter.delivery",
            target=AsyncEffectTarget(
                owner_subject_id="owner-effect-1",
                vault_id="vault-effect-1",
                resource_type="timeLetter",
                resource_id="letter-effect-1",
                resource_version=1,
                purpose="delivery",
                authority_epoch=0,
            ),
            payload_hash=payload_hash("metadata-only"),
        )
        connection = _FakeConnection()

        result = PostgresEffectKernelRepository(connection).accept(intent)

        self.assertEqual(result.outcome, "accepted")
        calls = connection.cursor_instance.calls
        self.assertEqual(len(calls), 4)
        self.assertIn("async_effects.outbox_events", calls[1][0])
        self.assertEqual(calls[1][1][-2:], (intent.event_type, intent.payload_hash))
        self.assertIn("async_effects.jobs", calls[2][0])
        self.assertEqual(calls[2][1][-2:], (intent.job_type, intent.payload_hash))
        self.assertIn("async_effects.business_receipts", calls[3][0])
        self.assertEqual(
            calls[3][1][-2:],
            (intent.business_target_key, intent.payload_hash),
        )

if __name__ == "__main__":
    unittest.main()
