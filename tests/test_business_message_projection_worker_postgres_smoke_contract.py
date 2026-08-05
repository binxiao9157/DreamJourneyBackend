from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "scripts/backend-business-message-projection-worker-postgres-smoke.py"
RUNNER = ROOT / "scripts/run-backend-business-message-projection-worker-postgres-smoke.sh"


class BusinessMessageProjectionWorkerPostgresSmokeContractTests(unittest.TestCase):
    def test_disposable_smoke_keeps_the_worker_below_the_public_mailbox_boundary(self) -> None:
        source = SMOKE.read_text(encoding="utf-8")

        self.assertIn("business_message_projection_requests", source)
        self.assertIn("business_message_projections", source)
        self.assertIn("businessMessageProjectionWorkerDisabled", source)
        self.assertIn("businessMessageProjectionRetriesExhausted", source)
        self.assertIn("seed_verified_owner_inbox_bridge", source)
        self.assertIn("businessMessageProjectionInboxSnapshotMismatch", source)
        self.assertIn("BusinessMessageProjectionEnqueueCoordinator", source)
        self.assertIn("async_effects.dead_letters", source)
        self.assertIn("mailbox_letters", source)
        self.assertIn('table_count(dsn, "mailbox_letters") == 0', source)
        self.assertNotIn("insert_mailbox", source)
        self.assertNotIn("dispatch_notification", source)

    def test_smoke_uses_a_disposable_database_and_requires_an_explicit_database_url(self) -> None:
        source = SMOKE.read_text(encoding="utf-8")
        runner = RUNNER.read_text(encoding="utf-8")

        self.assertIn('database_name = f"dj_business_message_worker_', source)
        self.assertIn("drop_database(admin_dsn, database_name)", source)
        self.assertIn("DATABASE_URL is required", runner)
        self.assertIn(
            "scripts/backend-business-message-projection-worker-postgres-smoke.py",
            runner,
        )
        self.assertNotIn("python -m unittest", runner)


if __name__ == "__main__":
    unittest.main()
