from __future__ import annotations

import unittest

from app.domain.owner_truth.legacy_migration import LegacyMigrationDomain
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_legacy_migration import (
    InMemoryOwnerTruthLegacyMigrationRepository,
    LegacyMigrationLegacyRows,
    OwnerTruthLegacyMigrationAccessDenied,
    OwnerTruthLegacyMigrationInventoryService,
    OwnerTruthLegacyMigrationUnavailable,
)


class _InventoryStore:
    def __init__(self, repository: InMemoryOwnerTruthLegacyMigrationRepository) -> None:
        self._repository = repository

    def owner_truth_legacy_migration_repository(self):
        return self._repository


class OwnerTruthLegacyMigrationInventoryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = LegacyMigrationLegacyRows(
            archive_items=(
                {
                    "id": "archive-private-001",
                    "user_id": "owner-a",
                    "owner_subject_id": "owner-a",
                    "authority_state": "active",
                    "payload": {"note": "不应出现在盘点报告中的档案正文"},
                },
                {
                    "id": "archive-quarantined-002",
                    "user_id": "owner-a",
                    "owner_subject_id": "owner-a",
                    "authority_state": "quarantined",
                    "payload": {"note": "冲突归属的私有内容"},
                },
            ),
            memories=(
                {
                    "id": "memory-private-001",
                    "user_id": "owner-a",
                    "owner_subject_id": "owner-a",
                    "authority_state": "active",
                    "payload": {"summary": "旧记忆没有终态决策"},
                },
            ),
            kb_snapshots=(
                {
                    "user_id": "owner-a",
                    "revision": 3,
                    "graph": {"facts": ["旧图谱正文不能泄漏"]},
                    "updated_at": "2026-07-19T00:00:00+00:00",
                },
            ),
            kb_changes=(
                {
                    "user_id": "owner-a",
                    "revision": 3,
                    "operation_id": "kb-operation-private-003",
                    "graph": {"facts": ["图谱变更正文"]},
                    "mutation": {"op": "replace"},
                    "created_at": "2026-07-19T00:00:01+00:00",
                },
            ),
            kb_receipts=(
                {
                    "user_id": "owner-a",
                    "operation_id": "kb-receipt-private-003",
                    "operation_kind": "sync",
                    "schema_version": 1,
                    "payload_hash": "a" * 64,
                    "result": {"private": "receipt result"},
                    "created_at": "2026-07-19T00:00:02+00:00",
                },
            ),
        )
        self.repository = InMemoryOwnerTruthLegacyMigrationRepository(
            row_supplier=lambda _owner: self.rows,
        )
        self.service = OwnerTruthLegacyMigrationInventoryService(
            _InventoryStore(self.repository),
            enabled=True,
        )
        self.context = OwnerTruthCommandContext(
            vault_id="vault-a",
            owner_subject_id="owner-a",
            actor_subject_id="owner-a",
        )

    def test_inventory_is_value_free_deterministic_and_replay_safe(self) -> None:
        created = self.service.inventory(context=self.context)
        replayed = self.service.inventory(context=self.context)
        summary = created.public_summary()

        self.assertEqual(created.outcome, "created")
        self.assertEqual(replayed.outcome, "deduplicated")
        self.assertEqual(created.run_id, replayed.run_id)
        self.assertEqual(summary["inventory"]["entryCount"], 6)
        self.assertEqual(
            summary["inventory"]["classificationCounts"],
            {
                "needs_review": 1,
                "observed_candidate": 4,
                "quarantine": 1,
            },
        )
        checkpoint_domains = {checkpoint["domain"] for checkpoint in summary["checkpoints"]}
        self.assertEqual(checkpoint_domains, {domain.value for domain in LegacyMigrationDomain})
        conversation = next(
            checkpoint
            for checkpoint in summary["checkpoints"]
            if checkpoint["domain"] == LegacyMigrationDomain.CONVERSATION_CACHE.value
        )
        self.assertEqual(conversation["availability"], "unavailable")
        self.assertEqual(conversation["entryCount"], 0)
        rendered = str(summary)
        self.assertNotIn("不应出现在盘点报告中的档案正文", rendered)
        self.assertNotIn("旧图谱正文不能泄漏", rendered)
        self.assertNotIn("archive-private-001", rendered)
        self.assertNotIn("kb-operation-private-003", rendered)

    def test_changed_legacy_body_creates_a_new_checkpoint_hash(self) -> None:
        initial = self.service.inventory(context=self.context)
        self.rows = LegacyMigrationLegacyRows(
            archive_items=self.rows.archive_items,
            memories=self.rows.memories,
            kb_snapshots=(
                {
                    **self.rows.kb_snapshots[0],
                    "graph": {"facts": ["同一 revision 但内容被篡改"]},
                },
            ),
            kb_changes=self.rows.kb_changes,
            kb_receipts=self.rows.kb_receipts,
        )

        changed = self.service.inventory(context=self.context)

        self.assertEqual(changed.outcome, "created")
        self.assertNotEqual(initial.run_id, changed.run_id)
        self.assertNotEqual(initial.inventory.inventory_hash, changed.inventory.inventory_hash)
        self.assertEqual(self.repository.snapshot()["runCount"], 2)

    def test_non_owner_and_disabled_inventory_fail_closed(self) -> None:
        non_owner = OwnerTruthCommandContext(
            vault_id=self.context.vault_id,
            owner_subject_id=self.context.owner_subject_id,
            actor_subject_id="other-subject",
        )
        with self.assertRaises(OwnerTruthLegacyMigrationAccessDenied):
            self.service.inventory(context=non_owner)

        disabled = OwnerTruthLegacyMigrationInventoryService(
            _InventoryStore(self.repository),
            enabled=False,
        )
        with self.assertRaises(OwnerTruthLegacyMigrationUnavailable):
            disabled.inventory(context=self.context)


if __name__ == "__main__":
    unittest.main()
