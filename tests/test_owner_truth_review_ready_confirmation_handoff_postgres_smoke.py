from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/run-backend-owner-truth-review-ready-confirmation-handoff-postgres-smoke.sh"
SMOKE = ROOT / "scripts/backend-owner-truth-review-ready-confirmation-handoff-postgres-smoke.py"


class OwnerTruthReviewReadyConfirmationHandoffPostgresSmokeTests(unittest.TestCase):
    def test_smoke_is_formal_read_only_and_owner_vault_isolated(self) -> None:
        runner = RUNNER.read_text(encoding="utf-8")
        smoke = SMOKE.read_text(encoding="utf-8")

        self.assertIn("DREAMJOURNEY_OWNER_TRUTH_REVIEW_READY_HANDOFF_SMOKE=1 is required", runner)
        self.assertIn("OWNER_TRUTH_FORMAL_SMOKE_ADMIN_DATABASE_URL is required", runner)
        self.assertIn("FORMAL.create_database", smoke)
        self.assertIn("FORMAL.drop_database", smoke)
        self.assertIn("/candidate-proposal/status", smoke)
        self.assertIn("/interview-candidate-confirmations", smoke)
        self.assertIn("ownerTruthCandidateReview", smoke)
        self.assertIn("X-DreamJourney-QA-Owner-Truth", smoke)
        self.assertIn("release_policy_denied", smoke)
        self.assertIn("ownerTruthInterviewCandidateReviewDenied", smoke)
        self.assertIn('headers.get("cache-control") == "no-store"', smoke)
        self.assertIn("staleAndRedactedFiltered=true", smoke)
        self.assertIn("noDecisionReceiptMemoryVersionProjectionOrProviderEffect=true", smoke)
        self.assertIn("decisionReceipts", smoke)
        self.assertIn("memoryVersions", smoke)
        self.assertIn("memoryProjectionEntries", smoke)
        self.assertIn("providerEffects", smoke)
        self.assertNotIn("/confirmation/batch-accept", smoke)
        self.assertNotIn("/memory-activation", smoke)
        self.assertNotIn("OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = True", smoke)
        self.assertNotIn('os.environ.get("DATABASE_URL"', smoke)


if __name__ == "__main__":
    unittest.main()
