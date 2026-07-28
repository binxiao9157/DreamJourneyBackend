from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SQL = ROOT / "db/migrations/0049_owner_truth_migration_parity_shadow.sql"
MIGRATION_MANIFEST = MIGRATION_SQL.with_suffix(".json")


class OwnerTruthMigrationParityShadowMigrationContractTests(unittest.TestCase):
    def test_c05_schema_is_additive_append_only_and_has_no_effect_capability(self) -> None:
        sql = MIGRATION_SQL.read_text(encoding="utf-8")
        manifest = json.loads(MIGRATION_MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual(manifest["version"], "0049")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertFalse(manifest["releaseFlags"]["migrationParityShadowV1"])
        self.assertIn("CREATE TABLE owner_truth.migration_parity_shadow_reports", sql)
        self.assertIn("CREATE TABLE owner_truth.migration_parity_shadow_mismatches", sql)
        self.assertIn("owner_truth.vaults", sql)
        self.assertIn("shadow_only = TRUE", sql)
        self.assertIn("command_effect_execution_count = 0", sql)
        self.assertIn("object_copy_execution_count = 0", sql)
        self.assertIn("provider_call_count = 0", sql)
        self.assertIn("provider_cost_charged = FALSE", sql)
        self.assertIn("write_operation_count = 0", sql)
        self.assertIn("cutover_allowed = FALSE", sql)
        self.assertIn("legacy_writer_retired = FALSE", sql)
        self.assertIn("validate_migration_parity_shadow_mismatch_count", sql)
        self.assertIn("DEFERRABLE INITIALLY DEFERRED", sql)
        self.assertIn("owner_truth_migration_parity_shadow_reports_no_update", sql)
        self.assertIn("owner_truth_migration_parity_shadow_mismatches_no_update", sql)
        self.assertIn("M01", sql)
        self.assertIn("M08", sql)
        self.assertIn("surface NOT IN ('command', 'objectCopy')", sql)
        for forbidden in (
            "INSERT INTO async_effects",
            "INSERT INTO owner_truth.sources",
            "INSERT INTO owner_truth.memory_candidates",
            "INSERT INTO owner_truth.memory_versions",
            "UPDATE owner_truth.vaults",
            "UPDATE archive_items",
            "UPDATE memories",
            "DELETE FROM",
        ):
            self.assertNotIn(forbidden, sql)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
