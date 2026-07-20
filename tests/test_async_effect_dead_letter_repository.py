from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import unittest

from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectJobState, AsyncEffectTarget
from app.async_effects.dead_letter_effects import DeadLetterCause, DeadLetterState, admit_dead_letter
from app.async_effects.dead_letter_repository import (
    DeadLetterPersistenceConflict,
    DeadLetterPersistenceError,
    InMemoryAsyncEffectDeadLetterRepository,
)


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _admission():
    intent = AsyncEffectIntent(
        operation_type="asyncEffect.synthetic.deadLetterPersistence",
        target=AsyncEffectTarget(
            owner_subject_id="owner-dead-letter-store-001",
            vault_id="vault-dead-letter-store-001",
            resource_type="timeLetter",
            resource_id="letter-dead-letter-store-001",
            resource_version=2,
            purpose="timeLetterDelivery",
            authority_epoch=1,
        ),
        payload_hash=_hash("dead-letter-store-payload"),
    )
    return admit_dead_letter(
        intent=intent,
        job_state=AsyncEffectJobState.BLOCKED,
        attempt=1,
        max_attempts=1,
        cause=DeadLetterCause.POISON_PAYLOAD,
        failure_hash=_hash("dead-letter-store-failure"),
        last_receipt_hash=_hash("dead-letter-store-last-receipt"),
    )


class AsyncEffectDeadLetterRepositoryTests(unittest.TestCase):
    def test_same_open_admission_is_deduplicated_and_loadable(self):
        repository = InMemoryAsyncEffectDeadLetterRepository()
        admission = _admission()

        first = repository.record(admission)
        replay = repository.record(admission)

        self.assertEqual(first.outcome, "admitted")
        self.assertEqual(replay.outcome, "deduplicated")
        self.assertEqual(repository.load(admission.dead_letter_id), admission)
        self.assertEqual(repository.record_count(), 1)
        self.assertNotIn("owner-dead-letter-store", str(first.value_free_summary()))
        self.assertNotIn("last-receipt", str(first.value_free_summary()))

    def test_same_job_attempt_cannot_change_failure_evidence(self):
        repository = InMemoryAsyncEffectDeadLetterRepository()
        admission = _admission()
        repository.record(admission)

        with self.assertRaises(DeadLetterPersistenceConflict):
            repository.record(replace(admission, failure_hash=_hash("changed-failure")))

        self.assertEqual(repository.record_count(), 1)

    def test_terminal_state_cannot_be_admitted_as_a_new_record(self):
        repository = InMemoryAsyncEffectDeadLetterRepository()

        with self.assertRaises(DeadLetterPersistenceError):
            repository.record(replace(_admission(), state=DeadLetterState.RECONCILED))


if __name__ == "__main__":
    unittest.main()
