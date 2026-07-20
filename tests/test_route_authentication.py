import unittest
from pathlib import Path
import runpy
from types import SimpleNamespace

from app.main import app
from app.services.route_authentication import (
    MACHINE_API_AUDIENCE,
    PrincipalKind,
    RequestPrincipal,
    RouteAuthenticationConfigurationError,
    RouteAuthenticationDecisionRecorder,
    RouteAuthenticationPolicy,
    validate_route_authentication_startup,
)
from app.services.route_ownership import (
    RouteAuthenticationMode,
    RouteOwnershipRegistry,
)


class RouteAuthenticationPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = RouteOwnershipRegistry()
        self.policy = RouteAuthenticationPolicy(self.registry)
        self.user = RequestPrincipal.user(
            principal_id="user_1",
            session_id="session_1",
            token_family_id="family_1",
            session_version=2,
        )

    def test_every_route_has_one_typed_authentication_contract(self):
        rules = self.registry.rules

        self.assertEqual(len(rules), 98)
        self.assertEqual(
            len({(rule.method, rule.path_template) for rule in rules}),
            len(rules),
        )
        self.assertTrue(all(isinstance(rule.auth_mode, RouteAuthenticationMode) for rule in rules))
        self.assertEqual(
            self.registry.rule_for_template("GET", "/config/runtime").auth_mode,
            RouteAuthenticationMode.PUBLIC,
        )
        self.assertEqual(
            self.registry.rule_for_template("GET", "/maps/district").auth_mode,
            RouteAuthenticationMode.USER,
        )
        for rule in rules:
            if rule.auth_mode == RouteAuthenticationMode.MACHINE:
                self.assertEqual(rule.required_audience, "dreamjourney-backend")
                self.assertEqual(len(rule.required_scopes), 1)

    def test_complete_route_principal_matrix(self):
        anonymous = RequestPrincipal.anonymous()
        machine_all_scopes = RequestPrincipal.machine(
            principal_id="scheduled-jobs",
            audience=MACHINE_API_AUDIENCE,
            scopes={
                scope
                for rule in self.registry.rules
                for scope in rule.required_scopes
                if rule.auth_mode == RouteAuthenticationMode.MACHINE
            },
        )

        for rule in self.registry.rules:
            with self.subTest(route=f"{rule.method} {rule.path_template}", principal="anonymous"):
                decision = self.policy.evaluate(
                    method=rule.method,
                    path=rule.path_template,
                    principal=anonymous,
                )
                self.assertEqual(
                    decision.allowed,
                    rule.auth_mode == RouteAuthenticationMode.PUBLIC,
                )
            with self.subTest(route=f"{rule.method} {rule.path_template}", principal="user"):
                decision = self.policy.evaluate(
                    method=rule.method,
                    path=rule.path_template,
                    principal=self.user,
                )
                self.assertEqual(
                    decision.allowed,
                    rule.auth_mode in {RouteAuthenticationMode.PUBLIC, RouteAuthenticationMode.USER},
                )
            with self.subTest(route=f"{rule.method} {rule.path_template}", principal="machine"):
                decision = self.policy.evaluate(
                    method=rule.method,
                    path=rule.path_template,
                    principal=machine_all_scopes,
                )
                self.assertEqual(
                    decision.allowed,
                    rule.auth_mode in {RouteAuthenticationMode.PUBLIC, RouteAuthenticationMode.MACHINE},
                )

    def test_every_protected_route_rejects_wrong_audience_and_missing_scope(self):
        for rule in self.registry.rules:
            if rule.auth_mode == RouteAuthenticationMode.PUBLIC:
                continue
            principal_kind = (
                PrincipalKind.USER
                if rule.auth_mode == RouteAuthenticationMode.USER
                else PrincipalKind.MACHINE
            )
            common = {
                "kind": principal_kind,
                "principal_id": "principal_1",
                "audience": "wrong-audience",
                "scopes": frozenset(rule.required_scopes),
            }
            if principal_kind == PrincipalKind.USER:
                common.update(
                    session_id="session_1",
                    token_family_id="family_1",
                    session_version=1,
                )
            wrong_audience = RequestPrincipal(**common)
            common["audience"] = rule.required_audience
            common["scopes"] = frozenset({"wrong:scope"})
            missing_scope = RequestPrincipal(**common)

            for principal, expected_reason in (
                (wrong_audience, "principalAudienceMismatch"),
                (missing_scope, "principalScopeMissing"),
            ):
                with self.subTest(
                    route=f"{rule.method} {rule.path_template}",
                    reason=expected_reason,
                ):
                    decision = self.policy.evaluate(
                        method=rule.method,
                        path=rule.path_template,
                        principal=principal,
                    )
                    self.assertFalse(decision.allowed)
                    self.assertEqual(decision.reason, expected_reason)

    def test_public_routes_allow_anonymous_but_user_routes_do_not(self):
        public = self.policy.evaluate(
            method="GET",
            path="/config/runtime",
            principal=RequestPrincipal.anonymous(),
        )
        protected = self.policy.evaluate(
            method="POST",
            path="/profile",
            principal=RequestPrincipal.anonymous(),
        )

        self.assertTrue(public.allowed)
        self.assertEqual(public.reason, "publicRoute")
        self.assertFalse(protected.allowed)
        self.assertEqual(protected.reason, "userPrincipalRequired")

    def test_user_principal_requires_trusted_audience_and_scope(self):
        allowed = self.policy.evaluate(
            method="POST",
            path="/profile",
            principal=self.user,
        )
        wrong_audience = RequestPrincipal(
            kind=PrincipalKind.USER,
            principal_id="user_1",
            session_id="session_1",
            token_family_id="family_1",
            session_version=2,
            audience="other-audience",
            scopes=frozenset({"user:api"}),
        )
        denied = self.policy.evaluate(
            method="POST",
            path="/profile",
            principal=wrong_audience,
        )

        self.assertTrue(allowed.allowed)
        self.assertEqual(allowed.reason, "userPrincipalAuthorized")
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.reason, "principalAudienceMismatch")

    def test_machine_principal_is_limited_by_route_audience_and_scope(self):
        allowed = RequestPrincipal.machine(
            principal_id="scheduled-jobs",
            audience="dreamjourney-backend",
            scopes={"timeLetter:dispatch"},
        )
        missing_scope = RequestPrincipal.machine(
            principal_id="scheduled-jobs",
            audience="dreamjourney-backend",
            scopes={"echo:dispatch"},
        )

        accepted = self.policy.evaluate(
            method="POST",
            path="/archive/time-letters/dispatch-due",
            principal=allowed,
        )
        denied_scope = self.policy.evaluate(
            method="POST",
            path="/archive/time-letters/dispatch-due",
            principal=missing_scope,
        )
        denied_business = self.policy.evaluate(
            method="POST",
            path="/profile",
            principal=allowed,
        )

        self.assertTrue(accepted.allowed)
        self.assertEqual(accepted.reason, "machineScopeAuthorized")
        self.assertFalse(denied_scope.allowed)
        self.assertEqual(denied_scope.reason, "principalScopeMissing")
        self.assertFalse(denied_business.allowed)
        self.assertEqual(denied_business.reason, "userPrincipalRequired")

    def test_user_cannot_call_machine_route_and_unknown_route_fails_closed(self):
        system_route = self.policy.evaluate(
            method="POST",
            path="/archive/time-letters/dispatch-due",
            principal=self.user,
        )
        unknown = self.policy.evaluate(
            method="POST",
            path="/future/unclassified",
            principal=self.user,
        )

        self.assertFalse(system_route.allowed)
        self.assertEqual(system_route.reason, "machinePrincipalRequired")
        self.assertFalse(unknown.allowed)
        self.assertEqual(unknown.reason, "routeNotClassified")

    def test_principal_diagnostic_descriptor_never_contains_tokens(self):
        descriptor = RequestPrincipal.machine(
            principal_id="scheduled-jobs",
            audience="dreamjourney-backend",
            scopes={"timeLetter:dispatch"},
        ).diagnostic_descriptor()

        self.assertEqual(descriptor["kind"], "machine")
        self.assertEqual(descriptor["audience"], "dreamjourney-backend")
        self.assertEqual(descriptor["scopes"], ["timeLetter:dispatch"])
        self.assertNotIn("token", str(descriptor).lower())


