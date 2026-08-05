"""Static contract checks for the P2-S1 publication authority migration."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from app.db.migrator import load_migrations


ROOT = Path(__file__).parents[1]
MIGRATION = ROOT / "db/migrations/0079_publication_authority_public_projection.sql"
MANIFEST = ROOT / "db/migrations/0079_publication_authority_public_projection.json"
TRIGGER_FIX = ROOT / "db/migrations/0080_publication_authority_receipt_trigger_fix.sql"
TRIGGER_FIX_MANIFEST = ROOT / "db/migrations/0080_publication_authority_receipt_trigger_fix.json"
POSTGRES_SMOKE = ROOT / "scripts/backend-publication-authority-postgres-smoke.py"
POSTGRES_SMOKE_RUNNER = ROOT / "scripts/run-backend-publication-authority-postgres-smoke.sh"


class PublicationAuthorityMigrationContractTests(unittest.TestCase):
    def test_additive_default_off_independent_public_copy_schema_exists(self) -> None:
        sql = MIGRATION.read_text(encoding="utf-8").lower()
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual(manifest["version"], "0079")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertEqual(
            manifest["releaseFlags"],
            {
                "publicationAuthorityV1": False,
                "publicationPublicProjectionReadV1": False,
                "publicationProjectionInvalidationWorkerV1": False,
            },
        )
        for relation in (
            "publication_authority_receipts",
            "publication_draft_public_contents",
            "public_projections",
            "projection_invalidation_requests",
        ):
            self.assertIn(f"create table publication.{relation}", sql)
            self.assertIn(f"revoke all on table publication.{relation} from public;", sql)
        self.assertIn("publication_public_projections_content_immutable", sql)
        self.assertIn("publication_draft_public_contents_no_direct_identifiers", sql)
        self.assertIn("publication_public_projections_no_direct_identifiers", sql)
        self.assertIn("publication_authority_receipts_validate_authority", sql)
        self.assertIn("publication.validate_publication_authority_receipt", sql)
        self.assertIn("foreign key (vault_id, memory_version_id)", sql)
        self.assertIn("publication_block_projections_for_source_change", sql)
        self.assertIn("publication_block_projections_for_memory_change", sql)
        self.assertIn("publication_block_projections_for_current_version_change", sql)
        self.assertIn("publication_block_projections_for_vault_change", sql)
        self.assertIn("vaultauthoritychanged", sql)
        self.assertIn("memoryversionsuperseded", sql)

    def test_migration_never_adds_private_read_model_or_provider_fields(self) -> None:
        sql = MIGRATION.read_text(encoding="utf-8").lower()
        for forbidden in (
            "private_projection",
            "kblite",
            "source_payload",
            "object_url",
            "preview_url",
            "provider_token",
            "credential",
        ):
            self.assertNotIn(forbidden, sql)

    def test_migration_loader_accepts_publication_authority_head(self) -> None:
        migrations = load_migrations(ROOT / "db/migrations")
        item = next(value for value in migrations if value.version == "0079")
        self.assertEqual(item.name, "publication_authority_public_projection")
        self.assertEqual(item.phase, "expand")

        trigger_fix = next(value for value in migrations if value.version == "0080")
        self.assertEqual(trigger_fix.name, "publication_authority_receipt_trigger_fix")
        self.assertEqual(trigger_fix.phase, "expand")

    def test_trigger_fix_qualifies_the_pinned_memory_version_column(self) -> None:
        sql = TRIGGER_FIX.read_text(encoding="utf-8").lower()
        manifest = json.loads(TRIGGER_FIX_MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual(manifest["version"], "0080")
        self.assertEqual(manifest["compatibility"], "backwardCompatible")
        self.assertIn("stored_pinned_memory_version_id", sql)
        self.assertIn("select version.pinned_memory_version_id", sql)
        self.assertIn("from publication.publication_versions as version", sql)

    def test_disposable_postgres_smoke_is_explicit_about_its_database_boundary(self) -> None:
        smoke = POSTGRES_SMOKE.read_text(encoding="utf-8")
        runner = POSTGRES_SMOKE_RUNNER.read_text(encoding="utf-8")

        self.assertIn("DATABASE_URL is required", smoke)
        self.assertIn("CREATE DATABASE", smoke)
        self.assertIn("DROP DATABASE IF EXISTS", smoke)
        self.assertIn("0079", smoke)
        self.assertIn("0080", smoke)
        self.assertIn("backend-publication-authority-postgres-smoke.py", runner)
        self.assertIn("DATABASE_URL is required", runner)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
