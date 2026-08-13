import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SQL_PATH = ROOT / "db/migrations/0093_realtime_voice_subject_authority.sql"
MANIFEST_PATH = ROOT / "db/migrations/0093_realtime_voice_subject_authority.json"


class RealtimeVoiceSubjectAuthorityMigrationContractTests(unittest.TestCase):
    def test_0093_moves_new_ticket_ownership_to_strong_identity_subjects(self):
        sql = SQL_PATH.read_text(encoding="utf-8").lower()
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

        self.assertEqual(manifest["version"], "0093")
        self.assertEqual(manifest["phase"], "contract")
        self.assertEqual(manifest["compatibility"], "contract")
        self.assertTrue(manifest["requiresOldWorkerDrain"])
        self.assertIn(
            "drop constraint realtime_voice_session_tickets_user_id_fkey",
            sql,
        )
        self.assertIn(
            "constraint realtime_voice_session_tickets_subject_id_fkey",
            sql,
        )
        self.assertIn("foreign key (user_id) references subjects(id)", sql)
        self.assertNotIn("foreign key (user_id) references users(id)", sql)
        self.assertIn("not valid", sql)
        for destructive in ("drop table", "truncate ", "delete from"):
            self.assertNotIn(destructive, sql)


if __name__ == "__main__":
    unittest.main()
