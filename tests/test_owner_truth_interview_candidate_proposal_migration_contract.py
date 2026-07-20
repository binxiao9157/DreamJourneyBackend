import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SQL = ROOT / "db/migrations/0033_owner_truth_interview_candidate_proposal_admission.sql"
MIGRATION_MANIFEST = ROOT / "db/migrations/0033_owner_truth_interview_candidate_proposal_admission.json"


class OwnerTruthInterviewCandidateProposalMigrationContractTests(unittest.TestCase):
    def test_additive_default_off_admission_requires_acknowledged_batch_and_conversation_source(self) -> None:
        manifest = json.loads(MIGRATION_MANIFEST.read_text(encoding="utf-8"))
        sql = MIGRATION_SQL.read_text(encoding="utf-8")

        self.assertEqual(manifest["version"], "0033")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertFalse(manifest["releaseFlags"]["ownerTruthConversationV1"])
        self.assertFalse(manifest["releaseFlags"]["guidedInterviewM0A"])
        self.assertFalse(manifest["releaseFlags"]["ownerTruthInterviewCandidateProposalM0A"])
        self.assertIn("CREATE TABLE owner_truth.interview_review_batch_candidate_admissions", sql)
        self.assertIn("batch_state IS DISTINCT FROM 'acknowledged'", sql)
        self.assertIn("source_kind_value IS DISTINCT FROM 'conversation'", sql)
        self.assertIn("owner_truth.conversation_append_only", sql)
        self.assertNotIn("INSERT INTO owner_truth.memory_candidates", sql)
        self.assertNotIn("INSERT INTO owner_truth.decision_receipts", sql)
        self.assertNotIn("INSERT INTO owner_truth.memories", sql)
        self.assertNotIn("INSERT INTO owner_truth.memory_versions", sql)


if __name__ == "__main__":
    unittest.main()