class RouteAuthenticationStartupTests(unittest.TestCase):
    def test_deployed_route_smoke_inventory_matches_registry(self):
        script_path = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "backend-route-authentication-postgres-smoke.py"
        )
        smoke_contract = runpy.run_path(str(script_path))

        self.assertEqual(
            smoke_contract["EXPECTED_ROUTE_COUNT"],
            len(RouteOwnershipRegistry().rules),
        )

    def test_current_application_is_complete_in_production_enforce_mode(self):
        summary = validate_route_authentication_startup(
            app,
            registry=RouteOwnershipRegistry(),
            environment="production",
            enforcement_mode="enforce",
        )

        self.assertEqual(summary["routeCount"], 98)
        self.assertEqual(summary["unclassifiedCount"], 0)
        self.assertEqual(summary["enforcementMode"], "enforce")

    def test_missing_route_or_production_shadow_mode_stops_startup(self):
        registry = RouteOwnershipRegistry()
        incomplete = SimpleNamespace(rules=registry.rules[:-1])

        with self.assertRaises(RouteAuthenticationConfigurationError):
            validate_route_authentication_startup(
                app,
                registry=incomplete,
                environment="production",
                enforcement_mode="enforce",
            )
        with self.assertRaises(RouteAuthenticationConfigurationError):
            validate_route_authentication_startup(
                app,
                registry=registry,
                environment="production",
                enforcement_mode="shadow",
            )

    def test_invalid_mode_and_missing_production_machine_credential_stop_startup(self):
        registry = RouteOwnershipRegistry()

        with self.assertRaises(RouteAuthenticationConfigurationError):
            validate_route_authentication_startup(
                app,
                registry=registry,
                environment="production",
                enforcement_mode="enfroce",
            )
        with self.assertRaises(RouteAuthenticationConfigurationError):
            validate_route_authentication_startup(
                app,
                registry=registry,
                environment="production",
                enforcement_mode="enforce",
                machine_credential_configured=False,
            )


