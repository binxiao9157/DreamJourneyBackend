from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "db/migrations/0088_apns_postgres_outbox.sql").read_text()
MANIFEST = (ROOT / "db/migrations/0088_apns_postgres_outbox.json").read_text()


class APNSPostgresOutboxMigrationContractTests(unittest.TestCase):
    def test_migration_keeps_tokens_encrypted_and_receipts_append_only(self) -> None:
        self.assertIn("notification.apns_token_secrets", SQL)
        self.assertIn("ciphertext", SQL)
        self.assertNotIn("device_token text", SQL.lower())
        self.assertIn("notification.apns_delivery_outbox", SQL)
        self.assertIn("registration_generation", SQL)
        self.assertIn(
            'generation=int(row.get("registration_generation") or 0)',
            (ROOT / "app/services/apns_postgres_outbox.py").read_text(),
        )
        self.assertIn("FOR UPDATE", (ROOT / "app/services/apns_postgres_outbox.py").read_text())
        self.assertIn("SKIP LOCKED", (ROOT / "app/services/apns_postgres_outbox.py").read_text())
        self.assertIn("notification.apns_delivery_receipts", SQL)
        self.assertIn('"apnsDelivery": false', MANIFEST)


if __name__ == "__main__":
    unittest.main()
