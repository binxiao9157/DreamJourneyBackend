import hashlib
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.main as main_module
from app.main import app
from app.services.in_memory_store import InMemoryStore
from app.services.release_policy import (
    ReleasePolicyCommandGate,
    ReleasePolicyDecisionRecorder,
    ReleasePolicyFeatureAccessDenied,
    ReleasePolicyService,
    ReleasePolicySnapshot,
    ReleasePolicyVersionDowngrade,
    normalize_release_policy_audience,
)


class ReleasePolicyServiceTests(unittest.TestCase):
    def test_feature_canary_and_kill_switch_choose_enforcement_per_feature(self):
        service = ReleasePolicyService(
            shadow_mode=True,
            enforced_features={"familyManagement"},
            emergency_revision=4,
            emergency_disabled_features={"profileSettings"},
        )

        self.assertEqual(service.command_mode_for("timeLetters"), "observe")
        self.assertEqual(service.command_mode_for("familyManagement"), "enforce")
        self.assertEqual(service.command_mode_for("profileSettings"), "enforce")
        descriptor = service.public_descriptor()
        self.assertEqual(descriptor["commandMode"], "mixed")
        self.assertEqual(descriptor["canaryFeatures"], ["familyManagement"])
        self.assertEqual(descriptor["killSwitchFeatures"], ["profileSettings"])

    def test_rollout_feature_configuration_rejects_unknown_aliases(self):
        with self.assertRaises(ValueError):
            ReleasePolicyService(enforced_features={"familyManagementLegacy"})

        with self.assertRaises(ValueError):
            ReleasePolicyService(emergency_disabled_features={"voiceCloneEnabled"})

    def test_qa_audience_requires_nonproduction_system_principal(self):
        self.assertEqual(
            normalize_release_policy_audience(
                "qa",
                environment="production",
                principal_kind="user",
            ),
            "owner",
        )
        self.assertEqual(
            normalize_release_policy_audience(
                "qa",
                environment="production",
                principal_kind="system",
            ),
            "owner",
        )
        self.assertEqual(
            normalize_release_policy_audience(
                "qa",
                environment="development",
                principal_kind="user",
            ),
            "owner",
        )
        self.assertEqual(
            normalize_release_policy_audience(
                "qa",
                environment="development",
                principal_kind="system",
            ),
            "qa",
        )

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
        self.assertEqual(decisions["echoTextInput"].releaseStage, "M0")
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

        self.assertEqual(decisions["voiceCloneShell"].releaseStage, "M1")
        self.assertEqual(decisions["digitalHumanLivePanel"].releaseStage, "M2")
        self.assertEqual(decisions["careDashboard"].releaseStage, "M3")
        self.assertEqual(decisions["digitalInheritance"].releaseStage, "M4")

    def test_m1_through_m4_are_explicit_default_closed_stages_during_shadow_rollout(self):
        service = ReleasePolicyService(shadow_mode=True)

        self.assertEqual(service.command_mode_for("echoTextInput"), "observe")
        for feature in [
            "voiceCloneShell",
            "digitalHumanLivePanel",
            "careDashboard",
            "digitalInheritance",
        ]:
            decision = service.build_snapshot(
                audience="owner",
                cohort="closedPilotAdultSelf",
                client_build=1,
                requested_feature=feature,
            ).features[0]
            self.assertFalse(decision.enabled, feature)
            self.assertFalse(decision.releaseVisible, feature)
            self.assertIn(decision.releaseStage, {"M1", "M2", "M3", "M4"})
            self.assertEqual(service.command_mode_for(feature), "enforce")
        self.assertEqual(
            service.public_descriptor()["defaultClosedStages"],
            ["M1", "M2", "M3", "M4"],
        )
        self.assertTrue(
            service.public_descriptor()["defaultClosedStageEffectsEnforced"]
        )

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
        self.previous_store = main_module.store
        self.previous_backend_token = main_module.BACKEND_API_TOKEN
        self.previous_recorder = main_module.RELEASE_POLICY_DECISION_RECORDER
        main_module.store = InMemoryStore()
        main_module.BACKEND_API_TOKEN = ""
        main_module.RELEASE_POLICY_DECISION_RECORDER = ReleasePolicyDecisionRecorder()
        self.client = TestClient(app)

    def tearDown(self):
        main_module.store = self.previous_store
        main_module.BACKEND_API_TOKEN = self.previous_backend_token
        main_module.RELEASE_POLICY_DECISION_RECORDER = self.previous_recorder

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

    def test_default_closed_voice_effect_is_enforced_while_global_mode_observes(self):
        response = self.client.post(
            "/voice/realtime-token",
            json={"userId": "default-closed-user"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["code"], "release_policy_denied")
        self.assertEqual(response.json()["detail"]["feature"], "voiceCloneShell")
        self.assertEqual(
            response.headers["X-DreamJourney-Release-Policy-Mode"],
            "enforce",
        )

    def test_runtime_contract_observation_is_system_only_and_value_free(self):
        main_module.BACKEND_API_TOKEN = "release-policy-system-token"
        system_headers = {"Authorization": "Bearer release-policy-system-token"}

        typed = self.client.get(
            "/config/runtime",
            headers={
                **system_headers,
                "X-DreamJourney-Client-Build": "42",
                "X-DreamJourney-Runtime-Contract-Version": "2",
            },
        )
        legacy = self.client.get(
            "/config/runtime",
            headers={
                **system_headers,
                "X-DreamJourney-Client-Build": "41",
            },
        )
        summary = self.client.get(
            "/ops/release-policy/observations",
            headers=system_headers,
        )
        anonymous = self.client.get("/ops/release-policy/observations")

        self.assertEqual(typed.status_code, 200)
        self.assertEqual(legacy.status_code, 200)
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.headers["cache-control"], "no-store")
        payload = summary.json()
        self.assertEqual(payload["typedRuntimeContractHitCount"], 1)
        self.assertEqual(payload["legacyRuntimeAliasHitCount"], 1)
        self.assertNotIn("token", str(payload).lower())
        self.assertEqual(anonymous.status_code, 401)

    def test_enforced_hidden_route_denies_even_with_client_allow_headers(self):
        previous_mode = main_module.RELEASE_POLICY_COMMAND_MODE
        main_module.RELEASE_POLICY_COMMAND_MODE = "enforce"
        try:
            response = self.client.post(
                "/family/invite",
                headers={
                    "X-DreamJourney-Feature": "familyManagement",
                    "X-DreamJourney-Feature-Allowed": "true",
                    "X-DreamJourney-Policy-Version": "release-policy-v1",
                    "X-DreamJourney-Policy-Revision": "1",
                    "X-DreamJourney-Account-Generation": "session-a",
                },
                json={
                    "userId": "policy-user",
                    "name": "测试家人",
                    "relation": "家人",
                    "phone": "13800000001",
                },
            )
        finally:
            main_module.RELEASE_POLICY_COMMAND_MODE = previous_mode

        self.assertEqual(response.status_code, 403)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "release_policy_denied")
        self.assertEqual(detail["feature"], "familyManagement")
        self.assertEqual(detail["reason"], "notApprovedForClosedPilot")

    def test_enforced_dynamic_text_archive_route_remains_core_compatible(self):
        previous_mode = main_module.RELEASE_POLICY_COMMAND_MODE
        main_module.RELEASE_POLICY_COMMAND_MODE = "enforce"
        try:
            response = self.client.post(
                "/archive/items",
                json={
                    "userId": "policy-user",
                    "id": "policy-text-item",
                    "kind": "textNote",
                    "title": "文字记忆",
                    "note": "核心文字档案",
                    "privacyMetadata": {"scope": "generationAllowed"},
                },
            )
        finally:
            main_module.RELEASE_POLICY_COMMAND_MODE = previous_mode

        self.assertEqual(response.status_code, 200)

    @patch.object(main_module, "AUTH_LEGACY_PHONE_LOGIN_ENABLED", True)
    def test_enforced_authenticated_profile_command_accepts_matching_captured_decision(self):
        login = self.client.post(
            "/auth/login",
            json={
                "phone": "13800139001",
                "nickname": "策略用户",
                "password": "password123",
            },
        )
        self.assertEqual(login.status_code, 200)
        body = login.json()
        session_id = body["auth"]["sessionId"]
        account_generation = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:24]
        headers = {
            "Authorization": f"Bearer {body['auth']['accessToken']}",
            "X-DreamJourney-Feature": "profileSettings",
            "X-DreamJourney-Feature-Decision-Id": "decision-profile-save",
            "X-DreamJourney-Feature-Allowed": "true",
            "X-DreamJourney-Policy-Version": "release-policy-v1",
            "X-DreamJourney-Policy-Revision": "1",
            "X-DreamJourney-Account-Generation": account_generation,
        }
        previous_mode = main_module.RELEASE_POLICY_COMMAND_MODE
        main_module.RELEASE_POLICY_COMMAND_MODE = "enforce"
        try:
            response = self.client.post(
                "/profile",
                headers=headers,
                json={
                    "userId": body["user"]["id"],
                    "nickname": "策略用户",
                },
            )
        finally:
            main_module.RELEASE_POLICY_COMMAND_MODE = previous_mode

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["X-DreamJourney-Release-Policy-Decision"],
            "allow",
        )
        self.assertEqual(
            response.headers["X-DreamJourney-Release-Policy-Decision-Id"],
            "decision-profile-save",
        )

    def test_feature_canary_enforces_only_selected_hidden_command(self):
        previous_service = main_module.RELEASE_POLICY_SERVICE
        previous_gate = main_module.RELEASE_POLICY_COMMAND_GATE
        service = ReleasePolicyService(
            shadow_mode=True,
            enforced_features={"familyManagement"},
        )
        main_module.RELEASE_POLICY_SERVICE = service
        main_module.RELEASE_POLICY_COMMAND_GATE = ReleasePolicyCommandGate(service)
        try:
            family = self.client.post(
                "/family/invite",
                json={
                    "userId": "policy-canary-user",
                    "name": "测试家人",
                    "relation": "家人",
                    "phone": "13800000002",
                },
            )
            time_letter = self.client.post(
                "/archive/items",
                json={
                    "userId": "policy-canary-user",
                    "id": "policy-time-letter",
                    "kind": "timeLetter",
                    "title": "观察态时间信件",
                    "metadata": {"deliveryStatus": "draft"},
                },
            )
        finally:
            main_module.RELEASE_POLICY_SERVICE = previous_service
            main_module.RELEASE_POLICY_COMMAND_GATE = previous_gate

        self.assertEqual(family.status_code, 403)
        self.assertEqual(
            family.headers["X-DreamJourney-Release-Policy-Mode"],
            "enforce",
        )
        self.assertEqual(
            time_letter.headers["X-DreamJourney-Release-Policy-Decision"],
            "observeDeny",
        )
        self.assertEqual(
            time_letter.headers["X-DreamJourney-Release-Policy-Mode"],
            "observe",
        )
        time_letter_detail = time_letter.json().get("detail")
        self.assertFalse(
            isinstance(time_letter_detail, dict)
            and time_letter_detail.get("code") == "release_policy_denied"
        )

    def test_kill_switch_enforces_owner_core_while_global_rollout_observes(self):
        previous_service = main_module.RELEASE_POLICY_SERVICE
        previous_gate = main_module.RELEASE_POLICY_COMMAND_GATE
        service = ReleasePolicyService(
            shadow_mode=True,
            emergency_revision=5,
            emergency_disabled_features={"profileSettings"},
        )
        main_module.RELEASE_POLICY_SERVICE = service
        main_module.RELEASE_POLICY_COMMAND_GATE = ReleasePolicyCommandGate(service)
        try:
            response = self.client.post(
                "/profile",
                json={"userId": "policy-kill-switch-user", "nickname": "被止损"},
            )
        finally:
            main_module.RELEASE_POLICY_SERVICE = previous_service
            main_module.RELEASE_POLICY_COMMAND_GATE = previous_gate

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["reason"], "emergencyRevoked")
        self.assertEqual(
            response.headers["X-DreamJourney-Release-Policy-Mode"],
            "enforce",
        )

    def test_old_client_canary_returns_upgrade_and_read_only_contract(self):
        previous_service = main_module.RELEASE_POLICY_SERVICE
        previous_gate = main_module.RELEASE_POLICY_COMMAND_GATE
        service = ReleasePolicyService(
            shadow_mode=True,
            min_client_build=10,
            enforced_features={"profileSettings"},
        )
        main_module.RELEASE_POLICY_SERVICE = service
        main_module.RELEASE_POLICY_COMMAND_GATE = ReleasePolicyCommandGate(service)
        try:
            response = self.client.post(
                "/profile",
                headers={"X-DreamJourney-Client-Build": "9"},
                json={"userId": "old-client-user", "nickname": "旧版本"},
            )
        finally:
            main_module.RELEASE_POLICY_SERVICE = previous_service
            main_module.RELEASE_POLICY_COMMAND_GATE = previous_gate

        self.assertEqual(response.status_code, 426)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "client_upgrade_required")
        self.assertEqual(detail["minimumClientBuild"], 10)
        self.assertEqual(detail["accessMode"], "readOnly")


