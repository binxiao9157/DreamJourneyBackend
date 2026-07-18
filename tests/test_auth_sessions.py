import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.services.auth_sessions import AuthSessionError, AuthSessionService, auth_token_hash
from app.services.in_memory_store import InMemoryStore
from app.services.release_policy import ReleasePolicyCommandGate, ReleasePolicyService


client = TestClient(app)


class AuthSessionAPITests(unittest.TestCase):
    def setUp(self):
        self.previous_store = main_module.store
        self.previous_backend_token = main_module.BACKEND_API_TOKEN
        self.previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
        self.previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        self.previous_release_policy_service = main_module.RELEASE_POLICY_SERVICE
        self.previous_release_policy_gate = main_module.RELEASE_POLICY_COMMAND_GATE
        self.previous_delegated_access_api_enabled = (
            main_module.DELEGATED_ACCESS_CONTRACT_API_ENABLED
        )
        main_module.store = InMemoryStore()
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_OWNERSHIP_MODE = "shadow"
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.DELEGATED_ACCESS_CONTRACT_API_ENABLED = True
        service = ReleasePolicyService(
            shadow_mode=True,
            enforce_default_closed_stages=False,
        )
        main_module.RELEASE_POLICY_SERVICE = service
        main_module.RELEASE_POLICY_COMMAND_GATE = ReleasePolicyCommandGate(service)

    def tearDown(self):
        main_module.store = self.previous_store
        main_module.BACKEND_API_TOKEN = self.previous_backend_token
        main_module.AUTH_OWNERSHIP_MODE = self.previous_ownership_mode
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = self.previous_legacy_phone_login
        main_module.RELEASE_POLICY_SERVICE = self.previous_release_policy_service
        main_module.RELEASE_POLICY_COMMAND_GATE = self.previous_release_policy_gate
        main_module.DELEGATED_ACCESS_CONTRACT_API_ENABLED = (
            self.previous_delegated_access_api_enabled
        )

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

    def grant_family_access(
        self,
        owner,
        family,
        member,
        *,
        purpose: str,
        resource_type: str,
        resource_id=None,
    ):
        response = client.post(
            "/family/access-grants",
            headers=self.access_headers(owner),
            json={
                "userId": owner["user"]["id"],
                "relationshipId": member["relationshipId"],
                "granteeSubjectId": family["user"]["id"],
                "purpose": purpose,
                "resourceType": resource_type,
                "resourceId": resource_id,
                "operations": ["read"],
                "expiresAt": "2099-01-01T00:00:00Z",
            },
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["grant"]

    def test_login_issues_opaque_tokens_without_persisting_raw_values(self):
        body = self.login()
        auth = body["auth"]

        self.assertEqual(auth["tokenType"], "Bearer")
        self.assertTrue(auth["accessToken"].startswith("dja_"))
        self.assertTrue(auth["refreshToken"].startswith("djr_"))
        self.assertEqual(auth["userId"], body["user"]["id"])
        self.assertTrue(auth["tokenFamilyId"].startswith("tf_"))
        self.assertEqual(auth["sessionVersion"], 1)
        self.assertEqual(auth["contractVersion"], 2)
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
        auth_runtime = runtime.json()["auth"]
        self.assertEqual(auth_runtime["sessionContractVersion"], 2)
        self.assertEqual(auth_runtime["tokenFamilyContractVersion"], 1)
        self.assertEqual(auth_runtime["sessionLineageFields"], ["tokenFamilyId", "sessionVersion"])
        self.assertTrue(auth_runtime["refreshReuseRevokesFamily"])
        self.assertEqual(auth_runtime["legacyRefreshPolicy"], "reauthRequired")
        self.assertEqual(auth_runtime["logoutScopes"], ["session", "family", "allDevices"])
        policy = auth_runtime["crossAccountPolicy"]
        self.assertEqual(policy["contractVersion"], 1)
        self.assertEqual(policy["mode"], "shadow")
        self.assertFalse(policy["productionEnforceReady"])
        self.assertIn("careSnapshotRead", policy["coveredPolicies"])
        self.assertIn("timeLetterDetail", policy["coveredPolicies"])
        self.assertTrue(policy["principalBoundRouteEnforcement"])
        self.assertEqual(policy["routeOwnershipAudit"]["routeCount"], 83)
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
        refreshed_auth = refreshed.json()["auth"]
        new_access_before_replay = client.get(
            "/config/runtime",
            headers={"Authorization": f"Bearer {refreshed_auth['accessToken']}"},
        )
        replay = client.post(
            "/auth/refresh",
            json={"refreshToken": auth["refreshToken"]},
        )

        self.assertEqual(refreshed.status_code, 200)
        self.assertNotEqual(refreshed_auth["accessToken"], auth["accessToken"])
        self.assertNotEqual(refreshed_auth["refreshToken"], auth["refreshToken"])
        self.assertEqual(refreshed_auth["subjectId"], auth["userId"])
        self.assertEqual(refreshed_auth["parentSessionId"], auth["sessionId"])
        self.assertEqual(refreshed_auth["sessionVersion"], auth["sessionVersion"] + 1)
        self.assertEqual(new_access_before_replay.status_code, 200)
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
        self.assertEqual(new_access.status_code, 401)

    def test_refresh_reuse_revokes_the_entire_token_family(self):
        auth = self.login("13800138201")["auth"]
        first_refresh = client.post(
            "/auth/refresh",
            json={"refreshToken": auth["refreshToken"]},
        )
        self.assertEqual(first_refresh.status_code, 200)
        successor = first_refresh.json()["auth"]

        second_refresh = client.post(
            "/auth/refresh",
            json={"refreshToken": successor["refreshToken"]},
        )
        self.assertEqual(second_refresh.status_code, 200)
        descendant = second_refresh.json()["auth"]

        replay = client.post(
            "/auth/refresh",
            json={"refreshToken": auth["refreshToken"]},
        )
        descendant_access = client.get(
            "/config/runtime",
            headers={"Authorization": f"Bearer {descendant['accessToken']}"},
        )

        self.assertEqual(replay.status_code, 401)
        self.assertEqual(replay.json()["detail"]["code"], "refresh_token_reuse_detected")
        self.assertEqual(descendant_access.status_code, 401)
        self.assertEqual(successor["tokenFamilyId"], auth["tokenFamilyId"])
        self.assertEqual(descendant["tokenFamilyId"], auth["tokenFamilyId"])
        self.assertEqual(auth["sessionVersion"], 1)
        self.assertEqual(successor["sessionVersion"], 2)
        self.assertEqual(descendant["sessionVersion"], 3)

    def test_concurrent_refresh_creates_at_most_one_successor_and_revokes_it_on_reuse(self):
        store = InMemoryStore()
        service = AuthSessionService(
            store,
            access_ttl_seconds=900,
            refresh_ttl_seconds=3600,
        )
        issued = service.issue("sub_concurrent")

        def refresh_once():
            try:
                return ("success", service.refresh(issued["refreshToken"]))
            except AuthSessionError as exc:
                return (exc.code, None)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: refresh_once(), range(2)))

        successes = [payload for outcome, payload in results if outcome == "success"]
        failures = [outcome for outcome, _ in results if outcome != "success"]
        self.assertEqual(len(successes), 1)
        self.assertEqual(failures, ["refresh_token_reuse_detected"])
        self.assertIsNone(service.resolve_access_token(successes[0]["accessToken"]))
        family_events = store.list_auth_session_events(issued["tokenFamilyId"])
        self.assertEqual(
            [event["eventType"] for event in family_events].count("refreshReuseDetected"),
            1,
        )

    def test_refresh_fails_closed_when_family_subject_drifts(self):
        store = InMemoryStore()
        service = AuthSessionService(store, access_ttl_seconds=900, refresh_ttl_seconds=3600)
        issued = service.issue("subject-a")
        store._auth_token_families[issued["tokenFamilyId"]]["userId"] = "subject-b"

        with self.assertRaises(AuthSessionError) as raised:
            service.refresh(issued["refreshToken"])

        self.assertEqual(raised.exception.code, "invalid_or_expired_refresh_token")

    def test_refresh_fails_closed_when_family_version_drifts(self):
        store = InMemoryStore()
        service = AuthSessionService(store, access_ttl_seconds=900, refresh_ttl_seconds=3600)
        issued = service.issue("subject-version")
        store._auth_token_families[issued["tokenFamilyId"]]["currentSessionVersion"] = 7

        with self.assertRaises(AuthSessionError) as raised:
            service.refresh(issued["refreshToken"])

        self.assertEqual(raised.exception.code, "invalid_or_expired_refresh_token")

    def test_legacy_session_refresh_requires_reauthentication(self):
        now = datetime(2026, 7, 17, tzinfo=timezone.utc)
        store = InMemoryStore()
        store.save_auth_session(
            {
                "sessionId": "auth_legacy",
                "userId": "legacy_user",
                "accessTokenHash": auth_token_hash("dja_legacy"),
                "refreshTokenHash": auth_token_hash("djr_legacy"),
                "status": "active",
                "createdAt": now.isoformat(),
                "accessExpiresAt": (now + timedelta(minutes=15)).isoformat(),
                "refreshExpiresAt": (now + timedelta(days=1)).isoformat(),
                "contractVersion": 1,
            }
        )
        service = AuthSessionService(
            store,
            access_ttl_seconds=900,
            refresh_ttl_seconds=3600,
        )

        with self.assertRaises(AuthSessionError) as raised:
            service.refresh("djr_legacy", now=now)

        self.assertEqual(raised.exception.code, "legacy_session_reauth_required")

    def test_all_device_revoke_invalidates_every_family_for_only_that_user(self):
        store = InMemoryStore()
        service = AuthSessionService(
            store,
            access_ttl_seconds=900,
            refresh_ttl_seconds=3600,
        )
        first = service.issue("sub_owner")
        second = service.issue("sub_owner")
        other = service.issue("sub_other")

        receipt = service.revoke_all_for_user("sub_owner", reason="riskEvent")

        self.assertEqual(receipt["scope"], "allDevices")
        self.assertEqual(receipt["revokedFamilyCount"], 2)
        self.assertIn(receipt["revocationReceiptId"], store._auth_session_events)
        aggregate_event = store._auth_session_events[receipt["revocationReceiptId"]]
        self.assertEqual(aggregate_event["eventType"], "allDevicesRevoked")
        self.assertIsNone(aggregate_event["tokenFamilyId"])
        self.assertIsNone(service.resolve_access_token(first["accessToken"]))
        self.assertIsNone(service.resolve_access_token(second["accessToken"]))
        self.assertIsNotNone(service.resolve_access_token(other["accessToken"]))

    def test_account_deletion_lock_blocks_a_late_session_issue(self):
        store = InMemoryStore()
        phone = "13800138221"
        user = store.upsert_user(phone=phone, nickname="并发删除用户")
        user_id = user["id"]
        service = AuthSessionService(
            store,
            access_ttl_seconds=900,
            refresh_ttl_seconds=3600,
        )
        existing = service.issue(user_id)
        started = Event()

        def issue_after_delete_starts():
            started.set()
            try:
                service.issue(user_id)
            except AuthSessionError as exc:
                return exc.code
            return "unexpected_success"

        with ThreadPoolExecutor(max_workers=1) as executor:
            with store.auth_user_operation(user_id):
                future = executor.submit(issue_after_delete_starts)
                self.assertTrue(started.wait(timeout=1))
                deleted = store.soft_delete_user(user_id, phone=phone)
                revocation = service.revoke_all_for_user(
                    user_id,
                    reason="accountSoftDeleted",
                )
            outcome = future.result(timeout=1)

        self.assertEqual(deleted["deletionState"], "softDeleted")
        self.assertEqual(revocation["revokedFamilyCount"], 1)
        self.assertEqual(outcome, "account_session_issuance_blocked")
        self.assertIsNone(service.resolve_access_token(existing["accessToken"]))

    def test_account_epoch_rejects_old_tokens_even_before_family_revoke(self):
        store = InMemoryStore()
        phone = "13800138223"
        user = store.upsert_user(phone=phone, nickname="访问态用户")
        service = AuthSessionService(
            store,
            access_ttl_seconds=900,
            refresh_ttl_seconds=3600,
        )
        issued = service.issue(user["id"])

        deleted = store.soft_delete_user(user["id"], phone=phone)

        self.assertEqual(deleted["accessState"], "suspended_restorable")
        self.assertEqual(deleted["authEpoch"], 1)
        self.assertIsNone(service.resolve_access_token(issued["accessToken"]))
        with self.assertRaises(AuthSessionError) as raised:
            service.refresh(issued["refreshToken"])
        self.assertEqual(raised.exception.code, "account_session_revoked")

    def test_purged_account_is_terminal_for_delete_and_restore(self):
        phone = "13800138222"
        user = main_module.store.upsert_user(phone=phone, nickname="永久删除用户")
        user_id = user["id"]
        main_module.store.soft_delete_user(
            user_id,
            phone=phone,
            requested_at_iso="2026-01-01T00:00:00+00:00",
        )
        purged = main_module.store.purge_expired_deleted_users(
            "2026-02-01T00:00:00+00:00"
        )

        repeated_delete = main_module.store.soft_delete_user(user_id, phone=phone)
        repeated_restore = main_module.store.restore_user(user_id, phone=phone)
        restore_response = client.post("/auth/restore", json={"phone": phone})

        self.assertEqual(len(purged), 1)
        self.assertIsNone(repeated_delete)
        self.assertIsNone(repeated_restore)
        self.assertEqual(main_module.store.get_user(user_id)["deletionState"], "purged")
        self.assertEqual(restore_response.status_code, 410)
        self.assertEqual(
            restore_response.json()["detail"],
            "account was permanently deleted",
        )

    def test_password_change_and_soft_delete_revoke_all_user_sessions(self):
        main_module.RELEASE_POLICY_SERVICE._CLOSED_PILOT_OWNER_VISIBLE = {
            *main_module.RELEASE_POLICY_SERVICE._CLOSED_PILOT_OWNER_VISIBLE,
            "accountPasswordChange",
        }
        login = self.login("13800138202")
        auth = login["auth"]
        headers = self.access_headers(login)

        changed = client.post(
            "/auth/password",
            headers=headers,
            json={
                "userId": login["user"]["id"],
                "oldPassword": "password123",
                "newPassword": "password456",
            },
        )
        after_password_change = client.get("/config/runtime", headers=headers)

        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.json()["sessionRevocation"]["scope"], "allDevices")
        self.assertEqual(after_password_change.status_code, 401)

        second_login_response = client.post(
            "/auth/login",
            json={
                "phone": "13800138202",
                "nickname": "测试用户",
                "password": "password456",
            },
        )
        self.assertEqual(second_login_response.status_code, 200)
        second_login = second_login_response.json()
        delete_headers = self.access_headers(second_login)
        deleted = client.post(
            "/auth/delete",
            headers=delete_headers,
            json={
                "userId": second_login["user"]["id"],
                "phone": "13800138202",
                "firstConfirmation": True,
                "secondConfirmation": True,
            },
        )
        after_delete = client.get("/config/runtime", headers=delete_headers)

        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json()["sessionRevocation"]["scope"], "allDevices")
        self.assertEqual(after_delete.status_code, 401)

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
        self.assertEqual(logout.json()["scope"], "session")
        self.assertTrue(logout.json()["revocationReceiptId"].startswith("ase_"))
        self.assertEqual(after_logout.status_code, 401)

    def test_logout_family_and_all_devices_scopes_do_not_cross_users(self):
        first = self.login("13800138211")
        second = self.login("13800138211")
        other = self.login("13800138212")

        family_logout = client.post(
            "/auth/logout",
            headers=self.access_headers(first),
            json={"scope": "family"},
        )
        first_after = client.get("/config/runtime", headers=self.access_headers(first))
        second_after_family = client.get("/config/runtime", headers=self.access_headers(second))
        self.assertEqual(family_logout.status_code, 200)
        self.assertEqual(family_logout.json()["scope"], "family")
        self.assertEqual(first_after.status_code, 401)
        self.assertEqual(second_after_family.status_code, 200)

        all_devices_logout = client.post(
            "/auth/logout",
            headers=self.access_headers(second),
            json={"scope": "allDevices"},
        )
        second_after_all = client.get("/config/runtime", headers=self.access_headers(second))
        other_after = client.get("/config/runtime", headers=self.access_headers(other))
        self.assertEqual(all_devices_logout.status_code, 200)
        self.assertEqual(all_devices_logout.json()["scope"], "allDevices")
        self.assertEqual(second_after_all.status_code, 401)
        self.assertEqual(other_after.status_code, 200)

    def test_logout_rejects_unknown_revocation_scope_without_revoking_session(self):
        login = self.login("13800138213")
        rejected = client.post(
            "/auth/logout",
            headers=self.access_headers(login),
            json={"scope": "everything"},
        )
        still_active = client.get("/config/runtime", headers=self.access_headers(login))

        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(rejected.json()["detail"]["code"], "unsupported_revocation_scope")
        self.assertEqual(still_active.status_code, 200)

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

    def test_owner_body_route_derives_user_id_from_authenticated_principal(self):
        owner = self.login("13800138113")
        user_id = owner["user"]["id"]

        saved = client.post(
            "/profile",
            headers=self.access_headers(owner),
            json={"nickname": "服务端派生本人"},
        )
        fetched = client.get(f"/profile/{user_id}", headers=self.access_headers(owner))

        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["profile"]["userId"], user_id)
        self.assertEqual(saved.headers["x-dreamjourney-authorization-reason"], "ownerDerivedFromPrincipal")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json()["profile"]["nickname"], "服务端派生本人")

    def test_nested_owner_claim_cannot_transfer_archive_authority(self):
        owner = self.login("13800138114")
        user_id = owner["user"]["id"]
        response = client.post(
            "/archive/items",
            headers=self.access_headers(owner),
            json={
                "id": "nested_owner_conflict",
                "userId": user_id,
                "kind": "photo",
                "privacyMetadata": {"scope": "generationAllowed"},
                "metadata": {"ownerUserId": "user_other"},
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.headers["x-dreamjourney-authorization-decision"], "deny")
        self.assertEqual(response.headers["x-dreamjourney-authorization-reason"], "ownerClaimMismatch")
        self.assertEqual(main_module.store.list_archive_items(user_id), [])

    def test_archive_owner_is_principal_derived_and_cross_owner_access_is_denied(self):
        owner = self.login("13800138115")
        attacker = self.login("13800138116")
        owner_id = owner["user"]["id"]
        attacker_id = attacker["user"]["id"]
        archive_id = "archive_owner_contract"
        base_payload = {
            "id": archive_id,
            "kind": "photo",
            "privacyMetadata": {"scope": "generationAllowed"},
        }

        created = client.post(
            "/archive/items",
            headers=self.access_headers(owner),
            json={**base_payload, "title": "owner original"},
        )
        forged = client.post(
            "/archive/items",
            headers=self.access_headers(owner),
            json={**base_payload, "userId": attacker_id, "title": "forged owner"},
        )
        updated = client.post(
            "/archive/items",
            headers=self.access_headers(owner),
            json={**base_payload, "title": "owner updated"},
        )
        collision = client.post(
            "/archive/items",
            headers=self.access_headers(attacker),
            json={**base_payload, "title": "attacker overwrite"},
        )
        owner_list = client.get(
            f"/archive/items/{owner_id}",
            headers=self.access_headers(owner),
        )
        cross_owner_get = client.get(
            f"/archive/items/{owner_id}",
            headers=self.access_headers(attacker),
        )
        cross_owner_delete = client.delete(
            f"/archive/items/{attacker_id}/{archive_id}",
            headers=self.access_headers(attacker),
        )

        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["item"]["userId"], owner_id)
        self.assertEqual(created.json()["item"]["ownerUserId"], owner_id)
        self.assertEqual(created.headers["x-dreamjourney-auth-principal"], "user")
        self.assertEqual(
            created.headers["x-dreamjourney-authorization-reason"],
            "ownerDerivedFromPrincipal",
        )
        self.assertEqual(forged.status_code, 403)
        self.assertEqual(
            forged.headers["x-dreamjourney-authorization-reason"],
            "ownerPrincipalMismatch",
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["item"]["title"], "owner updated")
        self.assertEqual(collision.status_code, 409)
        self.assertEqual(
            collision.json()["detail"]["code"],
            "archiveItemOwnershipConflict",
        )
        self.assertEqual(owner_list.status_code, 200)
        self.assertEqual(
            [(item["id"], item["title"]) for item in owner_list.json()["items"]],
            [(archive_id, "owner updated")],
        )
        self.assertEqual(cross_owner_get.status_code, 403)
        self.assertEqual(
            cross_owner_get.headers["x-dreamjourney-authorization-reason"],
            "ownerPrincipalMismatch",
        )
        self.assertEqual(cross_owner_delete.status_code, 403)
        self.assertEqual(
            cross_owner_delete.headers["x-dreamjourney-authorization-reason"],
            "resourceOwnerMismatch",
        )
        self.assertEqual(
            main_module.store.list_archive_items(owner_id)[0]["title"],
            "owner updated",
        )

    def test_family_care_read_is_classified_as_delegated_and_enforce_safe(self):
        owner, family, member = self.accepted_family_fixture(
            "13800138101",
            "13900138101",
        )
        owner_user_id = owner["user"]["id"]
        self.grant_family_access(
            owner,
            family,
            member,
            purpose="care.snapshot",
            resource_type="careSnapshot",
        )
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
        self.grant_family_access(
            owner,
            family,
            member,
            purpose="timeLetter.read",
            resource_type="timeLetter",
            resource_id="letter_auth_policy",
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
        self.assertEqual(response.headers["x-dreamjourney-auth-principal"], "machine")

        dispatch = client.post(
            "/archive/time-letters/dispatch-due",
            headers={"Authorization": "Bearer legacy-test-token"},
            json={"now": "2026-07-10T00:00:00Z", "limit": 1},
        )
        self.assertEqual(dispatch.status_code, 200)
        self.assertEqual(dispatch.headers["x-dreamjourney-auth-principal"], "machine")


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

    def test_expired_refresh_requests_commit_of_terminal_session_state(self):
        store = InMemoryStore()
        now = datetime.now(timezone.utc)
        store.upsert_user(phone="13800138223", nickname="过期刷新用户")
        user_id = next(iter(store._users))
        service = AuthSessionService(
            store,
            access_ttl_seconds=1,
            refresh_ttl_seconds=2,
        )
        issued = service.issue(user_id, now=now)

        with self.assertRaises(AuthSessionError) as raised:
            service.refresh(issued["refreshToken"], now=now + timedelta(seconds=3))

        persisted = next(
            item
            for item in store._auth_sessions.values()
            if item.get("refreshTokenHash") == auth_token_hash(issued["refreshToken"])
        )
        self.assertEqual(raised.exception.code, "invalid_or_expired_refresh_token")
        self.assertTrue(raised.exception.commit_state_change)
        self.assertEqual(persisted["status"], "expired")

    def test_all_device_logout_rechecks_token_after_acquiring_user_lock(self):
        class PurgingOnSecondAccessLookupStore(InMemoryStore):
            def __init__(self):
                super().__init__()
                self.access_lookup_count = 0

            def get_auth_session_by_access_token_hash(self, token_hash):
                self.access_lookup_count += 1
                if self.access_lookup_count == 2:
                    self._auth_sessions.clear()
                    self._auth_token_families.clear()
                    self._auth_session_events.clear()
                    for user_id, user in list(self._users.items()):
                        purged = dict(user)
                        purged["deletionState"] = "purged"
                        self._users[user_id] = purged
                return super().get_auth_session_by_access_token_hash(token_hash)

        store = PurgingOnSecondAccessLookupStore()
        user = store.upsert_user(phone="13800138224", nickname="并发清除用户")
        service = AuthSessionService(
            store,
            access_ttl_seconds=900,
            refresh_ttl_seconds=3600,
        )
        issued = service.issue(user["id"])

        result = service.revoke_access_token(
            issued["accessToken"],
            scope="allDevices",
            reason="logout",
        )

        self.assertIsNone(result)
        self.assertEqual(store._auth_session_events, {})


if __name__ == "__main__":
    unittest.main()
