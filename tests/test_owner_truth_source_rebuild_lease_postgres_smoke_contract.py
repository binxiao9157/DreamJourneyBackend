from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class OwnerTruthSourceRebuildLeasePostgresSmokeContractTests(unittest.TestCase):
    def test_runner_requires_explicit_database_url_and_uses_disposable_database(self) -> None:
        runner = (
            ROOT
            / "scripts"
            / "run-backend-owner-truth-source-rebuild-lease-postgres-smoke.sh"
        ).read_text(encoding="utf-8")
        source = (
            ROOT
            / "scripts"
            / "backend-owner-truth-source-rebuild-lease-postgres-smoke.py"
        ).read_text(encoding="utf-8")

        self.assertIn("DATABASE_URL is required", runner)
        self.assertIn("create_database(admin_dsn, database_name)", source)
        self.assertIn("drop_database(admin_dsn, database_name)", source)
        self.assertIn("verify_repeated_crash_exhaustion", source)
        self.assertIn("verify_concurrent_recovery", source)
        self.assertIn("OwnerTruthSourceProjectionRebuildLeaseLost", source)
        self.assertNotIn("DELETE FROM owner_truth.source_projection_rebuild_requests", source)


if __name__ == "__main__":
    unittest.main()
