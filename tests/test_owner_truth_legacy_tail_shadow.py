"""G0 contracts for C04 legacy tail/outbox/object/Provider would-run mapping."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Optional
import unittest
from uuid import uuid4

from app.async_effects.provider_effects import provider_effect_catalog
from app.domain.owner_truth.legacy_backfill import (
    LegacyBackfillAdmissionAction,
    build_legacy_backfill_admission_plan,
)
from app.domain.owner_truth.legacy_migration import (
    LegacyMigrationDomain,
    LegacyMigrationRecord,
    build_legacy_migration_inventory,
)
from app.domain.owner_truth.legacy_tail_shadow import (
    LegacyTailShadowChannel,
    LegacyTailShadowOperation,
    OwnerTruthLegacyTailShadowConflict,
    OwnerTruthLegacyTailShadowError,
    build_legacy_tail_shadow_report,
)


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class OwnerTruthLegacyTailShadowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.owner_id = "owner-tail-shadow"
        self.vault_id = "vault-tail-shadow"
        inventory = build_legacy_migration_inventory(
            vault_id=self.vault_id,
            classifier_version="legacy-tail-shadow-test-v1",
            records=(
                LegacyMigrationRecord(
                    domain=LegacyMigrationDomain.ARCHIVE_ITEM,
                    legacy_id="archive-proven",
                    record_hash=_digest("archive-proven-record"),
                    canonical_owner_subject_id=self.owner_id,
                    observed_owner_subject_id=self.owner_id,
                    source_evidence_id="source-proof",
                    decision_receipt_id="decision-proof",
                    decision_is_terminal=True,
                    revision_evidence_id="revision-proof",
                ),
                LegacyMigrationRecord(
                    domain=LegacyMigrationDomain.KB_CHANGE,
                    legacy_id="kb-candidate",
                    record_hash=_digest("kb-candidate-record"),
                    canonical_owner_subject_id=self.owner_id,
                    observed_owner_subject_id=self.owner_id,
                ),
                LegacyMigrationRecord(
                    domain=LegacyMigrationDomain.MEMORY,
                    legacy_id="memory-review",
                    record_hash=_digest("memory-review-record"),
                    canonical_owner_subject_id=self.owner_id,
                    observed_owner_subject_id=self.owner_id,
                ),
                LegacyMigrationRecord(
                    domain=LegacyMigrationDomain.MEMORY,
                    legacy_id="memory-quarantine",
                    record_hash=_digest("memory-quarantine-record"),
                    canonical_owner_subject_id=self.owner_id,
                    observed_owner_subject_id="other-owner",
                ),
                LegacyMigrationRecord(
                    domain=LegacyMigrationDomain.CONVERSATION_CACHE,
                    legacy_id="conversation-excluded",
                    record_hash=_digest("conversation-excluded-record"),
                    canonical_owner_subject_id=self.owner_id,
                    observed_owner_subject_id=self.owner_id,
                ),
            ),
        )
        self.plan = build_legacy_backfill_admission_plan(
            inventory_run_id=str(uuid4()),
            inventory=inventory,
            owner_subject_id=self.owner_id,
            authority_epoch=3,
        )

    def _operation(
        self,
        entry,
        channel: LegacyTailShadowChannel,
        *,
        index: int,
        provider_catalog_key: Optional[str] = None,
        object_reference_hash: Optional[str] = None,
        callback_fixture_hash: Optional[str] = None,
        source_version: int = 1,
    ) -> LegacyTailShadowOperation:
        return LegacyTailShadowOperation(
            channel=channel,
            domain=entry.domain,
            legacy_id_hash=entry.legacy_id_hash,
            record_hash=entry.record_hash,
            tail_cursor_hash=_digest(f"tail-cursor:{entry.legacy_id_hash}:{channel.value}:{index}"),
            source_version=source_version,
            provider_catalog_key=provider_catalog_key,
            object_reference_hash=object_reference_hash,
            callback_fixture_hash=callback_fixture_hash,
        )

    def _complete_operations(self) -> list[LegacyTailShadowOperation]:
        operations: list[LegacyTailShadowOperation] = []
        eligible = [
            entry
            for entry in self.plan.entries
            if entry.action
            in {
                LegacyBackfillAdmissionAction.REQUIRE_INDEPENDENT_LINEAGE_REPLAY,
                LegacyBackfillAdmissionAction.REQUIRE_OWNER_CANDIDATE_REVIEW,
                LegacyBackfillAdmissionAction.REQUIRE_EVIDENCE_REVIEW,
            }
        ]
        for index, entry in enumerate(eligible, start=1):
            operations.append(self._operation(entry, LegacyTailShadowChannel.OUTBOX_JOB, index=index))
        archive = next(
            entry for entry in eligible if entry.domain is LegacyMigrationDomain.ARCHIVE_ITEM
        )
        operations.append(
            self._operation(
                archive,
                LegacyTailShadowChannel.OBJECT_REFERENCE,
                index=30,
                object_reference_hash=_digest("trusted-object-reference"),
            )
        )
        for index, catalog_entry in enumerate(provider_effect_catalog(), start=100):
            if catalog_entry.requires_stable_provider_effect:
                operations.append(
                    self._operation(
                        archive,
                        LegacyTailShadowChannel.PROVIDER_EFFECT,
                        index=index,
                        provider_catalog_key=catalog_entry.key,
                        callback_fixture_hash=_digest(f"callback-fixture:{catalog_entry.key}"),
                    )
                )
        return operations

    def test_complete_shadow_map_is_deterministic_value_free_and_side_effect_free(self) -> None:
        first = build_legacy_tail_shadow_report(
            plan=self.plan,
            operations=self._complete_operations(),
        )
        second = build_legacy_tail_shadow_report(
            plan=self.plan,
            operations=list(reversed(self._complete_operations())),
        )

        self.assertEqual(first.report_hash, second.report_hash)
        self.assertEqual(first.tail_checkpoint_hash, second.tail_checkpoint_hash)
        self.assertTrue(first.ready_for_next_gate)
        summary = first.value_free_summary()
        self.assertTrue(summary["shadowOnly"])
        self.assertEqual(summary["effectExecutionCount"], 0)
        self.assertEqual(summary["outboxWriteCount"], 0)
        self.assertEqual(summary["jobWriteCount"], 0)
        self.assertEqual(summary["objectStorageOperationCount"], 0)
        self.assertEqual(summary["providerCallCount"], 0)
        self.assertEqual(summary["providerCallbackProcessedCount"], 0)
        self.assertEqual(summary["callbackAcceptedCount"], 0)
        self.assertEqual(summary["missingOutboxMappingCount"], 0)
        self.assertEqual(summary["archiveObjectEvidenceGapCount"], 0)
        self.assertEqual(summary["unmappedProviderCatalogKeys"], [])
        self.assertNotIn("archive-proven", repr(summary))
        self.assertNotIn("memory-review", repr(summary))
        self.assertNotIn(self.owner_id, repr(summary))
        self.assertNotIn(self.vault_id, repr(summary))

    def test_exact_duplicate_is_observed_but_changed_same_identity_is_a_conflict(self) -> None:
        first = self._complete_operations()[0]
        report = build_legacy_tail_shadow_report(plan=self.plan, operations=[first, first])
        self.assertEqual(report.duplicate_input_count, 1)
        self.assertEqual(len(report.mappings), 1)

        conflicting = LegacyTailShadowOperation(
            channel=first.channel,
            domain=first.domain,
            legacy_id_hash=first.legacy_id_hash,
            record_hash=first.record_hash,
            tail_cursor_hash=first.tail_cursor_hash,
            source_version=first.source_version + 1,
        )
        with self.assertRaises(OwnerTruthLegacyTailShadowConflict):
            build_legacy_tail_shadow_report(plan=self.plan, operations=[first, conflicting])

    def test_missing_maps_are_reported_as_gaps_not_silently_executed(self) -> None:
        report = build_legacy_tail_shadow_report(plan=self.plan, operations=[])
        summary = report.value_free_summary()

        self.assertFalse(report.ready_for_next_gate)
        self.assertEqual(summary["mappingCount"], 0)
        self.assertGreater(summary["missingOutboxMappingCount"], 0)
        self.assertGreater(summary["archiveObjectEvidenceGapCount"], 0)
        self.assertGreater(len(summary["unmappedProviderCatalogKeys"]), 0)
        self.assertEqual(summary["effectExecutionCount"], 0)
        self.assertEqual(summary["providerCallCount"], 0)

    def test_quarantined_or_excluded_entries_cannot_enter_tail_mapping(self) -> None:
        forbidden = next(
            entry
            for entry in self.plan.entries
            if entry.action
            in {
                LegacyBackfillAdmissionAction.QUARANTINED,
                LegacyBackfillAdmissionAction.EXCLUDED,
            }
        )
        operation = self._operation(forbidden, LegacyTailShadowChannel.OUTBOX_JOB, index=1)

        with self.assertRaises(OwnerTruthLegacyTailShadowError):
            build_legacy_tail_shadow_report(plan=self.plan, operations=[operation])

    def test_provider_map_requires_catalog_and_callback_fixture(self) -> None:
        eligible = next(
            entry
            for entry in self.plan.entries
            if entry.action
            is LegacyBackfillAdmissionAction.REQUIRE_INDEPENDENT_LINEAGE_REPLAY
        )
        with self.assertRaises(OwnerTruthLegacyTailShadowError):
            LegacyTailShadowOperation(
                channel=LegacyTailShadowChannel.PROVIDER_EFFECT,
                domain=eligible.domain,
                legacy_id_hash=eligible.legacy_id_hash,
                record_hash=eligible.record_hash,
                tail_cursor_hash=_digest("missing-provider-fixture"),
                source_version=1,
                provider_catalog_key="unknown.provider",
            )
        unknown_catalog = self._operation(
            eligible,
            LegacyTailShadowChannel.PROVIDER_EFFECT,
            index=2,
            provider_catalog_key="unknown.provider",
            callback_fixture_hash=_digest("unknown-provider-fixture"),
        )
        with self.assertRaises(OwnerTruthLegacyTailShadowError):
            build_legacy_tail_shadow_report(plan=self.plan, operations=[unknown_catalog])

    def test_module_cannot_import_or_call_effect_persistence_network_or_provider_clients(self) -> None:
        source = (
            Path(__file__).parents[1] / "app/domain/owner_truth/legacy_tail_shadow.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
            "app.main",
            "EffectKernelRepository",
            "PostgresEffectKernelRepository",
            "requests",
            "httpx",
            "urllib.request",
            "psycopg",
            "boto3",
            "ProviderEffectReceipt",
        ):
            self.assertNotIn(forbidden, source)
        self.assertNotIn(".accept(", source)


if __name__ == "__main__":
    unittest.main()
