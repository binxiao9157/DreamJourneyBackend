from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/backend-owner-truth-dfx-postgres-load.py"
RUNNER = ROOT / "scripts/run-backend-owner-truth-dfx-postgres-load.sh"


class OwnerTruthDfxPostgresLoadContractTests(unittest.TestCase):
    def test_runner_is_bounded_disposable_and_explicitly_approved(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertTrue(RUNNER.is_file())
        for required in (
            "OWNER_TRUTH_DFX_POSTGRES_APPROVED",
            "OWNER_TRUTH_DFX_CAPACITY_ACK",
            "dj_terra_a_dfx_",
            "create_database(admin_dsn, database_name)",
            "drop_database(admin_dsn, database_name)",
            '"smoke": LoadProfile',
            '"baseline": LoadProfile',
            '"capacity": LoadProfile',
            "online_users=100",
            "retrieval_qps=20.0",
            "receipt_qps=5.0",
            "extraction_concurrency=3",
            "search_owner_count=300",
            "facts_per_search_owner=5_000",
        ):
            self.assertIn(required, source)

    def test_report_separates_database_and_real_model_evidence(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        for required in (
            '"evidenceClass": "realPostgresPgvectorNoExternalModel"',
            '"realDatabase": True',
            '"realPgvector": True',
            '"realModel": False',
            '"externalProviderCalls": 0',
            '"realModelCostMeasured": False',
            '"notProvenByThisReport"',
            '"productionCapacity"',
        ):
            self.assertIn(required, source)

    def test_required_service_metrics_and_budgets_are_present(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        for required in (
            'name="reliableConversationReceipt"',
            "latency_budget_ms=300",
            'name="authorizedInternalHybridRetrieval"',
            "latency_budget_ms=250",
            'name="livePreGeneratedSnapshotRead"',
            "latency_budget_ms=200",
            'name="atomicFormalFactReviewTransaction"',
            "budget_ms=500",
            'name="formalCommitToSearchVisibility"',
            "budget_ms=5_000",
            'name="shortSessionToCandidateVisible"',
            "budget_ms=30_000",
            'name": "asyncWorkerQueueDrain"',
            "OwnerTruthMemoryProjectionWorkerRuntime",
            "BusinessMessageProjectionWorkerRuntime",
            '"asyncBacklogByJobType"',
            '"queueGrowth"',
            '"databaseConnections"',
        ):
            self.assertIn(required, source)

        self.assertIn('name="formalReviewToSearchWorkflow"', source)
        self.assertIn('"measuredFromEachCommit": True', source)
        self.assertNotIn("review_completion_times", source)
        self.assertEqual(source.count("OwnerTruthMemoryProjectionService(store).rebuild"), 1)

    def test_report_does_not_emit_connection_or_private_payload_values(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn('"databaseUrl"', source)
        self.assertNotIn('"dsn"', source)
        self.assertNotIn('"sourceText"', source)
        self.assertNotIn('"apiKey"', source)


if __name__ == "__main__":
    unittest.main()
