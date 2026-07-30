from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "scripts/backend-time-letter-recipient-admission-postgres-smoke.py"
RUNNER = ROOT / "scripts/run-backend-time-letter-recipient-admission-postgres-smoke.sh"
ADMISSION_SERVICE = ROOT / "app/async_effects/business_message_recipient_admission.py"


class TimeLetterRecipientAdmissionPostgresSmokeContractTests(unittest.TestCase):
    def test_smoke_is_disposable_and_retains_read_only_admission_boundaries(self) -> None:
        source = SMOKE.read_text(encoding="utf-8")
        admission_service = ADMISSION_SERVICE.read_text(encoding="utf-8")
        module = ast.parse(source)
        functions = {node.name for node in module.body if isinstance(node, ast.FunctionDef)}

        self.assertTrue(
            {"create_database", "drop_database", "seed_identity_bridge", "exercise"}.issubset(functions)
        )
        self.assertIn("record_receipt=False", admission_service)
        self.assertIn("mailbox_letters", source)
        self.assertIn("async_effects.business_message_projections", source)
        self.assertIn("delegatedAccessDenied:activeGrantRequired", source)
        self.assertIn("recipientInboxUnavailable", source)
        self.assertNotIn("requests.", source)

    def test_deployed_runner_requires_database_and_executes_only_disposable_smoke(self) -> None:
        source = RUNNER.read_text(encoding="utf-8")

        self.assertIn("DATABASE_URL is required", source)
        self.assertIn(
            "scripts/backend-time-letter-recipient-admission-postgres-smoke.py",
            source,
        )
        self.assertNotIn("python -m unittest", source)
        self.assertNotIn("curl ", source)


if __name__ == "__main__":
    unittest.main()
