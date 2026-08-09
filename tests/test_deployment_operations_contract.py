import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DeploymentOperationsContractTests(unittest.TestCase):
    def test_contract_preflight_is_runnable_without_server_secrets(self):
        result = subprocess.run(
            ["bash", str(ROOT / "scripts/deployment-preflight.sh"), "--contract-only"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn('"status":"passed"', result.stdout)
        self.assertNotIn("TOKEN", result.stdout.upper())
        self.assertNotIn("PASSWORD", result.stdout.upper())

    def test_runbook_fixes_one_operator_and_forbids_automatic_destructive_recovery(self):
        runbook = (ROOT / "docs/backend/2026-08-09-deployment-account-recovery-runbook.md").read_text()
        for required in (
            "ubuntu",
            "miao",
            "deployment-preflight.sh",
            "pull --ff-only origin main",
            "migrate_db.py --apply",
            "run-backend-readiness-deployed-smoke.sh",
            "RECOVERY_EXPECTED_CUTOVER=NO_GO",
            "不执行生产 down migration",
            "不自动删除",
        ):
            self.assertIn(required, runbook)

    def test_private_environment_backups_cannot_pollute_git_status(self):
        gitignore = (ROOT / ".gitignore").read_text()
        self.assertIn(".env.backup*", gitignore)
        self.assertIn(".env.bak*", gitignore)


if __name__ == "__main__":
    unittest.main()
