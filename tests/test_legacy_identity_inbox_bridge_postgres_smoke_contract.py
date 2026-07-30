from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "scripts/backend-legacy-identity-inbox-bridge-postgres-smoke.py"
RUNNER = ROOT / "scripts/run-backend-legacy-identity-inbox-bridge-postgres-smoke.sh"


class LegacyIdentityInboxBridgePostgresSmokeContractTests(unittest.TestCase):
    def test_smoke_is_disposable_and_asserts_only_internal_bridge_boundaries(self) -> None:
        source = SMOKE.read_text(encoding="utf-8")
        module = ast.parse(source)
        functions = {node.name for node in module.body if isinstance(node, ast.FunctionDef)}

        self.assertTrue(
            {"create_database", "drop_database", "seed_verified_bridge", "exercise"}.issubset(functions)
        )
        self.assertIn("mailbox_letters", source)
        self.assertIn("async_effects.business_message_projections", source)
        self.assertIn("accountAccessNotActive", source)
        self.assertIn("accountDeletionNotActive", source)
        self.assertIn("legacy bridge accepted an immutable coordinate mutation", source)
        self.assertNotIn("requests.", source)
        self.assertNotIn("requests.", source)

    def test_deployed_runner_requires_database_and_executes_only_disposable_smoke(self) -> None:
        source = RUNNER.read_text(encoding="utf-8")

        self.assertIn("DATABASE_URL is required", source)
        self.assertIn("scripts/backend-legacy-identity-inbox-bridge-postgres-smoke.py", source)
        self.assertNotIn("python -m unittest", source)
        self.assertNotIn("mailbox_letters", source)
        self.assertNotIn("curl ", source)


if __name__ == "__main__":
    unittest.main()
