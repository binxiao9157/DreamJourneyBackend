from __future__ import annotations

import json
import unittest

from app.db.migrator import default_migrations_dir, load_migrations


class BusinessMessageProjectionMigrationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.migration = next(
            item
            for item in load_migrations(default_migrations_dir())
            if item.version == "0059"
        )
        self.metadata = json.loads(self.migration.sql_path.with_suffix(".json").read_text())

    def test_manifest_is_additive_and_all_runtime_paths_default_off(self) -> None:
        self.assertEqual(self.migration.name, "async_effect_business_message_projection_shadow")
        self.assertEqual(self.migration.phase, "expand")
        self.assertEqual(self.migration.compatibility, "additive")
        self.assertEqual(
            self.metadata["runtimeCompatibility"],
            "asyncEffectBusinessMessageProjectionShadow",
        )
        self.assertFalse(self.metadata["releaseFlags"]["asyncEffectBusinessMessageProjectionV1"])
        self.assertFalse(self.metadata["releaseFlags"]["asyncEffectBusinessMessagePublicReadV1"])
        self.assertFalse(self.metadata["releaseFlags"]["asyncEffectBusinessMessageDispatchV1"])

    def test_schema_keeps_resource_and_inbox_coordinates_separate_and_immutable(self) -> None:
        sql = self.migration.sql
        self.assertIn("CREATE TABLE async_effects.business_message_projections", sql)
        for required in (
            "business_receipt_id UUID NOT NULL",
            "resource_owner_subject_id TEXT NOT NULL",
            "resource_vault_id TEXT NOT NULL",
            "inbox_subject_id TEXT NOT NULL",
            "inbox_vault_id TEXT NOT NULL",
            "inbox_account_epoch BIGINT NOT NULL",
            "message_kind TEXT NOT NULL",
            "state TEXT NOT NULL CHECK (state = 'unread')",
            "projection_hash TEXT NOT NULL",
            "async_effects_business_message_projections_validate_receipt",
            "validate_business_message_projection",
            "async_effects_business_message_projections_no_update",
            "async_effects_business_message_projections_no_delete",
        ):
            self.assertIn(required, sql)
        self.assertIn("UNIQUE (business_receipt_id, message_kind, inbox_subject_id, inbox_vault_id)", sql)
        self.assertNotIn("INSERT INTO mailbox_letters", sql)
        self.assertNotIn("UPDATE mailbox_letters", sql)
        self.assertNotIn("DELETE FROM mailbox_letters", sql)
        self.assertNotIn("async_effects.jobs", sql)
        self.assertNotIn("provider_effects", sql)
        self.assertNotIn("provider_receipts", sql)


if __name__ == "__main__":
    unittest.main()
