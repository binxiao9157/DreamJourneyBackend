from __future__ import annotations

from hashlib import sha256
import unittest
from uuid import uuid4

from app.async_effects.consumer_repository import (
    AsyncEffectConsumerError,
    InMemoryAsyncEffectConsumerRepository,
    OwnerTruthSourceBlockedConsumerCommand,
)
from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget
from app.async_effects.target_admission import InMemoryOwnerTruthSourceTargetAdmissionRepository


def digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class TypedConsumerCompletionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.owner_subject_id = "owner-typed-completion"
        self.vault_id = "vault-typed-completion"
        self.source_id = str(uuid4())
        self.admission_repository = InMemoryOwnerTruthSourceTargetAdmissionRepository()
        self.admission_repository.seed_vault(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            authority_epoch=3,
            status="active",
        )
        self.admission_repository.seed_source(
            vault_id=self.vault_id,
            source_id=self.source_id,
            owner_subject_id=self.owner_subject_id,
            authority_epoch=3,
            source_version=1,
            state="active",
        )
        self.intent = AsyncEffectIntent(
            operation_type="ownerTruth.source.created",
            target=AsyncEffectTarget(
                owner_subject_id=self.owner_subject_id,
                vault_id=self.vault_id,
                resource_type="source",
                resource_id=self.source_id,
                resource_version=1,
                purpose="candidateExtraction",
                authority_epoch=3,
            ),
            payload_hash=digest("typed-consumer-metadata-only"),
        )
        self.consumer_repository = InMemoryAsyncEffectConsumerRepository()

    def test_stale_source_authority_writes_one_blocked_completion_receipt(self):
        self.admission_repository.seed_vault(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            authority_epoch=4,
            status="active",
        )
        admission = self.admission_repository.admit_owner_truth_source(self.intent)
        command = OwnerTruthSourceBlockedConsumerCommand(
            intent=self.intent,
            consumer_name="ownerTruth.source.blocked",
            business_target_key=self.intent.business_target_key,
            outcome="blocked",
            reason_code="authorityEpochChanged",
            result_ref_hash=digest("source-blocked-result"),
            admission=admission,
        )

        created = self.consumer_repository.consume(command)
        replayed = self.consumer_repository.consume(command)

        self.assertFalse(admission.allowed)
        self.assertEqual(created.outcome, "accepted")
        self.assertEqual(created.business_outcome, "blocked")
        self.assertEqual(created.inbox_state, "skipped")
        self.assertEqual(replayed.outcome, "deduplicated")
        self.assertEqual(created.business_receipt_id, replayed.business_receipt_id)

    def test_current_target_cannot_be_falsely_completed_as_a_blocked_result(self):
        admission = self.admission_repository.admit_owner_truth_source(self.intent)

        with self.assertRaises(AsyncEffectConsumerError):
            OwnerTruthSourceBlockedConsumerCommand(
                intent=self.intent,
                consumer_name="ownerTruth.source.blocked",
                business_target_key=self.intent.business_target_key,
                outcome="blocked",
                reason_code="targetAuthorized",
                result_ref_hash=digest("source-current-result"),
                admission=admission,
            )

    def test_blocked_completion_must_preserve_the_live_admission_reason(self):
        self.admission_repository.seed_vault(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            authority_epoch=4,
            status="active",
        )
        admission = self.admission_repository.admit_owner_truth_source(self.intent)

        with self.assertRaises(AsyncEffectConsumerError):
            OwnerTruthSourceBlockedConsumerCommand(
                intent=self.intent,
                consumer_name="ownerTruth.source.blocked",
                business_target_key=self.intent.business_target_key,
                outcome="blocked",
                reason_code="sourceVersionChanged",
                result_ref_hash=digest("source-reason-mismatch-result"),
                admission=admission,
            )

    def test_blocked_completion_cannot_change_its_fixed_consumer_or_target(self):
        self.admission_repository.seed_vault(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            authority_epoch=4,
            status="active",
        )
        admission = self.admission_repository.admit_owner_truth_source(self.intent)

        with self.assertRaises(AsyncEffectConsumerError):
            OwnerTruthSourceBlockedConsumerCommand(
                intent=self.intent,
                consumer_name="ownerTruth.source.other",
                business_target_key=self.intent.business_target_key,
                outcome="blocked",
                reason_code=admission.reason_code,
                result_ref_hash=digest("source-other-consumer"),
                admission=admission,
            )
        with self.assertRaises(AsyncEffectConsumerError):
            OwnerTruthSourceBlockedConsumerCommand(
                intent=self.intent,
                consumer_name="ownerTruth.source.blocked",
                business_target_key=digest("source-other-target"),
                outcome="blocked",
                reason_code=admission.reason_code,
                result_ref_hash=digest("source-other-target-result"),
                admission=admission,
            )


if __name__ == "__main__":
    unittest.main()
