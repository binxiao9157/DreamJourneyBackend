import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SQL = ROOT / "db/migrations/0063_owner_truth_interview_session_end.sql"
MIGRATION_MANIFEST = ROOT / "db/migrations/0063_owner_truth_interview_session_end.json"


class OwnerTruthInterviewSessionEndMigrationContractTests(unittest.TestCase):
    def test_additive_default_off_end_command_extends_only_private_receipts(self) -> None:
        manifest = json.loads(MIGRATION_MANIFEST.read_text(encoding="utf-8"))
        sql = MIGRATION_SQL.read_text(encoding="utf-8")

        self.assertEqual(manifest["version"], "0063")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertFalse(manifest["releaseFlags"]["ownerTruthConversationV1"])
        self.assertFalse(manifest["releaseFlags"]["ownerTruthInterviewSessionEndQA"])
        self.assertFalse(manifest["releaseFlags"]["publicEchoInterviewEnd"])
        self.assertIn("endInterviewSession", sql)
        self.assertIn("expected_thread_version IS NOT NULL", sql)
        self.assertIn("expected_session_version IS NOT NULL", sql)
        self.assertNotIn("conversation_messages", sql)
        self.assertNotIn("INSERT INTO owner_truth.memory_candidates", sql)
        self.assertNotIn("INSERT INTO owner_truth.memories", sql)
        self.assertNotIn("INSERT INTO owner_truth.memory_versions", sql)


if __name__ == "__main__":
    unittest.main()
