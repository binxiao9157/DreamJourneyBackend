from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SQL = ROOT / "db/migrations/0039_owner_truth_saved_continuation_cues.sql"
MIGRATION_MANIFEST = MIGRATION_SQL.with_suffix(".json")


class OwnerTruthSavedContinuationMigrationContractTests(unittest.TestCase):
    def test_cues_are_additive_explicit_and_append_only(self) -> None:
        sql = MIGRATION_SQL.read_text(encoding="utf-8")
        manifest = json.loads(MIGRATION_MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual(manifest["version"], "0039")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertFalse(manifest["releaseFlags"]["ownerTruthSavedContinuationCueQa"])
        self.assertIn("CREATE TABLE owner_truth.saved_continuation_cues", sql)
        self.assertIn("UNIQUE (vault_id, command_id_hash)", sql)
        self.assertIn("UNIQUE (vault_id, session_id)", sql)
        self.assertIn("expected_session_version", sql)
        self.assertIn("session_row_version IS DISTINCT FROM NEW.expected_session_version", sql)
        self.assertIn("saved continuation cue facet is already covered", sql)
        self.assertIn("owner_truth_saved_continuation_cues_no_update", sql)
        self.assertIn("owner_truth_saved_continuation_cues_no_delete", sql)
        self.assertNotIn("transcript TEXT", sql)
        self.assertNotIn("question_text", sql.lower())
        self.assertNotIn("payload JSONB", sql)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
