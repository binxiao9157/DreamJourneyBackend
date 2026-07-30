import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SQL = (
    ROOT / "db/migrations/0067_owner_truth_interview_candidate_proposal_authorization_evidence.sql"
)
MIGRATION_MANIFEST = (
    ROOT / "db/migrations/0067_owner_truth_interview_candidate_proposal_authorization_evidence.json"
)
POSTGRES_SMOKE = ROOT / "scripts/backend-owner-truth-conversation-postgres-smoke.py"


class OwnerTruthInterviewCandidateProposalAuthorizationEvidenceMigrationContractTests(
    unittest.TestCase
):
    def test_additive_authorization_evidence_stays_on_admission_ledger_only(self) -> None:
        manifest = json.loads(MIGRATION_MANIFEST.read_text(encoding="utf-8"))
        sql = MIGRATION_SQL.read_text(encoding="utf-8")

        self.assertEqual(manifest["version"], "0067")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertFalse(manifest["releaseFlags"]["ownerTruthCandidateReview"])
        self.assertFalse(manifest["releaseFlags"]["ownerTruthCandidateReviewQa"])
        self.assertIn(
            "ALTER TABLE owner_truth.interview_review_batch_candidate_admissions",
            sql,
        )
        self.assertIn("ADD COLUMN authorization_evidence JSONB NOT NULL DEFAULT '{}'::JSONB", sql)
        self.assertIn("ownerTruthCandidateReview", sql)
        self.assertIn("owner-truth-command-authorization-capture-v1", sql)
        self.assertIn("CREATE TRIGGER owner_truth_interview_candidate_proposal_auth_evidence_validate", sql)
        self.assertNotIn("ALTER TABLE owner_truth.sources", sql)
        self.assertNotIn("async_effects.operations", sql)
        self.assertNotIn("memory_candidates", sql)
        self.assertNotIn("memory_versions", sql)

    def test_disposable_postgres_smoke_proves_formal_evidence_persistence(self) -> None:
        smoke = POSTGRES_SMOKE.read_text(encoding="utf-8")

        self.assertIn("formal_candidate_proposal_context", smoke)
        self.assertIn("formal_candidate_proposal_retry_context", smoke)
        self.assertIn("authorization_evidence", smoke)
        self.assertIn("formal candidate proposal admission must persist its release-policy feature", smoke)
        self.assertIn("formal admission evidence must not be copied to Source metadata", smoke)
        self.assertIn("ownerTruthCandidateReview", smoke)


if __name__ == "__main__":
    unittest.main()
