from __future__ import annotations

import json
from pathlib import Path
import unittest

from app.db.migrator import default_migrations_dir, load_migrations


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SQL = (
    ROOT / "db/migrations/0072_owner_truth_family_contribution_formal_authorization.sql"
)
MIGRATION_MANIFEST = MIGRATION_SQL.with_suffix(".json")
AUTHENTICATED_OWNER_MIGRATION_SQL = (
    ROOT / "db/migrations/0118_owner_truth_family_contribution_authenticated_owner.sql"
)
AUTHENTICATED_OWNER_MIGRATION_MANIFEST = AUTHENTICATED_OWNER_MIGRATION_SQL.with_suffix(
    ".json"
)


class OwnerTruthFamilyContributionFormalMigrationContractTests(unittest.TestCase):
    def test_formal_admission_is_additive_value_minimized_and_default_off(self) -> None:
        migration = next(
            item
            for item in load_migrations(default_migrations_dir())
            if item.version == "0072"
        )
        manifest = json.loads(MIGRATION_MANIFEST.read_text(encoding="utf-8"))
        sql = MIGRATION_SQL.read_text(encoding="utf-8")

        self.assertEqual(migration.name, "owner_truth_family_contribution_formal_authorization")
        self.assertEqual(migration.phase, "expand")
        self.assertEqual(migration.compatibility, "additive")
        self.assertEqual(
            manifest["runtimeCompatibility"],
            "ownerTruthFamilyContributionClosedPilotDefaultOff",
        )
        self.assertFalse(manifest["releaseFlags"]["ownerTruthFamilyContribution"])
        self.assertIn("ADD COLUMN IF NOT EXISTS admission_mode", sql)
        self.assertIn("ADD COLUMN IF NOT EXISTS authorization_evidence JSONB", sql)
        self.assertIn("ownerTruthFamilyContribution", sql)
        self.assertIn("owner-truth-command-authorization-capture-v1", sql)
        self.assertIn("OLD.authorization_evidence IS DISTINCT FROM NEW.authorization_evidence", sql)
        self.assertNotIn("voice_profile", sql.lower())
        self.assertNotIn("digital_human", sql.lower())
        self.assertNotIn("GRANT SELECT", sql.upper())

    def test_authenticated_owner_expansion_preserves_prior_modes_and_user_facts(self) -> None:
        migration = next(
            item
            for item in load_migrations(default_migrations_dir())
            if item.version == "0118"
        )
        manifest = json.loads(
            AUTHENTICATED_OWNER_MIGRATION_MANIFEST.read_text(encoding="utf-8")
        )
        sql = AUTHENTICATED_OWNER_MIGRATION_SQL.read_text(encoding="utf-8")

        self.assertEqual(
            migration.name,
            "owner_truth_family_contribution_authenticated_owner",
        )
        self.assertEqual(migration.phase, "expand")
        self.assertEqual(migration.compatibility, "additive")
        self.assertEqual(
            manifest["runtimeCompatibility"],
            "ownerTruthFamilyContributionAuthenticatedOwnerV1",
        )
        self.assertTrue(manifest["releaseFlags"]["ownerTruthFamilyContribution"])
        self.assertFalse(manifest["production"]["rewritesUserFacts"])
        self.assertIn("'qa', 'closedPilot', 'authenticatedOwner'", sql)
        self.assertIn("admission_mode IN ('closedPilot', 'authenticatedOwner')", sql)
        self.assertIn("ownerTruthFamilyContribution", sql)
        self.assertIn("VALIDATE CONSTRAINT", sql)
        self.assertNotIn("UPDATE owner_truth", sql)
        self.assertNotIn("DELETE FROM", sql)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
