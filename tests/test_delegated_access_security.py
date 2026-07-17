from __future__ import annotations

import threading
import unittest
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.services.delegated_access import (
    AccessGrantCommand,
    AccessGrantPurpose,
    DelegatedAccessService,
    GrantOperation,
    RelationshipLifecycleCommand,
    RelationshipOperation,
    ResourceScopeType,
)
from app.services.in_memory_store import InMemoryStore
from app.services.release_policy import ReleasePolicyCommandGate, ReleasePolicyService


client = TestClient(app)


class DelegatedAccessSecurityAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = main_module.store
        self.previous_backend_token = main_module.BACKEND_API_TOKEN
        self.previous_route_mode = main_module.AUTH_ROUTE_MODE
        self.previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
        self.previous_legacy_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        self.previous_release_policy_service = main_module.RELEASE_POLICY_SERVICE
        self.previous_release_policy_gate = main_module.RELEASE_POLICY_COMMAND_GATE
        self.previous_delegated_access_api_enabled = (
            main_module.DELEGATED_ACCESS_CONTRACT_API_ENABLED
        )

        main_module.store = InMemoryStore()
        main_module.BACKEND_API_TOKEN = "delegated-access-security-machine-token"
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.DELEGATED_ACCESS_CONTRACT_API_ENABLED = False
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
        main_module.DELEGATED_ACCESS_CONTRACT_API_ENABLED = (
            self.previous_delegated_access_api_enabled
        )

    def login(self, phone: str, nickname: str) -> Dict[str, Any]:
        response = client.post(
            "/auth/login",
            json={"phone": phone, "nickname": nickname, "password": "password123"},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    @staticmethod
    def headers(login_body: Dict[str, Any]) -> Dict[str, str]:
        return {"Authorization": f"Bearer {login_body['auth']['accessToken']}"}

    def test_owner_cannot_accept_invitation_or_bind_relationship_to_self(self) -> None:
        owner = self.login("13800139101", "owner")
        owner_id = owner["user"]["id"]
        invited_phone = "13800139102"
        invited = client.post(
            "/family/invite",
            headers=self.headers(owner),
            json={
                "userId": owner_id,
                "name": "受邀家人",
                "relation": "亲属",
                "phone": invited_phone,
            },
        )
        self.assertEqual(invited.status_code, 200)
        member = invited.json()["member"]

        accepted_by_owner = client.post(
            f"/family/members/{owner_id}/{member['id']}/accept",
            headers=self.headers(owner),
            json={"phone": invited_phone},
        )

        self.assertEqual(accepted_by_owner.status_code, 403)
        stored_member = main_module.store.list_family_members(owner_id)[0]
        self.assertNotEqual(stored_member.get("invitationStatus"), "accepted")
        relationship = main_module.store.get_family_relationship_by_member(
            owner_id,
            member["id"],
        )
        if relationship is not None:
            self.assertNotEqual(relationship.get("memberSubjectId"), owner_id)

    def test_client_now_query_cannot_open_future_time_letter(self) -> None:
        owner = self.login("13800139111", "owner")
        owner_id = owner["user"]["id"]
        item_id = "security-future-time-letter"
        main_module.store.add_archive_item(
            owner_id,
            {
                "id": item_id,
                "ownerUserId": owner_id,
                "kind": "timeLetter",
                "title": "未来的信",
                "note": "未到时间不可读取的正文",
                "openAt": "2099-01-01T00:00:00Z",
                "recipients": [{"id": "self", "name": "我", "type": "self"}],
                "sealedAt": "2026-07-18T08:00:00Z",
                "deliveryStatus": "scheduled",
                "metadata": {
                    "contentKind": "time_letter",
                    "timeLetterStatus": "sealed",
                    "openAt": "2099-01-01T00:00:00Z",
                    "sealedAt": "2026-07-18T08:00:00Z",
                    "deliveryStatus": "scheduled",
                },
            },
        )

        response = client.get(
            f"/archive/time-letters/{owner_id}/{item_id}/detail",
            headers=self.headers(owner),
            params={
                "viewerUserId": owner_id,
                "now": "2099-01-02T00:00:00Z",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "timeLetter is not open yet")
        self.assertNotIn("未到时间不可读取的正文", response.text)

    def test_delegated_grant_contract_endpoints_are_default_off(self) -> None:
        owner = self.login("13800139121", "owner")
        owner_id = owner["user"]["id"]
        headers = self.headers(owner)

        responses = [
            client.post(
                "/family/access-grants",
                headers=headers,
                json={"userId": owner_id},
            ),
            client.get(f"/family/access-grants/{owner_id}", headers=headers),
            client.post(
                f"/family/access-grants/{owner_id}/grant-missing/revoke",
                headers=headers,
                json={"expectedVersion": 1},
            ),
            client.post(
                f"/family/relationships/{owner_id}/relationship-missing/lifecycle",
                headers=headers,
                json={"operation": "pause", "expectedEpoch": 1},
            ),
        ]

        self.assertEqual([response.status_code for response in responses], [403] * 4)
        self.assertTrue(
            all(
                response.json()["detail"]["code"]
                == "delegatedAccessContractDefaultOff"
                for response in responses
            )
        )


class DelegatedAccessSecurityServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 18, 8, 0, tzinfo=timezone.utc)
        self.store = InMemoryStore()
        self.service = DelegatedAccessService(self.store, now_provider=lambda: self.now)
        self.owner_id = "security_owner"
        self.member_id = "security_family_member"
        self.grantee_id = "security_grantee"
        self.relationship = self.service.ensure_relationship(
            owner_subject_id=self.owner_id,
            family_member_id=self.member_id,
            member_subject_id=self.grantee_id,
            status="accepted",
        )

    def grant(
        self,
        *,
        purpose: AccessGrantPurpose,
        resource_type: ResourceScopeType,
        resource_id: str | None = None,
    ) -> Dict[str, Any]:
        return self.service.grant_access(
            AccessGrantCommand(
                grantorSubjectId=self.owner_id,
                relationshipId=self.relationship["id"],
                granteeSubjectId=self.grantee_id,
                purpose=purpose,
                resourceType=resource_type,
                resourceId=resource_id,
                operations=[GrantOperation.READ],
                expiresAt=self.now + timedelta(days=7),
            )
        )

    def test_relationship_revoke_revokes_all_active_grants_and_records_events(self) -> None:
        grants = [
            self.grant(
                purpose=AccessGrantPurpose.CARE_SNAPSHOT,
                resource_type=ResourceScopeType.CARE_SNAPSHOT,
            ),
            self.grant(
                purpose=AccessGrantPurpose.FAMILY_PERSONA,
                resource_type=ResourceScopeType.FAMILY_MEMBER,
                resource_id=self.member_id,
            ),
        ]

        self.service.change_relationship(
            RelationshipLifecycleCommand(
                ownerSubjectId=self.owner_id,
                relationshipId=self.relationship["id"],
                operation=RelationshipOperation.REVOKE,
                expectedEpoch=self.relationship["relationshipEpoch"],
            )
        )

        actual = []
        for grant in grants:
            stored = self.store.get_access_grant(grant["id"])
            events = self.store.list_grant_events(grant["id"])
            actual.append(
                {
                    "status": stored["status"] if stored is not None else None,
                    "eventTypes": [event["eventType"] for event in events],
                    "revokeReason": events[-1]["reason"] if len(events) > 1 else None,
                }
            )

        self.assertEqual(
            actual,
            [
                {
                    "status": "revoked",
                    "eventTypes": ["granted", "revoked"],
                    "revokeReason": "relationshipRevoked",
                },
                {
                    "status": "revoked",
                    "eventTypes": ["granted", "revoked"],
                    "revokeReason": "relationshipRevoked",
                },
            ],
        )

    def test_concurrent_same_scope_grant_is_idempotent_and_never_raises(self) -> None:
        store = InMemoryStore()
        service = DelegatedAccessService(store, now_provider=lambda: self.now)
        relationship = service.ensure_relationship(
            owner_subject_id=self.owner_id,
            family_member_id=self.member_id,
            member_subject_id=self.grantee_id,
            status="accepted",
        )
        command = AccessGrantCommand(
            grantorSubjectId=self.owner_id,
            relationshipId=relationship["id"],
            granteeSubjectId=self.grantee_id,
            purpose=AccessGrantPurpose.CARE_SNAPSHOT,
            resourceType=ResourceScopeType.CARE_SNAPSHOT,
            operations=[GrantOperation.READ],
            expiresAt=self.now + timedelta(days=7),
        )
        results: List[Dict[str, Any]] = []
        errors: List[BaseException] = []
        result_lock = threading.Lock()

        def create_grant() -> None:
            try:
                result = service.grant_access(command)
                with result_lock:
                    results.append(result)
            except Exception as exc:  # Capture the API-equivalent 500 boundary.
                with result_lock:
                    errors.append(exc)

        workers = [threading.Thread(target=create_grant) for _ in range(16)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)

        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(errors, [])
        active = [
            grant
            for grant in store.list_access_grants(
                owner_subject_id=self.owner_id,
                relationship_id=relationship["id"],
            )
            if grant.get("status") == "active"
        ]
        self.assertEqual(len(active), 1)
        self.assertEqual(len(results), 16)
        self.assertEqual({result["id"] for result in results}, {active[0]["id"]})

    def test_each_cross_owner_allow_persists_access_receipt(self) -> None:
        grant = self.grant(
            purpose=AccessGrantPurpose.CARE_SNAPSHOT,
            resource_type=ResourceScopeType.CARE_SNAPSHOT,
        )

        validation_only = self.service.authorize(
            owner_subject_id=self.owner_id,
            grantee_subject_id=self.grantee_id,
            family_member_id=self.member_id,
            purpose=AccessGrantPurpose.CARE_SNAPSHOT,
            operation=GrantOperation.READ,
            resource_type=ResourceScopeType.CARE_SNAPSHOT,
            record_receipt=False,
        )
        self.assertTrue(validation_only.allowed)
        self.assertIsNone(validation_only.receipt_id)
        self.assertEqual(
            self.store.list_access_receipts(
                owner_subject_id=self.owner_id,
                grant_id=grant["id"],
            ),
            [],
        )

        decisions = [
            self.service.authorize(
                owner_subject_id=self.owner_id,
                grantee_subject_id=self.grantee_id,
                family_member_id=self.member_id,
                purpose=AccessGrantPurpose.CARE_SNAPSHOT,
                operation=GrantOperation.READ,
                resource_type=ResourceScopeType.CARE_SNAPSHOT,
            )
            for _ in range(2)
        ]

        self.assertTrue(all(decision.allowed for decision in decisions))
        list_receipts = getattr(self.store, "list_access_receipts", None)
        self.assertTrue(
            callable(list_receipts),
            "delegated access store must persist and expose access receipts",
        )
        receipts = list_receipts(
            owner_subject_id=self.owner_id,
            grant_id=grant["id"],
        )
        self.assertEqual(len(receipts), 2)
        self.assertEqual(len({receipt["id"] for receipt in receipts}), 2)
        for receipt in receipts:
            self.assertEqual(receipt["decision"], "allow")
            self.assertEqual(receipt["grantId"], grant["id"])
            self.assertEqual(receipt["relationshipId"], self.relationship["id"])
            self.assertEqual(receipt["ownerSubjectId"], self.owner_id)
            self.assertEqual(receipt["granteeSubjectId"], self.grantee_id)
            self.assertEqual(receipt["purpose"], "care.snapshot")
            self.assertEqual(receipt["operation"], "read")
            self.assertEqual(receipt["resourceType"], "careSnapshot")
            self.assertEqual(receipt["grantVersion"], grant["rowVersion"])
            self.assertTrue(receipt.get("occurredAt"))


if __name__ == "__main__":
    unittest.main()
