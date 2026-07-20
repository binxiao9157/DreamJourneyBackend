import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SQL = ROOT / "db/migrations/0030_owner_truth_interview_pacing_state.sql"
MIGRATION_MANIFEST = ROOT / "db/migrations/0030_owner_truth_interview_pacing_state.json"


class OwnerTruthInterviewPacingStateMigrationContractTests(unittest.TestCase):
    def test_additive_default_off_pacing_state_cannot_enable_interview_or_memory_writes(self) -> None:
        manifest = json.loads(MIGRATION_MANIFEST.read_text(encoding="utf-8"))
        sql = MIGRATION_SQL.read_text(encoding="utf-8")

        self.assertEqual(manifest["version"], "0030")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertFalse(manifest["releaseFlags"]["ownerTruthConversationV1"])
        self.assertFalse(manifest["releaseFlags"]["guidedInterviewM0A"])
        self.assertIn("ADD COLUMN deepening_turn_count", sql)
        self.assertIn("ADD COLUMN candidate_batch_turn_count", sql)
        self.assertIn("ADD COLUMN fatigue", sql)
        self.assertIn("recordInterviewPacing", sql)
        self.assertIn("candidate_batch_turn_count = turn_count", sql)
        self.assertNotIn("INSERT INTO owner_truth.memory_candidates", sql)
        self.assertNotIn("INSERT INTO owner_truth.memories", sql)
        self.assertNotIn("INSERT INTO owner_truth.memory_versions", sql)


if __name__ == "__main__":
    unittest.main()
