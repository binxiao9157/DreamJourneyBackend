from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "scripts/backend-business-message-projection-postgres-smoke.py"
RUNNER = ROOT / "scripts/run-backend-business-message-projection-postgres-smoke.sh"


class BusinessMessageProjectionPostgresSmokeContractTests(unittest.TestCase):
    def test_smoke_asserts_immutable_receipts_without_mutating_them_for_a_mismatch(self) -> None:
        source = SMOKE.read_text(encoding="utf-8")
        module = ast.parse(source)
        mutation_functions = []
        for node in module.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            body = ast.get_source_segment(source, node) or ""
            if "UPDATE async_effects.business_receipts" in body:
                mutation_functions.append(node.name)

        self.assertIn("assert_business_receipt_append_only", source)
        self.assertEqual(mutation_functions, ["assert_business_receipt_append_only"])

    def test_deployed_runner_requires_a_database_and_executes_only_the_disposable_smoke(self) -> None:
        source = RUNNER.read_text(encoding="utf-8")

        self.assertIn("DATABASE_URL is required", source)
        self.assertIn(
            "scripts/backend-business-message-projection-postgres-smoke.py",
            source,
        )
        self.assertNotIn("python -m unittest", source)
        self.assertNotIn("provider_query_operations", source)


if __name__ == "__main__":
    unittest.main()
