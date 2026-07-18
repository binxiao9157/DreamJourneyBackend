import json
import unittest

from app.db.migrator import default_migrations_dir, load_migrations


class AsyncEffectsMigrationContractTests(unittest.TestCase):
    def setUp(self):
        self.migration = next(
            item
            for item in load_migrations(default_migrations_dir())
            if item.version == "0013"
        )
        self.metadata = json.loads(self.migration.sql_path.with_suffix(".json").read_text())

    def test_manifest_keeps_effect_kernel_additive_and_disabled(self):
        self.assertEqual(self.migration.name, "async_effects_kernel")
        self.assertEqual(self.migration.phase, "expand")
        self.assertEqual(self.migration.compatibility, "additive")
        self.assertEqual(self.metadata["runtimeCompatibility"], "asyncEffectV1SchemaOnly")
        self.assertFalse(self.metadata["releaseFlags"]["asyncEffectV1"])
        self.assertFalse(self.metadata["releaseFlags"]["asyncEffectWorker"])
        self.assertFalse(self.metadata["releaseFlags"]["asyncEffectScheduler"])

    def test_schema_covers_all_effect_kernel_record_types(self):
        for relation in (
            "operations",
            "outbox_events",
            "jobs",
            "job_attempts",
            "consumer_inbox",
            "business_receipts",
            "provider_effects",
            "provider_receipts",
            "dead_letters",
            "scheduler_leases",
        ):
            self.assertIn(f"CREATE TABLE async_effects.{relation}", self.migration.sql)

    def test_schema_stores_hashes_not_effect_payload_bodies_or_credentials(self):
        lower_sql = self.migration.sql.lower()
        self.assertIn("payload_hash", lower_sql)
        self.assertNotIn("payload jsonb", lower_sql)
        self.assertNotIn("content jsonb", lower_sql)
        self.assertNotIn("credential jsonb", lower_sql)
        self.assertNotIn("secret jsonb", lower_sql)

    def test_schema_has_idempotency_and_terminal_state_guards(self):
        sql = self.migration.sql
        self.assertIn("UNIQUE (vault_id, stable_key)", sql)
        self.assertIn("UNIQUE (operation_id, event_type)", sql)
        self.assertIn("UNIQUE (operation_id, job_type)", sql)
        self.assertIn("UNIQUE (consumer_name, event_id)", sql)
        self.assertIn("async_effects.guard_terminal_state", sql)
        self.assertIn("async_effects_business_receipts_no_update", sql)
        self.assertIn("async_effects_provider_receipts_no_delete", sql)


if __name__ == "__main__":
    unittest.main()
