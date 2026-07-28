from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SQL = ROOT / "db/migrations/0048_owner_truth_legacy_tail_shadow.sql"
MIGRATION_MANIFEST = MIGRATION_SQL.with_suffix(".json")


class OwnerTruthLegacyTailShadowMigrationContractTests(unittest.TestCase):
    def test_c04_shadow_schema_is_additive_append_only_and_side_effect_free(self) -> None:
        sql = MIGRATION_SQL.read_text(encoding="utf-8")
        manifest = json.loads(MIGRATION_MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual(manifest["version"], "0048")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertFalse(manifest["releaseFlags"]["legacyMigrationTailShadowV1"])
        self.assertIn("CREATE TABLE owner_truth.legacy_migration_tail_shadow_reports", sql)
        self.assertIn("CREATE TABLE owner_truth.legacy_migration_tail_shadow_mappings", sql)
        self.assertIn("owner_truth.legacy_migration_backfill_plans", sql)
        self.assertIn("owner_truth.legacy_migration_backfill_plan_entries", sql)
        self.assertIn("shadow_only = TRUE", sql)
        self.assertIn("effect_execution_count = 0", sql)
        self.assertIn("outbox_write_count = 0", sql)
        self.assertIn("object_storage_operation_count = 0", sql)
        self.assertIn("provider_call_count = 0", sql)
        self.assertIn("cutover_allowed = FALSE", sql)
        self.assertIn("legacy_writer_retired = FALSE", sql)
        self.assertIn("input_operation_count = mapping_count + duplicate_input_count", sql)
        self.assertIn("validate_legacy_migration_tail_shadow_mapping_count", sql)
        self.assertIn("DEFERRABLE INITIALLY DEFERRED", sql)
        self.assertIn("owner_truth_legacy_migration_tail_shadow_reports_no_update", sql)
        self.assertIn("owner_truth_legacy_migration_tail_shadow_mappings_no_delete", sql)
        self.assertIn("ON DELETE RESTRICT", sql)
        for forbidden in (
            "INSERT INTO async_effects",
            "INSERT INTO owner_truth.sources",
            "INSERT INTO owner_truth.memory_candidates",
            "INSERT INTO owner_truth.memory_versions",
            "UPDATE archive_items",
            "UPDATE memories",
            "UPDATE owner_truth.vaults",
            "DELETE FROM",
        ):
            self.assertNotIn(forbidden, sql)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
