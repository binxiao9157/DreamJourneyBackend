import unittest
from datetime import datetime, timedelta, timezone
from typing import Optional

from pydantic import ValidationError

from app.services.delegated_access import (
    AccessGrantCommand,
    AccessGrantPurpose,
    DelegatedAccessService,
    GrantOperation,
    RelationshipLifecycleCommand,
    RelationshipOperation,
    ResourceScopeType,
    RevokeAccessGrantCommand,
)
from app.services.in_memory_store import InMemoryStore


class DelegatedAccessServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 18, 8, 0, tzinfo=timezone.utc)
        self.store = InMemoryStore()
        self.service = DelegatedAccessService(
            self.store,
            now_provider=lambda: self.now,
        )
        self.owner_id = "subject_owner"
        self.member_id = "family_member_1"
        self.grantee_id = "subject_family_member"
        self.relationship = self.service.ensure_relationship(
            owner_subject_id=self.owner_id,
            family_member_id=self.member_id,
            member_subject_id=self.grantee_id,
            status="accepted",
        )

    def grant(
        self,
        *,
        purpose: AccessGrantPurpose = AccessGrantPurpose.CARE_SNAPSHOT,
        resource_type: ResourceScopeType = ResourceScopeType.CARE_SNAPSHOT,
        resource_id: Optional[str] = None,
        expires_at: Optional[datetime] = None,
    ):
        return self.service.grant_access(
            AccessGrantCommand(
                grantorSubjectId=self.owner_id,
                relationshipId=self.relationship["id"],
                granteeSubjectId=self.grantee_id,
                purpose=purpose,
                resourceType=resource_type,
                resourceId=resource_id,
                operations=[GrantOperation.READ],
                expiresAt=expires_at or (self.now + timedelta(days=7)),
            )
        )

    def authorize_care(self, *, grantee_id: Optional[str] = None):
        return self.service.authorize(
            owner_subject_id=self.owner_id,
            grantee_subject_id=grantee_id or self.grantee_id,
            family_member_id=self.member_id,
            purpose=AccessGrantPurpose.CARE_SNAPSHOT,
            operation=GrantOperation.READ,
            resource_type=ResourceScopeType.CARE_SNAPSHOT,
        )

    def test_accepted_relationship_without_independent_grant_is_denied(self):
        decision = self.authorize_care()

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "activeGrantRequired")

    def test_matching_active_grant_allows_verified_grantee(self):
        grant = self.grant()

        decision = self.authorize_care()

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "activeGrant")
        self.assertEqual(decision.grant_id, grant["id"])
        self.assertEqual(decision.relationship_id, self.relationship["id"])

    def test_duplicate_active_scope_returns_existing_grant_without_new_event(self):
        first = self.grant()
        second = self.grant()

        self.assertEqual(second["id"], first["id"])
        self.assertEqual(len(self.store.list_access_grants(owner_subject_id=self.owner_id)), 1)
        self.assertEqual(
            [item["eventType"] for item in self.store.list_grant_events(first["id"])],
            ["granted"],
        )

    def test_purpose_mismatch_is_denied(self):
        self.grant(
            purpose=AccessGrantPurpose.FAMILY_PERSONA,
            resource_type=ResourceScopeType.FAMILY_MEMBER,
            resource_id=self.member_id,
        )

        decision = self.authorize_care()

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "activeGrantRequired")

    def test_resource_mismatch_is_denied(self):
        self.grant(
            purpose=AccessGrantPurpose.TIME_LETTER_READ,
            resource_type=ResourceScopeType.TIME_LETTER,
            resource_id="letter_1",
        )

        decision = self.service.authorize(
            owner_subject_id=self.owner_id,
            grantee_subject_id=self.grantee_id,
            family_member_id=self.member_id,
            purpose=AccessGrantPurpose.TIME_LETTER_READ,
            operation=GrantOperation.READ,
            resource_type=ResourceScopeType.TIME_LETTER,
            resource_id="letter_2",
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "activeGrantRequired")

    def test_expired_grant_is_denied_and_projected_as_expired(self):
        grant = self.grant(expires_at=self.now + timedelta(seconds=1))
        self.now += timedelta(seconds=2)

        decision = self.authorize_care()
        projected = self.service.list_relationship_grants(
            owner_subject_id=self.owner_id,
            relationship_id=self.relationship["id"],
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "activeGrantRequired")
        self.assertEqual(projected[0]["id"], grant["id"])
        self.assertEqual(projected[0]["status"], "expired")

    def test_expired_scope_can_be_regranted_without_unique_active_conflict(self):
        expired = self.grant(expires_at=self.now + timedelta(seconds=1))
        self.now += timedelta(seconds=2)

        replacement = self.grant(expires_at=self.now + timedelta(days=7))

        self.assertNotEqual(replacement["id"], expired["id"])
        self.assertEqual(self.store.get_access_grant(expired["id"])["status"], "revoked")
        self.assertTrue(self.authorize_care().allowed)
        self.assertEqual(
            [item["reason"] for item in self.store.list_grant_events(expired["id"])],
            ["ownerGranted", "expiredBeforeRegrant"],
        )

    def test_revoked_grant_is_denied_and_records_event(self):
        grant = self.grant()

        revoked = self.service.revoke_access(
            RevokeAccessGrantCommand(
                grantorSubjectId=self.owner_id,
                grantId=grant["id"],
                expectedVersion=grant["rowVersion"],
                reason="ownerRequested",
            )
        )
        decision = self.authorize_care()
        events = self.store.list_grant_events(grant["id"])

        self.assertEqual(revoked["status"], "revoked")
        self.assertFalse(decision.allowed)
        self.assertEqual([item["eventType"] for item in events], ["granted", "revoked"])

    def test_paused_relationship_revokes_effective_access_without_deleting_grant(self):
        grant = self.grant()

        paused = self.service.change_relationship(
            RelationshipLifecycleCommand(
                ownerSubjectId=self.owner_id,
                relationshipId=self.relationship["id"],
                operation=RelationshipOperation.PAUSE,
                expectedEpoch=self.relationship["relationshipEpoch"],
            )
        )
        decision = self.authorize_care()

        self.assertEqual(paused["status"], "paused")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "relationshipInactive")
        self.assertEqual(self.store.get_access_grant(grant["id"])["status"], "active")

    def test_revoked_relationship_permanently_blocks_existing_grant(self):
        grant = self.grant()

        revoked = self.service.change_relationship(
            RelationshipLifecycleCommand(
                ownerSubjectId=self.owner_id,
                relationshipId=self.relationship["id"],
                operation=RelationshipOperation.REVOKE,
                expectedEpoch=self.relationship["relationshipEpoch"],
            )
        )
        decision = self.authorize_care()

        self.assertEqual(revoked["status"], "revoked")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "relationshipInactive")
        self.assertEqual(self.store.get_access_grant(grant["id"])["status"], "revoked")
        self.assertEqual(
            [item["eventType"] for item in self.store.list_grant_events(grant["id"])],
            ["granted", "revoked"],
        )
        with self.assertRaisesRegex(ValueError, "relationshipTransitionInvalid"):
            self.service.change_relationship(
                RelationshipLifecycleCommand(
                    ownerSubjectId=self.owner_id,
                    relationshipId=self.relationship["id"],
                    operation=RelationshipOperation.RESUME,
                    expectedEpoch=revoked["relationshipEpoch"],
                )
            )

    def test_subject_mismatch_is_denied_even_when_relationship_and_grant_are_active(self):
        self.grant()

        decision = self.authorize_care(grantee_id="subject_reused_phone")

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "relationshipSubjectMismatch")

    def test_time_letter_grant_requires_resource_id(self):
        with self.assertRaises(ValidationError):
            AccessGrantCommand(
                grantorSubjectId=self.owner_id,
                relationshipId=self.relationship["id"],
                granteeSubjectId=self.grantee_id,
                purpose=AccessGrantPurpose.TIME_LETTER_READ,
                resourceType=ResourceScopeType.TIME_LETTER,
                operations=[GrantOperation.READ],
                expiresAt=self.now + timedelta(days=1),
            )

    def test_family_persona_grant_requires_member_resource_id(self):
        with self.assertRaises(ValidationError):
            AccessGrantCommand(
                grantorSubjectId=self.owner_id,
                relationshipId=self.relationship["id"],
                granteeSubjectId=self.grantee_id,
                purpose=AccessGrantPurpose.FAMILY_PERSONA,
                resourceType=ResourceScopeType.FAMILY_MEMBER,
                operations=[GrantOperation.READ],
                expiresAt=self.now + timedelta(days=1),
            )

    def test_existing_accepted_member_migration_does_not_create_grant(self):
        legacy = self.store.add_family_member(
            "legacy_owner",
            {
                "id": "legacy_family_1",
                "phone": "13900001111",
                "accessStatus": "active",
                "invitationStatus": "accepted",
            },
        )

        relationship = self.service.ensure_relationship_for_member(
            owner_subject_id="legacy_owner",
            member=legacy,
        )

        self.assertEqual(relationship["status"], "accepted")
        self.assertEqual(
            self.service.list_relationship_grants(
                owner_subject_id="legacy_owner",
                relationship_id=relationship["id"],
            ),
            [],
        )

    def test_failed_invitation_remains_pending_and_creates_no_grant(self):
        failed = self.store.add_family_member(
            "failed_owner",
            {
                "id": "failed_family_1",
                "phone": "13900002222",
                "accessStatus": "failed",
                "invitationStatus": "failed",
            },
        )

        relationship = self.service.ensure_relationship_for_member(
            owner_subject_id="failed_owner",
            member=failed,
        )

        self.assertEqual(relationship["status"], "pending")
        self.assertEqual(
            self.service.list_relationship_grants(
                owner_subject_id="failed_owner",
                relationship_id=relationship["id"],
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
