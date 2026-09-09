from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SQL_PATH = ROOT / "db/migrations/0119_owner_truth_projection_revision_fence.sql"
MANIFEST_PATH = ROOT / "db/migrations/0119_owner_truth_projection_revision_fence.json"


class OwnerTruthProjectionRevisionFenceMigrationContractTests(unittest.TestCase):
    def test_projection_revision_fence_is_additive_and_fail_closed(self) -> None:
        sql = SQL_PATH.read_text(encoding="utf-8")
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

        self.assertEqual(manifest["version"], "0119")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertFalse(manifest["production"]["rewritesUserFacts"])
        self.assertTrue(manifest["production"]["invalidatesDerivedProjections"])
        self.assertIn("ADD COLUMN IF NOT EXISTS memory_revision", sql)
        self.assertIn("owner_truth.invalidate_memory_derivatives", sql)
        self.assertIn("owner_truth_memory_revisions_invalidate_derivatives", sql)
        self.assertIn("owner_truth_sources_invalidate_derivatives", sql)
        self.assertIn("owner_truth_memories_invalidate_derivatives", sql)
        self.assertIn("owner_truth_memory_versions_invalidate_derivatives", sql)
        self.assertIn("owner_truth_projection_rights_invalidate_derivatives", sql)
        self.assertIn("NEW.memory_revision IS DISTINCT FROM current_memory_revision", sql)
        self.assertIn("state = 'rebuilding'", sql)
        self.assertNotIn("DROP TABLE", sql.upper())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
