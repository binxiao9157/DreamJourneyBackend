from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "scripts/backend-owner-truth-thread-summary-projection-postgres-smoke.py"
RUNNER = ROOT / "scripts/run-backend-owner-truth-thread-summary-projection-postgres-smoke.sh"
GATE = ROOT / "scripts/run-backend-owner-truth-thread-summary-projection-gate.sh"


class OwnerTruthThreadSummaryProjectionPostgresSmokeContractTests(unittest.TestCase):
    def test_smoke_is_isolated_and_covers_checkpoint_currentness(self) -> None:
        source = SMOKE.read_text(encoding="utf-8")
        runner = RUNNER.read_text(encoding="utf-8")
        gate = GATE.read_text(encoding="utf-8")

        self.assertIn('database_name = f"dj_thread_summary_projection_smoke_', source)
        self.assertIn("drop_database(admin_dsn, database_name)", source)
        self.assertIn("PostgresMigrator", source)
        self.assertIn("TestClient(main_module.app)", source)
        self.assertIn("thread-summary-projections/rebuild", source)
        self.assertIn("thread-summary-projections/read", source)
        self.assertIn("checkpoint_counts", source)
        self.assertIn("invalidate_saved_cue_session", source)
        self.assertIn("cross-owner checkpoint reads must be denied", source)
        self.assertIn("thread-summary checkpoint route must remain independently default-off", source)
        self.assertIn("DATABASE_URL is required", runner)
        self.assertIn("RUN_OWNER_TRUTH_THREAD_SUMMARY_PROJECTION_POSTGRES_SMOKE", gate)
        self.assertIn("test_owner_truth_thread_summary_projection", gate)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
