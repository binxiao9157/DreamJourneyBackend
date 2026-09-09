from __future__ import annotations

from pathlib import Path
import json
import unittest


ROOT = Path(__file__).resolve().parents[1]
SQL_PATH = ROOT / "db/migrations/0117_owner_truth_b_migration_execution.sql"
MANIFEST_PATH = ROOT / "db/migrations/0117_owner_truth_b_migration_execution.json"


class OwnerTruthBMigrationExecutionMigrationContractTests(unittest.TestCase):
    def test_schema_is_checkpointed_review_only_and_has_append_only_receipts(self) -> None:
        sql = SQL_PATH.read_text(encoding="utf-8")

        self.assertIn("b_migration_execution_runs", sql)
        self.assertIn("b_migration_execution_entries", sql)
        self.assertIn("b_migration_execution_receipts", sql)
        self.assertIn("sourceQueuedForReview", sql)
        self.assertIn("manualEvidenceReview", sql)
        self.assertIn("blockedRecordChanged", sql)
        self.assertIn("failedRetryable", sql)
        self.assertIn("REFERENCES owner_truth.b_migration_dry_run_reports", sql)
        self.assertIn("REFERENCES owner_truth.source_command_receipts", sql)
        self.assertIn("REFERENCES async_effects.operations", sql)
        self.assertIn("execution receipts are append-only", sql)
        self.assertNotIn("INSERT INTO owner_truth.memories", sql)
        self.assertNotIn("INSERT INTO owner_truth.memory_versions", sql)

    def test_manifest_requires_real_postgres_and_forbids_direct_formal_write(self) -> None:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

        self.assertEqual(manifest["version"], "0117")
        self.assertEqual(manifest["phase"], "expand")
        self.assertFalse(
            manifest["releaseFlags"]["ownerTruthBMigrationExecution"]
        )
        self.assertTrue(manifest["production"]["requiresDryRun"])
        self.assertTrue(manifest["production"]["requiresPostgresIntegrationEvidence"])
        self.assertFalse(manifest["production"]["allowsDirectFormalMemoryWrite"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
