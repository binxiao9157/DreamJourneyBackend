import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.services.auth_sessions import AuthSessionService, auth_token_hash
from app.services.in_memory_store import InMemoryStore


client = TestClient(app)


class AuthSessionAPITests(unittest.TestCase):
    def setUp(self):
        self.previous_store = main_module.store
        self.previous_backend_token = main_module.BACKEND_API_TOKEN
        self.previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
        main_module.store = InMemoryStore()
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_OWNERSHIP_MODE = "shadow"

    def tearDown(self):
        main_module.store = self.previous_store
        main_module.BACKEND_API_TOKEN = self.previous_backend_token
        main_module.AUTH_OWNERSHIP_MODE = self.previous_ownership_mode

    def login(self, phone: str = "13800138000"):
        response = client.post(
            "/auth/login",
            json={"phone": phone, "nickname": "测试用户", "password": "password123"},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    @staticmethod
    def access_headers(login_body):
        return {"Authorization": f"Bearer {login_body['auth']['accessToken']}"}

    def accepted_family_fixture(self, owner_phone: str, family_phone: str):
        owner = self.login(owner_phone)
        family = self.login(family_phone)
        invitation = client.post(
            "/family/invite",
            headers=self.access_headers(owner),
            json={
                "userId": owner["user"]["id"],
                "name": "陈岚",
                "relation": "女儿",
                "phone": family_phone,
            },
        )
        self.assertEqual(invitation.status_code, 200)
        member = invitation.json()["member"]
        accepted = client.post(
            f"/family/invitations/{member['invitationCode']}/accept",
            headers=self.access_headers(family),
            json={"phone": family_phone},
        )
        self.assertEqual(accepted.status_code, 200)
        return owner, family, accepted.json()["member"]

    def test_login_issues_opaque_tokens_without_persisting_raw_values(self):
        body = self.login()
        auth = body["auth"]

        self.assertEqual(auth["tokenType"], "Bearer")
        self.assertTrue(auth["accessToken"].startswith("dja_"))
        self.assertTrue(auth["refreshToken"].startswith("djr_"))
        self.assertEqual(auth["userId"], body["user"]["id"])
        self.assertGreater(auth["accessExpiresInSeconds"], 0)
        self.assertGreater(auth["refreshExpiresInSeconds"], auth["accessExpiresInSeconds"])

        persisted = main_module.store.get_auth_session_by_access_token_hash(
            auth_token_hash(auth["accessToken"])
        )
        self.assertIsNotNone(persisted)
        self.assertNotIn(auth["accessToken"], str(persisted))
        self.assertNotIn(auth["refreshToken"], str(persisted))
        self.assertIn("accessTokenHash", persisted)
        self.assertIn("refreshTokenHash", persisted)

    def test_runtime_exposes_cross_account_policy_without_claiming_enforce_ready(self):
        runtime = client.get("/config/runtime")

        self.assertEqual(runtime.status_code, 200)
        policy = runtime.json()["auth"]["crossAccountPolicy"]
        self.assertEqual(policy["contractVersion"], 1)
        self.assertEqual(policy["mode"], "shadow")
        self.assertFalse(policy["productionEnforceReady"])
        self.assertIn("careSnapshotRead", policy["coveredPolicies"])
        self.assertIn("timeLetterDetail", policy["coveredPolicies"])
        self.assertTrue(policy["principalBoundRouteEnforcement"])
        self.assertEqual(policy["routeOwnershipAudit"]["routeCount"], 58)
        self.assertEqual(policy["routeOwnershipAudit"]["unclassifiedCount"], 0)
        self.assertEqual(
            policy["diagnosticHeaders"],
            [
                "X-DreamJourney-Authorization-Policy",
                "X-DreamJourney-Authorization-Decision",
                "X-DreamJourney-Authorization-Reason",
            ],
        )

    def test_refresh_rotates_tokens_and_rejects_refresh_replay(self):
        auth = self.login()["auth"]
        refreshed = client.post(
            "/auth/refresh",
            json={"refreshToken": auth["refreshToken"]},
        )
        replay = client.post(
            "/auth/refresh",
            json={"refreshToken": auth["refreshToken"]},
        )

        self.assertEqual(refreshed.status_code, 200)
        refreshed_auth = refreshed.json()["auth"]
        self.assertNotEqual(refreshed_auth["accessToken"], auth["accessToken"])
        self.assertNotEqual(refreshed_auth["refreshToken"], auth["refreshToken"])
        self.assertEqual(replay.status_code, 401)

        old_access = client.get(
            "/config/runtime",
            headers={"Authorization": f"Bearer {auth['accessToken']}"},
        )
        new_access = client.get(
            "/config/runtime",
            headers={"Authorization": f"Bearer {refreshed_auth['accessToken']}"},
        )
        self.assertEqual(old_access.status_code, 401)
        self.assertEqual(new_access.status_code, 200)
        self.assertEqual(new_access.headers["x-dreamjourney-auth-principal"], "user")

    def test_logout_revokes_access_token(self):
        auth = self.login()["auth"]
        headers = {"Authorization": f"Bearer {auth['accessToken']}"}
        logout = client.post(
            "/auth/logout",
            headers=headers,
            json={"refreshToken": auth["refreshToken"]},
        )
        after_logout = client.get("/config/runtime", headers=headers)

        self.assertEqual(logout.status_code, 200)
        self.assertEqual(logout.json()["status"], "revoked")
        self.assertEqual(after_logout.status_code, 401)

    def test_principal_bound_owner_mismatch_is_blocked_while_global_mode_stays_shadow(self):
        login = self.login()
        auth = login["auth"]
        response = client.post(
            "/profile",
            headers={"Authorization": f"Bearer {auth['accessToken']}"},
            json={"userId": "user_other", "nickname": "shadow allowed"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.headers["x-dreamjourney-ownership-mode"], "shadow")
        self.assertEqual(response.headers["x-dreamjourney-ownership-decision"], "mismatch")
        self.assertEqual(response.headers["x-dreamjourney-authorization-policy"], "profileOwner")
        self.assertEqual(response.headers["x-dreamjourney-authorization-reason"], "ownerPrincipalMismatch")

    def test_owner_path_routes_reject_cross_user_access_in_shadow_mode(self):
        attacker = self.login("13800138110")
        routes = [
            ("GET", "/profile/user_other"),
            ("GET", "/voice/profiles/user_other"),
            ("GET", "/kb/snapshot/user_other"),
            ("GET", "/kb/changes/user_other"),
            ("GET", "/kb/source-ref-audit/user_other"),
            ("GET", "/memories/user_other"),
            ("GET", "/archive/items/user_other"),
            ("GET", "/mailbox/letters/user_other"),
            ("GET", "/echo/delayed-replies/user_other"),
            ("GET", "/family/members/user_other"),
        ]

        for method, path in routes:
            with self.subTest(method=method, path=path):
                response = client.request(method, path, headers=self.access_headers(attacker))
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.headers["x-dreamjourney-ownership-mode"], "shadow")
                self.assertEqual(response.headers["x-dreamjourney-authorization-decision"], "deny")
                self.assertEqual(response.headers["x-dreamjourney-authorization-reason"], "ownerPrincipalMismatch")

    def test_owner_body_routes_reject_cross_user_access_before_endpoint_validation(self):
        attacker = self.login("13800138111")
        routes = [
            ("POST", "/digital-human/sessions"),
            ("POST", "/voice/realtime-token"),
            ("POST", "/archive/items"),
            ("POST", "/kb/sync"),
            ("POST", "/kb/mutations"),
            ("POST", "/kb/governance/actions"),
            ("POST", "/devices/push-token"),
            ("POST", "/echo/delayed-replies"),
            ("POST", "/family/invite"),
        ]

        for method, path in routes:
            with self.subTest(method=method, path=path):
                response = client.request(
                    method,
                    path,
                    headers=self.access_headers(attacker),
                    json={"userId": "user_other"},
                )
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.headers["x-dreamjourney-authorization-decision"], "deny")
                self.assertEqual(response.headers["x-dreamjourney-authorization-reason"], "ownerPrincipalMismatch")

    def test_owner_can_save_and_read_own_profile_in_shadow_mode(self):
        owner = self.login("13800138112")
        user_id = owner["user"]["id"]
        saved = client.post(
            "/profile",
            headers=self.access_headers(owner),
            json={"userId": user_id, "nickname": "本人资料"},
        )
        fetched = client.get(f"/profile/{user_id}", headers=self.access_headers(owner))

        self.assertEqual(saved.status_code, 200)
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json()["profile"]["nickname"], "本人资料")
        self.assertEqual(saved.headers["x-dreamjourney-authorization-decision"], "allowOwner")

    def test_family_care_read_is_classified_as_delegated_and_enforce_safe(self):
        owner, family, member = self.accepted_family_fixture(
            "13800138101",
            "13900138101",
        )
        owner_user_id = owner["user"]["id"]
        main_module.store.save_care_snapshot(
            owner_user_id,
            {"summary": "仅家庭成员可见的关怀摘要"},
            viewer_family_member_id=member["id"],
        )
        params = {
            "viewerFamilyMemberID": member["id"],
        }

        shadow = client.get(
            f"/care/snapshots/latest/{owner_user_id}",
            headers=self.access_headers(family),
            params=params,
        )
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        enforced = client.get(
            f"/care/snapshots/latest/{owner_user_id}",
            headers=self.access_headers(family),
            params=params,
        )

        self.assertEqual(shadow.status_code, 200)
        self.assertEqual(shadow.headers["x-dreamjourney-ownership-decision"], "delegated")
        self.assertEqual(shadow.headers["x-dreamjourney-authorization-policy"], "careSnapshotRead")
        self.assertEqual(shadow.headers["x-dreamjourney-authorization-decision"], "allowFamily")
        self.assertEqual(enforced.status_code, 200)
        self.assertEqual(enforced.headers["x-dreamjourney-authorization-decision"], "allowFamily")

    def test_forged_family_care_viewer_is_observable_then_blocked_in_enforce(self):
        owner, _, member = self.accepted_family_fixture(
            "13800138102",
            "13900138102",
        )
        attacker = self.login("13700138102")
        owner_user_id = owner["user"]["id"]
        main_module.store.save_care_snapshot(
            owner_user_id,
            {"summary": "不能被伪造 viewer 读取"},
            viewer_family_member_id=member["id"],
        )
        params = {
            "viewerFamilyMemberID": member["id"],
        }

        shadow = client.get(
            f"/care/snapshots/latest/{owner_user_id}",
            headers=self.access_headers(attacker),
            params=params,
        )
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        enforced = client.get(
            f"/care/snapshots/latest/{owner_user_id}",
            headers=self.access_headers(attacker),
            params=params,
        )

        self.assertEqual(shadow.status_code, 403)
        self.assertEqual(shadow.headers["x-dreamjourney-authorization-decision"], "deny")
        self.assertEqual(shadow.headers["x-dreamjourney-authorization-reason"], "familyPrincipalMismatch")
        self.assertEqual(shadow.headers["x-dreamjourney-ownership-decision"], "mismatch")
        self.assertEqual(enforced.status_code, 403)
        self.assertEqual(enforced.headers["x-dreamjourney-authorization-decision"], "deny")

    def test_time_letter_recipient_is_bound_to_authenticated_viewer(self):
        owner, family, member = self.accepted_family_fixture(
            "13800138103",
            "13900138103",
        )
        attacker = self.login("13700138103")
        owner_user_id = owner["user"]["id"]
        family_user_id = family["user"]["id"]
        main_module.store.add_archive_item(
            owner_user_id,
            {
                "id": "letter_auth_policy",
                "kind": "timeLetter",
                "openAt": "2026-07-01T00:00:00Z",
                "recipients": [
                    {"id": "self", "name": "我", "type": "self"},
                    {"id": member["id"], "name": "陈岚", "type": "family"},
                ],
                "note": "到期后仅收件人可读",
            },
        )
        params = {
            "viewerUserId": family_user_id,
            "now": "2026-07-10T00:00:00Z",
        }

        allowed = client.get(
            f"/archive/time-letters/{owner_user_id}/letter_auth_policy/detail",
            headers=self.access_headers(family),
            params=params,
        )
        forged = client.get(
            f"/archive/time-letters/{owner_user_id}/letter_auth_policy/detail",
            headers=self.access_headers(attacker),
            params=params,
        )

        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.headers["x-dreamjourney-ownership-decision"], "delegated")
        self.assertEqual(allowed.headers["x-dreamjourney-authorization-decision"], "allowRecipient")
        self.assertEqual(forged.status_code, 403)
        self.assertEqual(forged.headers["x-dreamjourney-authorization-decision"], "deny")
        self.assertEqual(forged.headers["x-dreamjourney-authorization-reason"], "viewerPrincipalMismatch")

    def test_system_only_route_is_blocked_for_user_principal_even_in_shadow(self):
        login = self.login("13800138104")
        payload = {"now": "2026-07-10T00:00:00Z", "limit": 1}

        shadow = client.post(
            "/archive/time-letters/dispatch-due",
            headers=self.access_headers(login),
            json=payload,
        )
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        enforced = client.post(
            "/archive/time-letters/dispatch-due",
            headers=self.access_headers(login),
            json=payload,
        )

        self.assertEqual(shadow.status_code, 403)
        self.assertEqual(shadow.headers["x-dreamjourney-authorization-policy"], "systemTimeLetterDispatch")
        self.assertEqual(shadow.headers["x-dreamjourney-authorization-decision"], "deny")
        self.assertEqual(enforced.status_code, 403)
        self.assertEqual(enforced.headers["x-dreamjourney-authorization-reason"], "systemPrincipalRequired")

    def test_enforce_mode_rejects_mismatch_without_changing_default(self):
        login = self.login()
        auth = login["auth"]
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        response = client.post(
            "/profile",
            headers={"Authorization": f"Bearer {auth['accessToken']}"},
            json={"userId": "user_other", "nickname": "must reject"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(main_module.AUTH_OWNERSHIP_MODE, "enforce")

    def test_large_json_body_is_still_bound_to_authenticated_owner(self):
        login = self.login()
        auth = login["auth"]
        response = client.post(
            "/kb/sync",
            headers={"Authorization": f"Bearer {auth['accessToken']}"},
            json={"userId": login["user"]["id"], "graph": {"blob": "x" * 300_000}},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["x-dreamjourney-ownership-decision"],
            "match",
        )
        self.assertEqual(response.json()["userId"], login["user"]["id"])

    def test_legacy_backend_token_remains_compatible(self):
        main_module.BACKEND_API_TOKEN = "legacy-test-token"
        response = client.get(
            "/config/runtime",
            headers={"Authorization": "Bearer legacy-test-token"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["x-dreamjourney-auth-principal"], "system")

        dispatch = client.post(
            "/archive/time-letters/dispatch-due",
            headers={"Authorization": "Bearer legacy-test-token"},
            json={"now": "2026-07-10T00:00:00Z", "limit": 1},
        )
        self.assertEqual(dispatch.status_code, 200)
        self.assertEqual(dispatch.headers["x-dreamjourney-auth-principal"], "system")


class AuthSessionServiceTests(unittest.TestCase):
    def test_expired_access_token_is_not_resolved(self):
        store = InMemoryStore()
        now = datetime.now(timezone.utc)
        service = AuthSessionService(
            store,
            access_ttl_seconds=1,
            refresh_ttl_seconds=60,
        )
        issued = service.issue("user_expired", now=now - timedelta(seconds=2))

        self.assertIsNone(service.resolve_access_token(issued["accessToken"], now=now))


if __name__ == "__main__":
    unittest.main()
