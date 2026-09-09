import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SQL = ROOT / "db/migrations/0109_owner_truth_live_delivery_watermark.sql"
MIGRATION_MANIFEST = ROOT / "db/migrations/0109_owner_truth_live_delivery_watermark.json"


class OwnerTruthLiveDeliveryMigrationContractTests(unittest.TestCase):
    def test_additive_live_delivery_watermark_keeps_turns_private(self) -> None:
        manifest = json.loads(MIGRATION_MANIFEST.read_text(encoding="utf-8"))
        sql = MIGRATION_SQL.read_text(encoding="utf-8")

        self.assertEqual(manifest["version"], "0109")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertTrue(manifest["releaseFlags"]["ownerTruthLiveReliableDelivery"])
        self.assertIn("continuous_client_sequence", sql)
        self.assertIn("close_requested_client_sequence", sql)
        self.assertIn("client_sequence_number", sql)
        self.assertIn("captured_at", sql)
        self.assertIn("session_client_sequence_unique", sql)
        self.assertNotIn("INSERT INTO owner_truth.memory_candidates", sql)
        self.assertNotIn("INSERT INTO owner_truth.memories", sql)
        self.assertNotIn("INSERT INTO owner_truth.memory_versions", sql)


if __name__ == "__main__":
    unittest.main()
