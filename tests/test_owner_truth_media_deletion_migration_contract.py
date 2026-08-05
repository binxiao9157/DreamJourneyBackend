from __future__ import annotations

import json
import unittest

from app.db.migrator import default_migrations_dir, load_migrations


class OwnerTruthMediaDeletionMigrationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.migration = next(
            item
            for item in load_migrations(default_migrations_dir())
            if item.version == "0075"
        )
        self.metadata = json.loads(
            self.migration.sql_path.with_suffix(".json").read_text(encoding="utf-8")
        )

    def test_migration_is_additive_and_default_off(self) -> None:
        self.assertEqual(self.migration.name, "owner_truth_media_deletion_receipts")
        self.assertEqual(self.migration.phase, "expand")
        self.assertEqual(self.migration.compatibility, "additive")
        self.assertEqual(
            self.metadata["runtimeCompatibility"],
            "ownerTruthMediaDeletionClosedPilotDefaultOff",
        )
        self.assertFalse(self.metadata["releaseFlags"]["ownerMediaCaptureV1"])

    def test_migration_records_revocation_without_claiming_physical_delete(self) -> None:
        sql = self.migration.sql
        self.assertIn("access_state TEXT NOT NULL DEFAULT 'available'", sql)
        self.assertIn("deletion_status TEXT NOT NULL DEFAULT 'notRequested'", sql)
        self.assertIn("media_source_object_deletion_commands", sql)
        self.assertIn("UNIQUE (vault_id, source_object_id, command_id_hash)", sql)
        self.assertNotIn("DROP COLUMN", sql)
        self.assertNotIn("DELETE FROM", sql)
        self.assertNotIn("storage_key TEXT", sql)


if __name__ == "__main__":
    unittest.main()
