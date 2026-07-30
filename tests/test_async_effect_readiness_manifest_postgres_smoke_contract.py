from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/run-backend-async-effect-readiness-manifest-postgres-smoke.sh"


class AsyncEffectReadinessManifestPostgresSmokeContractTests(unittest.TestCase):
    def test_deployed_runner_requires_a_database_and_executes_only_the_disposable_smoke(self) -> None:
        source = RUNNER.read_text(encoding="utf-8")

        self.assertIn('DATABASE_URL is required', source)
        self.assertIn(
            'scripts/backend-async-effect-readiness-manifest-postgres-smoke.py',
            source,
        )
        self.assertNotIn('python -m unittest', source)
        self.assertNotIn('provider_query_operations', source)


if __name__ == "__main__":
    unittest.main()
