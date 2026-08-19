from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SQL = ROOT / "db/migrations/0103_publication_version_revision.sql"
MIGRATION_JSON = MIGRATION_SQL.with_suffix(".json")


class PublicationVersionRevisionMigrationContractTests(unittest.TestCase):
    def test_revision_drafts_bind_base_and_target_versions(self) -> None:
        sql = MIGRATION_SQL.read_text(encoding="utf-8").lower()

        self.assertIn("base_publication_version_id uuid", sql)
        self.assertIn("target_version_number bigint not null default 1", sql)
        self.assertIn("publication_drafts_base_version_scope", sql)
        self.assertIn("references publication.publication_versions", sql)

    def test_old_projection_is_retained_and_only_one_version_can_be_active(self) -> None:
        sql = MIGRATION_SQL.read_text(encoding="utf-8").lower()

        self.assertIn("'superseded'", sql)
        self.assertIn("publication_one_active_projection_per_publication", sql)
        self.assertIn("where state = 'active'", sql)
        self.assertNotIn("delete from publication.public_projections", sql)

    def test_manifest_declares_v3_runtime_compatibility(self) -> None:
        metadata = json.loads(MIGRATION_JSON.read_text(encoding="utf-8"))

        self.assertEqual(metadata["version"], "0103")
        self.assertEqual(metadata["runtimeCompatibility"], "publicationAuthorityV3")
        self.assertFalse(metadata["requiresOldWorkerDrain"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
