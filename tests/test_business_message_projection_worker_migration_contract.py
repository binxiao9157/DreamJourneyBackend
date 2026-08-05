from __future__ import annotations

import json
import unittest

from app.db.migrator import default_migrations_dir, load_migrations


class BusinessMessageProjectionWorkerMigrationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.migration = next(
            item
            for item in load_migrations(default_migrations_dir())
            if item.version == "0076"
        )
        self.metadata = json.loads(
            self.migration.sql_path.with_suffix(".json").read_text(encoding="utf-8")
        )

    def test_migration_is_additive_and_runtime_stays_default_off(self) -> None:
        self.assertEqual(self.migration.name, "async_effect_business_message_projection_worker_inputs")
        self.assertEqual(self.migration.phase, "expand")
        self.assertEqual(self.migration.compatibility, "additive")
        self.assertEqual(
            self.metadata["runtimeCompatibility"],
            "asyncEffectBusinessMessageProjectionWorkerDefaultOff",
        )
        self.assertFalse(self.metadata["releaseFlags"]["asyncEffectBusinessMessageProjectionWorkerV1"])
        self.assertFalse(self.metadata["releaseFlags"]["asyncEffectBusinessMessagePublicReadV1"])
        self.assertFalse(self.metadata["releaseFlags"]["asyncEffectBusinessMessageDispatchV1"])

    def test_request_input_binds_job_completed_receipt_and_inbox_without_content_or_dispatch(self) -> None:
        sql = self.migration.sql
        for required in (
            "CREATE TABLE async_effects.business_message_projection_requests",
            "source_business_receipt_id UUID NOT NULL",
            "source_consumer_inbox_id UUID NOT NULL",
            "source_consumer_name TEXT NOT NULL",
            "message_id UUID NOT NULL UNIQUE",
            "inbox_account_epoch BIGINT NOT NULL",
            "request_hash TEXT NOT NULL",
            "validate_business_message_projection_request",
            "receipt_type IS DISTINCT FROM ('consumer.' || NEW.source_consumer_name || '.completion')",
            "business message projection request source operation is missing",
            "SELECT job.operation_id, job.job_type, job.resource_type, job.resource_id",
            "FROM async_effects.business_receipts AS receipt",
            "FROM async_effects.consumer_inbox AS inbox",
            "receipt_owner_subject_id IS DISTINCT FROM source_owner_subject_id",
            "inbox_owner_subject_id IS DISTINCT FROM source_owner_subject_id",
            "async_effects_business_message_projection_requests_no_update",
            "async_effects_business_message_projection_requests_no_delete",
            "UNIQUE (\n        source_business_receipt_id, message_kind, inbox_subject_id, inbox_vault_id\n    )",
        ):
            self.assertIn(required, sql)
        for forbidden in (
            "INSERT INTO mailbox_letters",
            "UPDATE mailbox_letters",
            "DELETE FROM mailbox_letters",
            "INSERT INTO provider_effects",
            "INSERT INTO provider_receipts",
            "message_body",
            "title TEXT",
            "payload JSON",
        ):
            self.assertNotIn(forbidden, sql)


if __name__ == "__main__":
    unittest.main()
