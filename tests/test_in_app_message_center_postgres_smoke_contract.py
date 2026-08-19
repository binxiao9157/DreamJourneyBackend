from pathlib import Path
import unittest


class InAppMessageCenterPostgresSmokeContractTests(unittest.TestCase):
    def test_smoke_covers_lifecycle_isolation_and_closed_kinds(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = (root / "scripts/backend-in-app-message-center-postgres-smoke.py").read_text(
            encoding="utf-8"
        )
        wrapper = (
            root / "scripts/run-backend-in-app-message-center-postgres-smoke.sh"
        ).read_text(encoding="utf-8")

        self.assertIn("InAppMessageKind.TIME_LETTER", source)
        self.assertIn("cross-account message read must fail closed", source)
        self.assertIn('["applied", "deduplicated"]', source)
        self.assertIn("message created after command must remain unread", source)
        self.assertIn("delete-read must not affect another account", source)
        self.assertIn('int(str(verified["expectedHead"])) >= 100', source)
        self.assertIn("DATABASE_URL", wrapper)


if __name__ == "__main__":
    unittest.main()
