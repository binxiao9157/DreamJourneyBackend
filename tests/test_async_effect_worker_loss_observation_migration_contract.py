import json
import unittest

from app.db.migrator import default_migrations_dir, load_migrations


class AsyncEffectWorkerLossObservationMigrationContractTests(unittest.TestCase):
    def setUp(self):
        self.migration = next(
            item
            for item in load_migrations(default_migrations_dir())
            if item.version == "0028"
        )
        self.metadata = json.loads(self.migration.sql_path.with_suffix(".json").read_text())

    def test_manifest_is_additive_and_default_off(self):
        self.assertEqual(self.migration.name, "async_effect_worker_loss_observations")
        self.assertEqual(self.migration.phase, "expand")
        self.assertEqual(self.migration.compatibility, "additive")
        self.assertEqual(
            self.metadata["runtimeCompatibility"],
            "asyncEffectV1WorkerLossEvidenceShadow",
        )
        self.assertFalse(self.metadata["releaseFlags"]["asyncEffectV1"])
        self.assertFalse(self.metadata["releaseFlags"]["asyncEffectWorker"])
        self.assertFalse(self.metadata["releaseFlags"]["asyncEffectWorkerLossAutoRecoverV1"])

    def test_migration_is_value_free_append_only_and_cannot_enable_worker_actions(self):
        sql = self.migration.sql
        self.assertIn("CREATE TABLE async_effects.worker_loss_observations", sql)
        self.assertIn("observer_worker_id_hash", sql)
        self.assertIn("expired_job_type_counts", sql)
        self.assertIn("async_effects_worker_loss_observations_no_update", sql)
        self.assertIn("async_effects_worker_loss_observations_no_delete", sql)
        self.assertNotIn("job_id", sql)
        self.assertNotIn("owner_subject_id", sql)
        self.assertNotIn("vault_id", sql)
        self.assertNotIn("UPDATE async_effects.jobs", sql)
        self.assertNotIn("INSERT INTO async_effects.jobs", sql)
        self.assertNotIn("async_effects.job_attempts", sql)
        self.assertNotIn("async_effects.provider_effects", sql)
        self.assertNotIn("async_effects.provider_receipts", sql)


if __name__ == "__main__":
    unittest.main()
