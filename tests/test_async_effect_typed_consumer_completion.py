from __future__ import annotations

from hashlib import sha256
import unittest
from uuid import uuid4

from app.async_effects.consumer_repository import (
    AsyncEffectConsumerError,
    InMemoryAsyncEffectConsumerRepository,
    OwnerTruthMemoryProjectionRebuildConsumerCommand,
    OwnerTruthSourceCandidateExtractionConsumerCommand,
    OwnerTruthSourceBlockedConsumerCommand,
)
from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget
from app.async_effects.target_admission import (
    InMemoryOwnerTruthMemoryProjectionTargetAdmissionRepository,
    InMemoryOwnerTruthSourceTargetAdmissionRepository,
)
from app.services.owner_truth_memory_projection_effects import (
    MEMORY_PROJECTION_REBUILD_EVENT_TYPE,
    MEMORY_PROJECTION_REBUILD_JOB_TYPE,
    MEMORY_PROJECTION_REBUILD_OPERATION_TYPE,
)


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

    def test_admitted_extraction_completion_has_one_fixed_result_target(self):
        admission = self.admission_repository.admit_owner_truth_source(self.intent)
        extraction_id = str(uuid4())
        command = OwnerTruthSourceCandidateExtractionConsumerCommand(
            intent=self.intent,
            consumer_name="ownerTruth.source.extraction",
            business_target_key=digest(f"owner-truth-extraction:{extraction_id}"),
            outcome="completed",
            reason_code="candidateProposalsPersisted",
            result_ref_hash=digest("candidate-result"),
            admission=admission,
            extraction_id=extraction_id,
            extraction_status="succeeded",
        )

        created = self.consumer_repository.consume(command)
        replayed = self.consumer_repository.consume(command)

        self.assertTrue(admission.allowed)
        self.assertEqual(created.outcome, "accepted")
        self.assertEqual(replayed.outcome, "deduplicated")
        self.assertEqual(created.business_target_key, command.business_target_key)

    def test_extraction_completion_refuses_an_unauthorized_source_target(self):
        self.admission_repository.seed_source(
            vault_id=self.vault_id,
            source_id=self.source_id,
            owner_subject_id=self.owner_subject_id,
            authority_epoch=4,
            source_version=1,
            state="active",
        )
        admission = self.admission_repository.admit_owner_truth_source(self.intent)
        extraction_id = str(uuid4())

        with self.assertRaises(AsyncEffectConsumerError):
            OwnerTruthSourceCandidateExtractionConsumerCommand(
                intent=self.intent,
                consumer_name="ownerTruth.source.extraction",
                business_target_key=digest(f"owner-truth-extraction:{extraction_id}"),
                outcome="completed",
                reason_code="candidateProposalsPersisted",
                result_ref_hash=digest("candidate-result-blocked"),
                admission=admission,
                extraction_id=extraction_id,
                extraction_status="succeeded",
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


class MemoryProjectionTypedConsumerCompletionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.owner_subject_id = "owner-projection-consumer"
        self.vault_id = "vault-projection-consumer"
        self.memory_version_id = str(uuid4())
        self.content_hash = digest("projection-consumer-metadata-only")
        self.admission_repository = InMemoryOwnerTruthMemoryProjectionTargetAdmissionRepository()
        self.admission_repository.seed_vault(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            authority_epoch=4,
            status="active",
        )
        self.admission_repository.seed_memory_version(
            vault_id=self.vault_id,
            memory_version_id=self.memory_version_id,
            owner_subject_id=self.owner_subject_id,
            authority_epoch=4,
            state="active",
            source_version=1,
            version_number=1,
            is_current=True,
            content_hash=self.content_hash,
            source_owner_subject_id=self.owner_subject_id,
            source_authority_epoch=4,
            source_state="active",
            source_version_current=1,
        )
        self.intent = AsyncEffectIntent(
            operation_type=MEMORY_PROJECTION_REBUILD_OPERATION_TYPE,
            target=AsyncEffectTarget(
                owner_subject_id=self.owner_subject_id,
                vault_id=self.vault_id,
                resource_type="memoryVersion",
                resource_id=self.memory_version_id,
                resource_version=1,
                purpose="compatibilityProjection",
                authority_epoch=4,
            ),
            payload_hash=self.content_hash,
            event_type=MEMORY_PROJECTION_REBUILD_EVENT_TYPE,
            job_type=MEMORY_PROJECTION_REBUILD_JOB_TYPE,
        )
        self.consumer_repository = InMemoryAsyncEffectConsumerRepository()

    def test_admitted_projection_rebuild_writes_one_completed_receipt(self):
        admission = self.admission_repository.admit_owner_truth_memory_projection(self.intent)
        command = OwnerTruthMemoryProjectionRebuildConsumerCommand(
            intent=self.intent,
            consumer_name="ownerTruth.memoryProjection.rebuild",
            business_target_key=self.intent.business_target_key,
            outcome="completed",
            reason_code="memoryProjectionRebuilt",
            result_ref_hash=digest("projection-checkpoint"),
            admission=admission,
            projection_outcome="rebuilt",
        )

        created = self.consumer_repository.consume(command)
        replayed = self.consumer_repository.consume(command)

        self.assertTrue(admission.allowed)
        self.assertEqual(created.business_outcome, "completed")
        self.assertEqual(created.inbox_state, "completed")
        self.assertEqual(replayed.outcome, "deduplicated")

    def test_stale_projection_target_can_only_write_a_blocked_completion(self):
        self.admission_repository.seed_vault(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            authority_epoch=5,
            status="active",
        )
        admission = self.admission_repository.admit_owner_truth_memory_projection(self.intent)
        command = OwnerTruthMemoryProjectionRebuildConsumerCommand(
            intent=self.intent,
            consumer_name="ownerTruth.memoryProjection.rebuild",
            business_target_key=self.intent.business_target_key,
            outcome="blocked",
            reason_code="authorityEpochChanged",
            result_ref_hash=digest("projection-blocked"),
            admission=admission,
            projection_outcome=None,
        )

        result = self.consumer_repository.consume(command)

        self.assertFalse(admission.allowed)
        self.assertEqual(result.business_outcome, "blocked")
        self.assertEqual(result.inbox_state, "skipped")

    def test_projection_completion_rejects_a_mismatched_live_reason(self):
        self.admission_repository.seed_vault(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            authority_epoch=5,
            status="active",
        )
        admission = self.admission_repository.admit_owner_truth_memory_projection(self.intent)

        with self.assertRaises(AsyncEffectConsumerError):
            OwnerTruthMemoryProjectionRebuildConsumerCommand(
                intent=self.intent,
                consumer_name="ownerTruth.memoryProjection.rebuild",
                business_target_key=self.intent.business_target_key,
                outcome="blocked",
                reason_code="memoryVersionChanged",
                result_ref_hash=digest("projection-bad-reason"),
                admission=admission,
                projection_outcome=None,
            )


if __name__ == "__main__":
    unittest.main()
