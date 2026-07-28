from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SQL = ROOT / "db/migrations/0046_owner_truth_search_document_projection.sql"
MIGRATION_MANIFEST = MIGRATION_SQL.with_suffix(".json")


class OwnerTruthMemorySearchProjectionMigrationContractTests(unittest.TestCase):
    def test_search_documents_are_additive_private_and_checkpoint_bound(self) -> None:
        sql = MIGRATION_SQL.read_text(encoding="utf-8")
        manifest = json.loads(MIGRATION_MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual(manifest["version"], "0046")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertFalse(manifest["releaseFlags"]["ownerTruthMemorySearchProjectionQa"])
        self.assertFalse(manifest["releaseFlags"]["ownerTruthMemorySearchReadQa"])
        self.assertIn("CREATE TABLE owner_truth.search_document_checkpoints", sql)
        self.assertIn("CREATE TABLE owner_truth.search_documents", sql)
        self.assertIn("source_projection_checkpoint", sql)
        self.assertIn("document_hash", sql)
        self.assertIn("owner_truth.memory_projection_entries", sql)
        self.assertIn("owner truth search document is not derived from a current projection entry", sql)
        self.assertIn("ON DELETE RESTRICT", sql)
        self.assertNotIn("ALTER TABLE memories", sql)
        self.assertNotIn("UPDATE owner_truth.memory_versions", sql)
        self.assertNotIn("vector(", sql.lower())
        self.assertNotIn("embedding", sql.lower())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
