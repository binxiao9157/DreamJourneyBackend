import json
from pathlib import Path
import unittest

from app.db.migrator import load_migrations


ROOT = Path(__file__).resolve().parents[1]
SQL = (
    ROOT / "db/migrations/0107_narrative_selection_manifests.sql"
).read_text(encoding="utf-8")
MANIFEST = json.loads(
    (
        ROOT / "db/migrations/0107_narrative_selection_manifests.json"
    ).read_text(encoding="utf-8")
)


class NarrativeSelectionManifestMigrationContractTests(unittest.TestCase):
    def test_manifest_is_additive_private_and_immutable(self):
        self.assertEqual(MANIFEST["version"], "0107")
        self.assertEqual(MANIFEST["phase"], "expand")
        self.assertEqual(MANIFEST["compatibility"], "additive")
        self.assertIn("CREATE TABLE narrative.selection_manifests", SQL)
        self.assertIn("UNIQUE (job_id)", SQL)
        self.assertIn("jsonb_array_length(selected_memory_version_ids) BETWEEN 1 AND 3", SQL)
        self.assertIn("narrative_selection_manifests_immutable", SQL)
        self.assertIn("narrative.reject_immutable_update()", SQL)
        self.assertNotIn("content_text", SQL)
        self.assertNotIn("provider_key", SQL.lower())

    def test_migration_loader_accepts_selection_manifest_head(self):
        migrations = load_migrations(ROOT / "db/migrations")
        self.assertEqual(migrations[-1].version, "0107")
        self.assertEqual(migrations[-1].name, "narrative_selection_manifests")


if __name__ == "__main__":
    unittest.main()
