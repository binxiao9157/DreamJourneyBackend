from pathlib import Path
import unittest


class InAppMessageCenterMigrationContractTests(unittest.TestCase):
    def test_migration_keeps_projection_immutable_and_adds_monotonic_lifecycle(self) -> None:
        sql = (
            Path(__file__).resolve().parents[1]
            / "db/migrations/0100_in_app_message_center.sql"
        ).read_text(encoding="utf-8")

        self.assertIn("CREATE TABLE async_effects.in_app_message_lifecycle", sql)
        self.assertIn("CREATE TABLE async_effects.in_app_message_commands", sql)
        self.assertIn("OLD.state = 'deleted'", sql)
        self.assertIn("OLD.state = 'read' AND NEW.state <> 'deleted'", sql)
        self.assertIn("NEW.read_at < projection_created_at", sql)
        self.assertIn("projection.created_at <= %s", (
            Path(__file__).resolve().parents[1]
            / "app/services/in_app_message_center.py"
        ).read_text(encoding="utf-8"))
        self.assertIn("in_app_message_lifecycle_no_delete", sql)
        self.assertIn("in_app_message_commands_no_update", sql)
        self.assertIn("in_app_message_commands_no_delete", sql)
        self.assertNotIn("DROP TABLE async_effects.business_message_projections", sql)
        self.assertIn("'candidateReady'", sql)
        self.assertIn("'taskRetryRequired'", sql)


if __name__ == "__main__":
    unittest.main()
