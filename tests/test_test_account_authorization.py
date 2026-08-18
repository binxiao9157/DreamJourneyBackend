from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.services.auth_sessions import AuthSessionError, AuthSessionService
from app.services.in_memory_store import InMemoryStore
from app.services.release_policy import ReleasePolicyCommandGate, ReleasePolicyService
from app.services.test_account_admin_auth import (
    TestAccountAdminAuthService,
    encode_test_account_admin_password,
)
from app.services.test_account_allowlist import (
    TestAccountAllowlistConflict,
    TestAccountAllowlistService,
    TestAccountAllowlistValidationError,
)


NOW = datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc)
HMAC_KEY = "test-account-authorization-key-" + ("x" * 40)
TARGET = "10000000001"
TARGET_PREFIX = "8610000000"
ADMIN_USERNAME = "dreamjourney_admin"
ADMIN_PASSWORD = "correct-horse-battery-staple"
SESSION_KEY = "test-account-admin-session-" + ("s" * 48)


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


def make_admin_service() -> TestAccountAdminAuthService:
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
    )


class TestAccountAuthorizationServiceTests(unittest.TestCase):
    def test_role_matrix_never_grants_features_implicitly(self):
        store = InMemoryStore()
        service = make_allowlist(store)
        roles = ("superTest", "ownerTest", "familyTest", "operatorTest")

        for index, role in enumerate(roles, start=1):
            account = service.create(
                target=f"1000000000{index}",
                label=role,
                actor_id="admin",
                test_role=role,
                feature_entitlements=[],
                scenario_bindings={},
                now=NOW,
            )["testAccount"]
            self.assertEqual(account["testRole"], role)
            self.assertEqual(account["featureEntitlements"], [])
            self.assertFalse(account["authorizationConfigured"])

    def test_new_account_has_no_product_entitlement_and_update_is_revisioned(self):
        store = InMemoryStore()
        service = make_allowlist(store)
        created = service.create(
            target=TARGET,
            label="Role matrix",
            actor_id="administrator@example.test",
            now=NOW,
        )["testAccount"]

        self.assertIsNone(created["testRole"])
        self.assertEqual(created["featureEntitlements"], [])
        self.assertEqual(created["scenarioBindings"], {})
        self.assertEqual(created["entitlementRevision"], 1)
        self.assertFalse(created["authorizationConfigured"])
        self.assertNotIn("administrator@example.test", json.dumps(created))

        updated = service.update_authorization(
            created["accountId"],
            test_role="ownerTest",
            feature_entitlements=["profileSettings", "familyManagement"],
            scenario_bindings={
                "relationshipId": "rel_test_001",
                "grantIds": ["grant_test_001"],
            },
            expected_entitlement_revision=1,
            actor_id="administrator@example.test",
            now=NOW,
        )["testAccount"]

        self.assertEqual(updated["testRole"], "ownerTest")
        self.assertEqual(
            updated["featureEntitlements"],
            ["familyManagement", "profileSettings"],
        )
        self.assertEqual(updated["entitlementRevision"], 2)
        self.assertTrue(updated["authorizationConfigured"])
        self.assertRegex(updated["entitlementSnapshotId"], r"^tae_[a-f0-9]{32}$")
        self.assertRegex(updated["updatedByHash"], r"^[a-f0-9]{64}$")
        self.assertNotIn("administrator@example.test", json.dumps(updated))

        with self.assertRaises(TestAccountAllowlistConflict):
            service.update_authorization(
                created["accountId"],
                test_role="superTest",
                feature_entitlements=["profileSettings"],
                scenario_bindings={},
                expected_entitlement_revision=1,
                actor_id="other-admin",
                now=NOW,
            )

    def test_role_feature_and_scenario_values_are_bounded(self):
        store = InMemoryStore()
        service = make_allowlist(store)
        account_id = service.create(
            target=TARGET,
            label="Validation",
            actor_id="admin",
            now=NOW,
        )["testAccount"]["accountId"]

        invalid_requests = (
            {
                "test_role": "root",
                "feature_entitlements": [],
                "scenario_bindings": {},
            },
            {
                "test_role": "ownerTest",
                "feature_entitlements": ["unknownFeature"],
                "scenario_bindings": {},
            },
            {
                "test_role": None,
                "feature_entitlements": ["profileSettings"],
                "scenario_bindings": {},
            },
            {
                "test_role": "familyTest",
                "feature_entitlements": ["familyManagement"],
                "scenario_bindings": {"grantId": "contains whitespace"},
            },
            {
                "test_role": "ownerTest",
                "feature_entitlements": {"profileSettings": True},
                "scenario_bindings": {},
            },
        )
        for request in invalid_requests:
            with self.subTest(request=request):
                with self.assertRaises(TestAccountAllowlistValidationError):
                    service.update_authorization(
                        account_id,
                        expected_entitlement_revision=1,
                        actor_id="admin",
                        now=NOW,
                        **request,
                    )

    def test_session_snapshot_fails_closed_after_authorization_change(self):
        store = InMemoryStore()
        allowlist = make_allowlist(store)
        account = allowlist.create(
            target=TARGET,
            label="Session revision",
            actor_id="admin",
            now=NOW,
        )["testAccount"]
        self.assertTrue(
            allowlist.record_successful_login(
                account["accountId"],
                subject_id="sub_test_authorization",
                now=NOW,
            )
        )
        sessions = AuthSessionService(
            store,
            access_ttl_seconds=900,
            refresh_ttl_seconds=3600,
            authorization_snapshot_resolver=(
                allowlist.authorization_snapshot_for_subject
            ),
        )
        issued = sessions.issue("sub_test_authorization", now=NOW)

        self.assertEqual(issued["authorizationSnapshot"]["revision"], 1)
        self.assertNotIn("testRole", issued["authorizationSnapshot"])
        self.assertIsNotNone(sessions.resolve_access_token(issued["accessToken"], now=NOW))

        allowlist.update_authorization(
            account["accountId"],
            test_role="superTest",
            feature_entitlements=["profileSettings"],
            scenario_bindings={},
            expected_entitlement_revision=1,
            actor_id="admin",
            now=NOW,
        )

        self.assertIsNone(sessions.resolve_access_token(issued["accessToken"], now=NOW))
        with self.assertRaises(AuthSessionError) as raised:
            sessions.refresh(issued["refreshToken"], now=NOW)
        self.assertEqual(
            raised.exception.code,
            "test_account_authorization_reauthentication_required",
        )


