import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL_PATH = ROOT / "db/migrations/0086_data_export_download_credentials.sql"
MANIFEST_PATH = ROOT / "db/migrations/0086_data_export_download_credentials.json"
POSTGRES_STORE_PATH = ROOT / "app/services/postgres_store.py"


class DataExportDownloadCredentialMigrationContractTests(unittest.TestCase):
    def test_migration_stores_only_hash_and_enforces_single_active_row_per_job(self) -> None:
        sql = SQL_PATH.read_text(encoding="utf-8")
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

        self.assertEqual(manifest["version"], "0086")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertIn("CREATE TABLE data_export_download_credentials", sql)
        self.assertIn("job_id TEXT PRIMARY KEY", sql)
        self.assertIn("token_hash TEXT NOT NULL", sql)
        self.assertIn("status IN ('active', 'consumed', 'expired', 'revoked')", sql)
        self.assertIn("REVOKE ALL ON TABLE data_export_download_credentials FROM PUBLIC", sql)
        self.assertNotIn("download_token", sql)
        self.assertNotIn("token TEXT", sql)

    def test_postgres_store_uses_supported_atomic_statement_path(self) -> None:
        source = POSTGRES_STORE_PATH.read_text(encoding="utf-8")

        self.assertNotIn("self._execute(", source)
        self.assertIn("SET status = 'revoked'", source)
        self.assertIn("RETURNING job_id", source)


if __name__ == "__main__":
    unittest.main()
