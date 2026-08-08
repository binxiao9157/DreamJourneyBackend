import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL_PATH = ROOT / "db/migrations/0084_data_export_jobs.sql"
MANIFEST_PATH = ROOT / "db/migrations/0084_data_export_jobs.json"


class DataExportJobMigrationContractTests(unittest.TestCase):
    def test_migration_is_additive_and_keeps_artifacts_private(self) -> None:
        sql = SQL_PATH.read_text(encoding="utf-8")
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

        self.assertEqual(manifest["version"], "0084")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertFalse(manifest["releaseFlags"]["dataExportJobsV1"])
        self.assertIn("CREATE TABLE data_export_jobs", sql)
        self.assertIn("UNIQUE (owner_user_id, request_key_hash)", sql)
        self.assertIn("artifact_payload JSONB", sql)
        self.assertIn("manifest_payload JSONB", sql)
        self.assertIn("REVOKE ALL ON TABLE data_export_jobs FROM PUBLIC", sql)
        self.assertNotIn("request_key TEXT", sql)
        self.assertNotIn("provider_response", sql)


if __name__ == "__main__":
    unittest.main()
