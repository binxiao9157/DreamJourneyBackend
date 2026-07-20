import json
import unittest

from app.db.migrator import default_migrations_dir, load_migrations


class AsyncEffectDeadLetterReplayRequestMigrationContractTests(unittest.TestCase):
    def setUp(self):
        self.migration = next(
            item
            for item in load_migrations(default_migrations_dir())
            if item.version == "0027"
        )
        self.metadata = json.loads(self.migration.sql_path.with_suffix(".json").read_text())

    def test_manifest_is_additive_and_default_off(self):
        self.assertEqual(self.migration.name, "async_effect_dead_letter_replay_requests")
        self.assertEqual(self.migration.phase, "expand")
        self.assertEqual(self.migration.compatibility, "additive")
        self.assertEqual(
            self.metadata["runtimeCompatibility"],
            "asyncEffectV1DeadLetterReplayRequestShadow",
        )
        self.assertFalse(self.metadata["releaseFlags"]["asyncEffectV1"])
        self.assertFalse(self.metadata["releaseFlags"]["asyncEffectWorker"])
        self.assertFalse(self.metadata["releaseFlags"]["asyncEffectDeadLetterReplayV1"])

    def test_migration_is_append_only_and_cannot_enable_execution(self):
        sql = self.migration.sql
        self.assertIn("CREATE TABLE async_effects.dead_letter_replay_requests", sql)
        self.assertIn("UNIQUE (dead_letter_id)", sql)
        self.assertIn("state = 'authorized'", sql)
        self.assertIn("async_effects_dead_letter_replay_requests_no_update", sql)
        self.assertIn("async_effects_dead_letter_replay_requests_no_delete", sql)
        self.assertNotIn("UPDATE async_effects.jobs", sql)
        self.assertNotIn("INSERT INTO async_effects.jobs", sql)
        self.assertNotIn("async_effects.job_attempts", sql)
        self.assertNotIn("async_effects.provider_effects", sql)
        self.assertNotIn("async_effects.provider_receipts", sql)


if __name__ == "__main__":
    unittest.main()
