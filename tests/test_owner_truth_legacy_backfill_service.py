from __future__ import annotations

import unittest
from uuid import uuid4

from app.domain.owner_truth.source_commands import (
    CreateTextSourceCommand,
    OwnerTruthCommandContext,
)
from app.services.in_memory_store import InMemoryStore
from app.services.owner_truth_legacy_backfill import (
    InMemoryOwnerTruthLegacyBackfillRepository,
    OwnerTruthLegacyBackfillAccessDenied,
    OwnerTruthLegacyBackfillConflict,
    OwnerTruthLegacyBackfillPlanService,
    OwnerTruthLegacyBackfillUnavailable,
)
from app.services.owner_truth_legacy_migration import (
    InMemoryOwnerTruthLegacyMigrationRepository,
    LegacyMigrationLegacyRows,
)
from app.services.owner_truth_source import OwnerTruthSourceCommandService


class _BackfillStore:
    def __init__(self) -> None:
        self.vault = {
            "ownerSubjectId": "owner-backfill-service",
            "authorityEpoch": 3,
            "status": "active",
        }
        self.legacy_repository = InMemoryOwnerTruthLegacyMigrationRepository(
            row_supplier=lambda _owner: LegacyMigrationLegacyRows(
                archive_items=(
                    {
                        "id": "legacy-private-archive-id",
                        "user_id": "owner-backfill-service",
                        "owner_subject_id": "owner-backfill-service",
                        "authority_state": "active",
                        "payload": {"description": "private archive body"},
                    },
                ),
            ),
        )
        self.backfill_repository = InMemoryOwnerTruthLegacyBackfillRepository(
            authority_supplier=self._authority,
        )

    def _authority(self, vault_id: str, _owner_subject_id: str):
        if vault_id != "vault-backfill-service":
            return None
        return dict(self.vault)

    def owner_truth_legacy_migration_repository(self):
        return self.legacy_repository

    def owner_truth_legacy_backfill_repository(self):
        return self.backfill_repository


class OwnerTruthLegacyBackfillPlanServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = _BackfillStore()
        self.context = OwnerTruthCommandContext(
            vault_id="vault-backfill-service",
            owner_subject_id="owner-backfill-service",
            actor_subject_id="owner-backfill-service",
        )
        self.service = OwnerTruthLegacyBackfillPlanService(self.store, enabled=True)

    def test_plan_is_owner_scoped_replay_safe_and_non_authorizing(self) -> None:
        created = self.service.plan(context=self.context)
        replayed = self.service.plan(context=self.context)
        summary = created.public_summary()

        self.assertEqual(created.outcome, "created")
        self.assertEqual(replayed.outcome, "deduplicated")
        self.assertEqual(created.plan.plan_id, replayed.plan.plan_id)
        self.assertEqual(created.plan.inventory_run_id, replayed.plan.inventory_run_id)
        self.assertEqual(summary["authorityEpoch"], 3)
        self.assertEqual(summary["entryCount"], 1)
        self.assertEqual(summary["targetState"], "notCreated")
        self.assertFalse(summary["cutoverAllowed"])
        self.assertFalse(summary["legacyWriterRetired"])
        self.assertEqual(self.store.backfill_repository.snapshot()["planCount"], 1)
        self.assertNotIn("private archive body", str(summary))
        self.assertNotIn("legacy-private-archive-id", str(summary))

    def test_non_owner_and_disabled_service_fail_closed(self) -> None:
        attacker = OwnerTruthCommandContext(
            vault_id=self.context.vault_id,
            owner_subject_id=self.context.owner_subject_id,
            actor_subject_id="other-subject",
        )
        with self.assertRaises(OwnerTruthLegacyBackfillAccessDenied):
            self.service.plan(context=attacker)

        disabled = OwnerTruthLegacyBackfillPlanService(self.store, enabled=False)
        with self.assertRaises(OwnerTruthLegacyBackfillUnavailable):
            disabled.plan(context=self.context)

    def test_unknown_or_cross_owner_vault_fails_before_inventory_is_written(self) -> None:
        attacker = OwnerTruthCommandContext(
            vault_id=self.context.vault_id,
            owner_subject_id="attacker-owner",
            actor_subject_id="attacker-owner",
        )

        with self.assertRaises(OwnerTruthLegacyBackfillAccessDenied):
            self.service.plan(context=attacker)

        self.assertEqual(self.store.legacy_repository.snapshot()["runCount"], 0)
        self.assertEqual(self.store.backfill_repository.snapshot()["planCount"], 0)

    def test_epoch_change_rejects_stale_plan_before_persistence(self) -> None:
        inventory_run = self.store.legacy_repository.persist(
            owner_subject_id=self.context.owner_subject_id,
            inventory=self.store.legacy_repository.collect(
                vault_id=self.context.vault_id,
                owner_subject_id=self.context.owner_subject_id,
                classifier_version="owner-truth-legacy-classifier-v1",
            ),
        )
        from app.domain.owner_truth.legacy_backfill import build_legacy_backfill_admission_plan

        plan = build_legacy_backfill_admission_plan(
            inventory_run_id=inventory_run.run_id,
            inventory=inventory_run.inventory,
            owner_subject_id=self.context.owner_subject_id,
            authority_epoch=3,
        )
        self.store.vault["authorityEpoch"] = 4

        with self.assertRaises(OwnerTruthLegacyBackfillConflict):
            self.store.backfill_repository.persist(
                owner_subject_id=self.context.owner_subject_id,
                inventory_run=inventory_run,
                plan=plan,
            )

    def test_in_memory_store_binds_plan_to_active_owner_truth_vault(self) -> None:
        store = InMemoryStore()
        context = OwnerTruthCommandContext(
            vault_id="vault-backfill-integration",
            owner_subject_id="owner-backfill-integration",
            actor_subject_id="owner-backfill-integration",
        )
        OwnerTruthSourceCommandService(store).create_text_source(
            command=CreateTextSourceCommand(
                command_id="backfill-integration-seed",
                source_id=str(uuid4()),
                expected_version=0,
                text="seed active owner truth vault",
                metadata={},
            ),
            context=context,
        )
        store.add_archive_item(
            context.owner_subject_id,
            {
                "id": "legacy-backfill-integration-private-id",
                "kind": "text",
                "note": "private legacy body",
            },
        )

        result = OwnerTruthLegacyBackfillPlanService(store, enabled=True).plan(context=context)

        self.assertEqual(result.outcome, "created")
        self.assertEqual(result.plan.authority_epoch, 0)
        self.assertEqual(result.plan.summary()["entryCount"], 1)
        self.assertEqual(result.plan.summary()["targetState"], "notCreated")


if __name__ == "__main__":
    unittest.main()
