import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.services.in_memory_store import InMemoryStore
from app.services.release_policy import ReleasePolicyCommandGate, ReleasePolicyService


client = TestClient(app)


class RouteAuthenticationAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = main_module.store
        self.previous_backend_token = main_module.BACKEND_API_TOKEN
        self.previous_route_mode = main_module.AUTH_ROUTE_MODE
        self.previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
        self.previous_legacy_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        self.previous_release_policy_service = main_module.RELEASE_POLICY_SERVICE
        self.previous_release_policy_gate = main_module.RELEASE_POLICY_COMMAND_GATE
        main_module.store = InMemoryStore()
        main_module.BACKEND_API_TOKEN = "route-auth-machine-token"
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        release_policy = ReleasePolicyService(
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
        main_module.RELEASE_POLICY_SERVICE = self.previous_release_policy_service
        main_module.RELEASE_POLICY_COMMAND_GATE = self.previous_release_policy_gate

    def login(self, phone: str = "13800138901"):
        response = client.post(
            "/auth/login",
            json={"phone": phone, "nickname": "route auth", "password": "password123"},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    @staticmethod
    def user_headers(login_body):
        return {"Authorization": f"Bearer {login_body['auth']['accessToken']}"}

    @staticmethod
    def machine_headers():
        return {"Authorization": "Bearer route-auth-machine-token"}

    def test_anonymous_is_limited_to_public_routes(self):
        runtime = client.get("/config/runtime")
        protected = client.post(
            "/profile",
            json={"userId": "user_other", "nickname": "blocked"},
        )

        self.assertEqual(runtime.status_code, 200)
        self.assertEqual(runtime.headers["x-dreamjourney-route-auth-decision"], "allow")
        self.assertEqual(protected.status_code, 401)
        self.assertEqual(
            protected.headers["x-dreamjourney-route-auth-reason"],
            "userPrincipalRequired",
        )

    def test_user_can_call_owner_route_but_not_machine_route(self):
        login = self.login()
        user_id = login["user"]["id"]

        profile = client.post(
            "/profile",
            headers=self.user_headers(login),
            json={"userId": user_id, "nickname": "accepted"},
        )
        dispatch = client.post(
            "/archive/time-letters/dispatch-due",
            headers=self.user_headers(login),
            json={"limit": 1},
        )

        self.assertEqual(profile.status_code, 200)
        self.assertEqual(profile.headers["x-dreamjourney-route-auth-reason"], "userPrincipalAuthorized")
        self.assertEqual(dispatch.status_code, 403)
        self.assertEqual(dispatch.headers["x-dreamjourney-route-auth-reason"], "machinePrincipalRequired")

    def test_machine_scope_allows_system_route_but_not_user_business_route(self):
        dispatch = client.post(
            "/archive/time-letters/dispatch-due",
            headers=self.machine_headers(),
            json={"now": "2026-07-17T10:00:00Z", "limit": 1},
        )
        business = client.post(
            "/profile",
            headers=self.machine_headers(),
            json={"userId": "user_other", "nickname": "blocked"},
        )

        self.assertEqual(dispatch.status_code, 200)
        self.assertEqual(dispatch.headers["x-dreamjourney-auth-principal"], "machine")
        self.assertEqual(dispatch.headers["x-dreamjourney-route-auth-reason"], "machineScopeAuthorized")
        self.assertEqual(business.status_code, 403)
        self.assertEqual(business.headers["x-dreamjourney-route-auth-reason"], "userPrincipalRequired")

    def test_policy_exception_fails_closed_in_enforce_mode(self):
        with patch.object(
            main_module.ROUTE_AUTHENTICATION_POLICY,
            "evaluate",
            side_effect=RuntimeError("synthetic evaluator failure"),
        ):
            response = client.get("/config/runtime")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["x-dreamjourney-route-auth-reason"], "policyEvaluationFailed")


if __name__ == "__main__":
    unittest.main()
