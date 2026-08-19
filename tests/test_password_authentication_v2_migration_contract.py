import json
from pathlib import Path
import unittest

from app.db.migrator import load_migrations


ROOT = Path(__file__).resolve().parents[1]
SQL_PATH = ROOT / "db/migrations/0094_password_authentication_v2.sql"
MANIFEST_PATH = ROOT / "db/migrations/0094_password_authentication_v2.json"


class PasswordAuthenticationV2MigrationContractTests(unittest.TestCase):
    def test_0094_adds_lockout_and_single_use_action_grants(self):
        sql = SQL_PATH.read_text(encoding="utf-8")
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

        self.assertEqual(manifest["version"], "0094")
        self.assertEqual(manifest["phase"], "expand")
        self.assertIn("CREATE TABLE password_login_states", sql)
        self.assertIn("CREATE TABLE password_action_grants", sql)
        self.assertIn("'passwordreset'", sql)
        self.assertIn("'sensitiveoperation'", sql)
        self.assertIn("token_hash TEXT NOT NULL UNIQUE", sql)

    def test_migration_loader_accepts_password_authentication_head(self):
        migrations = load_migrations(ROOT / "db/migrations")

        self.assertEqual(migrations[-1].version, "0097")
        self.assertEqual(migrations[-1].name, "voice_profile_creation_quota")


if __name__ == "__main__":
    unittest.main()
