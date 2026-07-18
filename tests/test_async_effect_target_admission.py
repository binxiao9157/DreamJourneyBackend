from __future__ import annotations

from hashlib import sha256
import unittest
from uuid import uuid4

from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget
from app.async_effects.target_admission import InMemoryOwnerTruthSourceTargetAdmissionRepository


def digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class AsyncEffectTargetAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryOwnerTruthSourceTargetAdmissionRepository()
        self.owner_subject_id = "owner-target-admission"
        self.vault_id = "vault-target-admission"
        self.source_id = str(uuid4())
        self.repository.seed_vault(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            authority_epoch=7,
            status="active",
        )
        self.repository.seed_source(
            vault_id=self.vault_id,
            source_id=self.source_id,
            owner_subject_id=self.owner_subject_id,
            authority_epoch=7,
            source_version=3,
            state="active",
        )

    def intent(
        self,
        *,
        operation_type: str = "ownerTruth.source.created",
        authority_epoch: int = 7,
        resource_version: int = 3,
        resource_type: str = "source",
        purpose: str = "candidateExtraction",
    ) -> AsyncEffectIntent:
        return AsyncEffectIntent(
            operation_type=operation_type,
            target=AsyncEffectTarget(
                owner_subject_id=self.owner_subject_id,
                vault_id=self.vault_id,
                resource_type=resource_type,
                resource_id=self.source_id,
                resource_version=resource_version,
                purpose=purpose,
                authority_epoch=authority_epoch,
            ),
            payload_hash=digest("target-admission-metadata-only"),
        )

    def test_active_source_with_current_owner_epoch_and_version_is_admitted(self):
        result = self.repository.admit_owner_truth_source(self.intent())

        self.assertTrue(result.allowed)
        self.assertEqual(result.outcome, "admitted")
        self.assertEqual(result.reason_code, "targetAuthorized")
        self.assertEqual(result.authority_epoch, 7)
        self.assertEqual(result.resource_version, 3)

    def test_stale_vault_epoch_is_blocked_before_any_consumer_completion(self):
        self.repository.seed_vault(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            authority_epoch=8,
            status="active",
        )

        result = self.repository.admit_owner_truth_source(self.intent())

        self.assertFalse(result.allowed)
        self.assertEqual(result.outcome, "blocked")
        self.assertEqual(result.reason_code, "authorityEpochChanged")

    def test_changed_source_version_is_blocked(self):
        self.repository.seed_source(
            vault_id=self.vault_id,
            source_id=self.source_id,
            owner_subject_id=self.owner_subject_id,
            authority_epoch=7,
            source_version=4,
            state="active",
        )

        result = self.repository.admit_owner_truth_source(self.intent())

        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_code, "sourceVersionChanged")

    def test_inactive_source_is_blocked_without_exposing_source_content(self):
        self.repository.seed_source(
            vault_id=self.vault_id,
            source_id=self.source_id,
            owner_subject_id=self.owner_subject_id,
            authority_epoch=7,
            source_version=3,
            state="redacted",
        )

        result = self.repository.admit_owner_truth_source(self.intent())

        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_code, "sourceInactive")
        self.assertFalse(hasattr(result, "content"))
        self.assertFalse(hasattr(result, "metadata"))

    def test_wrong_operation_or_target_shape_is_fail_closed(self):
        operation_result = self.repository.admit_owner_truth_source(
            self.intent(operation_type="timeLetter.delivery")
        )
        target_result = self.repository.admit_owner_truth_source(
            self.intent(resource_type="syntheticEffect", purpose="consumerFoundation")
        )

        self.assertEqual(operation_result.reason_code, "unsupportedOperation")
        self.assertEqual(target_result.reason_code, "unsupportedTarget")


if __name__ == "__main__":
    unittest.main()
