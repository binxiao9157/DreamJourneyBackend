from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "scripts/backend-owner-truth-legacy-tail-shadow-postgres-smoke.py"
WRAPPER = ROOT / "scripts/run-backend-owner-truth-legacy-tail-shadow-postgres-smoke.sh"


class OwnerTruthLegacyTailShadowPostgresSmokeContractTests(unittest.TestCase):
    def test_smoke_uses_a_disposable_database_and_proves_zero_side_effects(self) -> None:
        source = SMOKE.read_text(encoding="utf-8")
        wrapper = WRAPPER.read_text(encoding="utf-8")

        self.assertIn("CREATE DATABASE", source)
        self.assertIn("DROP DATABASE IF EXISTS", source)
        self.assertIn("dj_owner_truth_tail_shadow_", source)
        self.assertIn('"0047" in applied["appliedVersions"]', source)
        self.assertIn('"0048" in applied["appliedVersions"]', source)
        self.assertIn("effect_execution_count", source)
        self.assertIn("async_effects.operations", source)
        self.assertIn("async_effects.outbox_events", source)
        self.assertIn("async_effects.provider_effects", source)
        self.assertIn("mapping checkpoint is incomplete", source)
        self.assertIn('"${DATABASE_URL:?DATABASE_URL is required}"', wrapper)
        for forbidden in ("requests", "httpx", "boto3", "app.main", "ProviderEffectReceipt"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
