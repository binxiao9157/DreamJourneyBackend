from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SQL = ROOT / "db/migrations/0114_owner_truth_b_migration_dry_run.sql"
METADATA = ROOT / "db/migrations/0114_owner_truth_b_migration_dry_run.json"


class OwnerTruthBMigrationDryRunMigrationContractTests(unittest.TestCase):
    def test_migration_is_append_only_value_free_and_cannot_write_formal_memory(self) -> None:
        sql = SQL.read_text(encoding="utf-8")
        metadata = METADATA.read_text(encoding="utf-8")

        self.assertIn("owner_truth.b_migration_dry_run_reports", sql)
        self.assertIn("owner_truth.b_migration_dry_run_entries", sql)
        self.assertIn("formal_memory_write_count = 0", sql)
        self.assertIn("target_state = 'notCreated'", sql)
        self.assertIn("append-only", sql)
        self.assertNotIn("INSERT INTO owner_truth.memory_versions", sql)
        self.assertIn('"requiresOwnerReviewForSemanticChanges": true', metadata)
        self.assertIn('"requiresDerivedArtifactRevocationCheck": true', metadata)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
