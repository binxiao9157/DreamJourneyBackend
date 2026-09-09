from __future__ import annotations

from pathlib import Path
import os
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/run-owner-truth-memory-search-quality-evaluation.py"


class OwnerTruthMemorySearchQualityEvaluationRunnerTests(unittest.TestCase):
    def test_real_provider_evaluation_is_explicitly_cost_and_approval_gated(self) -> None:
        environment = dict(os.environ)
        environment.pop("OWNER_TRUTH_MEMORY_SEARCH_QUALITY_EVALUATION_APPROVED", None)
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
        self.assertIn("OWNER_TRUTH_MEMORY_SEARCH_QUALITY_EVALUATION_APPROVED=1", result.stderr)
        self.assertNotIn("http", result.stderr.casefold())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
