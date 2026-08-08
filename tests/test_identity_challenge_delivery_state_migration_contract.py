import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SQL_PATH = ROOT / "db" / "migrations" / "0085_identity_challenge_delivery_state.sql"
MANIFEST_PATH = ROOT / "db" / "migrations" / "0085_identity_challenge_delivery_state.json"


class IdentityChallengeDeliveryStateMigrationContractTests(unittest.TestCase):
    def test_0085_is_additive_redacted_delivery_state(self):
        sql = SQL_PATH.read_text(encoding="utf-8")
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

        self.assertEqual(manifest["version"], "0085")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        for column in (
            "delivery_state",
            "recovery_state",
            "provider_receipt_hash",
            "provider_retry_after_seconds",
            "recovery_attempts",
            "provider_checked_at",
            "provider_delivered_at",
        ):
            self.assertIn(column, sql)
        self.assertIn("REVOKE ALL ON TABLE auth_challenges FROM PUBLIC", sql)
        self.assertNotRegex(sql.lower(), r"add column\s+(phone|target|code|receipt_id)\b")
        for destructive in ("DROP TABLE", "TRUNCATE ", "DELETE FROM"):
            self.assertNotIn(destructive, sql.upper())


if __name__ == "__main__":
    unittest.main()
