from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/backend-owner-truth-real-deepseek-postgres-smoke.py"


class OwnerTruthRealDeepSeekPostgresSmokeContractTests(unittest.TestCase):
    def test_real_provider_postgres_smoke_is_opt_in_synthetic_and_value_free(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn("OWNER_TRUTH_REAL_MODEL_POSTGRES_VALIDATION_APPROVED", source)
        self.assertIn("syntheticRealProviderValidation", source)
        self.assertIn('"privateVaultRead": False', source)
        self.assertIn('"responseContentRetained": False', source)
        self.assertIn("drop_database(admin_dsn, database_name)", source)
        self.assertNotIn("print(new_fact", source)
        self.assertNotIn("print(bound", source)
        self.assertNotIn("print(search", source)


if __name__ == "__main__":
    unittest.main()
