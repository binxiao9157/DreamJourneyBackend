import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SQL = ROOT / "db/migrations/0031_owner_truth_interview_review_batches.sql"
MIGRATION_MANIFEST = ROOT / "db/migrations/0031_owner_truth_interview_review_batches.json"


class OwnerTruthInterviewReviewBatchMigrationContractTests(unittest.TestCase):
    def test_additive_default_off_review_boundary_never_promotes_owner_truth(self) -> None:
        manifest = json.loads(MIGRATION_MANIFEST.read_text(encoding="utf-8"))
        sql = MIGRATION_SQL.read_text(encoding="utf-8")

        self.assertEqual(manifest["version"], "0031")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertFalse(manifest["releaseFlags"]["ownerTruthConversationV1"])
        self.assertFalse(manifest["releaseFlags"]["guidedInterviewM0A"])
        self.assertFalse(manifest["releaseFlags"]["ownerTruthInterviewReviewBatchM0A"])
        self.assertIn("ADD COLUMN pending_review_batch_id", sql)
        self.assertIn("CREATE TABLE owner_truth.interview_review_batches", sql)
        self.assertIn("createInterviewReviewBatch", sql)
        self.assertIn("acknowledgeInterviewReviewBatch", sql)
        self.assertIn("captured_candidate_batch_turn_count", sql)
        self.assertIn("conversation_append_only", sql)
        self.assertNotIn("INSERT INTO owner_truth.memory_candidates", sql)
        self.assertNotIn("INSERT INTO owner_truth.memories", sql)
        self.assertNotIn("INSERT INTO owner_truth.memory_versions", sql)


if __name__ == "__main__":
    unittest.main()
