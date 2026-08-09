import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SQL_PATH = ROOT / "db/migrations/0087_owner_truth_family_contribution_review.sql"
MANIFEST_PATH = ROOT / "db/migrations/0087_owner_truth_family_contribution_review.json"


class OwnerTruthFamilyContributionReviewMigrationContractTests(unittest.TestCase):
    def test_review_migration_is_additive_default_off_and_owner_fenced(self):
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        sql = SQL_PATH.read_text(encoding="utf-8")

        self.assertEqual(manifest["version"], "0087")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertFalse(manifest["releaseFlags"]["ownerTruthFamilyContribution"])
        self.assertIn("family_contribution_submissions", sql)
        self.assertIn("pendingReview", sql)
        self.assertIn("accepted", sql)
        self.assertIn("rejected", sql)
        self.assertIn("withdrawn", sql)
        self.assertIn("REVOKE ALL", sql)
        self.assertNotIn("GRANT SELECT", sql)


if __name__ == "__main__":
    unittest.main()
