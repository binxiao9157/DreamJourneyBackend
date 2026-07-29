from __future__ import annotations

import json
import unittest
from pathlib import Path

from app.db.migrator import default_migrations_dir, load_migrations


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SQL = ROOT / "db/migrations/0058_owner_truth_correction_resolution_currentness.sql"
MIGRATION_JSON = MIGRATION_SQL.with_suffix(".json")


class OwnerTruthCorrectionResolutionCurrentnessMigrationContractTests(unittest.TestCase):
    def test_replaces_final_trigger_validation_without_opening_the_feature(self) -> None:
        migration = next(
            item
            for item in load_migrations(default_migrations_dir())
            if item.version == "0058"
        )
        metadata = json.loads(MIGRATION_JSON.read_text(encoding="utf-8"))

        self.assertEqual(migration.name, "owner_truth_correction_resolution_currentness")
        self.assertEqual(migration.phase, "expand")
        self.assertEqual(migration.compatibility, "additive")
        self.assertEqual(
            metadata["runtimeCompatibility"],
            "ownerTruthV1CorrectionResolutionCurrentnessShadow",
        )
        self.assertFalse(metadata["releaseFlags"]["correctionResolverV1"])

        sql = MIGRATION_SQL.read_text(encoding="utf-8")
        self.assertIn("CREATE OR REPLACE FUNCTION owner_truth.validate_correction_resolution", sql)
        self.assertIn("predecessor_source_state IS DISTINCT FROM 'active'", sql)
        self.assertIn("correction_source_state IS DISTINCT FROM 'active'", sql)
        self.assertIn("predecessor_is_current IS DISTINCT FROM TRUE", sql)
        self.assertIn("FOR SHARE OF version, memory, source", sql)
        self.assertNotIn("CREATE TABLE owner_truth.correction_resolutions", sql)


if __name__ == "__main__":
    unittest.main()
