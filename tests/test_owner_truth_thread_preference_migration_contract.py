from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SQL = ROOT / "db/migrations/0040_owner_truth_thread_preferences.sql"
MIGRATION_MANIFEST = MIGRATION_SQL.with_suffix(".json")


class OwnerTruthThreadPreferenceMigrationContractTests(unittest.TestCase):
    def test_preferences_are_additive_value_minimized_and_receipts_are_append_only(self) -> None:
        sql = MIGRATION_SQL.read_text(encoding="utf-8")
        manifest = json.loads(MIGRATION_MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual(manifest["version"], "0040")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertFalse(manifest["releaseFlags"]["ownerTruthInterviewThreadPreferenceQa"])
        self.assertIn("CREATE TABLE owner_truth.thread_preferences", sql)
        self.assertIn("CREATE TABLE owner_truth.thread_preference_receipts", sql)
        self.assertIn("cooldown_until", sql)
        self.assertIn("UNIQUE (vault_id, thread_id)", sql)
        self.assertIn("UNIQUE (vault_id, command_id_hash)", sql)
        self.assertIn("validate_thread_preference_receipt", sql)
        self.assertIn("owner_truth_thread_preference_receipts_no_update", sql)
        self.assertIn("owner_truth_thread_preference_receipts_no_delete", sql)
        self.assertIn("session_boundary IS DISTINCT FROM NEW.preference", sql)
        self.assertNotIn("topic_title", sql)
        self.assertNotIn("transcript TEXT", sql)
        self.assertNotIn("message_text", sql)
        self.assertNotIn("provider_payload", sql)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
