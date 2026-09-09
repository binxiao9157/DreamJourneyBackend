from __future__ import annotations

from pathlib import Path
import os
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/run-owner-truth-real-deepseek-validation.py"


class OwnerTruthRealDeepSeekValidationRunnerTests(unittest.TestCase):
    def test_real_provider_validation_is_explicitly_cost_gated_and_value_free(self) -> None:
        environment = dict(os.environ)
        environment.pop("OWNER_TRUTH_REAL_MODEL_VALIDATION_APPROVED", None)
        environment.pop("DEEPSEEK_API_KEY", None)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["PYTHONPYCACHEPREFIX"] = "/private/tmp/dreamjourney-pycache"
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 3)
        self.assertIn("OWNER_TRUTH_REAL_MODEL_VALIDATION_APPROVED=1", result.stderr)
        self.assertNotIn("晨光大学", result.stderr)
        self.assertNotIn("Authorization", result.stderr)

    def test_script_does_not_read_application_store_or_emit_model_content(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertNotIn("PostgresStore", source)
        self.assertNotIn("InMemoryStore", source)
        self.assertIn('"responseContentRetained": False', source)
        self.assertIn('"privateVaultRead": False', source)
        self.assertEqual(source.count("request_organization("), 2)
        self.assertEqual(source.count("request_answer("), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
