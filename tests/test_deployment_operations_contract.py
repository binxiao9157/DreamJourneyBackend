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
        self.assertIn('"workerCount":8', result.stdout)
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
            "read -r worker_kind flag_name service_name enabled <&3",
            'done 3<<< "$inventory"',
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
            "owner-truth-memory-search-embedding-worker",
            "owner-truth-media-processing-worker",
            "owner-truth-media-deletion-worker",
            "business-message-projection-worker",
            "publication-external-cleanup-materializer-worker",
        ):
            self.assertIn(service_name, registry)

        preflight = (ROOT / "scripts/deployment-preflight.sh").read_text()
        self.assertIn("workerImageAlignmentScriptUnavailable", preflight)
        self.assertIn("pgvectorImagePreflightUnavailable", preflight)
        self.assertIn("derivedProjectionMaintenanceScriptUnavailable", preflight)

        projection_maintenance = (
            ROOT / "scripts/rebuild-owner-truth-derived-projections.py"
        )
        self.assertTrue(os.access(projection_maintenance, os.X_OK))
        self.assertIn("--apply", projection_maintenance.read_text())

    def test_runbook_fixes_one_operator_and_forbids_automatic_destructive_recovery(self):
        runbook = (ROOT / "docs/backend/2026-08-09-deployment-account-recovery-runbook.md").read_text()
        for required in (
            "ubuntu",
            "miao",
            "deployment-preflight.sh",
            "rebuild-enabled-workers-after-migration.sh",
            "verify-pgvector-image.sh",
            "pull --ff-only origin main",
            "migrate_db.py --apply",
            "run-backend-owner-truth-memory-search-pgvector-postgres-smoke.sh",
            "run-backend-owner-truth-b-migration-execution-postgres-smoke.sh",
            "execute-owner-truth-b-migration-batch.py",
            "OWNER_TRUTH_B_MIGRATION_EXECUTION_ACK=YES",
            "OWNER_TRUTH_B_MIGRATION_EXECUTION_ENABLED=true",
            "单批最多 25",
            "run-backend-owner-truth-dfx-postgres-load.sh",
            "OWNER_TRUTH_DFX_POSTGRES_APPROVED=1",
            "--profile baseline",
            "100 在线会话",
            "20 检索 QPS",
            "5 接收 QPS",
            "3 整理并发",
            "OWNER_TRUTH_DFX_CAPACITY_ACK=YES",
            "OWNER_TRUTH_MEMORY_SEARCH_QUALITY_EVALUATION_APPROVED=1",
            "OWNER_TRUTH_REAL_MODEL_VALIDATION_APPROVED=1",
            "run-owner-truth-real-deepseek-validation.py",
            "不可变镜像摘要",
            "不得自动切回不含 pgvector",
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

    def test_release_build_context_excludes_local_credentials(self):
        dockerignore = (ROOT / ".dockerignore").read_text()
        for required in (
            ".git/",
            ".env",
            ".env.*",
            "!.env.example",
            "*.pem",
            "*.key",
            "secrets/",
            "private/",
            ".venv/",
        ):
            self.assertIn(required, dockerignore)

        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertNotIn("COPY .", dockerfile)

    def test_pgvector_runtime_is_explicitly_configurable_and_preflighted(self):
        compose = (ROOT / "docker-compose.yml").read_text()
        environment = (ROOT / ".env.example").read_text()
        preflight = ROOT / "scripts/verify-pgvector-image.sh"

        self.assertIn("${POSTGRES_IMAGE:-pgvector/pgvector:pg16}", compose)
        self.assertIn("POSTGRES_IMAGE=pgvector/pgvector:pg16", environment)
        for required in (
            "OWNER_TRUTH_MEMORY_SEARCH_EMBEDDING_PROVIDER=disabled",
            "OWNER_TRUTH_MEMORY_SEARCH_EMBEDDING_HTTP_JSON_URL=",
            "OWNER_TRUTH_MEMORY_SEARCH_EMBEDDING_HTTP_JSON_API_KEY=",
            "OWNER_TRUTH_MEMORY_SEARCH_EMBEDDING_PROVIDER_MODEL_ID=bge-m3",
            "OWNER_TRUTH_MEMORY_SEARCH_EMBEDDING_MODEL_ID=bge-m3",
            "OWNER_TRUTH_MEMORY_SEARCH_EMBEDDING_MODEL_VERSION=v1",
            "OWNER_TRUTH_MEMORY_SEARCH_EMBEDDING_DIMENSIONS=1024",
            "OWNER_TRUTH_MEMORY_SEARCH_EMBEDDING_EGRESS_APPROVED=false",
            "OWNER_TRUTH_MEMORY_SEARCH_EMBEDDING_WORKER_ENABLED=false",
        ):
            self.assertIn(required, environment)
        self.assertTrue(os.access(preflight, os.X_OK))
        result = subprocess.run(
            ["bash", str(preflight), "--contract-only"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn('"status":"passed"', result.stdout)
        self.assertNotIn("PASSWORD", result.stdout.upper())

    def test_b_migration_execution_command_is_present_and_not_a_release_default(self):
        command = ROOT / "scripts/execute-owner-truth-b-migration-batch.py"
        verifier = (ROOT / "scripts/verify_backend.sh").read_text()
        environment = (ROOT / ".env.example").read_text()

        self.assertTrue(command.is_file())
        self.assertIn("execute-owner-truth-b-migration-batch.py", verifier)
        self.assertIn(
            "test_owner_truth_b_migration_execution_command_contract.py",
            verifier,
        )
        self.assertIn(
            "OWNER_TRUTH_B_MIGRATION_EXECUTION_ENABLED=false",
            environment,
        )

    def test_dfx_postgres_load_is_part_of_backend_verification(self):
        verifier = (ROOT / "scripts/verify_backend.sh").read_text()
        for required in (
            "owner_truth_dfx_load.py",
            "test_owner_truth_dfx_load.py",
            "backend-owner-truth-dfx-postgres-load.py",
            "run-backend-owner-truth-dfx-postgres-load.sh",
            "test_owner_truth_dfx_postgres_load_contract.py",
        ):
            self.assertIn(required, verifier)


if __name__ == "__main__":
    unittest.main()
