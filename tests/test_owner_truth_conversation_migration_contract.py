import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SQL = ROOT / "db/migrations/0029_owner_truth_conversation_session_bootstrap.sql"
MIGRATION_MANIFEST = ROOT / "db/migrations/0029_owner_truth_conversation_session_bootstrap.json"


class OwnerTruthConversationMigrationContractTests(unittest.TestCase):
    def test_additive_default_off_schema_keeps_messages_in_the_private_conversation_lane(self) -> None:
        manifest = json.loads(MIGRATION_MANIFEST.read_text(encoding="utf-8"))
        sql = MIGRATION_SQL.read_text(encoding="utf-8")

        self.assertEqual(manifest["version"], "0029")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertFalse(manifest["releaseFlags"]["ownerTruthConversationV1"])
        self.assertFalse(manifest["releaseFlags"]["guidedInterviewM0A"])
        for relation in (
            "owner_truth.conversation_threads",
            "owner_truth.interview_sessions",
            "owner_truth.conversation_messages",
            "owner_truth.conversation_command_receipts",
        ):
            self.assertIn(f"CREATE TABLE {relation}", sql)
        self.assertIn("owner_truth.bind_vault_authority", sql)
        self.assertIn("conversation_messages_no_update", sql)
        self.assertIn("conversation_messages_no_delete", sql)
        self.assertIn("conversation_command_receipts_no_update", sql)
        self.assertIn("conversation_command_receipts_no_delete", sql)
        self.assertIn("owner_truth_conversation_command_receipts_vault_command_unique", sql)
        self.assertNotIn("INSERT INTO owner_truth.memory_candidates", sql)
        self.assertNotIn("INSERT INTO owner_truth.memories", sql)
        self.assertNotIn("INSERT INTO owner_truth.memory_versions", sql)


if __name__ == "__main__":
    unittest.main()
