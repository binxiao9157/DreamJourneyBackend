import unittest
from datetime import datetime, timezone

from app.services.authorization_policy import (
    CrossAccountAuthorizationPolicy,
    owner_authority_claims,
)
from app.services.delegated_access import (
    AccessGrantCommand,
    AccessGrantPurpose,
    DelegatedAccessService,
    GrantOperation,
    ResourceScopeType,
)
from app.services.in_memory_store import InMemoryStore
from app.services.user_identity import stable_user_id


class CrossAccountAuthorizationPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryStore()
        self.policy = CrossAccountAuthorizationPolicy(self.store)
        self.delegated_access = DelegatedAccessService(self.store)
        self.owner_user_id = "user_owner"
        self.family_phone = "13900001111"
        self.family_user_id = stable_user_id(self.family_phone)

    def add_family_member(self, *, accepted: bool = True):
        member = self.store.add_family_member(
            self.owner_user_id,
            {
                "name": "陈岚",
                "phone": self.family_phone,
                "accessStatus": "pending",
                "invitationStatus": "pending",
                "invitationCode": "FAMILYCODE",
            },
        )
        if accepted:
            member = self.store.accept_family_member(
                self.owner_user_id,
                member["id"],
                phone=self.family_phone,
            )
        self.delegated_access.ensure_relationship_for_member(
            owner_subject_id=self.owner_user_id,
            member=member,
            accepted_subject_id=self.family_user_id if accepted else None,
        )
        return member

    def grant(
        self,
        member,
        *,
        purpose: AccessGrantPurpose,
        resource_type: ResourceScopeType,
        resource_id=None,
    ):
        relationship = self.store.get_family_relationship_by_member(
            self.owner_user_id,
            member["id"],
        )
        return self.delegated_access.grant_access(
            AccessGrantCommand(
                grantorSubjectId=self.owner_user_id,
                relationshipId=relationship["id"],
                granteeSubjectId=self.family_user_id,
                purpose=purpose,
                resourceType=resource_type,
                resourceId=resource_id,
                operations=[GrantOperation.READ],
                expiresAt=datetime(2099, 1, 1, tzinfo=timezone.utc),
            )
        )

    def evaluate(self, *, method: str, path: str, principal: str, query=None, payload=None):
        return self.policy.evaluate(
            method=method,
            path=path,
            principal_user_id=principal,
            query=query or {},
            payload=payload or {},
        )

    def test_owner_can_read_own_care_snapshot(self):
        decision = self.evaluate(
            method="GET",
            path=f"/care/snapshots/latest/{self.owner_user_id}",
            principal=self.owner_user_id,
        )

        self.assertEqual(decision.policy_id, "careSnapshotRead")
        self.assertEqual(decision.decision, "allowOwner")
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.delegated)
        self.assertTrue(decision.principal_bound)

    def test_only_active_family_principal_can_read_member_care_snapshot(self):
        member = self.add_family_member(accepted=True)
        self.grant(
            member,
            purpose=AccessGrantPurpose.CARE_SNAPSHOT,
            resource_type=ResourceScopeType.CARE_SNAPSHOT,
        )

        allowed = self.evaluate(
            method="GET",
            path=f"/care/snapshots/latest/{self.owner_user_id}",
            principal=self.family_user_id,
            query={"viewerFamilyMemberID": member["id"]},
        )
        denied = self.evaluate(
            method="GET",
            path=f"/care/snapshots/latest/{self.owner_user_id}",
            principal=stable_user_id("13999999999"),
            query={"viewerFamilyMemberID": member["id"]},
        )

        self.assertEqual(allowed.decision, "allowFamily")
        self.assertTrue(allowed.allowed)
        self.assertTrue(allowed.delegated)
        self.assertEqual(denied.decision, "deny")
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.reason, "familyPrincipalMismatch")

    def test_accepted_relationship_without_grant_cannot_read_care_snapshot(self):
        member = self.add_family_member(accepted=True)

        decision = self.evaluate(
            method="GET",
            path=f"/care/snapshots/latest/{self.owner_user_id}",
            principal=self.family_user_id,
            query={"viewerFamilyMemberID": member["id"]},
        )

        self.assertEqual(decision.decision, "deny")
        self.assertEqual(decision.reason, "activeGrantRequired")

    def test_pending_family_member_is_denied(self):
        member = self.add_family_member(accepted=False)

        decision = self.evaluate(
            method="GET",
            path=f"/care/snapshots/{self.owner_user_id}",
            principal=self.family_user_id,
            query={"viewerFamilyMemberID": member["id"]},
        )

        self.assertEqual(decision.decision, "deny")
        self.assertEqual(decision.reason, "familyAccessInactive")

    def test_time_letter_recipient_requires_principal_bound_viewer(self):
        member = self.add_family_member(accepted=True)
        self.store.add_archive_item(
            self.owner_user_id,
            {
                "id": "letter_1",
                "kind": "timeLetter",
                "openAt": "2026-06-20T00:00:00Z",
                "recipients": [
                    {"id": "self", "name": "我", "type": "self"},
                    {"id": member["id"], "name": member["name"], "type": "family"},
                ],
            },
        )
        self.grant(
            member,
            purpose=AccessGrantPurpose.TIME_LETTER_READ,
            resource_type=ResourceScopeType.TIME_LETTER,
            resource_id="letter_1",
        )

        allowed = self.evaluate(
            method="GET",
            path=f"/archive/time-letters/{self.owner_user_id}/letter_1/detail",
            principal=self.family_user_id,
            query={"viewerUserId": self.family_user_id},
        )
        forged = self.evaluate(
            method="GET",
            path=f"/archive/time-letters/{self.owner_user_id}/letter_1/detail",
            principal=stable_user_id("13700000000"),
            query={"viewerUserId": self.family_user_id},
        )

        self.assertEqual(allowed.policy_id, "timeLetterDetail")
        self.assertEqual(allowed.decision, "allowRecipient")
        self.assertTrue(allowed.delegated)
        self.assertEqual(forged.decision, "deny")
        self.assertEqual(forged.reason, "viewerPrincipalMismatch")

    def test_invitation_acceptance_is_bound_to_phone_principal(self):
        allowed = self.evaluate(
            method="POST",
            path="/family/invitations/FAMILYCODE/accept",
            principal=self.family_user_id,
            payload={"phone": self.family_phone},
        )
        denied = self.evaluate(
            method="POST",
            path="/family/invitations/FAMILYCODE/accept",
            principal=stable_user_id("13800000000"),
            payload={"phone": self.family_phone},
        )

        self.assertEqual(allowed.decision, "allowRecipient")
        self.assertEqual(denied.decision, "deny")
        self.assertEqual(denied.reason, "invitationPrincipalMismatch")

    def test_user_principal_cannot_run_system_only_dispatch(self):
        decision = self.evaluate(
            method="POST",
            path="/archive/time-letters/dispatch-due",
            principal=self.owner_user_id,
            payload={"now": "2026-06-20T00:00:00Z"},
        )

        self.assertEqual(decision.policy_id, "systemTimeLetterDispatch")
        self.assertEqual(decision.decision, "deny")
        self.assertEqual(decision.reason, "systemPrincipalRequired")
        self.assertTrue(decision.principal_bound)

    def test_registry_owner_route_binds_principal(self):
        allowed = self.evaluate(
            method="POST",
            path="/profile",
            principal=self.owner_user_id,
            payload={"userId": self.owner_user_id},
        )
        denied = self.evaluate(
            method="POST",
            path="/profile",
            principal=self.owner_user_id,
            payload={"userId": "user_other"},
        )

        self.assertEqual(allowed.policy_id, "profileOwner")
        self.assertEqual(allowed.decision, "allowOwner")
        self.assertTrue(allowed.principal_bound)
        self.assertEqual(denied.decision, "deny")
        self.assertEqual(denied.reason, "ownerPrincipalMismatch")
        self.assertTrue(denied.principal_bound)

    def test_owner_route_derives_authority_from_principal_when_body_omits_user_id(self):
        decision = self.evaluate(
            method="POST",
            path="/profile",
            principal=self.owner_user_id,
            payload={"nickname": "本人资料"},
        )

        self.assertEqual(decision.decision, "allowOwner")
        self.assertEqual(decision.reason, "ownerDerivedFromPrincipal")
        self.assertTrue(decision.allowed)
        self.assertTrue(decision.principal_bound)

    def test_nested_owner_and_uploader_claims_cannot_override_principal(self):
        decision = self.evaluate(
            method="POST",
            path="/archive/items",
            principal=self.owner_user_id,
            payload={
                "userId": self.owner_user_id,
                "metadata": {
                    "ownerUserId": "user_other",
                    "uploaderUserId": self.owner_user_id,
                },
            },
        )

        self.assertEqual(decision.decision, "deny")
        self.assertEqual(decision.reason, "ownerClaimMismatch")
        self.assertFalse(decision.allowed)

    def test_recipient_identity_is_not_misclassified_as_resource_owner(self):
        claims = owner_authority_claims(
            {
                "userId": self.owner_user_id,
                "recipients": [
                    {"recipientUserId": "user_recipient"},
                    {"ownerUserId": self.owner_user_id},
                ],
            }
        )

        self.assertEqual(claims, {self.owner_user_id})

    def test_child_resource_is_resolved_from_store_owner_not_request_path(self):
        self.store.add_archive_item(
            self.owner_user_id,
            {"id": "archive-owned", "kind": "photo"},
        )

        allowed = self.evaluate(
            method="DELETE",
            path=f"/archive/items/{self.owner_user_id}/archive-owned",
            principal=self.owner_user_id,
        )
        forged_vault = self.evaluate(
            method="DELETE",
            path="/archive/items/user_other/archive-owned",
            principal="user_other",
        )
        missing = self.evaluate(
            method="DELETE",
            path=f"/archive/items/{self.owner_user_id}/archive-missing",
            principal=self.owner_user_id,
        )

        self.assertEqual(allowed.reason, "resourceOwner")
        self.assertTrue(allowed.allowed)
        self.assertEqual(forged_vault.reason, "resourceOwnerMismatch")
        self.assertFalse(forged_vault.allowed)
        self.assertEqual(missing.reason, "resourceNotFound")
        self.assertFalse(missing.allowed)

    def test_archive_side_effect_requires_an_existing_owned_item(self):
        self.store.add_archive_item(
            self.owner_user_id,
            {"id": "archive-analysis", "kind": "photo"},
        )

        allowed = self.evaluate(
            method="POST",
            path="/archive/image-analysis",
            principal=self.owner_user_id,
            payload={"archiveItemId": "archive-analysis"},
        )
        denied = self.evaluate(
            method="POST",
            path="/archive/image-analysis",
            principal=self.owner_user_id,
            payload={"archiveItemId": "missing"},
        )

        self.assertEqual(allowed.reason, "resourceOwner")
        self.assertTrue(allowed.allowed)
        self.assertEqual(denied.reason, "resourceNotFound")
        self.assertFalse(denied.allowed)

    def test_quarantined_resource_fails_closed(self):
        self.store.add_archive_item(
            self.owner_user_id,
            {
                "id": "archive-quarantined",
                "kind": "photo",
                "authorityState": "quarantined",
            },
        )

        decision = self.evaluate(
            method="POST",
            path="/archive/image-analysis",
            principal=self.owner_user_id,
            payload={"archiveItemId": "archive-quarantined"},
        )

        self.assertEqual(decision.reason, "resourceQuarantined")
        self.assertFalse(decision.allowed)

    def test_stale_expected_version_is_denied_before_resource_side_effect(self):
        self.store.add_archive_item(
            self.owner_user_id,
            {
                "id": "archive-versioned",
                "kind": "photo",
                "resourceVersion": 3,
            },
        )

        current = self.evaluate(
            method="POST",
            path="/archive/image-analysis",
            principal=self.owner_user_id,
            payload={"archiveItemId": "archive-versioned", "expectedVersion": 3},
        )
        stale = self.evaluate(
            method="POST",
            path="/archive/image-analysis",
            principal=self.owner_user_id,
            payload={"archiveItemId": "archive-versioned", "expectedVersion": 2},
        )

        self.assertTrue(current.allowed)
        self.assertEqual(stale.reason, "resourceVersionMismatch")
        self.assertFalse(stale.allowed)

    def test_unknown_route_defers_to_ownership_fallback(self):
        decision = self.evaluate(
            method="POST",
            path="/future/unclassified",
            principal=self.owner_user_id,
        )

        self.assertEqual(decision.decision, "fallback")
        self.assertIsNone(decision.allowed)
        self.assertFalse(decision.terminal)
        self.assertFalse(decision.principal_bound)

    def test_header_values_only_contain_fixed_safe_enums(self):
        decision = self.evaluate(
            method="POST",
            path="/family/invitations/FAMILYCODE/accept",
            principal=stable_user_id("13800000000"),
            payload={"phone": self.family_phone},
        )
        header_text = "|".join(decision.header_values().values())

        self.assertNotIn(self.family_phone, header_text)
        self.assertNotIn(self.family_user_id, header_text)
        self.assertEqual(
            decision.header_values(),
            {
                "policy": "familyInvitationAccept",
                "decision": "deny",
                "reason": "invitationPrincipalMismatch",
            },
        )


if __name__ == "__main__":
    unittest.main()
