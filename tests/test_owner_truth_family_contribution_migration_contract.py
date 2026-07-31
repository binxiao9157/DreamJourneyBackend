from __future__ import annotations

import json
from pathlib import Path
import unittest

from app.db.migrator import default_migrations_dir, load_migrations


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SQL = ROOT / "db/migrations/0070_owner_truth_family_contribution_grants.sql"
MIGRATION_MANIFEST = MIGRATION_SQL.with_suffix(".json")


class OwnerTruthFamilyContributionMigrationContractTests(unittest.TestCase):
    def test_schema_is_additive_default_off_and_does_not_grant_private_read_authority(self) -> None:
        migration = next(
            item
            for item in load_migrations(default_migrations_dir())
            if item.version == "0070"
        )
        manifest = json.loads(MIGRATION_MANIFEST.read_text(encoding="utf-8"))
        sql = MIGRATION_SQL.read_text(encoding="utf-8")

        self.assertEqual(migration.name, "owner_truth_family_contribution_grants")
        self.assertEqual(migration.phase, "expand")
        self.assertEqual(migration.compatibility, "additive")
        self.assertEqual(
            manifest["runtimeCompatibility"],
            "ownerTruthFamilyContributionDefaultOff",
        )
        self.assertFalse(manifest["releaseFlags"]["ownerTruthFamilyContributionQA"])
        self.assertIn("CREATE TABLE owner_truth.family_contribution_grants", sql)
        self.assertIn("scope = 'submitTextSource'", sql)
        self.assertIn("REFERENCES public.family_relationships(id)", sql)
        self.assertIn("ON DELETE CASCADE", sql)
        self.assertIn("family contribution grant identity is immutable", sql)
        self.assertNotIn("voice_profile", sql.lower())
        self.assertNotIn("digital_human", sql.lower())
        self.assertNotIn("CREATE TABLE owner_truth.publications", sql)
        self.assertNotIn("GRANT SELECT", sql.upper())
        self.assertNotIn("candidate_decision", sql.lower())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
