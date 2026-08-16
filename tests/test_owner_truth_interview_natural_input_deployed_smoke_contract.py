import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPT = ROOT_DIR / "scripts" / "backend-owner-truth-interview-natural-input-deployed-smoke.py"


class OwnerTruthInterviewNaturalInputDeployedSmokeContractTests(unittest.TestCase):
    def test_current_session_expectation_tracks_the_public_resume_handle(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn('"schemaVersion": "owner-truth-interview-current-session-v1"', source)
        self.assertIn('"entryMode": "naturalInput"', source)
        self.assertIn('"formalSessionExitCreatedHiddenReviewBatch": True', source)


if __name__ == "__main__":
    unittest.main()
