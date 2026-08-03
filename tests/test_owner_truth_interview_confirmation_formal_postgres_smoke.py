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
        self.assertIn('/interview-candidate-confirmations', smoke)
        self.assertIn('/interview-memory-activation-inbox', smoke)
        self.assertIn('/interview-memory-projection-recovery-inbox', smoke)
        self.assertIn('/confirmation/candidates/{single_candidate_id}/decision', smoke)
        self.assertIn('/memory-activation', smoke)
        self.assertIn('CREATE DATABASE', smoke)
        self.assertIn('DROP DATABASE IF EXISTS', smoke)
        self.assertIn('X-DreamJourney-QA-Owner-Truth', smoke)
        self.assertIn('release_policy_denied', smoke)
        self.assertIn('authorization_evidence', smoke)
        self.assertIn('interview_review_batch_candidate_decision_receipts', smoke)
        self.assertIn('assert_non_confirmation_feature_evidence_is_rejected', smoke)
        self.assertIn('formal confirmation inbox policy', smoke)
        self.assertIn('formal inbox must not expose Candidate or Source content', smoke)
        self.assertIn('completed batch must not remain in the formal confirmation inbox', smoke)
        self.assertIn('formalConfirmationInboxContentFree=true', smoke)
        self.assertIn('formalConfirmationInboxCompletionFiltered=true', smoke)
        self.assertIn('wrongFeatureAuthorityRejected=true', smoke)
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
        self.assertIn('"0036", "0037"', smoke)
        self.assertIn('candidate_command_id_hash', smoke)
        self.assertIn('"contentSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION', smoke)
        self.assertIn(
            "payload_schema_version, payload\n"
            "                ) VALUES",
            smoke,
        )
        self.assertIn(
            "OWNER_TRUTH_SCHEMA_VERSION,\n"
            "                    Jsonb(candidate_payload),",
            smoke,
        )
        self.assertIn('memory_counts', smoke)
        self.assertIn('formalMemoryActivationCreated=true', smoke)
        self.assertIn('formalMemoryActivationReplayDeduplicated=true', smoke)
        self.assertIn('formalMemoryActivationInboxContentFree=true', smoke)
        self.assertIn('formalMemoryActivationInboxCompletionFiltered=true', smoke)
        self.assertIn('formalMemoryProjectionRecoveryInboxContentFree=true', smoke)
        self.assertIn('formalMemoryProjectionRecoveryInboxCompletionFiltered=true', smoke)
        self.assertIn('OwnerTruthMemoryProjectionWorkerRuntime', smoke)
        self.assertIn('formalMemoryActivationProjectionRebuilt=true', smoke)
        self.assertIn('formal activation must reach the typed compatibility projection worker', smoke)
        self.assertIn('OwnerTruthContextMaterializationService', smoke)
        self.assertIn('context_materialization_summary', smoke)
        self.assertIn('"authority"]["projectionCheckpoint"]', smoke)
        self.assertIn('formal activation must fail closed until its Projection is rebuilt', smoke)
        self.assertIn('formal activation must materialize exactly one confirmed Projection citation after rebuild', smoke)
        self.assertIn('formalMemoryActivationContextMaterialized=true', smoke)
        self.assertIn('formalSingleMemoryActivationCreated=true', smoke)
        self.assertIn('currentMigrationHeadReady=true', smoke)
        self.assertIn('DREAMJOURNEY_OWNER_TRUTH_FORMAL_SMOKE=1 is required', smoke)
        self.assertIn('OWNER_TRUTH_FORMAL_SMOKE_ADMIN_DATABASE_URL is required', smoke)
        self.assertNotIn('os.environ.get("DATABASE_URL"', smoke)
        self.assertNotIn('OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = True', smoke)


if __name__ == '__main__':
    unittest.main()
