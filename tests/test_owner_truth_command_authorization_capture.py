import unittest

from app.domain.owner_truth.contracts import OwnerTruthContractError
from app.domain.owner_truth.source_commands import OwnerTruthCommandAuthorizationCapture


def _capture(**overrides: object) -> OwnerTruthCommandAuthorizationCapture:
    value: dict[str, object] = {
        "feature": "ownerTruthCandidateReview",
        "policy_version": "release-policy-v1",
        "policy_revision": 1,
        "emergency_revision": 0,
        "account_generation_hash": "a" * 24,
        "decision_id_hash": "b" * 64,
        "audience": "owner",
        "cohort": "closedPilotAdultSelf",
        "client_build": 1,
        "expires_at": "2026-07-22T00:00:00+00:00",
    }
    value.update(overrides)
    return OwnerTruthCommandAuthorizationCapture(**value)  # type: ignore[arg-type]


class OwnerTruthCommandAuthorizationCaptureTests(unittest.TestCase):
    def test_accepts_value_minimized_hashes(self) -> None:
        capture = _capture()

        self.assertEqual(capture.value_minimized_payload()["accountGenerationHash"], "a" * 24)
        self.assertEqual(capture.value_minimized_payload()["decisionIdHash"], "b" * 64)

    def test_rejects_non_opaque_or_non_sha256_hashes(self) -> None:
        with self.assertRaisesRegex(OwnerTruthContractError, "account_generation_hash"):
            _capture(account_generation_hash="session-id-must-not-be-stored")
        with self.assertRaisesRegex(OwnerTruthContractError, "decision_id_hash"):
            _capture(decision_id_hash="not-a-digest")


if __name__ == "__main__":
    unittest.main()
