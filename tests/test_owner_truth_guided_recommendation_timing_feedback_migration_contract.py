from __future__ import annotations

import json
from pathlib import Path
import unittest

from app.db.migrator import default_migrations_dir, load_migrations


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SQL = (
    ROOT / "db/migrations/0066_owner_truth_guided_recommendation_timing_feedback.sql"
)
MIGRATION_JSON = MIGRATION_SQL.with_suffix(".json")


class OwnerTruthGuidedRecommendationTimingFeedbackMigrationContractTests(unittest.TestCase):
    def test_expands_only_value_free_guided_timing_feedback(self) -> None:
        migration = next(
            item
            for item in load_migrations(default_migrations_dir())
            if item.version == "0066"
        )
        metadata = json.loads(MIGRATION_JSON.read_text(encoding="utf-8"))

        self.assertEqual(
            migration.name,
            "owner_truth_guided_recommendation_timing_feedback",
        )
        self.assertEqual(migration.phase, "expand")
        self.assertEqual(migration.compatibility, "additive")
        self.assertEqual(
            metadata["runtimeCompatibility"],
            "ownerTruthGuidedRecommendationTimingFeedbackDefaultOff",
        )
        self.assertFalse(metadata["releaseFlags"]["echoGuidedRecommendations"])

        sql = MIGRATION_SQL.read_text(encoding="utf-8")
        self.assertIn("knowledge_recommendation_feedback_receipts", sql)
        self.assertIn("feedback_action = 'defer'", sql)
        self.assertIn("feedback_reason = 'timing'", sql)
        self.assertIn("feedback_scope = 'candidate'", sql)
        self.assertIn("DROP CONSTRAINT %I", sql)
        self.assertNotIn("question_text", sql.lower())
        self.assertNotIn("transcript TEXT", sql)
        self.assertNotIn("payload JSONB", sql)
        self.assertNotIn("UPDATE owner_truth.knowledge_recommendation_feedback_receipts", sql)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