class ReleasePolicyDecisionRecorderTests(unittest.TestCase):
    def test_summary_contains_only_allowlisted_fields_and_tracks_legacy_contract_hits(self):
        now = datetime(2026, 7, 16, 2, 0, tzinfo=timezone.utc)
        recorder = ReleasePolicyDecisionRecorder(max_events=8, environment="test")
        recorder.record(
            feature="familyManagement",
            policy_version="release-policy-v2",
            client_build=42,
            decision="deny",
            reason="notApprovedForClosedPilot",
            route="POST /family/invite",
            occurred_at=now,
        )
        recorder.record_runtime_contract(
            client_build=42,
            contract_version=1,
            occurred_at=now,
        )

        summary = recorder.summary()
        self.assertEqual(summary["eventCount"], 2)
        self.assertEqual(summary["legacyRuntimeAliasHitCount"], 1)
        self.assertEqual(summary["decisionCounts"]["deny"], 1)
        self.assertEqual(summary["eventEnvelopeSchemaVersion"], 1)
        self.assertEqual(summary["evidenceStoreContractVersion"], 1)
        self.assertEqual(len(summary["operationEvents"]), 2)
        self.assertEqual(summary["operationEvents"][0]["type"], "operation")
        self.assertEqual(summary["operationEvents"][0]["env"], "test")
        serialized = str(summary)
        self.assertNotIn("userId", serialized)
        self.assertNotIn("phone", serialized)
        self.assertNotIn("token", serialized.lower())
        self.assertEqual(
            set(summary["events"][0]),
            {
                "feature",
                "policyVersion",
                "clientBuild",
                "decision",
                "reason",
                "route",
                "occurredAt",
            },
        )


