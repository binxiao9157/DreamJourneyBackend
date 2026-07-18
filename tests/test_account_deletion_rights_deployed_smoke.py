import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPT = ROOT_DIR / "scripts" / "backend-account-deletion-rights-deployed-smoke.py"
RUNNER = ROOT_DIR / "scripts" / "run-backend-account-deletion-rights-deployed-smoke.sh"


class AccountDeletionRightsDeployedSmokeContractTests(unittest.TestCase):
    def test_smoke_is_disposable_and_covers_the_required_g2_boundaries(self):
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn('database_name = f"dj_rights_smoke_', source)
        self.assertIn("drop_database(admin_dsn, database_name)", source)
        self.assertIn("assert_deployed_readiness()", source)
        self.assertIn("ThreadPoolExecutor(max_workers=2)", source)
        self.assertIn('outcomes == {"recorded", "deduplicated"}', source)
        self.assertIn('require(status == 409, "changed scope must conflict")', source)
        self.assertIn("RollbackProbe", source)
        self.assertIn('require(status == 403, "cross-account deletion must be denied")', source)
        self.assertIn("DREAMJOURNEY_DEPLOYED_CONTAINER_SMOKE=1 is required", source)
        self.assertIn("rights lifecycle smoke must run inside the deployed API container", source)
        self.assertIn("rights summary must survive store reconstruction", source)
        self.assertIn('"productionBusinessDataMutated": False', source)

    def test_runner_requires_an_explicit_deployed_base_url(self):
        source = RUNNER.read_text(encoding="utf-8")

        self.assertIn('BACKEND_BASE_URL is required', source)
        self.assertIn('DREAMJOURNEY_DEPLOYED_CONTAINER_SMOKE=1 is required', source)
        self.assertIn("backend-account-deletion-rights-deployed-smoke.py", source)


if __name__ == "__main__":
    unittest.main()
