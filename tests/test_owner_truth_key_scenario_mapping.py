import json
from pathlib import Path
import unittest


ROOT_DIR = Path(__file__).resolve().parents[1]
MAPPING_PATH = (
    ROOT_DIR / "tests/fixtures/owner_truth/key_scenario_automated_mapping_v1.json"
)
RUNNER_PATH = ROOT_DIR / "scripts/run-owner-truth-key-scenario-gate.py"


class OwnerTruthKeyScenarioMappingTests(unittest.TestCase):
    def test_mapping_covers_k01_through_k26_with_real_backend_selectors(self) -> None:
        payload = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            payload["schemaVersion"],
            "owner-truth-key-scenario-automated-mapping-v1",
        )
        scenarios = payload["scenarios"]
        self.assertEqual(
            [item["id"] for item in scenarios],
            [f"K{index:02d}" for index in range(1, 27)],
        )
        self.assertTrue(all(item["backendTests"] for item in scenarios))
        self.assertTrue(
            all(
                selector.startswith("tests.test_") and selector.count(".") >= 3
                for item in scenarios
                for selector in item["backendTests"]
            )
        )

    def test_runner_fails_closed_and_can_verify_the_original_fixture_hash(self) -> None:
        source = RUNNER_PATH.read_text(encoding="utf-8")
        self.assertIn("source key-scenario fixture hash does not match mapping", source)
        self.assertIn("behaviorTestFailed", source)
        self.assertNotIn("expectedFailure", source)


if __name__ == "__main__":
    unittest.main()
