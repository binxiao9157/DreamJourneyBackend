import json
import unittest

from app.db.migrator import default_migrations_dir, load_migrations


class ProviderEffectReconciliationMigrationContractTests(unittest.TestCase):
    def setUp(self):
        self.migration = next(
            item
            for item in load_migrations(default_migrations_dir())
            if item.version == "0025"
        )
        self.metadata = json.loads(self.migration.sql_path.with_suffix(".json").read_text())

    def test_manifest_is_additive_and_default_off(self):
        self.assertEqual(self.migration.name, "provider_effect_reconciliation_projection")
        self.assertEqual(self.migration.phase, "expand")
        self.assertEqual(self.migration.compatibility, "additive")
        self.assertEqual(
            self.metadata["runtimeCompatibility"],
            "asyncEffectV1ProviderReconciliationShadow",
        )
        self.assertFalse(self.metadata["releaseFlags"]["providerEffectPersistenceV1"])
        self.assertFalse(self.metadata["releaseFlags"]["providerEffectReconciliationV1"])

    def test_migration_keeps_unknown_fact_append_only_and_projects_late_evidence(self):
        sql = self.migration.sql
        self.assertIn("ADD COLUMN capability", sql)
        self.assertIn("ADD COLUMN provider_request_id_hash", sql)
        self.assertIn("ADD COLUMN observation_origin", sql)
        self.assertIn("CREATE OR REPLACE VIEW async_effects.provider_effect_reconciliation_projection", sql)
        self.assertIn("observation_origin = 'providerQuery'", sql)
        self.assertIn("reconciliationConflict", sql)
        self.assertIn("effect.state = 'unknown'", sql)
        self.assertNotIn("UPDATE async_effects.provider_effects SET state = 'completed'", sql)
        self.assertNotIn("UPDATE async_effects.provider_effects SET state = 'failed'", sql)


if __name__ == "__main__":
    unittest.main()
