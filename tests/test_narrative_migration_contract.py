import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "db/migrations/0106_narrative_writing.sql").read_text(encoding="utf-8")
MANIFEST = json.loads(
    (ROOT / "db/migrations/0106_narrative_writing.json").read_text(encoding="utf-8")
)


class NarrativeMigrationContractTests(unittest.TestCase):
    def test_migration_is_private_additive_and_append_only(self):
        self.assertEqual(MANIFEST["version"], "0106")
        self.assertEqual(MANIFEST["phase"], "expand")
        self.assertEqual(MANIFEST["compatibility"], "additive")
        for table in (
            "book_projects",
            "memory_snapshots",
            "artifact_versions",
            "artifact_memory_refs",
            "project_decisions",
            "generation_jobs",
            "generation_outbox",
            "generation_dead_letters",
        ):
            self.assertIn(f"CREATE TABLE narrative.{table}", SQL)
        self.assertIn("narrative_artifact_body_append_only", SQL)
        self.assertIn("narrative_memory_snapshots_immutable", SQL)
        self.assertGreaterEqual(SQL.count("writing_context JSONB NOT NULL"), 2)
        self.assertIn("REVOKE ALL ON SCHEMA narrative FROM PUBLIC", SQL)
        self.assertNotIn("provider_key", SQL.lower())
        self.assertNotIn("audio", SQL.lower())
        self.assertNotIn("publication", SQL.lower())


if __name__ == "__main__":
    unittest.main()
