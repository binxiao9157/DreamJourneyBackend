import json
import unittest

from app.db.migrator import default_migrations_dir, load_migrations


class AsyncEffectDeadLetterPersistenceMigrationContractTests(unittest.TestCase):
    def setUp(self):
        self.migration = next(
            item
            for item in load_migrations(default_migrations_dir())
            if item.version == "0026"
        )
        self.metadata = json.loads(self.migration.sql_path.with_suffix(".json").read_text())

    def test_manifest_is_additive_and_default_off(self):
        self.assertEqual(self.migration.name, "async_effect_dead_letter_persistence")
        self.assertEqual(self.migration.phase, "expand")
        self.assertEqual(self.migration.compatibility, "additive")
        self.assertEqual(
            self.metadata["runtimeCompatibility"],
            "asyncEffectV1DeadLetterPersistenceShadow",
        )
        self.assertFalse(self.metadata["releaseFlags"]["asyncEffectDeadLetterPersistenceV1"])
        self.assertFalse(self.metadata["releaseFlags"]["asyncEffectDeadLetterReplayV1"])

    def test_migration_adds_only_value_free_receipt_coordinate(self):
        sql = self.migration.sql
        self.assertIn("ADD COLUMN IF NOT EXISTS last_receipt_hash TEXT", sql)
        self.assertIn("last_receipt_hash ~ '^[0-9a-f]{64}$'", sql)
        self.assertIn("async_effects.dead_letters", sql)
        self.assertNotIn("CREATE TABLE", sql)
        self.assertNotIn("UPDATE async_effects.jobs", sql)
        self.assertNotIn("INSERT INTO async_effects.jobs", sql)


if __name__ == "__main__":
    unittest.main()
