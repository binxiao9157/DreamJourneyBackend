from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/backend-owner-truth-candidate-decision-result-postgres-smoke.py"


class OwnerTruthCandidateDecisionResultPostgresSmokeContractTests(unittest.TestCase):
    def test_smoke_uses_disposable_database_and_covers_transaction_boundaries(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn("DATABASE_URL is required", source)
        self.assertIn("create_database(admin_dsn, database_name)", source)
        self.assertIn("drop_database(admin_dsn, database_name)", source)
        self.assertIn("uncommitted receipt must be invisible", source)
        self.assertIn("failed POST must roll back every business row", source)
        self.assertIn("concurrent replay must create once and deduplicate once", source)
        self.assertIn("decision-result GET must not write business or effect rows", source)
        self.assertNotIn("DELETE FROM owner_truth", source)
        self.assertNotIn("TRUNCATE", source)


if __name__ == "__main__":
    unittest.main()
