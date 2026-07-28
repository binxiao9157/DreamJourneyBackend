from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SQL = ROOT / "db/migrations/0047_owner_truth_legacy_backfill_admission_plan.sql"
MIGRATION_MANIFEST = MIGRATION_SQL.with_suffix(".json")


class OwnerTruthLegacyBackfillMigrationContractTests(unittest.TestCase):
    def test_c03_plan_is_additive_append_only_and_never_writes_authority(self) -> None:
        sql = MIGRATION_SQL.read_text(encoding="utf-8")
        manifest = json.loads(MIGRATION_MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual(manifest["version"], "0047")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertFalse(manifest["releaseFlags"]["legacyMigrationBackfillAdmissionV1"])
        self.assertIn("CREATE TABLE owner_truth.legacy_migration_backfill_plans", sql)
        self.assertIn("CREATE TABLE owner_truth.legacy_migration_backfill_plan_entries", sql)
        self.assertIn("owner_truth.legacy_migration_runs", sql)
        self.assertIn("owner_truth.legacy_migration_entries", sql)
        self.assertIn("target_state = 'notCreated'", sql)
        self.assertIn("requireIndependentLineageReplay", sql)
        self.assertIn("owner_truth_legacy_migration_backfill_plans_no_update", sql)
        self.assertIn("owner_truth_legacy_migration_backfill_plan_entries_no_delete", sql)
        self.assertIn("ON DELETE RESTRICT", sql)
        self.assertNotIn("ALTER TABLE archive_items", sql)
        self.assertNotIn("ALTER TABLE memories", sql)
        self.assertNotIn("UPDATE archive_items", sql)
        self.assertNotIn("UPDATE memories", sql)
        self.assertNotIn("INSERT INTO owner_truth.sources", sql)
        self.assertNotIn("INSERT INTO owner_truth.memory_candidates", sql)
        self.assertNotIn("INSERT INTO owner_truth.memory_versions", sql)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
