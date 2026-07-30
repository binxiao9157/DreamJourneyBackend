from __future__ import annotations

import json
import unittest
from pathlib import Path

from app.db.migrator import default_migrations_dir, load_migrations


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SQL = (
    ROOT / "db/migrations/0065_owner_truth_correction_resolution_corrected_currentness.sql"
)
MIGRATION_JSON = MIGRATION_SQL.with_suffix(".json")


class OwnerTruthCorrectionResolutionCorrectedCurrentnessMigrationContractTests(
    unittest.TestCase
):
    def test_keeps_active_source_fences_and_uses_decision_aware_currentness(self) -> None:
        migration = next(
            item
            for item in load_migrations(default_migrations_dir())
            if item.version == "0065"
        )
        metadata = json.loads(MIGRATION_JSON.read_text(encoding="utf-8"))

        self.assertEqual(
            migration.name,
            "owner_truth_correction_resolution_corrected_currentness",
        )
        self.assertEqual(migration.phase, "expand")
        self.assertEqual(migration.compatibility, "additive")
        self.assertEqual(
            metadata["runtimeCompatibility"],
            "ownerTruthV1CorrectionResolutionCorrectedCurrentnessShadow",
        )
        self.assertFalse(metadata["releaseFlags"]["correctionResolverV1"])

        sql = MIGRATION_SQL.read_text(encoding="utf-8")
        self.assertIn("CREATE OR REPLACE FUNCTION owner_truth.validate_correction_resolution", sql)
        self.assertIn("predecessor_source_state IS DISTINCT FROM 'active'", sql)
        self.assertIn("correction_source_state IS DISTINCT FROM 'active'", sql)
        self.assertIn("IF NEW.decision = 'rejected' THEN", sql)
        self.assertIn(
            "owner truth rejected correction resolution requires a current predecessor",
            sql,
        )
        self.assertIn("predecessor_is_current IS DISTINCT FROM FALSE", sql)
        self.assertNotIn(
            "OR predecessor_is_current IS DISTINCT FROM TRUE\n        OR predecessor_source_owner_subject_id",
            sql,
        )
        self.assertNotIn("CREATE TABLE owner_truth.correction_resolutions", sql)
        self.assertNotIn("UPDATE owner_truth.memory_versions", sql)


if __name__ == "__main__":
    unittest.main()
