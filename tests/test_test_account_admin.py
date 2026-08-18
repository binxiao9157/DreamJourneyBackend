from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.services.in_memory_store import InMemoryStore
from app.services.route_ownership import (
    RouteAuthenticationMode,
    RouteOwnershipRegistry,
)
from app.services.test_account_admin_auth import (
    TestAccountAdminAuthenticationFailed,
    TestAccountAdminAuthService,
    TestAccountAdminLoginLimiter,
    TestAccountAdminRateLimited,
    encode_test_account_admin_password,
    verify_test_account_admin_password,
)
from app.services.test_account_allowlist import TestAccountAllowlistService


NOW = datetime(2026, 8, 11, 8, 0, tzinfo=timezone.utc)
HMAC_KEY = "test-account-allowlist-key-" + ("x" * 40)
TARGET = "10000000001"
TARGET_PREFIX = "8610000000"
ADMIN_USERNAME = "dreamjourney_admin"
ADMIN_PASSWORD = "correct-horse-battery-staple"
SESSION_KEY = "admin-session-test-key-" + ("s" * 48)


def make_admin_service(
    *,
    limiter: TestAccountAdminLoginLimiter | None = None,
) -> TestAccountAdminAuthService:
    return TestAccountAdminAuthService(
        enabled=True,
        username=ADMIN_USERNAME,
        password_hash=encode_test_account_admin_password(
            ADMIN_PASSWORD,
            salt=b"0123456789abcdef",
        ),
        session_hmac_key=SESSION_KEY,
        session_ttl_seconds=3600,
        cookie_path="/ops/test-accounts",
        cookie_secure=False,
        limiter=limiter,
    )


def make_allowlist(store: InMemoryStore) -> TestAccountAllowlistService:
    return TestAccountAllowlistService(
        store,
        hmac_key=HMAC_KEY,
        hmac_key_version="v1",
        enabled=True,
        allowed_phone_prefixes=(TARGET_PREFIX,),
        event_sink=store.append_evidence_event,
        environment="test",
    )


class TestAccountAdminAuthTests(unittest.TestCase):
    def test_password_hash_and_signed_session_are_verified(self):
        password_hash = encode_test_account_admin_password(
            ADMIN_PASSWORD,
            salt=b"0123456789abcdef",
        )
        self.assertRegex(
            password_hash,
            r"^pbkdf2_sha256:600000:[A-Za-z0-9_-]+:[A-Za-z0-9_-]+$",
        )
        self.assertTrue(
            verify_test_account_admin_password(ADMIN_PASSWORD, password_hash)
        )
        self.assertFalse(
            verify_test_account_admin_password("incorrect-password", password_hash)
        )

        service = make_admin_service()
        session = service.authenticate(
            username=ADMIN_USERNAME,
            password=ADMIN_PASSWORD,
            client_key="127.0.0.1",
            now=NOW,
        )
        resolved = service.resolve_session(session.token, now=NOW + timedelta(minutes=5))
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.username, ADMIN_USERNAME)
        self.assertIsNone(
            service.resolve_session(f"{session.token}x", now=NOW + timedelta(minutes=5))
        )
        self.assertIsNone(
            service.resolve_session(session.token, now=NOW + timedelta(hours=2))
        )

    def test_failed_logins_are_limited_per_client(self):
        limiter = TestAccountAdminLoginLimiter(max_attempts=2, window_seconds=120)
        service = make_admin_service(limiter=limiter)
        for username in (ADMIN_USERNAME, "different-name"):
            with self.assertRaises(TestAccountAdminAuthenticationFailed):
                service.authenticate(
                    username=username,
                    password="wrong-password-value",
                    client_key="203.0.113.9",
                    now=NOW,
                )
        with self.assertRaises(TestAccountAdminRateLimited) as context:
            service.authenticate(
                username=ADMIN_USERNAME,
                password=ADMIN_PASSWORD,
                client_key="203.0.113.9",
                now=NOW + timedelta(seconds=1),
            )
        self.assertGreaterEqual(context.exception.retry_after_seconds, 119)


class TestAccountAdminEndpointTests(unittest.TestCase):
    def test_admin_page_login_management_and_logout(self):
        store = InMemoryStore()
        allowlist = make_allowlist(store)
        admin_auth = make_admin_service()
        with (
            patch.object(main_module, "store", store),
            patch.object(main_module, "BACKEND_API_TOKEN", ""),
            patch.object(
                main_module,
                "_test_account_allowlist_service",
                return_value=allowlist,
            ),
            patch.object(
                main_module,
                "_test_account_admin_auth_service",
                return_value=admin_auth,
            ),
        ):
            client = TestClient(app)
            page = client.get("/ops/test-accounts/admin")
            denied = client.get("/ops/test-accounts")
            failed_login = client.post(
                "/ops/test-accounts/admin/login",
                json={"username": ADMIN_USERNAME, "password": "wrong-password-value"},
            )
            login = client.post(
                "/ops/test-accounts/admin/login",
                json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
            )
            session = client.get("/ops/test-accounts/admin/session")
            created = client.post(
                "/ops/test-accounts",
                json={"target": TARGET, "label": "Admin page QA"},
            )
            listed = client.get("/ops/test-accounts")
            unrelated = client.get("/ops/incidents/readiness")
            logout = client.post("/ops/test-accounts/admin/logout")
            denied_after_logout = client.get("/ops/test-accounts")

        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.headers["cache-control"], "no-store")
        self.assertIn("frame-ancestors 'none'", page.headers["content-security-policy"])
        self.assertNotIn("__CSP_NONCE__", page.text)
        self.assertEqual(len(re.findall(r'nonce="[^"]+"', page.text)), 2)
        self.assertIn("测试角色", page.text)
        self.assertIn("authorizationFeatureOptions", page.text)
        self.assertIn("expectedEntitlementRevision", page.text)
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(failed_login.status_code, 401)
        self.assertEqual(login.status_code, 200)
        set_cookie = login.headers["set-cookie"].lower()
        self.assertIn("httponly", set_cookie)
        self.assertIn("samesite=strict", set_cookie)
        self.assertNotIn("secure", set_cookie)
        self.assertEqual(session.status_code, 200)
        self.assertEqual(session.json()["administrator"]["username"], ADMIN_USERNAME)
        self.assertEqual(created.status_code, 201)
        self.assertIsNone(created.json()["testAccount"]["expiresAt"])
        self.assertEqual(created.json()["testAccount"]["validity"], "permanent")
        self.assertEqual(listed.status_code, 200)
        self.assertNotIn("verificationCode", listed.text)
        self.assertEqual(unrelated.status_code, 401)
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(denied_after_logout.status_code, 401)

    def test_admin_routes_have_narrow_authentication_contracts(self):
        registry = RouteOwnershipRegistry()
        public_routes = (
            ("GET", "/ops/test-accounts/admin"),
            ("POST", "/ops/test-accounts/admin/login"),
            ("POST", "/ops/test-accounts/admin/logout"),
        )
        for method, path in public_routes:
            with self.subTest(method=method, path=path):
                match = registry.match(method, path)
                self.assertIsNotNone(match)
                self.assertEqual(match.rule.auth_mode, RouteAuthenticationMode.PUBLIC)

        session_match = registry.match("GET", "/ops/test-accounts/admin/session")
        self.assertIsNotNone(session_match)
        self.assertEqual(
            session_match.rule.auth_mode,
            RouteAuthenticationMode.MACHINE,
        )
        self.assertEqual(
            session_match.rule.required_scopes,
            ("testAccount:manage",),
        )


if __name__ == "__main__":
    unittest.main()
