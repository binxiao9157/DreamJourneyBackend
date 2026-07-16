import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.services.release_policy import (
    ReleasePolicyService,
    ReleasePolicySnapshot,
    ReleasePolicyVersionDowngrade,
)


class ReleasePolicyServiceTests(unittest.TestCase):
    def test_closed_pilot_snapshot_explicitly_allows_only_owner_text_core(self):
        now = datetime(2026, 7, 16, 1, 0, tzinfo=timezone.utc)
        snapshot = ReleasePolicyService().build_snapshot(
            audience="owner",
            cohort="closedPilotAdultSelf",
            client_build=1,
            now=now,
        )

        self.assertEqual(snapshot.schemaVersion, 1)
        self.assertEqual(snapshot.policyVersion, "release-policy-v1")
        self.assertEqual(snapshot.policyRevision, 1)
        self.assertEqual(snapshot.audience, "owner")
        self.assertEqual(snapshot.cohort, "closedPilotAdultSelf")
        self.assertEqual(snapshot.issuedAt, now)
        self.assertEqual(snapshot.expiresAt, now + timedelta(seconds=300))
        self.assertEqual(snapshot.minClient, 1)
        self.assertEqual(snapshot.emergencyRevision, 0)
        self.assertTrue(snapshot.shadowMode)

        decisions = {item.feature: item for item in snapshot.features}
        self.assertTrue(decisions["echoTextInput"].releaseVisible)
        self.assertTrue(decisions["profileSettings"].releaseVisible)
        self.assertTrue(decisions["accountDeletion"].releaseVisible)
        for feature in [
            "familyManagement",
            "timeLetters",
            "voiceCloneShell",
            "digitalHumanLivePanel",
            "careDashboard",
            "archiveAudioUpload",
            "archiveVideoUpload",
        ]:
            self.assertFalse(decisions[feature].releaseVisible, feature)
            self.assertEqual(decisions[feature].reason, "notApprovedForClosedPilot")

    def test_unknown_feature_is_returned_as_explicit_fail_closed_decision(self):
        snapshot = ReleasePolicyService().build_snapshot(
            audience="owner",
            cohort="closedPilotAdultSelf",
            client_build=1,
            requested_feature="futureUnknownFeature",
        )

        self.assertEqual(len(snapshot.features), 1)
        decision = snapshot.features[0]
        self.assertEqual(decision.feature, "futureUnknownFeature")
        self.assertFalse(decision.enabled)
        self.assertFalse(decision.releaseVisible)
        self.assertEqual(decision.reason, "unknownFeature")

    def test_client_below_minimum_is_fail_closed(self):
        snapshot = ReleasePolicyService(min_client_build=10).build_snapshot(
            audience="owner",
            cohort="closedPilotAdultSelf",
            client_build=9,
        )

        self.assertEqual(snapshot.snapshotDecision, "clientBelowMinimum")
        self.assertTrue(all(not item.releaseVisible for item in snapshot.features))
        self.assertTrue(all(item.reason == "clientBelowMinimum" for item in snapshot.features))

    def test_known_newer_policy_revision_rejects_server_downgrade(self):
        with self.assertRaises(ReleasePolicyVersionDowngrade):
            ReleasePolicyService(policy_revision=3).build_snapshot(
                audience="owner",
                cohort="closedPilotAdultSelf",
                client_build=1,
                known_policy_revision=4,
            )

    def test_emergency_revoke_overrides_visible_decision(self):
        snapshot = ReleasePolicyService(
            emergency_revision=7,
            emergency_disabled_features={"echoTextInput"},
        ).build_snapshot(
            audience="owner",
            cohort="closedPilotAdultSelf",
            client_build=1,
        )

        decision = next(item for item in snapshot.features if item.feature == "echoTextInput")
        self.assertFalse(decision.enabled)
        self.assertFalse(decision.releaseVisible)
        self.assertEqual(decision.reason, "emergencyRevoked")
        self.assertEqual(snapshot.emergencyRevision, 7)

    def test_snapshot_contract_forbids_extra_fields_and_exposes_expiry(self):
        snapshot = ReleasePolicyService().build_snapshot(
            audience="owner",
            cohort="closedPilotAdultSelf",
            client_build=1,
        )
        payload = snapshot.model_dump(mode="json")
        payload["credential"] = "must-not-be-accepted"

        with self.assertRaises(ValidationError):
            ReleasePolicySnapshot.model_validate(payload)

        self.assertFalse(snapshot.is_expired(snapshot.issuedAt))
        self.assertTrue(snapshot.is_expired(snapshot.expiresAt))


class ReleasePolicyEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_release_policy_endpoint_is_anonymous_typed_and_no_store(self):
        response = self.client.get(
            "/v2/release-policy",
            params={
                "audience": "owner",
                "cohort": "closedPilotAdultSelf",
                "clientBuild": 1,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["schemaVersion"], 1)
        self.assertEqual(payload["source"], "server")
        self.assertTrue(payload["shadowMode"])
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn("token", str(payload).lower())
        self.assertNotIn("credential", str(payload).lower())

    def test_release_policy_endpoint_rejects_version_downgrade(self):
        response = self.client.get(
            "/v2/release-policy",
            params={
                "audience": "owner",
                "cohort": "closedPilotAdultSelf",
                "clientBuild": 1,
                "knownPolicyRevision": 999,
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "release_policy_version_downgrade")


if __name__ == "__main__":
    unittest.main()
