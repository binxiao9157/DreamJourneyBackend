import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SQL = ROOT / "db/migrations/0036_owner_truth_interview_confirmation_authority_receipts.sql"
MIGRATION_MANIFEST = ROOT / "db/migrations/0036_owner_truth_interview_confirmation_authority_receipts.json"


class OwnerTruthInterviewConfirmationAuthorityReceiptsMigrationContractTests(unittest.TestCase):
    def test_additive_authority_capture_and_receipt_links_remain_private(self) -> None:
        manifest = json.loads(MIGRATION_MANIFEST.read_text(encoding="utf-8"))
        sql = MIGRATION_SQL.read_text(encoding="utf-8")

        self.assertEqual(manifest["version"], "0036")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertFalse(manifest["releaseFlags"]["ownerTruthInterviewConfirmation"])
        self.assertIn("ADD COLUMN authorization_evidence JSONB", sql)
        self.assertIn(
            "CREATE TABLE owner_truth.interview_review_batch_candidate_decision_receipts",
            sql,
        )
        self.assertIn("UNIQUE (vault_id, decision_receipt_id)", sql)
        self.assertIn("candidate_command_id_hash TEXT NOT NULL", sql)
        self.assertIn("CREATE TRIGGER owner_truth_batch_decision_auth_evidence_validate", sql)
        self.assertLessEqual(len("owner_truth_batch_decision_auth_evidence_validate"), 63)
        self.assertIn("authorization evidence is malformed", sql)
        self.assertIn("receipt link does not match root authority", sql)
        self.assertIn("receipt_command_id_hash IS DISTINCT FROM NEW.candidate_command_id_hash", sql)
        self.assertIn("candidate_source_version IS DISTINCT FROM admission_source_version", sql)
        self.assertIn("owner_truth.conversation_append_only", sql)
        self.assertNotIn("INSERT INTO owner_truth.memories", sql)
        self.assertNotIn("INSERT INTO owner_truth.memory_versions", sql)
        self.assertNotIn("CREATE FUNCTION public.", sql)


if __name__ == "__main__":
    unittest.main()
