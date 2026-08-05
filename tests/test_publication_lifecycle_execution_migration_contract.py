"""Static safety contract for P2-S4A publication lifecycle execution."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from app.db.migrator import load_migrations


ROOT = Path(__file__).parents[1]
MIGRATION = ROOT / "db/migrations/0082_publication_lifecycle_execution.sql"
MANIFEST = ROOT / "db/migrations/0082_publication_lifecycle_execution.json"


class PublicationLifecycleExecutionMigrationContractTests(unittest.TestCase):
    def test_additive_local_deny_receipt_is_default_off(self) -> None:
        sql = MIGRATION.read_text(encoding="utf-8").lower()
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual(manifest["version"], "0082")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertFalse(manifest["releaseFlags"]["publicationLifecycleExecutionV1"])
        self.assertFalse(manifest["releaseFlags"]["publicationExternalCleanupWorkerV1"])
        self.assertIn("add column if not exists conflict_hold", sql)
        self.assertIn("create table publication.publication_lifecycle_receipts", sql)
        self.assertIn("publication_lifecycle_receipts_no_update_or_delete", sql)
        self.assertIn("update publication.share_grants", sql)
        self.assertIn("update publication.visitor_sessions", sql)
        self.assertIn("public_index_cleanup_state text not null check (public_index_cleanup_state = 'pending')", sql)
        self.assertIn("runtime_cleanup_state text not null check (runtime_cleanup_state = 'notapplicable')", sql)

    def test_authority_trigger_revokes_active_access_without_claiming_external_cleanup(self) -> None:
        sql = MIGRATION.read_text(encoding="utf-8").lower()

        self.assertIn("create or replace function publication.block_public_projection_version", sql)
        self.assertIn("state = 'revoked'", sql)
        self.assertIn("origin, reason_code, publication_state", sql)
        self.assertIn("'authoritytrigger'", sql)
        self.assertIn("'pending'", sql)
        self.assertIn("'notapplicable'", sql)
        for forbidden in (
            "provider_token",
            "object_url",
            "display_body",
            "raw_content",
            "visitor_subject_hash",
        ):
            self.assertNotIn(forbidden, sql)

    def test_loader_accepts_lifecycle_execution_head(self) -> None:
        item = next(value for value in load_migrations(ROOT / "db/migrations") if value.version == "0082")
        self.assertEqual(item.name, "publication_lifecycle_execution")
        self.assertEqual(item.phase, "expand")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
