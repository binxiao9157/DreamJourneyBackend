"""Static safety contract for P2-S4C external cleanup evidence."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from app.db.migrator import load_migrations


ROOT = Path(__file__).parents[1]
MIGRATION = ROOT / "db/migrations/0083_publication_lifecycle_external_cleanup.sql"
MANIFEST = ROOT / "db/migrations/0083_publication_lifecycle_external_cleanup.json"
SERVICE = ROOT / "app/services/publication_external_cleanup.py"


class PublicationExternalCleanupMigrationContractTests(unittest.TestCase):
    def test_external_cleanup_is_additive_default_off_and_receipt_bound(self) -> None:
        sql = MIGRATION.read_text(encoding="utf-8").lower()
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual(manifest["version"], "0083")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertFalse(manifest["releaseFlags"]["publicationExternalCleanupWorkerV1"])
        self.assertFalse(manifest["releaseFlags"]["publicationExternalCleanupMaterializerV1"])
        self.assertIn("create table publication.lifecycle_external_cleanup_effects", sql)
        self.assertIn("create table publication.lifecycle_external_cleanup_receipts", sql)
        self.assertIn("references publication.publication_lifecycle_receipts", sql)
        self.assertIn("references async_effects.operations", sql)
        self.assertIn("references async_effects.provider_effects", sql)
        self.assertIn("provider_receipt_hash text check", sql)
        self.assertIn("unique (effect_id, observation_hash)", sql)
        self.assertIn(
            "state <> 'completed' or (provider_receipt_present and provider_receipt_hash is not null)",
            sql,
        )
        self.assertIn("publication_lifecycle_external_cleanup_receipts_no_update_or_delete", sql)

    def test_migration_stores_only_opaque_effect_evidence(self) -> None:
        sql = MIGRATION.read_text(encoding="utf-8").lower()

        for forbidden in (
            "provider_token",
            "object_url",
            "source_payload",
            "private_projection",
            "display_body",
            "raw_content",
            "visitor_subject_hash",
        ):
            self.assertNotIn(forbidden, sql)

    def test_loader_accepts_external_cleanup_head(self) -> None:
        item = next(value for value in load_migrations(ROOT / "db/migrations") if value.version == "0083")
        self.assertEqual(item.name, "publication_lifecycle_external_cleanup")
        self.assertEqual(item.phase, "expand")

    def test_receipt_insert_uses_the_migration_idempotency_constraint(self) -> None:
        service = SERVICE.read_text(encoding="utf-8")
        self.assertIn("ON CONFLICT (effect_id, observation_hash) DO NOTHING", service)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
