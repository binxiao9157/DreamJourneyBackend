from __future__ import annotations

import json
from pathlib import Path
import unittest

from app.db.migrator import default_migrations_dir, load_migrations


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SQL = (
    ROOT / "db/migrations/0068_owner_truth_guided_recommendation_activation_binding.sql"
)
MIGRATION_JSON = MIGRATION_SQL.with_suffix(".json")


class OwnerTruthGuidedRecommendationActivationBindingMigrationContractTests(unittest.TestCase):
    def test_expands_only_opaque_guided_recommendation_set_binding(self) -> None:
        migration = next(
            item
            for item in load_migrations(default_migrations_dir())
            if item.version == "0068"
        )
        metadata = json.loads(MIGRATION_JSON.read_text(encoding="utf-8"))

        self.assertEqual(
            migration.name,
            "owner_truth_guided_recommendation_activation_binding",
        )
        self.assertEqual(migration.phase, "expand")
        self.assertEqual(migration.compatibility, "additive")
        self.assertEqual(
            metadata["runtimeCompatibility"],
            "ownerTruthGuidedRecommendationActivationDefaultOff",
        )
        self.assertFalse(metadata["releaseFlags"]["echoGuidedRecommendations"])

        sql = MIGRATION_SQL.read_text(encoding="utf-8")
        self.assertIn(
            "ADD COLUMN guided_recommendation_set_id TEXT NULL",
            sql,
        )
        self.assertIn("^[a-f0-9]{64}$", sql)
        self.assertNotIn("question_text", sql.lower())
        self.assertNotIn("narrative TEXT", sql)
        self.assertNotIn("payload JSONB", sql)
        self.assertNotIn("UPDATE owner_truth.knowledge_recommendation_activation_receipts", sql)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
