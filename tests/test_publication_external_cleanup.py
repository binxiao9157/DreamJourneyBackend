"""Contract tests for P2-S4C publication cleanup propagation.

The tests deliberately exercise the boundary before a real index/cache/DH/
voice/object provider adapter exists.  Local access denial must already be
complete; external work is only an append-only, value-minimized plan.
"""

from __future__ import annotations

import json
import unittest
from uuid import uuid4

from app.async_effects.provider_effect_repository import InMemoryProviderEffectRepository
from app.async_effects.repository import InMemoryEffectKernelRepository
from app.services.publication_external_cleanup import (
    InMemoryPublicationExternalCleanupRepository,
    PublicationExternalCleanupCoordinator,
    PublicationExternalCleanupDomain,
    PublicationExternalCleanupState,
)


class PublicationExternalCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryPublicationExternalCleanupRepository()
        self.coordinator = PublicationExternalCleanupCoordinator(
            effect_repository=InMemoryEffectKernelRepository(),
            provider_effect_repository=InMemoryProviderEffectRepository(),
            cleanup_repository=self.repository,
        )
        self.lifecycle_receipt_id = str(uuid4())
        self.publication_id = str(uuid4())
        self.publication_version_id = str(uuid4())

    def _enqueue(self):
        return self.coordinator.enqueue_after_access_deny(
            lifecycle_receipt_id=self.lifecycle_receipt_id,
            vault_id="vault-publication-cleanup",
            owner_subject_id="owner-publication-cleanup",
            publication_id=self.publication_id,
            publication_version_id=self.publication_version_id,
            authority_epoch=4,
            action="withdraw",
            reason_code="ownerWithdrawal",
        )

    def test_access_deny_is_a_precondition_and_all_domains_start_pending(self) -> None:
        summary = self._enqueue()

        self.assertEqual(len(summary), len(PublicationExternalCleanupDomain))
        self.assertEqual(
            {item.domain for item in summary},
            set(PublicationExternalCleanupDomain),
        )
        self.assertTrue(
            all(item.state is PublicationExternalCleanupState.PENDING for item in summary)
        )
        self.assertTrue(all(not item.provider_receipt_present for item in summary))

    def test_replay_deduplicates_without_rebinding_external_effects(self) -> None:
        first = self._enqueue()
        replay = self._enqueue()

        self.assertEqual(first, replay)
        self.assertEqual(
            self.repository.effect_count(self.lifecycle_receipt_id),
            len(PublicationExternalCleanupDomain),
        )

    def test_terminal_receipt_never_claims_completion_without_provider_receipt(self) -> None:
        self._enqueue()
        result = self.repository.record_outcome(
            lifecycle_receipt_id=self.lifecycle_receipt_id,
            domain=PublicationExternalCleanupDomain.PROVIDER_VOICE,
            state=PublicationExternalCleanupState.PARTIAL,
            reason_code="publicationCleanupProviderTimeout",
            provider_receipt_hash=None,
        )

        self.assertEqual(result.state, PublicationExternalCleanupState.PARTIAL)
        self.assertFalse(result.provider_receipt_present)
        with self.assertRaisesRegex(ValueError, "provider receipt"):
            self.repository.record_outcome(
                lifecycle_receipt_id=self.lifecycle_receipt_id,
                domain=PublicationExternalCleanupDomain.PROVIDER_VOICE,
                state=PublicationExternalCleanupState.COMPLETED,
                reason_code="publicationCleanupProviderReportedComplete",
                provider_receipt_hash=None,
            )

    def test_completion_requires_a_redacted_provider_receipt_hash(self) -> None:
        self._enqueue()

        result = self.repository.record_outcome(
            lifecycle_receipt_id=self.lifecycle_receipt_id,
            domain=PublicationExternalCleanupDomain.OBJECT_STORAGE,
            state=PublicationExternalCleanupState.COMPLETED,
            reason_code="publicationCleanupProviderReportedComplete",
            provider_receipt_hash="a" * 64,
        )

        self.assertEqual(result.state, PublicationExternalCleanupState.COMPLETED)
        self.assertTrue(result.provider_receipt_present)

        replay = self._enqueue()
        object_storage = next(
            item
            for item in replay
            if item.domain is PublicationExternalCleanupDomain.OBJECT_STORAGE
        )
        self.assertEqual(object_storage, result)

    def test_public_summary_is_redacted(self) -> None:
        summary = self._enqueue()
        rendered = json.dumps(
            [item.public_contract() for item in summary],
            ensure_ascii=False,
            sort_keys=True,
        )

        self.assertNotIn(self.lifecycle_receipt_id, rendered)
        self.assertNotIn(self.publication_id, rendered)
        self.assertNotIn(self.publication_version_id, rendered)
        self.assertNotIn("owner-publication-cleanup", rendered)
        self.assertNotIn("vault-publication-cleanup", rendered)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
