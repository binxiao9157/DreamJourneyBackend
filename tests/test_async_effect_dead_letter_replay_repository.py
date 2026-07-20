from __future__ import annotations

from hashlib import sha256
import unittest

from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectJobState, AsyncEffectTarget
from app.async_effects.dead_letter_effects import (
    DeadLetterCause,
    DeadLetterReplayCommand,
    admit_dead_letter,
)
from app.async_effects.dead_letter_repository import (
    DeadLetterPersistenceError,
    InMemoryAsyncEffectDeadLetterRepository,
)
from app.async_effects.dead_letter_replay_repository import (
    DeadLetterReplayRequestConflict,
    DeadLetterReplayRequestError,
    InMemoryAsyncEffectDeadLetterReplayRequestRepository,
)
from app.async_effects.recovery_evidence import DeadLetterRestoreReplayContext


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _admission():
    intent = AsyncEffectIntent(
        operation_type="asyncEffect.synthetic.deadLetterReplayRequest",
        target=AsyncEffectTarget(
            owner_subject_id="owner-dead-letter-replay-001",
            vault_id="vault-dead-letter-replay-001",
            resource_type="timeLetter",
            resource_id="letter-dead-letter-replay-001",
            resource_version=5,
            purpose="timeLetterDelivery",
            authority_epoch=2,
        ),
        payload_hash=_hash("dead-letter-replay-payload"),
    )
    return admit_dead_letter(
        intent=intent,
        job_state=AsyncEffectJobState.FAILED,
        attempt=1,
        max_attempts=1,
        cause=DeadLetterCause.MAX_ATTEMPTS_EXCEEDED,
        failure_hash=_hash("dead-letter-replay-failure"),
        last_receipt_hash=_hash("dead-letter-replay-last-receipt"),
    )


def _command(admission, *, authorization: str = "operator-replay-authorization"):
    return DeadLetterReplayCommand(
        dead_letter_id=admission.dead_letter_id,
        actor_subject_id="owner-dead-letter-replay-001",
        owner_subject_id="owner-dead-letter-replay-001",
        vault_id="vault-dead-letter-replay-001",
        authority_epoch=2,
        authorization_receipt_hash=_hash(authorization),
        reason_code="operatorApproved",
    )


def _restore_context(*, restore_id: str = "restore-dead-letter-replay-001"):
    return DeadLetterRestoreReplayContext(
        restore_id=restore_id,
        owner_subject_id="owner-dead-letter-replay-001",
        vault_id="vault-dead-letter-replay-001",
        authority_epoch=2,
        restore_checkpoint_hash=_hash("restore-dead-letter-replay-checkpoint"),
        recovery_authorization_receipt_hash=_hash("recovery-replay-authorization"),
    )


class AsyncEffectDeadLetterReplayRequestRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.dead_letters = InMemoryAsyncEffectDeadLetterRepository()
        self.admission = _admission()
        self.dead_letters.record(self.admission)
        self.repository = InMemoryAsyncEffectDeadLetterReplayRequestRepository(self.dead_letters)

    def test_authorized_restore_fenced_request_is_deduplicated_and_loadable(self):
        command = _command(self.admission)
        restore_context = _restore_context()

        first = self.repository.record(self.admission, command, restore_context)
        duplicate = self.repository.record(self.admission, command, restore_context)
        summary = first.value_free_summary()

        self.assertEqual(first.outcome, "authorized")
        self.assertEqual(duplicate.outcome, "deduplicated")
        self.assertEqual(first.request, duplicate.request)
        self.assertEqual(self.repository.load(first.request.replay_id), first.request)
        self.assertEqual(self.repository.record_count(), 1)
        self.assertEqual(first.request.next_attempt, 2)
        self.assertNotIn("owner-dead-letter-replay", str(summary))
        self.assertNotIn("authorization", str(summary).lower())

    def test_changed_restore_or_authorization_cannot_reuse_the_dead_letter(self):
        self.repository.record(self.admission, _command(self.admission), _restore_context())

        with self.assertRaises(DeadLetterReplayRequestConflict):
            self.repository.record(
                self.admission,
                _command(self.admission, authorization="later-operator-authorization"),
                _restore_context(),
            )
        with self.assertRaises(DeadLetterReplayRequestConflict):
            self.repository.record(
                self.admission,
                _command(self.admission),
                _restore_context(restore_id="restore-dead-letter-replay-002"),
            )
        self.assertEqual(self.repository.record_count(), 1)

    def test_requires_durable_open_admission_and_fresh_restore_authority(self):
        missing_dead_letter = InMemoryAsyncEffectDeadLetterReplayRequestRepository(
            InMemoryAsyncEffectDeadLetterRepository()
        )
        with self.assertRaises(DeadLetterPersistenceError):
            missing_dead_letter.record(self.admission, _command(self.admission), _restore_context())

        reused_authority = DeadLetterRestoreReplayContext(
            restore_id="restore-dead-letter-replay-001",
            owner_subject_id="owner-dead-letter-replay-001",
            vault_id="vault-dead-letter-replay-001",
            authority_epoch=2,
            restore_checkpoint_hash=_hash("restore-dead-letter-replay-checkpoint"),
            recovery_authorization_receipt_hash=_hash("operator-replay-authorization"),
        )
        with self.assertRaises(DeadLetterReplayRequestError):
            self.repository.record(self.admission, _command(self.admission), reused_authority)

        self.assertEqual(self.repository.record_count(), 0)


if __name__ == "__main__":
    unittest.main()