class RouteAuthenticationDecisionRecorderTests(unittest.TestCase):
    def test_summary_tracks_allow_deny_denominator_without_identity_values(self):
        policy = RouteAuthenticationPolicy(RouteOwnershipRegistry())
        recorder = RouteAuthenticationDecisionRecorder(max_events=4)
        user = RequestPrincipal.user(
            principal_id="private-user-id",
            session_id="private-session-id",
            token_family_id="private-family-id",
            session_version=1,
        )
        recorder.record(
            policy.evaluate(method="POST", path="/profile", principal=user)
        )
        recorder.record(
            policy.evaluate(
                method="POST",
                path="/archive/time-letters/dispatch-due",
                principal=user,
            )
        )

        summary = recorder.summary()
        serialized = str(summary)
        self.assertEqual(summary["eventCount"], 2)
        self.assertEqual(summary["decisionCounts"], {"allow": 1, "deny": 1})
        self.assertEqual(summary["principalKindCounts"], {"user": 2})
        self.assertEqual(summary["reasonCounts"]["machinePrincipalRequired"], 1)
        self.assertNotIn("private-user-id", serialized)
        self.assertNotIn("private-session-id", serialized)
        self.assertNotIn("private-family-id", serialized)


if __name__ == "__main__":
    unittest.main()
