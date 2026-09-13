from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class WorkerActivationPostgresSmokeContractTests(unittest.TestCase):
    def test_smoke_uses_disposable_database_and_media_root(self) -> None:
        source = (
            ROOT / "scripts" / "backend-worker-activation-postgres-smoke.py"
        ).read_text(encoding="utf-8")

        self.assertIn("create_database(admin_dsn, database_name)", source)
        self.assertIn("drop_database(admin_dsn, database_name)", source)
        self.assertIn('os.environ["DATABASE_URL"] = test_dsn', source)
        self.assertIn('os.environ["OWNER_TRUTH_MEDIA_STORAGE_ROOT"] = media_root', source)
        self.assertIn("run_worker_activation_preflight", source)
        self.assertIn("if not spec.enabled(settings)", source)
        self.assertNotIn("DELETE FROM", source)


if __name__ == "__main__":
    unittest.main()
