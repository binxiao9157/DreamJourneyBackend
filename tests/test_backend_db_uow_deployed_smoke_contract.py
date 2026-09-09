from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "backend-db-uow-deployed-smoke.py"


class BackendDatabaseUowDeployedSmokeContractTests(unittest.TestCase):
    def test_bypass_observation_does_not_require_a_database_correlation_id(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn("expect_correlation=True", source)
        self.assertIn("if expect_correlation:", source)
        self.assertIn("expect_correlation=False", source)
        self.assertIn("must bypass database correlation", source)

    def test_transaction_counts_use_routes_that_enter_the_uow_middleware(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertNotIn('request_json(\n        "/config/runtime"', source)
        self.assertEqual(
            source.count('request_json("/v2/release-policy?audience=owner&clientBuild=9004'),
            2,
        )
        self.assertIn(
            'request_json("/v2/release-policy?audience=invalid", expected_status=422)',
            source,
        )


if __name__ == "__main__":
    unittest.main()