class TestAccountAuthorizationEndpointTests(unittest.TestCase):
    def test_admin_assignment_revokes_sessions_and_keeps_audit_redacted(self):
        store = InMemoryStore()
        allowlist = make_allowlist(store)
        admin_auth = make_admin_service()
        account = allowlist.create(
            target=TARGET,
            label="Admin role assignment",
            actor_id="bootstrap-admin",
            now=NOW,
        )["testAccount"]
        allowlist.record_successful_login(
            account["accountId"],
            subject_id="sub_admin_assignment",
            now=NOW,
        )
        sessions = AuthSessionService(
            store,
            access_ttl_seconds=900,
            refresh_ttl_seconds=3600,
            authorization_snapshot_resolver=(
                allowlist.authorization_snapshot_for_subject
            ),
        )
        issued = sessions.issue("sub_admin_assignment")

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
            patch.object(
                main_module,
                "_auth_session_service",
                return_value=sessions,
            ),
        ):
            client = TestClient(app)
            login = client.post(
                "/ops/test-accounts/admin/login",
                json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
            )
            updated = client.put(
                f"/ops/test-accounts/{account['accountId']}/authorization",
                json={
                    "testRole": "superTest",
                    "featureEntitlements": ["profileSettings"],
                    "scenarioBindings": {"grantId": "grant_test_001"},
                    "expectedEntitlementRevision": 1,
                },
            )

        self.assertEqual(login.status_code, 200)
        self.assertEqual(updated.status_code, 200)
        body = updated.json()
        self.assertEqual(body["testAccount"]["testRole"], "superTest")
        self.assertEqual(body["sessionRevocation"]["scope"], "allDevices")
        self.assertIsNone(sessions.resolve_access_token(issued["accessToken"], now=NOW))
        self.assertNotIn(ADMIN_USERNAME, updated.text)
        event_payload = json.dumps(store.list_evidence_events(), ensure_ascii=False)
        self.assertNotIn(ADMIN_USERNAME, event_payload)

    def test_entitlements_do_not_bypass_owner_authority(self):
        store = InMemoryStore()
        allowlist = make_allowlist(store)
        account = allowlist.create(
            target=TARGET,
            label="Super negative",
            actor_id="admin",
            now=NOW,
        )["testAccount"]
        allowlist.record_successful_login(
            account["accountId"],
            subject_id="sub_super_test",
            now=NOW,
        )
        allowlist.update_authorization(
            account["accountId"],
            test_role="superTest",
            feature_entitlements=["profileSettings"],
            scenario_bindings={"grantId": "grant_not_authority"},
            expected_entitlement_revision=1,
            actor_id="admin",
            now=NOW,
        )
        sessions = AuthSessionService(
            store,
            access_ttl_seconds=900,
            refresh_ttl_seconds=3600,
            authorization_snapshot_resolver=(
                allowlist.authorization_snapshot_for_subject
            ),
        )
        issued = sessions.issue("sub_super_test")
        store.save_profile("sub_super_test", {"userId": "sub_super_test", "name": "Self"})
        store.save_profile("sub_other_owner", {"userId": "sub_other_owner", "name": "Other"})
        release_policy = ReleasePolicyService(
            authenticated_owner_v4_enabled=True,
            shadow_mode=False,
            enforce_default_closed_stages=False,
        )

        with (
            patch.object(main_module, "store", store),
            patch.object(main_module, "BACKEND_API_TOKEN", ""),
            patch.object(main_module, "AUTH_OWNERSHIP_MODE", "enforce"),
            patch.object(main_module, "RELEASE_POLICY_COMMAND_MODE", "enforce"),
            patch.object(main_module, "RELEASE_POLICY_SERVICE", release_policy),
            patch.object(
                main_module,
                "RELEASE_POLICY_COMMAND_GATE",
                ReleasePolicyCommandGate(release_policy),
            ),
            patch.object(
                main_module,
                "_test_account_allowlist_service",
                return_value=allowlist,
            ),
            patch.object(
                main_module,
                "_auth_session_service",
                return_value=sessions,
            ),
        ):
            client = TestClient(app)
            headers = {"Authorization": f"Bearer {issued['accessToken']}"}
            allowed = client.get("/profile/sub_super_test", headers=headers)
            denied = client.get("/profile/sub_other_owner", headers=headers)

        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.json()["profile"]["name"], "Self")
        self.assertEqual(denied.status_code, 403)
        self.assertNotIn("Other", denied.text)

    def test_unentitled_test_account_is_denied_before_product_route(self):
        store = InMemoryStore()
        allowlist = make_allowlist(store)
        account = allowlist.create(
            target=TARGET,
            label="No product rights",
            actor_id="admin",
            now=NOW,
        )["testAccount"]
        allowlist.record_successful_login(
            account["accountId"],
            subject_id="sub_no_entitlements",
            now=NOW,
        )
        sessions = AuthSessionService(
            store,
            access_ttl_seconds=900,
            refresh_ttl_seconds=3600,
            authorization_snapshot_resolver=(
                allowlist.authorization_snapshot_for_subject
            ),
        )
        issued = sessions.issue("sub_no_entitlements")

        with (
            patch.object(main_module, "store", store),
            patch.object(main_module, "BACKEND_API_TOKEN", ""),
            patch.object(main_module, "RELEASE_POLICY_COMMAND_MODE", "enforce"),
            patch.object(
                main_module,
                "_test_account_allowlist_service",
                return_value=allowlist,
            ),
            patch.object(
                main_module,
                "_auth_session_service",
                return_value=sessions,
            ),
        ):
            client = TestClient(app)
            policy = client.get(
                "/v2/release-policy?feature=profileSettings",
                headers={"Authorization": f"Bearer {issued['accessToken']}"},
            )
            denied = client.get(
                "/profile/sub_no_entitlements",
                headers={"Authorization": f"Bearer {issued['accessToken']}"},
            )

        self.assertEqual(policy.status_code, 200)
        self.assertEqual(policy.json()["cohort"], "testAccountRestricted")
        self.assertFalse(policy.json()["features"][0]["enabled"])
        self.assertEqual(
            policy.json()["features"][0]["reason"],
            "testAccountEntitlementMissing",
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(
            denied.json()["detail"]["code"],
            "test_account_entitlement_denied",
        )
        self.assertNotIn("testRole", denied.text)

    def test_family_and_visitor_scenario_bindings_do_not_grant_private_access(self):
        store = InMemoryStore()
        allowlist = make_allowlist(store)
        account = allowlist.create(
            target=TARGET,
            label="Family and visitor negative",
            actor_id="admin",
            now=NOW,
        )["testAccount"]
        allowlist.record_successful_login(
            account["accountId"],
            subject_id="sub_family_test",
            now=NOW,
        )
        allowlist.update_authorization(
            account["accountId"],
            test_role="familyTest",
            feature_entitlements=["profileSettings"],
            scenario_bindings={
                "relationshipId": "rel_reference_only",
                "visitorGrantId": "grant_reference_only",
            },
            expected_entitlement_revision=1,
            actor_id="admin",
            now=NOW,
        )
        sessions = AuthSessionService(
            store,
            access_ttl_seconds=900,
            refresh_ttl_seconds=3600,
            authorization_snapshot_resolver=(
                allowlist.authorization_snapshot_for_subject
            ),
        )
        issued = sessions.issue("sub_family_test")
        store.save_profile(
            "sub_private_owner",
            {"userId": "sub_private_owner", "name": "Private Owner"},
        )
        release_policy = ReleasePolicyService(
            authenticated_owner_v4_enabled=True,
            shadow_mode=False,
            enforce_default_closed_stages=False,
        )

        with (
            patch.object(main_module, "store", store),
            patch.object(main_module, "BACKEND_API_TOKEN", ""),
            patch.object(main_module, "AUTH_OWNERSHIP_MODE", "enforce"),
            patch.object(main_module, "RELEASE_POLICY_COMMAND_MODE", "enforce"),
            patch.object(main_module, "RELEASE_POLICY_SERVICE", release_policy),
            patch.object(
                main_module,
                "RELEASE_POLICY_COMMAND_GATE",
                ReleasePolicyCommandGate(release_policy),
            ),
            patch.object(
                main_module,
                "_test_account_allowlist_service",
                return_value=allowlist,
            ),
            patch.object(
                main_module,
                "_auth_session_service",
                return_value=sessions,
            ),
        ):
            client = TestClient(app)
            denied = client.get(
                "/profile/sub_private_owner",
                headers={"Authorization": f"Bearer {issued['accessToken']}"},
            )

        self.assertEqual(denied.status_code, 403)
        self.assertNotIn("Private Owner", denied.text)

    def test_0095_migration_adds_revisioned_authorization_without_raw_admin_data(self):
        migrations = Path(__file__).resolve().parents[1] / "db" / "migrations"
        sql = (migrations / "0095_test_account_authorization.sql").read_text(
            encoding="utf-8"
        )
        metadata = json.loads(
            (migrations / "0095_test_account_authorization.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(metadata["version"], "0095")
        self.assertEqual(metadata["phase"], "expand")
        self.assertIn("test_role", sql)
        self.assertIn("feature_entitlements", sql)
        self.assertIn("scenario_bindings", sql)
        self.assertIn("entitlement_revision", sql)
        self.assertIn("updated_by_hash", sql)
        self.assertNotIn("administrator_username", sql)


if __name__ == "__main__":
    unittest.main()
