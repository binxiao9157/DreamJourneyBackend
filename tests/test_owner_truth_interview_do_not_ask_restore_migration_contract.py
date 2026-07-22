import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SQL = ROOT / "db/migrations/0038_owner_truth_interview_do_not_ask_restore_receipts.sql"
MIGRATION_MANIFEST = ROOT / "db/migrations/0038_owner_truth_interview_do_not_ask_restore_receipts.json"


class OwnerTruthInterviewDoNotAskRestoreMigrationContractTests(unittest.TestCase):
    def test_restore_receipt_is_additive_and_has_only_the_session_boundary_shape(self) -> None:
        manifest = json.loads(MIGRATION_MANIFEST.read_text(encoding="utf-8"))
        sql = MIGRATION_SQL.read_text(encoding="utf-8")

        self.assertEqual(manifest["version"], "0038")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertFalse(manifest["releaseFlags"]["ownerTruthInterviewDoNotAskRestoreM0A"])
        self.assertIn("restoreDoNotAskInterviewBoundary", sql)
        self.assertIn("expected_thread_version IS NULL", sql)
        self.assertIn("expected_session_version IS NOT NULL", sql)
        self.assertIn("expected_review_batch_version IS NULL", sql)
        self.assertNotIn("INSERT INTO owner_truth.memory_candidates", sql)
        self.assertNotIn("INSERT INTO owner_truth.memories", sql)
        self.assertNotIn("INSERT INTO owner_truth.memory_versions", sql)


if __name__ == "__main__":
    unittest.main()
