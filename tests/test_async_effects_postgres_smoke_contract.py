import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/backend-async-effects-postgres-smoke.py"


class AsyncEffectsPostgresSmokeContractTests(unittest.TestCase):
    def test_smoke_is_disposable_and_covers_required_g2_boundaries(self):
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn('database_name = f"dj_async_effects_smoke_', source)
        self.assertIn("drop_database(admin_dsn, database_name)", source)
        self.assertIn("ThreadPoolExecutor(max_workers=2)", source)
        self.assertIn('outcomes == {"accepted", "deduplicated"}', source)
        self.assertIn("AsyncEffectConflict", source)
        self.assertIn("OwnerTruthSourceAsyncEffectCommandService", source)
        self.assertIn("source effect must create exactly one outbox event", source)
        self.assertIn("source must roll back when its effect request cannot commit", source)
        self.assertIn("force rollback", source)
        self.assertIn("def revert_terminal_operation", source)
        self.assertIn("revert_terminal_operation(cursor, intent.operation_id)", source)
        self.assertIn("terminal effect state must not revert", source)
        self.assertIn("business receipts must remain append-only", source)
        self.assertIn("kernel must not persist a payload body column", source)


if __name__ == "__main__":
    unittest.main()
