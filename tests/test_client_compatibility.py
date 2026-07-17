from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.services.client_compatibility import (
    ClientCompatibilityConfigurationError,
    ClientCompatibilityDecisionRecorder,
    ClientCompatibilityPolicy,
    resolve_client_compatibility_mode,
)
from app.services.in_memory_store import InMemoryStore
from app.services.release_policy import ReleasePolicyCommandGate, ReleasePolicyService
from app.services.route_ownership import RouteOwnershipRegistry


client = TestClient(app)


class ClientCompatibilityPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = RouteOwnershipRegistry()

    def policy(self, mode: str = "observe") -> ClientCompatibilityPolicy:
        return ClientCompatibilityPolicy(
            registry=self.registry,
            minimum_client_build=10,
            mode=mode,
        )

    def test_private_mutation_build_failures_observe_or_enforce(self):
        cases = (
            (None, "missing", "missingClientBuild"),
            ("not-a-build", "invalid", "invalidClientBuild"),
            ("0", "invalid", "invalidClientBuild"),
            ("9", "belowMinimum", "clientBelowMinimum"),
        )

        for raw_build, build_status, reason in cases:
            with self.subTest(mode="observe", raw_build=raw_build):
                observed = self.policy("observe").evaluate(
                    method="POST",
                    path="/profile",
                    client_build_header=raw_build,
                )
                self.assertEqual(observed.decision, "observeDeny")
                self.assertEqual(observed.reason, reason)
                self.assertEqual(observed.build_status, build_status)
                self.assertFalse(observed.blocked)

            with self.subTest(mode="enforce", raw_build=raw_build):
                enforced = self.policy("enforce").evaluate(
                    method="POST",
                    path="/profile",
                    client_build_header=raw_build,
                )
                self.assertEqual(enforced.decision, "deny")
                self.assertEqual(enforced.reason, reason)
                self.assertEqual(enforced.build_status, build_status)
                self.assertTrue(enforced.blocked)

    def test_supported_private_mutation_is_allowed(self):
        decision = self.policy("enforce").evaluate(
            method="POST",
            path="/profile",
            client_build_header="10",
        )

        self.assertEqual(decision.decision, "allow")
        self.assertEqual(decision.reason, "clientBuildSupported")
        self.assertEqual(decision.client_build, 10)
        self.assertFalse(decision.blocked)

    def test_read_only_and_logout_routes_remain_available_without_build(self):
        for method in ("GET", "HEAD"):
            with self.subTest(method=method):
                read = self.policy("enforce").evaluate(
                    method=method,
                    path="/profile/user-1",
                    client_build_header=None,
                )
                self.assertEqual(read.decision, "allow")
                self.assertEqual(read.reason, "readOnlyMethod")
                self.assertEqual(read.build_status, "missing")
                self.assertFalse(read.blocked)

        logout = self.policy("enforce").evaluate(
            method="POST",
            path="/auth/logout",
            client_build_header=None,
        )
        self.assertEqual(logout.decision, "allow")
        self.assertEqual(logout.reason, "safetyCleanupExempt")
        self.assertFalse(logout.blocked)

    def test_public_and_machine_routes_are_not_compatibility_routes(self):
        for method, path in (
            ("GET", "/config/runtime"),
            ("POST", "/archive/time-letters/dispatch-due"),
            ("POST", "/future/unclassified"),
        ):
            with self.subTest(route=f"{method} {path}"):
                decision = self.policy("enforce").evaluate(
                    method=method,
                    path=path,
                    client_build_header=None,
                )
                self.assertEqual(decision.decision, "notApplicable")
                self.assertFalse(decision.compat_route)
                self.assertFalse(decision.blocked)

    def test_mode_is_explicit_and_invalid_values_fail_configuration(self):
        self.assertEqual(resolve_client_compatibility_mode(""), "observe")
        self.assertEqual(resolve_client_compatibility_mode("observe"), "observe")
        self.assertEqual(resolve_client_compatibility_mode("enforce"), "enforce")
        with self.assertRaises(ClientCompatibilityConfigurationError):
            resolve_client_compatibility_mode("shadow")


