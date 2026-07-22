import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SQL = ROOT / "db/migrations/0037_owner_truth_interview_confirmation_feature_constraint.sql"
MIGRATION_MANIFEST = ROOT / "db/migrations/0037_owner_truth_interview_confirmation_feature_constraint.json"


class OwnerTruthInterviewConfirmationFeatureConstraintMigrationContractTests(unittest.TestCase):
    def test_formal_capture_is_limited_to_the_confirmation_feature(self) -> None:
        manifest = json.loads(MIGRATION_MANIFEST.read_text(encoding="utf-8"))
        sql = MIGRATION_SQL.read_text(encoding="utf-8")

        self.assertEqual(manifest["version"], "0037")
        self.assertEqual(manifest["phase"], "contract")
        self.assertEqual(manifest["compatibility"], "backwardCompatible")
        self.assertFalse(manifest["releaseFlags"]["ownerTruthInterviewConfirmation"])
        self.assertIn(
            "ADD CONSTRAINT owner_truth_interview_batch_decision_formal_feature",
            sql,
        )
        self.assertIn("VALIDATE CONSTRAINT owner_truth_interview_batch_decision_formal_feature", sql)
        self.assertIn("NOT VALID", sql)
        self.assertIn("ownerTruthCandidateReview", sql)
        self.assertIn("CREATE OR REPLACE FUNCTION owner_truth.validate_interview_batch_candidate_authorization_evidence", sql)
        self.assertIn("authorization evidence is malformed", sql)
        self.assertIn("'{}'::JSONB", sql)
        created_names = re.findall(
            r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:FUNCTION|TRIGGER|TABLE|INDEX)\s+([A-Za-z0-9_.]+)",
            sql,
            flags=re.IGNORECASE,
        )
        self.assertTrue(created_names)
        self.assertTrue(
            all(len(name.rsplit(".", 1)[-1]) <= 63 for name in created_names),
            "Postgres silently truncates identifiers over 63 characters",
        )
        self.assertNotIn("INSERT INTO owner_truth.memories", sql)
        self.assertNotIn("INSERT INTO owner_truth.memory_versions", sql)
        self.assertNotIn("CREATE FUNCTION public.", sql)


if __name__ == "__main__":
    unittest.main()
