import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SQL = ROOT / "db/migrations/0032_owner_truth_interview_topic_switch.sql"
MIGRATION_MANIFEST = ROOT / "db/migrations/0032_owner_truth_interview_topic_switch.json"


class OwnerTruthInterviewTopicSwitchMigrationContractTests(unittest.TestCase):
    def test_additive_default_off_topic_switch_only_extends_private_receipts(self) -> None:
        manifest = json.loads(MIGRATION_MANIFEST.read_text(encoding="utf-8"))
        sql = MIGRATION_SQL.read_text(encoding="utf-8")

        self.assertEqual(manifest["version"], "0032")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertFalse(manifest["releaseFlags"]["ownerTruthConversationV1"])
        self.assertFalse(manifest["releaseFlags"]["guidedInterviewM0A"])
        self.assertFalse(manifest["releaseFlags"]["ownerTruthInterviewTopicSwitchM0A"])
        self.assertIn("pauseInterviewForTopicSwitch", sql)
        self.assertIn("expected_thread_version IS NOT NULL", sql)
        self.assertIn("expected_session_version IS NOT NULL", sql)
        self.assertNotIn("INSERT INTO owner_truth.memory_candidates", sql)
        self.assertNotIn("INSERT INTO owner_truth.memories", sql)
        self.assertNotIn("INSERT INTO owner_truth.memory_versions", sql)


if __name__ == "__main__":
    unittest.main()
