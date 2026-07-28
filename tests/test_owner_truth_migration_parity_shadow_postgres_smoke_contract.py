from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "scripts/backend-owner-truth-migration-parity-shadow-postgres-smoke.py"
WRAPPER = ROOT / "scripts/run-backend-owner-truth-migration-parity-shadow-postgres-smoke.sh"


class OwnerTruthMigrationParityShadowPostgresSmokeContractTests(unittest.TestCase):
    def test_smoke_uses_a_disposable_database_and_proves_parity_evidence_boundaries(self) -> None:
        source = SMOKE.read_text(encoding="utf-8")
        wrapper = WRAPPER.read_text(encoding="utf-8")

        self.assertIn("CREATE DATABASE", source)
        self.assertIn("DROP DATABASE IF EXISTS", source)
        self.assertIn("dj_owner_truth_parity_shadow_", source)
        self.assertIn('"0049" in applied["appliedVersions"]', source)
        self.assertIn("migration_parity_shadow_reports", source)
        self.assertIn("migration_parity_shadow_mismatches", source)
        self.assertIn("command_effect_execution_count", source)
        self.assertIn("async_effects.operations", source)
        self.assertIn("M08 command-surface mismatch", source)
        self.assertIn('"${DATABASE_URL:?DATABASE_URL is required}"', wrapper)
        for forbidden in ("requests", "httpx", "boto3", "app.main", "ProviderEffectReceipt"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
