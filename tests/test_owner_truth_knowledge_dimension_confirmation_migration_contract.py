from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SQL = ROOT / "db/migrations/0035_owner_truth_knowledge_dimension_confirmation_receipts.sql"
MIGRATION_MANIFEST = MIGRATION_SQL.with_suffix(".json")


class OwnerTruthKnowledgeDimensionConfirmationMigrationContractTests(unittest.TestCase):
    def test_receipts_are_additive_hash_bound_and_append_only(self) -> None:
        sql = MIGRATION_SQL.read_text(encoding="utf-8")
        manifest = json.loads(MIGRATION_MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual(manifest["version"], "0035")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertIn("CREATE TABLE owner_truth.knowledge_dimension_confirmation_receipts", sql)
        self.assertIn("bound_content_hash", sql)
        self.assertIn("UNIQUE (vault_id, command_id_hash)", sql)
        self.assertIn("UNIQUE (vault_id, memory_version_id, dimension)", sql)
        self.assertIn("version_is_current IS DISTINCT FROM TRUE", sql)
        self.assertIn("version_content_hash IS DISTINCT FROM NEW.bound_content_hash", sql)
        self.assertIn("ownerExplicitSelection", sql)
        self.assertIn("owner_truth_knowledge_dimension_confirmation_receipts_no_update", sql)
        self.assertIn("owner_truth_knowledge_dimension_confirmation_receipts_no_delete", sql)
        self.assertNotIn("payload JSONB", sql)
        self.assertNotIn("claim", sql)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
