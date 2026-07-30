from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SQL = ROOT / "db/migrations/0069_owner_truth_thread_summary_projection_checkpoint.sql"
MIGRATION_MANIFEST = MIGRATION_SQL.with_suffix(".json")


class OwnerTruthThreadSummaryProjectionMigrationContractTests(unittest.TestCase):
    def test_checkpoint_is_additive_content_free_and_default_off(self) -> None:
        sql = MIGRATION_SQL.read_text(encoding="utf-8")
        manifest = json.loads(MIGRATION_MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual(manifest["version"], "0069")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertEqual(
            manifest["runtimeCompatibility"],
            "ownerTruthThreadSummaryProjectionDefaultOff",
        )
        self.assertFalse(manifest["releaseFlags"]["ownerTruthThreadSummaryProjectionQA"])
        self.assertIn(
            "CREATE TABLE owner_truth.thread_summary_projection_checkpoints",
            sql,
        )
        self.assertIn(
            "CREATE TABLE owner_truth.thread_summary_projection_threads",
            sql,
        )
        self.assertIn(
            "CREATE TABLE owner_truth.thread_summary_projection_anchors",
            sql,
        )
        self.assertIn("source_dimension_checkpoint", sql)
        self.assertIn("input_digest", sql)
        self.assertIn("projection_hash", sql)
        self.assertIn("owner_truth.vaults", sql)
        self.assertIn("ON DELETE CASCADE", sql)
        self.assertNotIn("ALTER TABLE owner_truth.memories", sql)
        self.assertNotIn("UPDATE owner_truth.memory_versions", sql)
        self.assertNotIn("transcript TEXT", sql)
        self.assertNotIn("narrative TEXT", sql)
        self.assertNotIn("payload JSONB", sql)
        self.assertNotIn("embedding", sql.lower())
        self.assertNotIn("provider_id", sql.lower())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
