from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SQL = ROOT / "db/migrations/0105_owner_truth_person_memory_projection_versions.sql"
MANIFEST = SQL.with_suffix(".json")


class OwnerTruthPersonMemoryProjectionMigrationContractTests(unittest.TestCase):
    def test_projection_history_is_additive_private_and_checkpoint_bound(self) -> None:
        sql = SQL.read_text(encoding="utf-8")

        self.assertIn("CREATE TABLE owner_truth.person_memory_projection_versions", sql)
        self.assertIn("REFERENCES owner_truth.memory_projection_checkpoints", sql)
        self.assertIn("CREATE UNIQUE INDEX owner_truth_person_memory_projection_one_current", sql)
        self.assertIn("WHERE is_current", sql)
        self.assertIn("validate_person_memory_projection_version", sql)
        self.assertIn("NEW.payload ->> 'modelVersion'", sql)
        self.assertIn("REVOKE ALL", sql)
        self.assertNotIn("UPDATE owner_truth.memory_versions", sql)
        self.assertNotIn("DELETE FROM owner_truth.memory_versions", sql)

    def test_manifest_requires_new_projection_worker_before_cutover(self) -> None:
        metadata = json.loads(MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual(metadata["version"], "0105")
        self.assertEqual(metadata["phase"], "expand")
        self.assertEqual(metadata["compatibility"], "additive")
        self.assertTrue(metadata["requiresOldWorkerDrain"])
        self.assertTrue(metadata["releaseFlags"]["ownerTruthMemoryProjectionWorker"])
        self.assertTrue(metadata["releaseFlags"]["ownerTruthPersonMemoryProfile"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
