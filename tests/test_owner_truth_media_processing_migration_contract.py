from __future__ import annotations

import json
import unittest

from app.db.migrator import default_migrations_dir, load_migrations


class OwnerTruthMediaProcessingMigrationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.migration = next(
            item
            for item in load_migrations(default_migrations_dir())
            if item.version == "0074"
        )
        self.metadata = json.loads(
            self.migration.sql_path.with_suffix(".json").read_text(encoding="utf-8")
        )

    def test_migration_is_additive_and_remains_default_off(self) -> None:
        self.assertEqual(self.migration.name, "owner_truth_media_processing")
        self.assertEqual(self.migration.phase, "expand")
        self.assertEqual(self.migration.compatibility, "additive")
        self.assertEqual(
            self.metadata["runtimeCompatibility"],
            "ownerTruthMediaProcessingClosedPilotDefaultOff",
        )
        self.assertFalse(self.metadata["releaseFlags"]["ownerMediaCaptureV1"])

    def test_migration_keeps_private_processing_evidence_separate_from_content(self) -> None:
        sql = self.migration.sql
        self.assertIn("external_processing_allowed BOOLEAN NOT NULL DEFAULT FALSE", sql)
        self.assertIn("processing_generation BIGINT NOT NULL DEFAULT 0", sql)
        self.assertIn("CREATE TABLE owner_truth.media_source_object_processing_results", sql)
        self.assertIn("extracted_text_sha256 TEXT", sql)
        self.assertIn("derived_source_id UUID", sql)
        self.assertIn("UNIQUE (", sql)
        self.assertIn("processing_generation, attempt", sql)
        self.assertNotIn("extracted_text TEXT", sql)
        self.assertNotIn("storage_key TEXT", sql)
        self.assertNotIn("public_url", sql)


if __name__ == "__main__":
    unittest.main()
