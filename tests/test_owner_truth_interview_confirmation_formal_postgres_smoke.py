from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/run-backend-owner-truth-interview-confirmation-formal-postgres-smoke.sh"
SMOKE = ROOT / "scripts/backend-owner-truth-interview-confirmation-formal-postgres-smoke.py"


class OwnerTruthInterviewConfirmationFormalPostgresSmokeTests(unittest.TestCase):
    def test_runner_requires_an_explicit_database_and_keeps_formal_route_separate_from_qa(self) -> None:
        runner = RUNNER.read_text(encoding="utf-8")
        smoke = SMOKE.read_text(encoding="utf-8")

        self.assertIn('DREAMJOURNEY_OWNER_TRUTH_FORMAL_SMOKE=1 is required', runner)
        self.assertIn('OWNER_TRUTH_FORMAL_SMOKE_ADMIN_DATABASE_URL is required', runner)
        self.assertIn('formal-postgres-smoke.py', runner)
        self.assertIn('/confirmation/batch-accept', smoke)
        self.assertIn('CREATE DATABASE', smoke)
        self.assertIn('DROP DATABASE IF EXISTS', smoke)
        self.assertIn('X-DreamJourney-QA-Owner-Truth', smoke)
        self.assertIn('release_policy_denied', smoke)
        self.assertIn('authorization_evidence', smoke)
        self.assertIn('interview_review_batch_candidate_decision_receipts', smoke)
        self.assertIn('assert_wrong_child_command_link_is_rejected', smoke)
        self.assertIn('assert_concurrent_formal_replay_is_idempotent', smoke)
        self.assertIn('assert_second_receipt_link_failure_rolls_back', smoke)
        self.assertIn('ThreadPoolExecutor', smoke)
        self.assertIn('formal_smoke_reject_second_link', smoke)
        self.assertIn('concurrentCommandDeduplicated=true', smoke)
        self.assertIn('batchLinkFailureRolledBack=true', smoke)
        self.assertIn('apply_migrations_through', smoke)
        self.assertIn('assert_legacy_qa_root_survives_upgrade', smoke)
        self.assertIn('legacyQaUpgradeCompatible=true', smoke)
        self.assertIn('final_version="0035"', smoke)
        self.assertIn('candidate_command_id_hash', smoke)
        self.assertIn('DREAMJOURNEY_OWNER_TRUTH_FORMAL_SMOKE=1 is required', smoke)
        self.assertIn('OWNER_TRUTH_FORMAL_SMOKE_ADMIN_DATABASE_URL is required', smoke)
        self.assertNotIn('os.environ.get("DATABASE_URL"', smoke)
        self.assertNotIn('OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = True', smoke)


if __name__ == '__main__':
    unittest.main()
