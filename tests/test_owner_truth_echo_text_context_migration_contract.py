import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SQL = ROOT / "db/migrations/0111_owner_truth_echo_text_context.sql"
MIGRATION_MANIFEST = ROOT / "db/migrations/0111_owner_truth_echo_text_context.json"


class OwnerTruthEchoTextContextMigrationContractTests(unittest.TestCase):
    def test_text_context_is_additive_expiring_and_not_a_formal_memory_writer(self) -> None:
        manifest = json.loads(MIGRATION_MANIFEST.read_text(encoding="utf-8"))
        sql = MIGRATION_SQL.read_text(encoding="utf-8")

        self.assertEqual(manifest["version"], "0111")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertTrue(manifest["releaseFlags"]["ownerTruthEchoTextContext"])
        self.assertTrue(manifest["production"]["requiresPostgresIntegrationEvidence"])
        self.assertIn("echo_conversation_contexts", sql)
        self.assertIn("expires_at", sql)
        self.assertIn("ON DELETE CASCADE", sql)
        self.assertNotIn("INSERT INTO owner_truth.memory_candidates", sql)
        self.assertNotIn("INSERT INTO owner_truth.memory_versions", sql)
        self.assertNotIn("INSERT INTO owner_truth.sources", sql)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
