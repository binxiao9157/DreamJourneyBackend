from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SQL = ROOT / "db/migrations/0045_owner_truth_knowledge_recommendation_feedback_receipts.sql"
MIGRATION_MANIFEST = MIGRATION_SQL.with_suffix(".json")


class OwnerTruthKnowledgeRecommendationFeedbackMigrationContractTests(unittest.TestCase):
    def test_feedback_receipts_are_additive_value_free_and_append_only(self) -> None:
        sql = MIGRATION_SQL.read_text(encoding="utf-8")
        manifest = json.loads(MIGRATION_MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual(manifest["version"], "0045")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertFalse(manifest["releaseFlags"]["ownerTruthKnowledgeRecommendationFeedbackQa"])
        self.assertIn(
            "CREATE TABLE owner_truth.knowledge_recommendation_feedback_receipts",
            sql,
        )
        self.assertIn("UNIQUE (vault_id, command_id_hash)", sql)
        self.assertIn("UNIQUE (vault_id, candidate_id)", sql)
        self.assertIn("feedback_action = 'replace'", sql)
        self.assertIn("feedback_reason = 'topicPreference'", sql)
        self.assertIn("session_row_version IS DISTINCT FROM NEW.expected_session_version", sql)
        self.assertIn("owner_truth_knowledge_recommendation_feedback_receipts_no_update", sql)
        self.assertIn("owner_truth_knowledge_recommendation_feedback_receipts_no_delete", sql)
        self.assertNotIn("question_text", sql.lower())
        self.assertNotIn("transcript TEXT", sql)
        self.assertNotIn("payload JSONB", sql)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
