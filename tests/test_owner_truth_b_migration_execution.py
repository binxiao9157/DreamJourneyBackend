from __future__ import annotations

import unittest
from uuid import uuid4

from app.domain.owner_truth.b_migration_execution import (
    OwnerTruthBMigrationExecutionConflict,
)
from app.domain.owner_truth.source_commands import (
    CreateTextSourceCommand,
    OwnerTruthCommandContext,
)
from app.services.in_memory_store import InMemoryStore
from app.services.owner_truth_b_migration_execution import (
    OwnerTruthBMigrationExecutionService,
    _persisted_execution_run_matches,
)
from app.services.owner_truth_legacy_migration import (
    LegacyMigrationLegacyRows,
    build_inventory_from_legacy_rows,
    build_replay_material_from_legacy_rows,
)
from app.services.owner_truth_source import OwnerTruthSourceCommandService


class OwnerTruthBMigrationExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryStore()
        self.owner_id = "owner-b-migration-execution"
        self.vault_id = "vault-b-migration-execution"
        self.context = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            actor_subject_id=self.owner_id,
        )
        OwnerTruthSourceCommandService(self.store).create_text_source(
            command=CreateTextSourceCommand(
                command_id="b-migration-execution-vault-seed",
                source_id=str(uuid4()),
                expected_version=0,
                text="seed only",
                metadata={},
            ),
            context=self.context,
        )

    def test_bounded_batches_resume_and_replay_without_duplicate_source_or_formal_memory(self) -> None:
        self.store.add_archive_item(
            self.owner_id,
            {
                "id": "legacy-archive-execution-1",
                "kind": "text",
                "title": "童年",
                "note": "我小时候住在山村。",
            },
        )
        self.store.add_memory(
            self.owner_id,
            {
                "id": "legacy-memory-execution-1",
                "summary": "我在二零一六年本科毕业。",
            },
        )
        service = OwnerTruthBMigrationExecutionService(self.store, enabled=True)

        first = service.execute_next_batch(context=self.context, batch_size=1)
        second = service.execute_next_batch(
            context=self.context,
            batch_size=1,
            report_id=first.report_id,
        )
        replay = service.execute_next_batch(
            context=self.context,
            batch_size=2,
            report_id=first.report_id,
        )

        self.assertEqual(first.status, "running")
        self.assertEqual(first.batch_processed_count, 1)
        self.assertEqual(second.status, "completed")
        self.assertEqual(second.checkpoint_ordinal, 2)
        self.assertEqual(
            second.disposition_counts,
            {"sourceQueuedForReview": 2},
        )
        self.assertEqual(replay.status, "completed")
        self.assertEqual(replay.batch_processed_count, 0)
        self.assertEqual(self.store.owner_truth_source_count(self.vault_id), 3)
        self.assertEqual(self.store.effect_kernel_repository().record_count(), 2)
        review_snapshot = self.store.owner_truth_candidate_review_repository().snapshot()
        self.assertEqual(review_snapshot["memoryActivations"], {})

    def test_retry_reuses_source_command_and_only_one_effect_survives(self) -> None:
        self.store.add_archive_item(
            self.owner_id,
            {
                "id": "legacy-archive-retry-1",
                "kind": "text",
                "note": "这段素材第一次投递任务失败，第二次应恢复。",
            },
        )
        effects = self.store.effect_kernel_repository()
        original_accept = effects.accept
        calls = {"count": 0}

        def fail_once(intent):
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("synthetic effect failure")
            return original_accept(intent)

        effects.accept = fail_once
        service = OwnerTruthBMigrationExecutionService(self.store, enabled=True)

        failed = service.execute_next_batch(context=self.context, batch_size=1)
        recovered = service.execute_next_batch(
            context=self.context,
            batch_size=1,
            report_id=failed.report_id,
        )

        self.assertEqual(failed.status, "running")
        self.assertEqual(failed.retryable_count, 1)
        self.assertEqual(recovered.status, "completed")
        self.assertEqual(recovered.retryable_count, 0)
        self.assertEqual(self.store.owner_truth_source_count(self.vault_id), 2)
        self.assertEqual(effects.record_count(), 1)

    def test_resume_rejects_changed_legacy_snapshot_before_another_source_is_created(self) -> None:
        self.store.add_archive_item(
            self.owner_id,
            {"id": "legacy-archive-stable", "kind": "text", "note": "稳定素材"},
        )
        self.store.add_memory(
            self.owner_id,
            {"id": "legacy-memory-to-change", "summary": "修改前"},
        )
        service = OwnerTruthBMigrationExecutionService(self.store, enabled=True)
        first = service.execute_next_batch(context=self.context, batch_size=1)
        self.store.add_memory(
            self.owner_id,
            {"id": "legacy-memory-added-after-dry-run", "summary": "新增记录"},
        )

        with self.assertRaises(OwnerTruthBMigrationExecutionConflict):
            service.execute_next_batch(
                context=self.context,
                batch_size=1,
                report_id=first.report_id,
            )

        self.assertEqual(self.store.owner_truth_source_count(self.vault_id), 2)

    def test_text_material_is_verbatim_and_non_text_payload_requires_manual_review(self) -> None:
        rows = LegacyMigrationLegacyRows(
            archive_items=(
                {
                    "id": "legacy-text-material",
                    "user_id": self.owner_id,
                    "owner_subject_id": self.owner_id,
                    "authority_state": "active",
                    "payload": {
                        "title": "求学",
                        "note": "我离开家乡去读书。",
                        "graph": {"assistantInference": "不得串入正文"},
                    },
                },
            ),
            memories=(
                {
                    "id": "legacy-structured-only",
                    "user_id": self.owner_id,
                    "owner_subject_id": self.owner_id,
                    "authority_state": "active",
                    "payload": {"graph": {"nodes": ["private"]}},
                },
            ),
        )
        inventory = build_inventory_from_legacy_rows(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            classifier_version="b-execution-test-v1",
            rows=rows,
        )
        entries = {entry.domain.value: entry for entry in inventory.entries}

        text = build_replay_material_from_legacy_rows(
            owner_subject_id=self.owner_id,
            domain=entries["archiveItem"].domain,
            legacy_id_hash=entries["archiveItem"].legacy_id_hash,
            record_hash=entries["archiveItem"].record_hash,
            rows=rows,
        )
        structured = build_replay_material_from_legacy_rows(
            owner_subject_id=self.owner_id,
            domain=entries["memory"].domain,
            legacy_id_hash=entries["memory"].legacy_id_hash,
            record_hash=entries["memory"].record_hash,
            rows=rows,
        )

        self.assertEqual(text.text, "求学\n\n我离开家乡去读书。")
        self.assertNotIn("assistantInference", text.text or "")
        self.assertEqual(structured.material_state, "manualEvidenceReview")
        self.assertIsNone(structured.text)

    def test_persisted_execution_accepts_zero_epoch_and_zero_entry_count(self) -> None:
        self.assertTrue(
            _persisted_execution_run_matches(
                {
                    "id": "run-zero",
                    "report_hash": "b" * 64,
                    "entry_count": 0,
                    "authority_epoch": 0,
                },
                expected_run_id="run-zero",
                expected_report_hash="b" * 64,
                expected_entry_count=0,
                expected_authority_epoch=0,
            )
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
