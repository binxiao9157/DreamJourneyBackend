from __future__ import annotations

import json
from pathlib import Path
import unittest


class OwnerTruthAnswerFeedbackMigrationContractTests(unittest.TestCase):
    def test_feedback_receipts_are_additive_default_off_and_append_only(self) -> None:
        root = Path(__file__).resolve().parents[1]
        sql = (root / "db/migrations/0062_owner_truth_answer_feedback.sql").read_text(
            encoding="utf-8"
        )
        metadata = json.loads(
            (root / "db/migrations/0062_owner_truth_answer_feedback.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertIn("CREATE TABLE owner_truth.answer_feedback", sql)
        self.assertIn("UNIQUE (vault_id, answer_id)", sql)
        self.assertIn("metric_eligible", sql)
        self.assertIn("projectionUnavailable", sql)
        self.assertIn("owner_truth_answer_feedback_validate_insert", sql)
        self.assertIn("owner_truth_answer_feedback_reject_mutation", sql)
        self.assertIn("answer_authority_epoch IS DISTINCT FROM NEW.authority_epoch", sql)
        self.assertEqual(metadata["version"], "0062")
        self.assertEqual(metadata["phase"], "expand")
        self.assertFalse(metadata["releaseFlags"]["ownerTruthAnswerFeedbackQA"])
        self.assertFalse(metadata["releaseFlags"]["publicEchoFeedback"])


if __name__ == "__main__":
    unittest.main()
