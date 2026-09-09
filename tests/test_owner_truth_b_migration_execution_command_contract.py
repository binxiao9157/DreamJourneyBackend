from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/execute-owner-truth-b-migration-batch.py"
CONFIG = ROOT / "app/core/config.py"
ENV_EXAMPLE = ROOT / ".env.example"


class OwnerTruthBMigrationExecutionCommandContractTests(unittest.TestCase):
    def test_command_is_postgres_only_double_gated_and_report_bound(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn('settings.store_backend != "postgres"', source)
        self.assertIn("owner_truth_b_migration_execution_enabled", source)
        self.assertIn("OWNER_TRUTH_B_MIGRATION_EXECUTION_ACK", source)
        self.assertIn("OWNER_TRUTH_B_MIGRATION_REPORT_ID", source)
        self.assertIn("report_id=report_id", source)
        self.assertNotIn("dry_run(context", source)

    def test_command_output_is_value_free_and_configuration_defaults_off(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        config = CONFIG.read_text(encoding="utf-8")
        env_example = ENV_EXAMPLE.read_text(encoding="utf-8")

        self.assertIn("ownerSubjectIdHash", source)
        self.assertIn("vaultIdHash", source)
        self.assertNotIn('summary["ownerSubjectId"]', source)
        self.assertIn(
            "owner_truth_b_migration_execution_enabled: bool = False",
            config,
        )
        self.assertIn("OWNER_TRUTH_B_MIGRATION_EXECUTION_ENABLED=false", env_example)


if __name__ == "__main__":
    unittest.main()
