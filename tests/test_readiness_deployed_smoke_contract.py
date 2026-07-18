import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPT = ROOT_DIR / "scripts/backend-readiness-deployed-smoke.py"


class ReadinessDeployedSmokeContractTests(unittest.TestCase):
    def test_smoke_covers_the_current_public_readiness_components(self):
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn('["database", "schema", "auth", "incident"]', source)
        self.assertIn('"incidentReady": True', source)
        self.assertIn('"x-dreamjourney-correlation-id" not in ready_headers', source)


if __name__ == "__main__":
    unittest.main()
