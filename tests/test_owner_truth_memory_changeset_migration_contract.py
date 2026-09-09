import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SQL = ROOT / "db/migrations/0108_owner_truth_memory_changesets.sql"
MIGRATION_MANIFEST = ROOT / "db/migrations/0108_owner_truth_memory_changesets.json"


class OwnerTruthMemoryChangeSetMigrationContractTests(unittest.TestCase):
    """Static migration contract; a disposable PostgreSQL run remains separate."""

    def test_additive_changeset_tables_bind_one_receipt_to_one_immutable_operation(self) -> None:
        manifest = json.loads(MIGRATION_MANIFEST.read_text(encoding="utf-8"))
        sql = MIGRATION_SQL.read_text(encoding="utf-8")

        self.assertEqual(manifest["version"], "0108")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertTrue(manifest["releaseFlags"]["ownerTruthEnhancedMemory"])
        self.assertIn("CREATE TABLE owner_truth.memory_revisions", sql)
        self.assertIn("CREATE TABLE owner_truth.memory_changesets", sql)
        self.assertIn("CREATE TABLE owner_truth.memory_changeset_operations", sql)
        self.assertIn("UNIQUE (vault_id, decision_receipt_id)", sql)
        self.assertIn("operation_index SMALLINT NOT NULL CHECK (operation_index = 1)", sql)
        self.assertIn("REFERENCES owner_truth.decision_receipts(vault_id, id)", sql)
        self.assertIn("REFERENCES owner_truth.memory_versions(vault_id, id)", sql)
        self.assertIn("CREATE TRIGGER owner_truth_memory_changesets_immutable", sql)
        self.assertIn("CREATE TRIGGER owner_truth_memory_changeset_operations_immutable", sql)
        self.assertNotIn("DROP TABLE", sql.upper())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
