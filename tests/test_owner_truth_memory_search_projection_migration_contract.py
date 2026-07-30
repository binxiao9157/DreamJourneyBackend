from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SQL = ROOT / "db/migrations/0046_owner_truth_search_document_projection.sql"
MIGRATION_MANIFEST = MIGRATION_SQL.with_suffix(".json")
QUALIFICATION_FIX_SQL = (
    ROOT / "db/migrations/0064_owner_truth_search_document_checkpoint_qualification.sql"
)
QUALIFICATION_FIX_MANIFEST = QUALIFICATION_FIX_SQL.with_suffix(".json")


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

    def test_checkpoint_qualification_fix_preserves_the_immutable_base_migration(self) -> None:
        sql = QUALIFICATION_FIX_SQL.read_text(encoding="utf-8")
        manifest = json.loads(QUALIFICATION_FIX_MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual(manifest["version"], "0064")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertFalse(manifest["releaseFlags"]["ownerTruthMemorySearchProjectionQa"])
        self.assertFalse(manifest["releaseFlags"]["ownerTruthMemorySearchReadQa"])
        self.assertIn(
            "CREATE OR REPLACE FUNCTION owner_truth.validate_search_document_checkpoint()",
            sql,
        )
        self.assertIn(
            "FROM owner_truth.memory_projection_checkpoints AS checkpoint",
            sql,
        )
        self.assertIn("checkpoint.projection_source", sql)
        self.assertIn("v_projection_source", sql)
        self.assertNotIn(
            "SELECT owner_subject_id, projection_source, state, projection_hash",
            sql,
        )
        self.assertNotIn("UPDATE owner_truth.search_documents", sql)
        self.assertNotIn("DELETE FROM", sql)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
