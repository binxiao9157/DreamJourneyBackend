from __future__ import annotations

from hashlib import sha256
import unittest

from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectJobState, AsyncEffectTarget
from app.async_effects.dead_letter_effects import (
    DeadLetterCause,
    DeadLetterContractError,
    DeadLetterReplayCommand,
    DeadLetterReplayReason,
    admit_dead_letter,
    authorize_dead_letter_replay,
)


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _intent() -> AsyncEffectIntent:
    return AsyncEffectIntent(
        operation_type="asyncEffect.synthetic.deadLetter",
        target=AsyncEffectTarget(
            owner_subject_id="owner-dead-letter-001",
            vault_id="vault-dead-letter-001",
            resource_type="timeLetter",
            resource_id="letter-dead-letter-001",
            resource_version=4,
            purpose="timeLetterDelivery",
            authority_epoch=3,
        ),
        payload_hash=_hash("dead-letter-fixture-payload"),
    )


def _admission(*, cause: DeadLetterCause = DeadLetterCause.MAX_ATTEMPTS_EXCEEDED):
    job_state = (
        AsyncEffectJobState.UNKNOWN
        if cause is DeadLetterCause.PROVIDER_UNKNOWN
        else AsyncEffectJobState.FAILED
    )
    return admit_dead_letter(
        intent=_intent(),
        job_state=job_state,
        attempt=3,
        max_attempts=3,
        cause=cause,
        failure_hash=_hash("provider-failure"),
        last_receipt_hash=_hash("last-receipt"),
    )


class AsyncEffectDeadLetterContractTests(unittest.TestCase):
    def test_terminal_failures_admit_value_free_dead_letters_without_payload_body(self):
        admission = _admission()
        summary = admission.value_free_summary()

        self.assertEqual(admission.cause, DeadLetterCause.MAX_ATTEMPTS_EXCEEDED)
        self.assertEqual(admission.attempt, 3)
        self.assertEqual(admission.stable_key, _intent().stable_key)
        self.assertEqual(summary["nextAction"], "authorizedReplayRequired")
        self.assertNotIn("payload", str(summary).lower())
        self.assertNotIn("provider-failure", str(summary))

        poison = admit_dead_letter(
            intent=_intent(),
            job_state=AsyncEffectJobState.BLOCKED,
            attempt=1,
            max_attempts=3,
            cause=DeadLetterCause.POISON_PAYLOAD,
            failure_hash=_hash("invalid-payload"),
            last_receipt_hash=_hash("blocked-receipt"),
        )
        self.assertEqual(poison.value_free_summary()["nextAction"], "payloadCorrectionRequired")

        with self.assertRaises(DeadLetterContractError):
            admit_dead_letter(
                intent=_intent(),
                job_state=AsyncEffectJobState.SUCCEEDED,
                attempt=1,
                max_attempts=3,
                cause=DeadLetterCause.POISON_PAYLOAD,
                failure_hash=_hash("should-not-admit-success"),
                last_receipt_hash=_hash("success-receipt"),
            )

    def test_owner_authorized_replay_preserves_stable_key_and_next_attempt(self):
        admission = _admission()
        command = DeadLetterReplayCommand(
            dead_letter_id=admission.dead_letter_id,
            actor_subject_id="owner-dead-letter-001",
            owner_subject_id="owner-dead-letter-001",
            vault_id="vault-dead-letter-001",
            authority_epoch=3,
            authorization_receipt_hash=_hash("owner-replay-approved"),
            reason_code="operatorApproved",
        )

        first = authorize_dead_letter_replay(admission, command)
        replay = authorize_dead_letter_replay(admission, command)

        self.assertTrue(first.authorized)
        self.assertEqual(first.reason, DeadLetterReplayReason.AUTHORIZED)
        self.assertEqual(first.stable_key, admission.stable_key)
        self.assertEqual(first.next_attempt, 4)
        self.assertEqual(first.replay_id, replay.replay_id)
        self.assertNotIn("authorization", str(first.value_free_summary()).lower())

    def test_replay_rejects_wrong_owner_and_unknown_or_poison_without_reconciliation(self):
        admission = _admission()
        wrong_owner = DeadLetterReplayCommand(
            dead_letter_id=admission.dead_letter_id,
            actor_subject_id="other-owner-001",
            owner_subject_id="owner-dead-letter-001",
            vault_id="vault-dead-letter-001",
            authority_epoch=3,
            authorization_receipt_hash=_hash("wrong-owner-replay"),
            reason_code="operatorApproved",
        )
        wrong_epoch = DeadLetterReplayCommand(
            dead_letter_id=admission.dead_letter_id,
            actor_subject_id="owner-dead-letter-001",
            owner_subject_id="owner-dead-letter-001",
            vault_id="vault-dead-letter-001",
            authority_epoch=4,
            authorization_receipt_hash=_hash("wrong-epoch-replay"),
            reason_code="operatorApproved",
        )

        self.assertEqual(
            authorize_dead_letter_replay(admission, wrong_owner).reason,
            DeadLetterReplayReason.ACTOR_OWNER_MISMATCH,
        )
        self.assertEqual(
            authorize_dead_letter_replay(admission, wrong_epoch).reason,
            DeadLetterReplayReason.AUTHORITY_EPOCH_MISMATCH,
        )

        unknown = _admission(cause=DeadLetterCause.PROVIDER_UNKNOWN)
        poison = _admission(cause=DeadLetterCause.POISON_PAYLOAD)
        command = DeadLetterReplayCommand(
            dead_letter_id=unknown.dead_letter_id,
            actor_subject_id="owner-dead-letter-001",
            owner_subject_id="owner-dead-letter-001",
            vault_id="vault-dead-letter-001",
            authority_epoch=3,
            authorization_receipt_hash=_hash("replay-approved"),
            reason_code="operatorApproved",
        )

        self.assertEqual(
            authorize_dead_letter_replay(unknown, command).reason,
            DeadLetterReplayReason.PROVIDER_RECONCILIATION_REQUIRED,
        )
        self.assertEqual(
            authorize_dead_letter_replay(
                poison,
                DeadLetterReplayCommand(
                    dead_letter_id=poison.dead_letter_id,
                    actor_subject_id="owner-dead-letter-001",
                    owner_subject_id="owner-dead-letter-001",
                    vault_id="vault-dead-letter-001",
                    authority_epoch=3,
                    authorization_receipt_hash=_hash("poison-replay-approved"),
                    reason_code="operatorApproved",
                ),
            ).reason,
            DeadLetterReplayReason.PAYLOAD_CORRECTION_REQUIRED,
        )


if __name__ == "__main__":
    unittest.main()
