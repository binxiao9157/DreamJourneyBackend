from pathlib import Path
import json
import unittest


ROOT = Path(__file__).resolve().parents[1]
SQL_PATH = ROOT / "db/migrations/0101_formal_memory_markdown_export.sql"
MANIFEST_PATH = ROOT / "db/migrations/0101_formal_memory_markdown_export.json"


class FormalMemoryMarkdownExportMigrationContractTests(unittest.TestCase):
    def test_0101_separates_export_type_scope_and_cancellation(self) -> None:
        sql = SQL_PATH.read_text(encoding="utf-8")
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

        self.assertEqual(manifest["version"], "0101")
        self.assertEqual(manifest["phase"], "expand")
        self.assertIn("ADD COLUMN export_type", sql)
        self.assertIn("'formalMemoryMarkdown'", sql)
        self.assertIn("ADD COLUMN scope_id", sql)
        self.assertIn("data_export_jobs_owner_type_scope_request_key_unique", sql)
        self.assertIn("'cancelled'", sql)
        self.assertIn("data_export_jobs_cancelled_artifact_check", sql)


if __name__ == "__main__":
    unittest.main()
