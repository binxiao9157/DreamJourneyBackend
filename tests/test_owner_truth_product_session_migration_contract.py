import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SQL = ROOT / "db/migrations/0110_owner_truth_product_session_isolation.sql"
MIGRATION_MANIFEST = ROOT / "db/migrations/0110_owner_truth_product_session_isolation.json"


class OwnerTruthProductSessionMigrationContractTests(unittest.TestCase):
    def test_product_session_migration_is_additive_and_keeps_legacy_lane_isolated(self) -> None:
        manifest = json.loads(MIGRATION_MANIFEST.read_text(encoding="utf-8"))
        sql = MIGRATION_SQL.read_text(encoding="utf-8")

        self.assertEqual(manifest["version"], "0110")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertTrue(manifest["releaseFlags"]["ownerTruthProductSessionIsolation"])
        self.assertTrue(manifest["production"]["requiresPostgresIntegrationEvidence"])
        self.assertIn("product_session_id", sql)
        self.assertIn("one_active_natural_per_vault", sql)
        self.assertIn("one_active_per_product_session", sql)
        self.assertIn("UPDATE owner_truth.interview_sessions", sql)
        self.assertNotIn("DELETE FROM", sql)
        self.assertNotIn("INSERT INTO owner_truth.memory_candidates", sql)


if __name__ == "__main__":
    unittest.main()
