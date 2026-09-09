from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import unittest
from uuid import uuid4

from app.domain.owner_truth.b_migration_dry_run import (
    OwnerTruthBMigrationDisposition,
    OwnerTruthBMigrationDryRunError,
    build_owner_truth_b_migration_dry_run_report,
    require_owner_truth_b_migration_restore_compatible,
)
from app.domain.owner_truth.legacy_backfill import build_legacy_backfill_admission_plan
from app.domain.owner_truth.legacy_migration import (
    LegacyMigrationDomain,
    LegacyMigrationRecord,
    build_legacy_migration_inventory,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_b_migration_dry_run import (
    InMemoryOwnerTruthBMigrationDryRunRepository,
    OwnerTruthBMigrationDryRunService,
    _persisted_plan_matches_report,
)
from app.services.owner_truth_legacy_backfill import (
    InMemoryOwnerTruthLegacyBackfillRepository,
)
from app.services.owner_truth_legacy_migration import (
    InMemoryOwnerTruthLegacyMigrationRepository,
    LegacyMigrationLegacyRows,
)


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class _Store:
    def __init__(self) -> None:
        self.vault = {
            "ownerSubjectId": "owner-b-migration",
            "authorityEpoch": 4,
            "status": "active",
        }
        self.legacy_repository = InMemoryOwnerTruthLegacyMigrationRepository(
            row_supplier=lambda _owner: LegacyMigrationLegacyRows(
                archive_items=(
                    {
                        "id": "archive-unreviewed",
                        "user_id": "owner-b-migration",
                        "owner_subject_id": "owner-b-migration",
                        "payload": {"body": "private unreviewed archive"},
                    },
                ),
            )
        )
        self.backfill_repository = InMemoryOwnerTruthLegacyBackfillRepository(
            authority_supplier=lambda vault_id, _owner: (
                dict(self.vault) if vault_id == "vault-b-migration" else None
            )
        )
        self.dry_run_repository = InMemoryOwnerTruthBMigrationDryRunRepository()

    def owner_truth_legacy_migration_repository(self):
        return self.legacy_repository

    def owner_truth_legacy_backfill_repository(self):
        return self.backfill_repository

    def owner_truth_b_migration_dry_run_repository(self):
        return self.dry_run_repository


class _UnitOfWorkRequiredDryRunRepository(InMemoryOwnerTruthBMigrationDryRunRepository):
    def __init__(self, is_active) -> None:
        super().__init__()
        self._is_active = is_active

    def persist(self, **kwargs):
        if not self._is_active():
            raise RuntimeError("dry-run persistence requires an active unit of work")
        return super().persist(**kwargs)


class _UnitOfWorkRequiredStore(_Store):
    def __init__(self) -> None:
        super().__init__()
        self._active_unit_of_work_count = 0
        self.unit_of_work_commands: list[str] = []
        self.dry_run_repository = _UnitOfWorkRequiredDryRunRepository(
            lambda: self._active_unit_of_work_count > 0
        )

    @contextmanager
    def request_unit_of_work(self, *, correlation_id: str, command_id: str):
        del correlation_id
        self.unit_of_work_commands.append(command_id)
        self._active_unit_of_work_count += 1
        try:
            yield
        finally:
            self._active_unit_of_work_count -= 1


class OwnerTruthBMigrationDryRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.owner_id = "owner-b-migration"
        self.vault_id = "vault-b-migration"
        self.inventory_run_id = str(uuid4())
        self.context = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            actor_subject_id=self.owner_id,
        )
        self.inventory = build_legacy_migration_inventory(
            vault_id=self.vault_id,
            classifier_version="b-migration-test-v1",
            records=(
                LegacyMigrationRecord(
                    domain=LegacyMigrationDomain.MEMORY,
                    legacy_id="verified-lineage",
                    record_hash=_digest("verified"),
                    canonical_owner_subject_id=self.owner_id,
                    observed_owner_subject_id=self.owner_id,
                    source_evidence_id="source-proof",
                    decision_receipt_id="decision-proof",
                    decision_is_terminal=True,
                    revision_evidence_id="revision-proof",
                ),
                LegacyMigrationRecord(
                    domain=LegacyMigrationDomain.MEMORY,
                    legacy_id="missing-semantic-evidence",
                    record_hash=_digest("requires review"),
                    canonical_owner_subject_id=self.owner_id,
                    observed_owner_subject_id=self.owner_id,
                ),
                LegacyMigrationRecord(
                    domain=LegacyMigrationDomain.ARCHIVE_ITEM,
                    legacy_id="observed-archive",
                    record_hash=_digest("observed archive"),
                    canonical_owner_subject_id=self.owner_id,
                    observed_owner_subject_id=self.owner_id,
                ),
                LegacyMigrationRecord(
                    domain=LegacyMigrationDomain.ARCHIVE_ITEM,
                    legacy_id="other-owner",
                    record_hash=_digest("owner mismatch"),
                    canonical_owner_subject_id=self.owner_id,
                    observed_owner_subject_id="different-owner",
                ),
                LegacyMigrationRecord(
                    domain=LegacyMigrationDomain.CONVERSATION_CACHE,
                    legacy_id="conversation-cache",
                    record_hash=_digest("assistant text"),
                    canonical_owner_subject_id=self.owner_id,
                    observed_owner_subject_id=self.owner_id,
                ),
            ),
        )
        self.plan = build_legacy_backfill_admission_plan(
            inventory_run_id=self.inventory_run_id,
            inventory=self.inventory,
            owner_subject_id=self.owner_id,
            authority_epoch=4,
        )

    def test_dry_run_preserves_unknown_semantics_and_never_creates_formal_memory(self) -> None:
        report = build_owner_truth_b_migration_dry_run_report(
            inventory_run_id=self.inventory_run_id,
            plan=self.plan,
        )

        by_disposition = {entry.disposition for entry in report.entries}
        self.assertIn(OwnerTruthBMigrationDisposition.REPLAY_CURRENT_REVIEW_PATH, by_disposition)
        self.assertIn(OwnerTruthBMigrationDisposition.OWNER_REVIEW_REQUIRED, by_disposition)
        self.assertIn(OwnerTruthBMigrationDisposition.QUARANTINED, by_disposition)
        self.assertIn(OwnerTruthBMigrationDisposition.EXCLUDED, by_disposition)
        self.assertTrue(all(entry.target_state == "notCreated" for entry in report.entries))
        self.assertTrue(
            all(
                set(entry.semantic_field_policy)
                == {"claimSubject", "provenance", "polarity", "timeRange", "perspective"}
                for entry in report.entries
            )
        )
        summary = report.summary()
        self.assertEqual(summary["formalMemoryWriteCount"], 0)
        self.assertEqual(summary["targetState"], "notCreated")
        self.assertNotIn("verified-lineage", str(summary))

    def test_same_plan_is_idempotent_but_a_changed_checkpoint_cannot_be_restored(self) -> None:
        first = build_owner_truth_b_migration_dry_run_report(
            inventory_run_id=self.inventory_run_id,
            plan=self.plan,
        )
        replay = build_owner_truth_b_migration_dry_run_report(
            inventory_run_id=self.inventory_run_id,
            plan=self.plan,
        )
        self.assertEqual(first.report_id, replay.report_id)
        self.assertEqual(first.report_hash, replay.report_hash)
        require_owner_truth_b_migration_restore_compatible(
            report=first,
            current_plan=self.plan,
        )

        changed_plan = build_legacy_backfill_admission_plan(
            inventory_run_id=self.inventory_run_id,
            inventory=self.inventory,
            owner_subject_id=self.owner_id,
            authority_epoch=5,
        )
        with self.assertRaises(OwnerTruthBMigrationDryRunError):
            require_owner_truth_b_migration_restore_compatible(
                report=first,
                current_plan=changed_plan,
            )

    def test_service_reuses_immutable_backfill_checkpoint_without_promoting_data(self) -> None:
        store = _Store()
        service = OwnerTruthBMigrationDryRunService(store, enabled=True)

        first = service.dry_run(context=self.context)
        second = service.dry_run(context=self.context)

        self.assertEqual(first.outcome, "created")
        self.assertEqual(second.outcome, "deduplicated")
        self.assertEqual(first.report.report_id, second.report.report_id)
        self.assertEqual(first.report.summary()["formalMemoryWriteCount"], 0)
        self.assertEqual(store.backfill_repository.snapshot()["planCount"], 1)
        self.assertEqual(store.dry_run_repository.snapshot()["reportCount"], 1)

    def test_service_persists_report_inside_a_separate_unit_of_work(self) -> None:
        store = _UnitOfWorkRequiredStore()

        result = OwnerTruthBMigrationDryRunService(store, enabled=True).dry_run(
            context=self.context
        )

        self.assertEqual(result.outcome, "created")
        self.assertIn(result.report.report_id, store.unit_of_work_commands)
        self.assertEqual(store._active_unit_of_work_count, 0)

    def test_persisted_plan_epoch_zero_is_not_treated_as_missing(self) -> None:
        plan = build_legacy_backfill_admission_plan(
            inventory_run_id=self.inventory_run_id,
            inventory=self.inventory,
            owner_subject_id=self.owner_id,
            authority_epoch=0,
        )
        report = build_owner_truth_b_migration_dry_run_report(
            inventory_run_id=self.inventory_run_id,
            plan=plan,
        )

        self.assertTrue(
            _persisted_plan_matches_report(
                {
                    "inventory_run_id": report.inventory_run_id,
                    "vault_id": report.vault_id,
                    "owner_subject_id": report.owner_subject_id,
                    "authority_epoch": 0,
                    "inventory_hash": report.inventory_hash,
                    "plan_hash": report.plan_hash,
                    "scope_hash": report.scope_hash,
                },
                report=report,
            )
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
