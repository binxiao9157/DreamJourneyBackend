import os
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

    def test_post_migration_worker_alignment_contract_is_value_free(self):
        script = ROOT / "scripts/rebuild-enabled-workers-after-migration.sh"
        self.assertTrue(os.access(script, os.X_OK))
        result = subprocess.run(
            ["bash", str(script), "--contract-only"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn('"status":"passed"', result.stdout)
        self.assertIn('"workerCount":6', result.stdout)
        self.assertNotIn("TOKEN", result.stdout.upper())
        self.assertNotIn("PASSWORD", result.stdout.upper())

        source = script.read_text()
        for required in (
            "migrate_db.py --verify",
            "worker_activation",
            "worker_deployment_registry",
            "--force-recreate",
            ".RestartCount",
            "STABILITY_DELAY_SECONDS",
            "first_state",
            "second_state",
            "workerRestartedAfterRecreate",
            "apiImageMigrationHeadMismatch",
        ):
            self.assertIn(required, source)

        registry = (
            ROOT / "app/async_effects/worker_deployment_registry.py"
        ).read_text()
        for service_name in (
            "owner-truth-candidate-extraction-worker",
            "owner-truth-memory-projection-worker",
            "owner-truth-media-processing-worker",
            "owner-truth-media-deletion-worker",
            "business-message-projection-worker",
            "publication-external-cleanup-materializer-worker",
        ):
            self.assertIn(service_name, registry)

        preflight = (ROOT / "scripts/deployment-preflight.sh").read_text()
        self.assertIn("workerImageAlignmentScriptUnavailable", preflight)

    def test_runbook_fixes_one_operator_and_forbids_automatic_destructive_recovery(self):
        runbook = (ROOT / "docs/backend/2026-08-09-deployment-account-recovery-runbook.md").read_text()
        for required in (
            "ubuntu",
            "miao",
            "deployment-preflight.sh",
            "rebuild-enabled-workers-after-migration.sh",
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
