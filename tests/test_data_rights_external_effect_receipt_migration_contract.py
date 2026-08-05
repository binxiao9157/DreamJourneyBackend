from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/0078_data_rights_external_effect_receipts.sql"
MANIFEST = ROOT / "db/migrations/0078_data_rights_external_effect_receipts.json"
SMOKE = ROOT / "scripts/backend-data-rights-external-effect-receipts-postgres-smoke.py"
RUNNER = ROOT / "scripts/run-backend-data-rights-external-effect-receipts-postgres-smoke.sh"


class DataRightsExternalEffectReceiptMigrationContractTests(unittest.TestCase):
    def test_additive_append_only_owner_fenced_migration_exists(self) -> None:
        source = MIGRATION.read_text(encoding="utf-8")
        manifest = MANIFEST.read_text(encoding="utf-8")

        self.assertIn("CREATE TABLE rights_external_effect_receipts", source)
        self.assertIn("owner_subject_hash", source)
        self.assertIn("rights_external_effect_receipts_validate_owner", source)
        self.assertIn("rights_external_effect_receipts_no_update", source)
        self.assertIn("providerVoice", source)
        self.assertIn("providerDigitalHuman", source)
        self.assertIn('"version": "0078"', manifest)
        self.assertIn('"compatibility": "additive"', manifest)

    def test_postgres_smoke_requires_isolated_database(self) -> None:
        source = SMOKE.read_text(encoding="utf-8")
        runner = RUNNER.read_text(encoding="utf-8")

        self.assertIn("DATABASE_URL is required", source)
        self.assertIn("CREATE DATABASE", source)
        self.assertIn("DROP DATABASE IF EXISTS", source)
        self.assertIn("cross-account receipt must be rejected", source)
        self.assertIn("append-only", source)
        self.assertIn("scripts/backend-data-rights-external-effect-receipts-postgres-smoke.py", runner)
        self.assertNotIn("python -m unittest", runner)


if __name__ == "__main__":
    unittest.main()
