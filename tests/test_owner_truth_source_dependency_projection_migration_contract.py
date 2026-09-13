from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SQL = ROOT / "db/migrations/0120_owner_truth_source_dependency_projection_invalidation.sql"
MANIFEST = SQL.with_suffix(".json")


class OwnerTruthSourceDependencyProjectionMigrationContractTests(unittest.TestCase):
    def test_unreviewed_source_insert_isolated_from_formal_projection(self) -> None:
        sql = SQL.read_text(encoding="utf-8")
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual(manifest["version"], "0120")
        self.assertFalse(manifest["production"]["rewritesUserFacts"])
        self.assertIn("IF TG_OP = 'INSERT' THEN", sql)
        self.assertIn("source_has_current_formal_dependency", sql)
        self.assertIn("version.is_current = TRUE", sql)
        self.assertIn("memory.status = 'active'", sql)
        self.assertIn("source_projection_rebuild_requests", sql)
        self.assertIn("state = 'rebuilding'", sql)
        self.assertNotIn("UPDATE owner_truth.memories", sql)
        self.assertNotIn("DELETE FROM owner_truth", sql)


if __name__ == "__main__":
    unittest.main()
