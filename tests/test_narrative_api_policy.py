import unittest

from app.api.narrative import _available_actions


class NarrativeApiPolicyTests(unittest.TestCase):
    def test_worker_off_keeps_reads_but_removes_generation_actions(self):
        self.assertIn(
            "confirmSetup",
            _available_actions("readyForConfirmation", generation_available=True),
        )
        self.assertNotIn(
            "confirmSetup",
            _available_actions("readyForConfirmation", generation_available=False),
        )
        self.assertIn(
            "editArtifact",
            _available_actions("outlineReview", generation_available=False),
        )


if __name__ == "__main__":
    unittest.main()