class ReleasePolicyCommandGateTests(unittest.TestCase):
    def test_server_denies_hidden_feature_even_when_client_claims_allow(self):
        gate = ReleasePolicyCommandGate(ReleasePolicyService())

        with self.assertRaises(ReleasePolicyFeatureAccessDenied) as caught:
            gate.capture(
                feature="voiceCloneShell",
                audience="owner",
                cohort="closedPilotAdultSelf",
                client_build=1,
                client_policy_version="release-policy-v1",
                client_policy_revision=1,
                client_account_generation="session-a",
                client_allowed=True,
            )

        self.assertEqual(caught.exception.feature, "voiceCloneShell")
        self.assertEqual(caught.exception.reason, "notApprovedForClosedPilot")

    def test_effect_time_revalidation_rejects_emergency_revoke(self):
        gate = ReleasePolicyCommandGate(ReleasePolicyService())
        captured = gate.capture(
            feature="profileSettings",
            audience="owner",
            cohort="closedPilotAdultSelf",
            client_build=1,
            client_policy_version="release-policy-v1",
            client_policy_revision=1,
            client_account_generation="session-a",
            client_allowed=True,
            client_decision_id="decision-effect-revoke",
        )

        with self.assertRaises(ReleasePolicyFeatureAccessDenied) as caught:
            gate.revalidate_effect(
                captured,
                policy_service=ReleasePolicyService(
                    policy_revision=2,
                    emergency_revision=3,
                    emergency_disabled_features={"profileSettings"},
                ),
            )

        self.assertEqual(caught.exception.reason, "emergencyRevoked")

    def test_route_inventory_covers_hidden_commands_and_dynamic_archive_payloads(self):
        gate = ReleasePolicyCommandGate(ReleasePolicyService())

        self.assertEqual(gate.feature_for_request("POST", "/voice/synthesis", {}), "voiceCloneShell")
        self.assertEqual(gate.feature_for_request("POST", "/digital-human/sessions", {}), "digitalHumanLivePanel")
        self.assertEqual(gate.feature_for_request("POST", "/family/invite", {}), "familyManagement")
        self.assertEqual(
            gate.feature_for_request(
                "POST",
                "/family/relationships/user-a/relationship-a/lifecycle",
                {},
            ),
            "familyManagement",
        )
        self.assertEqual(
            gate.feature_for_request("POST", "/family/access-grants", {}),
            "familyManagement",
        )
        self.assertEqual(
            gate.feature_for_request("GET", "/family/access-grants/user-a", {}),
            "familyManagement",
        )
        self.assertEqual(
            gate.feature_for_request(
                "POST",
                "/family/access-grants/user-a/grant-a/revoke",
                {},
            ),
            "familyManagement",
        )
        self.assertEqual(gate.feature_for_request("GET", "/care/snapshots/latest/user-a", {}), "careDashboard")
        self.assertEqual(gate.feature_for_request("POST", "/profile", {}), "profileSettings")
        self.assertEqual(gate.feature_for_request("POST", "/context/build", {}), "echoTextInput")
        self.assertEqual(
            gate.feature_for_request("POST", "/echo/delayed-replies/dispatch-due", {}),
            "echoTextInput",
        )
        self.assertEqual(gate.feature_for_request("POST", "/auth/delete", {}), "accountDeletion")
        self.assertEqual(
            gate.feature_for_request("GET", "/archive/items/user-a", {}),
            "archiveRemoteFetch",
        )
        self.assertEqual(
            gate.feature_for_request("POST", "/archive/items", {"kind": "timeLetter"}),
            "timeLetters",
        )
        self.assertEqual(
            gate.feature_for_request("POST", "/archive/media/upload-intent", {"mediaType": "video"}),
            "archiveVideoUpload",
        )
        self.assertIsNone(gate.feature_for_request("POST", "/archive/items", {"kind": "text"}))
        self.assertIsNone(gate.feature_for_request("POST", "/profile-legacy", {}))

    def test_unknown_feature_and_missing_client_metadata_fail_closed(self):
        gate = ReleasePolicyCommandGate(ReleasePolicyService())

        with self.assertRaises(ReleasePolicyFeatureAccessDenied) as unknown:
            gate.capture(
                feature="futureUnknownFeature",
                audience="owner",
                cohort="closedPilotAdultSelf",
                client_build=1,
                client_policy_version=None,
                client_policy_revision=None,
                client_account_generation=None,
                client_allowed=None,
            )
        self.assertEqual(unknown.exception.reason, "unknownFeature")

        with self.assertRaises(ReleasePolicyFeatureAccessDenied) as missing:
            gate.capture(
                feature="profileSettings",
                audience="owner",
                cohort="closedPilotAdultSelf",
                client_build=1,
                client_policy_version=None,
                client_policy_revision=None,
                client_account_generation=None,
                client_allowed=None,
            )
        self.assertEqual(missing.exception.reason, "missingCapturedPolicy")

    def test_authenticated_account_generation_mismatch_fails_closed(self):
        gate = ReleasePolicyCommandGate(ReleasePolicyService())

        with self.assertRaises(ReleasePolicyFeatureAccessDenied) as mismatch:
            gate.capture(
                feature="profileSettings",
                audience="owner",
                cohort="closedPilotAdultSelf",
                client_build=1,
                client_policy_version="release-policy-v1",
                client_policy_revision=1,
                client_account_generation="stale-session",
                client_allowed=True,
                client_decision_id="decision-stale-account",
                expected_account_generation="current-session",
            )

        self.assertEqual(mismatch.exception.reason, "accountGenerationMismatch")

    def test_system_effect_uses_server_capture_without_client_metadata(self):
        gate = ReleasePolicyCommandGate(ReleasePolicyService())

        captured = gate.capture(
            feature="profileSettings",
            audience="owner",
            cohort="closedPilotAdultSelf",
            client_build=1,
            client_policy_version=None,
            client_policy_revision=None,
            client_account_generation=None,
            client_allowed=None,
            expected_account_generation="system",
            require_client_capture=False,
        )

        self.assertTrue(captured.client_allowed)
        self.assertEqual(captured.account_generation, "system")

    def test_expired_capture_is_denied_before_effect(self):
        gate = ReleasePolicyCommandGate(ReleasePolicyService(ttl_seconds=60))
        captured_at = datetime(2026, 7, 16, 0, 0, tzinfo=timezone.utc)
        captured = gate.capture(
            feature="profileSettings",
            audience="owner",
            cohort="closedPilotAdultSelf",
            client_build=1,
            client_policy_version="release-policy-v1",
            client_policy_revision=1,
            client_account_generation="session-a",
            client_allowed=True,
            client_decision_id="decision-expired",
            now=captured_at,
        )

        with self.assertRaises(ReleasePolicyFeatureAccessDenied) as expired:
            gate.revalidate_effect(
                captured,
                now=captured_at + timedelta(seconds=61),
            )

        self.assertEqual(expired.exception.reason, "capturedPolicyExpiredBeforeEffect")


if __name__ == "__main__":
    unittest.main()
