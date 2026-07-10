import unittest

from app.services.authorization_policy import CrossAccountAuthorizationPolicy
from app.services.in_memory_store import InMemoryStore
from app.services.user_identity import stable_user_id


class CrossAccountAuthorizationPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryStore()
        self.policy = CrossAccountAuthorizationPolicy(self.store)
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
        return member

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

    def test_only_active_family_principal_can_read_member_care_snapshot(self):
        member = self.add_family_member(accepted=True)

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

        self.assertEqual(decision.policy_id, "systemOnly")
        self.assertEqual(decision.decision, "deny")
        self.assertEqual(decision.reason, "systemPrincipalRequired")

    def test_unknown_route_defers_to_ownership_fallback(self):
        decision = self.evaluate(
            method="POST",
            path="/profile",
            principal=self.owner_user_id,
            payload={"userId": self.owner_user_id},
        )

        self.assertEqual(decision.decision, "fallback")
        self.assertIsNone(decision.allowed)
        self.assertFalse(decision.terminal)

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
