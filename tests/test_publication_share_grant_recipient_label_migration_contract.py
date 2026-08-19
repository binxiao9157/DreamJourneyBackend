from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SQL = ROOT / "db/migrations/0104_publication_share_grant_recipient_label.sql"
MANIFEST = SQL.with_suffix(".json")


class PublicationShareGrantRecipientLabelMigrationContractTests(unittest.TestCase):
    def test_migration_adds_only_a_bounded_display_safe_label(self) -> None:
        sql = SQL.read_text(encoding="utf-8").lower()

        self.assertIn("add column grantee_display_label text not null", sql)
        self.assertIn("char_length(grantee_display_label) <= 80", sql)
        self.assertNotIn("add column phone", sql)
        self.assertNotIn("grantee_subject_id", sql)

    def test_manifest_is_additive_and_keeps_visitor_release_closed(self) -> None:
        metadata = json.loads(MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual(metadata["version"], "0104")
        self.assertEqual(metadata["compatibility"], "additive")
        self.assertFalse(metadata["requiresOldWorkerDrain"])
        self.assertFalse(metadata["releaseFlags"]["visitorAccess"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
