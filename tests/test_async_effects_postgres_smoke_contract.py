import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/backend-async-effects-postgres-smoke.py"
LEASE_REPOSITORY = ROOT / "app/async_effects/lease_repository.py"
SCHEDULER_REPOSITORY = ROOT / "app/async_effects/scheduler_repository.py"


class AsyncEffectsPostgresSmokeContractTests(unittest.TestCase):
    def test_smoke_is_disposable_and_covers_required_g2_boundaries(self):
        source = SCRIPT.read_text(encoding="utf-8")
        lease_source = LEASE_REPOSITORY.read_text(encoding="utf-8")
        scheduler_source = SCHEDULER_REPOSITORY.read_text(encoding="utf-8")

        self.assertIn('database_name = f"dj_async_effects_smoke_', source)
        self.assertIn("drop_database(admin_dsn, database_name)", source)
        self.assertIn("ThreadPoolExecutor(max_workers=2)", source)
        self.assertIn('outcomes == {"accepted", "deduplicated"}', source)
        self.assertIn("AsyncEffectConflict", source)
        self.assertIn("OwnerTruthSourceAsyncEffectCommandService", source)
        self.assertIn("source effect must create exactly one outbox event", source)
        self.assertIn("source must roll back when its effect request cannot commit", source)
        self.assertIn("current owner truth source target must be admitted", source)
        self.assertIn("stale source authority epoch must block target admission", source)
        self.assertIn("FOR UPDATE SKIP LOCKED", lease_source)
        self.assertIn("FOR UPDATE SKIP LOCKED", scheduler_source)
        self.assertIn("RETURNING lease.lease_id", scheduler_source)
        self.assertIn("only one worker may claim the same job", source)
        self.assertIn("expired lease must be reclaimed", source)
        self.assertIn("cancelled worker heartbeat must be rejected", source)
        self.assertIn("only one scheduler may claim the same scheduler lease", source)
        self.assertIn("expired scheduler lease must be reclaimed", source)
        self.assertIn("stale scheduler heartbeat must be rejected", source)
        self.assertIn("same consumer event must return one immutable completion receipt", source)
        self.assertIn("consumer inbox must roll back with its completion receipt", source)
        self.assertIn("consumer event cannot complete a changed business target", source)
        self.assertIn("force rollback", source)
        self.assertIn("def revert_terminal_operation", source)
        self.assertIn("revert_terminal_operation(cursor, intent.operation_id)", source)
        self.assertIn("terminal effect state must not revert", source)
        self.assertIn("business receipts must remain append-only", source)
        self.assertIn("kernel must not persist a payload body column", source)


if __name__ == "__main__":
    unittest.main()
