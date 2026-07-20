import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SQL = ROOT / "db/migrations/0034_owner_truth_interview_candidate_batch_decisions.sql"
MIGRATION_MANIFEST = ROOT / "db/migrations/0034_owner_truth_interview_candidate_batch_decisions.json"


class OwnerTruthInterviewCandidateBatchDecisionMigrationContractTests(unittest.TestCase):
    def test_additive_default_off_batch_decision_only_records_root_command_boundary(self) -> None:
        manifest = json.loads(MIGRATION_MANIFEST.read_text(encoding="utf-8"))
        sql = MIGRATION_SQL.read_text(encoding="utf-8")

        self.assertEqual(manifest["version"], "0034")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertFalse(manifest["releaseFlags"]["ownerTruthConversationV1"])
        self.assertFalse(manifest["releaseFlags"]["guidedInterviewM0A"])
        self.assertFalse(manifest["releaseFlags"]["ownerTruthInterviewCandidateProposalM0A"])
        self.assertFalse(manifest["releaseFlags"]["ownerTruthInterviewCandidateBatchDecisionM0A"])
        self.assertIn(
            "CREATE TABLE owner_truth.interview_review_batch_candidate_decisions",
            sql,
        )
        self.assertIn("UNIQUE (vault_id, command_id_hash)", sql)
        self.assertIn("selection_count BETWEEN 1 AND 50", sql)
        self.assertIn("batch_state IS DISTINCT FROM 'acknowledged'", sql)
        self.assertIn("owner_truth.conversation_append_only", sql)
        self.assertNotIn("INSERT INTO owner_truth.memory_candidates", sql)
        self.assertNotIn("INSERT INTO owner_truth.decision_receipts", sql)
        self.assertNotIn("INSERT INTO owner_truth.memories", sql)
        self.assertNotIn("INSERT INTO owner_truth.memory_versions", sql)


if __name__ == "__main__":
    unittest.main()
