import unittest

from app.services.closed_pilot_admission import (
    ClosedPilotAdmissionError,
    ClosedPilotReadiness,
    build_closed_pilot_admission_plan,
)


def ready(reason="gatePassed"):
    return ClosedPilotReadiness(ready=True, reason=reason)


class ClosedPilotAdmissionTests(unittest.TestCase):
    def test_allows_one_ready_synthetic_step_and_emits_rollback_without_raw_ids(self):
        result = build_closed_pilot_admission_plan(
            owner_ids=["synthetic-owner-001", "synthetic-owner-002"],
            current_features=["ownerTextCaptureV1"],
            requested_features=["ownerTextCaptureV1", "ownerTruthCandidateReview"],
            readiness={
                "ownerTextCaptureV1": ready(),
                "ownerTruthCandidateReview": ready("candidateGatePassed"),
            },
        )
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["nextFeature"], "ownerTruthCandidateReview")
        self.assertEqual(
            result["rollback"]["requestedFeatures"], ["ownerTextCaptureV1"]
        )
        self.assertTrue(result["rollback"]["requiresNewPolicyRevision"])
        self.assertNotIn("synthetic-owner-001", str(result))

    def test_blocks_missing_readiness_kill_switch_real_account_and_skipped_order(self):
        blocked = build_closed_pilot_admission_plan(
            owner_ids=["synthetic-owner-001"],
            current_features=[],
            requested_features=["ownerTextCaptureV1"],
            readiness={},
        )
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["blockedReasons"], ["readinessMissing"])

        fixtures = [
            dict(
                owner_ids=["real-owner"],
                current_features=[],
                requested_features=["ownerTextCaptureV1"],
                readiness={"ownerTextCaptureV1": ready()},
            ),
            dict(
                owner_ids=["synthetic-owner-001"],
                current_features=[],
                requested_features=["ownerTextCaptureV1"],
                readiness={"ownerTextCaptureV1": ready()},
                kill_switch_features=["ownerTextCaptureV1"],
            ),
            dict(
                owner_ids=["synthetic-owner-001"],
                current_features=[],
                requested_features=["ownerMediaCaptureV1"],
                readiness={"ownerMediaCaptureV1": ready()},
            ),
        ]
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                with self.assertRaises(ClosedPilotAdmissionError):
                    build_closed_pilot_admission_plan(**fixture)

    def test_rejects_multi_feature_activation_in_one_revision(self):
        with self.assertRaises(ClosedPilotAdmissionError) as raised:
            build_closed_pilot_admission_plan(
                owner_ids=["synthetic-owner-001"],
                current_features=[],
                requested_features=["ownerTextCaptureV1", "ownerTruthCandidateReview"],
                readiness={
                    "ownerTextCaptureV1": ready(),
                    "ownerTruthCandidateReview": ready(),
                },
            )
        self.assertEqual(raised.exception.code, "closedPilotSingleStepRequired")

    def test_family_contribution_requires_media_predecessors_and_has_explicit_rollback(self):
        current = [
            "ownerTextCaptureV1",
            "ownerTruthCandidateReview",
            "ownerMediaCaptureV1",
            "ownerMediaProcessingV1",
        ]
        result = build_closed_pilot_admission_plan(
            owner_ids=["synthetic-family-owner-001"],
            current_features=current,
            requested_features=[*current, "ownerTruthFamilyContribution"],
            readiness={
                feature: ready("syntheticGatePassed")
                for feature in [*current, "ownerTruthFamilyContribution"]
            },
        )

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["nextFeature"], "ownerTruthFamilyContribution")
        self.assertEqual(result["rollback"]["requestedFeatures"], current)
        self.assertEqual(
            result["rollback"]["emergencyDisable"],
            ["ownerTruthFamilyContribution"],
        )


if __name__ == "__main__":
    unittest.main()
