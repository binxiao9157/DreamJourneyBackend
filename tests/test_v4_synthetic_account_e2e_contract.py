import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "scripts/backend-owner-truth-media-processing-postgres-smoke.py"
GATE = ROOT / "scripts/run-backend-v4-synthetic-account-e2e-gate.sh"


class V4SyntheticAccountE2EContractTests(unittest.TestCase):
    def test_one_disposable_account_crosses_the_complete_m0_chain(self) -> None:
        smoke = SMOKE.read_text(encoding="utf-8")
        gate = GATE.read_text(encoding="utf-8")

        for route in (
            "/source-objects/upload-intents",
            "/candidates/{candidate_id}/decisions",
            "/context/build",
            "/auth/data-export/jobs",
            "/auth/data-export/jobs/{export_job_id}/download",
            "/source-objects/{source_object_id}/deletions",
        ):
            self.assertIn(route, smoke)
        for evidence in (
            '"exportJobCreated": True',
            '"exportOwnerTruthComplete": True',
            '"deletionProviderReceiptAccepted": True',
            '"physicalDeletionCompleted": physical_deletion_completed',
            '"deletedMediaExcludedFromContext": True',
        ):
            self.assertIn(evidence, smoke)
        self.assertIn("RUN_OWNER_TRUTH_MEDIA_PHYSICAL_DELETION_SMOKE=1", gate)
        self.assertIn("backend-owner-truth-media-processing-postgres-smoke.py", gate)
        self.assertIn("v4SyntheticAccountE2E", gate)
        self.assertIn("physicalDeletionCompleted", gate)


if __name__ == "__main__":
    unittest.main()
