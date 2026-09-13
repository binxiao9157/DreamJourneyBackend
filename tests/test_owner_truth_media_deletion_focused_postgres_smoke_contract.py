from __future__ import annotations

from pathlib import Path
import unittest


ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPT = ROOT_DIR / "scripts" / "backend-owner-truth-media-deletion-focused-postgres-smoke.py"
RUNNER = ROOT_DIR / "scripts" / "run-backend-owner-truth-media-deletion-focused-postgres-smoke.sh"


class OwnerTruthMediaDeletionFocusedPostgresSmokeContractTests(unittest.TestCase):
    def test_smoke_uses_disposable_database_and_filesystem_root(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("create_database(admin_dsn, database_name)", source)
        self.assertIn("drop_database(admin_dsn, database_name)", source)
        self.assertIn("TemporaryDirectory", source)
        self.assertIn("FilesystemPrivateMediaObjectStore", source)

    def test_smoke_runs_the_real_worker_and_checks_terminal_state(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("OwnerTruthMediaDeletionWorkerRuntime", source)
        self.assertIn('result.get("deletionStatus") == "completed"', source)
        self.assertIn('current["accessState"] == "accessRevoked"', source)
        self.assertIn('worker.run_once().get("status") == "idle"', source)

    def test_runner_requires_a_database_url(self) -> None:
        source = RUNNER.read_text(encoding="utf-8")
        self.assertIn('${DATABASE_URL:?DATABASE_URL is required}', source)
        self.assertIn("backend-owner-truth-media-deletion-focused-postgres-smoke.py", source)


if __name__ == "__main__":
    unittest.main()