class ClientCompatibilityDecisionRecorderTests(unittest.TestCase):
    def test_summary_tracks_value_free_build_and_route_counts(self):
        policy = ClientCompatibilityPolicy(
            registry=RouteOwnershipRegistry(),
            minimum_client_build=10,
            mode="enforce",
        )
        recorder = ClientCompatibilityDecisionRecorder(max_events=8)
        decisions = (
            policy.evaluate(
                method="POST",
                path="/profile",
                client_build_header="9",
            ),
            policy.evaluate(
                method="POST",
                path="/auth/password",
                client_build_header=None,
            ),
            policy.evaluate(
                method="POST",
                path="/archive/items",
                client_build_header="private-invalid-value",
            ),
            policy.evaluate(
                method="GET",
                path="/profile/user-1",
                client_build_header="10",
            ),
        )
        for decision in decisions:
            recorder.record(decision)
        recorder.record_upgrade_required_response()
        recorder.record_upgrade_required_response()

        summary = recorder.summary(
            mode="enforce",
            minimum_client_build=10,
        )
        serialized = str(summary)
        self.assertEqual(summary["eventCount"], 4)
        self.assertEqual(summary["clientBuildCounts"], {"9": 1, "10": 1})
        self.assertEqual(summary["belowMinCount"], 1)
        self.assertEqual(summary["missingBuildCount"], 1)
        self.assertEqual(summary["invalidBuildCount"], 1)
        self.assertEqual(summary["upgradeRequired426Count"], 2)
        self.assertEqual(summary["compatRouteCounts"]["POST profileOwner"], 1)
        self.assertTrue(summary["valueFree"])
        self.assertNotIn("private-invalid-value", serialized)
        self.assertNotIn("user-1", serialized)


class ClientCompatibilityAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = main_module.store
        self.previous_backend_token = main_module.BACKEND_API_TOKEN
        self.previous_route_mode = main_module.AUTH_ROUTE_MODE
        self.previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
        self.previous_legacy_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        self.previous_compatibility_policy = main_module.CLIENT_COMPATIBILITY_POLICY
        self.previous_compatibility_recorder = (
            main_module.CLIENT_COMPATIBILITY_DECISION_RECORDER
        )
        self.previous_release_policy_command_mode = (
            main_module.RELEASE_POLICY_COMMAND_MODE
        )
        self.previous_release_policy_service = main_module.RELEASE_POLICY_SERVICE
        self.previous_release_policy_gate = main_module.RELEASE_POLICY_COMMAND_GATE

        main_module.store = InMemoryStore()
        main_module.BACKEND_API_TOKEN = "compat-machine-token"
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.RELEASE_POLICY_COMMAND_MODE = "observe"
        self.set_compatibility_mode("observe")
        release_policy = ReleasePolicyService(
            min_client_build=1,
            shadow_mode=True,
            enforce_default_closed_stages=False,
        )
        main_module.RELEASE_POLICY_SERVICE = release_policy
        main_module.RELEASE_POLICY_COMMAND_GATE = ReleasePolicyCommandGate(release_policy)

    def tearDown(self) -> None:
        main_module.store = self.previous_store
        main_module.BACKEND_API_TOKEN = self.previous_backend_token
        main_module.AUTH_ROUTE_MODE = self.previous_route_mode
        main_module.AUTH_OWNERSHIP_MODE = self.previous_ownership_mode
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = self.previous_legacy_login
        main_module.CLIENT_COMPATIBILITY_POLICY = self.previous_compatibility_policy
        main_module.CLIENT_COMPATIBILITY_DECISION_RECORDER = (
            self.previous_compatibility_recorder
        )
        main_module.RELEASE_POLICY_COMMAND_MODE = (
            self.previous_release_policy_command_mode
        )
        main_module.RELEASE_POLICY_SERVICE = self.previous_release_policy_service
        main_module.RELEASE_POLICY_COMMAND_GATE = self.previous_release_policy_gate

    def set_compatibility_mode(self, mode: str) -> None:
        main_module.CLIENT_COMPATIBILITY_POLICY = ClientCompatibilityPolicy(
            registry=main_module.ROUTE_AUTHENTICATION_POLICY.registry,
            minimum_client_build=10,
            mode=mode,
        )
        main_module.CLIENT_COMPATIBILITY_DECISION_RECORDER = (
            ClientCompatibilityDecisionRecorder()
        )

    def login(self, phone: str = "13800138911") -> dict:
        response = client.post(
            "/auth/login",
            json={
                "phone": phone,
                "nickname": "compat user",
                "password": "password123",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    @staticmethod
    def user_headers(login_body: dict, client_build: str | None = None) -> dict:
        headers = {"Authorization": f"Bearer {login_body['auth']['accessToken']}"}
        if client_build is not None:
            headers["X-DreamJourney-Client-Build"] = client_build
        return headers

    def assert_upgrade_contract(self, response, reason: str) -> None:
        self.assertEqual(response.status_code, 426, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "upgrade_required")
        self.assertEqual(detail["reason"], reason)
        self.assertFalse(detail["retryable"])
        self.assertFalse(detail["reauthenticationRequired"])
        self.assertEqual(detail["minimumClientBuild"], 10)
        self.assertEqual(detail["accessMode"], "readOnly")

    def test_observe_allows_private_mutation_and_emits_diagnostic(self):
        login = self.login()
        response = client.post(
            "/profile",
            headers=self.user_headers(login),
            json={"userId": login["user"]["id"], "nickname": "observed"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.headers["x-dreamjourney-client-compatibility-mode"],
            "observe",
        )
        self.assertEqual(
            response.headers["x-dreamjourney-client-compatibility-decision"],
            "observeDeny",
        )
        self.assertEqual(
            response.headers["x-dreamjourney-client-compatibility-reason"],
            "missingClientBuild",
        )

    def test_enforce_returns_stable_426_but_preserves_read_and_logout(self):
        login = self.login()
        self.set_compatibility_mode("enforce")
        user_id = login["user"]["id"]

        for raw_build, reason in (
            (None, "missingClientBuild"),
            ("invalid", "invalidClientBuild"),
            ("9", "clientBelowMinimum"),
        ):
            with self.subTest(raw_build=raw_build):
                response = client.post(
                    "/profile",
                    headers=self.user_headers(login, raw_build),
                    json={"userId": user_id, "nickname": "blocked"},
                )
                self.assert_upgrade_contract(response, reason)

        supported = client.post(
            "/profile",
            headers=self.user_headers(login, "10"),
            json={"userId": user_id, "nickname": "supported"},
        )
        main_module.RELEASE_POLICY_COMMAND_MODE = "enforce"
        readable = client.get(
            f"/profile/{user_id}",
            headers=self.user_headers(login),
        )
        head_readable = client.head(
            f"/profile/{user_id}",
            headers=self.user_headers(login),
        )
        logout = client.post(
            "/auth/logout",
            headers=self.user_headers(login),
            json={"scope": "session"},
        )

        self.assertEqual(supported.status_code, 200, supported.text)
        self.assertEqual(readable.status_code, 200, readable.text)
        self.assertEqual(
            readable.headers["x-dreamjourney-release-policy-decision"],
            "allow",
        )
        self.assertEqual(
            readable.headers["x-dreamjourney-client-compatibility-reason"],
            "readOnlyMethod",
        )
        self.assertEqual(head_readable.status_code, 200, head_readable.text)
        self.assertEqual(head_readable.content, b"")
        self.assertEqual(
            head_readable.headers["x-dreamjourney-client-compatibility-reason"],
            "readOnlyMethod",
        )
        self.assertEqual(logout.status_code, 200, logout.text)
        self.assertEqual(
            logout.headers["x-dreamjourney-client-compatibility-reason"],
            "safetyCleanupExempt",
        )

        observations = client.get(
            "/ops/release-policy/observations",
            headers={"Authorization": "Bearer compat-machine-token"},
        )
        self.assertEqual(observations.status_code, 200, observations.text)
        compatibility = observations.json()["clientCompatibility"]
        self.assertEqual(compatibility["upgradeRequired426Count"], 3)
        self.assertEqual(compatibility["belowMinCount"], 1)
        self.assertEqual(compatibility["invalidBuildCount"], 1)
        self.assertGreaterEqual(compatibility["missingBuildCount"], 1)
        self.assertEqual(compatibility["clientBuildCounts"], {"9": 1, "10": 1})

    def test_retired_legacy_login_and_restore_share_upgrade_contract(self):
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = False
        main_module.RELEASE_POLICY_COMMAND_MODE = "enforce"

        login = client.post(
            "/auth/login",
            json={"phone": "13800138912", "password": "password123"},
        )
        restore = client.post(
            "/auth/restore",
            json={"phone": "13800138912"},
        )

        self.assert_upgrade_contract(login, "legacyIdentityFlowRetired")
        self.assert_upgrade_contract(restore, "legacyIdentityFlowRetired")
        self.assertEqual(login.json(), restore.json())
        self.assertEqual(
            login.headers["x-dreamjourney-client-compatibility-mode"],
            "enforce",
        )
        self.assertEqual(
            restore.headers["x-dreamjourney-client-compatibility-decision"],
            "deny",
        )
        summary = main_module.CLIENT_COMPATIBILITY_DECISION_RECORDER.summary(
            mode=main_module.CLIENT_COMPATIBILITY_POLICY.mode,
            minimum_client_build=main_module.CLIENT_COMPATIBILITY_POLICY.minimum_client_build,
        )
        self.assertEqual(summary["upgradeRequired426Count"], 2)
        self.assertEqual(summary["compatRouteCounts"]["POST legacyLogin"], 1)
        self.assertEqual(summary["compatRouteCounts"]["POST legacyRestore"], 1)


if __name__ == "__main__":
    unittest.main()
