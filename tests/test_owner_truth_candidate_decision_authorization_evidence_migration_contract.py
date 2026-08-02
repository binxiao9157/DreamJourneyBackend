import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SQL = (
    ROOT / "db/migrations/0071_owner_truth_candidate_decision_authorization_evidence.sql"
)
MIGRATION_MANIFEST = (
    ROOT / "db/migrations/0071_owner_truth_candidate_decision_authorization_evidence.json"
)


class OwnerTruthCandidateDecisionAuthorizationEvidenceMigrationContractTests(
    unittest.TestCase
):
    def test_additive_evidence_stays_on_immutable_decision_receipt(self) -> None:
        manifest = json.loads(MIGRATION_MANIFEST.read_text(encoding="utf-8"))
        sql = MIGRATION_SQL.read_text(encoding="utf-8")

        self.assertEqual(manifest["version"], "0071")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertFalse(manifest["releaseFlags"]["ownerTruthCandidateReview"])
        self.assertFalse(manifest["releaseFlags"]["ownerTruthCandidateReviewQa"])
        self.assertIn("ALTER TABLE owner_truth.decision_receipts", sql)
        self.assertIn("ADD COLUMN IF NOT EXISTS authorization_evidence JSONB", sql)
        self.assertIn("ownerTruthCandidateReview", sql)
        self.assertIn("owner-truth-command-authorization-capture-v1", sql)
        self.assertIn("CREATE TRIGGER owner_truth_decision_receipts_auth_evidence_validate", sql)
        self.assertNotIn("ALTER TABLE owner_truth.sources", sql)
        self.assertNotIn("ALTER TABLE owner_truth.memory_candidates", sql)
        self.assertNotIn("ALTER TABLE owner_truth.memory_versions", sql)


if __name__ == "__main__":
    unittest.main()
