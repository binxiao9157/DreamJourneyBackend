from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.domain.owner_truth.source_commands import (
    CreateTextSourceCommand,
    OwnerTruthCommandContext,
)
from app.services.delegated_access import (
    AccessGrantCommand,
    AccessGrantPurpose,
    DelegatedAccessService,
    GrantOperation,
    ResourceScopeType,
)
from app.services.family_relationship_termination import (
    FamilyRelationshipTerminationCommand,
    FamilyRelationshipTerminationError,
    FamilyRelationshipTerminationService,
)
from app.services.in_memory_store import InMemoryStore
from app.services.owner_truth_family_contribution import (
    CreateFamilyContributionGrantCommand,
    OwnerTruthFamilyContributionError,
    OwnerTruthFamilyContributionService,
    ReviewFamilyContributionSubmissionCommand,
    SubmitFamilyContributionForReviewCommand,
)
from app.services.owner_truth_source import OwnerTruthSourceCommandService
from app.services.release_policy import ReleasePolicyCommandGate, ReleasePolicyService


client = TestClient(app)


class FamilyRelationshipTerminationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 19, 8, 0, tzinfo=timezone.utc)
        self.store = InMemoryStore()
        self.owner = "subject_owner"
        self.member = "subject_member"
        self.outsider = "subject_outsider"
        self.vault_id = self.owner
        self.family_member_id = "family_member_1"
        self.delegated_access = DelegatedAccessService(
            self.store,
            now_provider=lambda: self.now,
        )
        self.contributions = OwnerTruthFamilyContributionService(
            self.store,
            now_provider=lambda: self.now,
        )
        self.service = FamilyRelationshipTerminationService(
            self.store,
            now_provider=lambda: self.now,
        )
        self.store.add_family_member(
            self.owner,
            {
                "id": self.family_member_id,
                "name": "家庭成员",
                "relation": "亲属",
                "phone": "13800139002",
                "accessStatus": "active",
                "invitationStatus": "accepted",
            },
        )
        self.relationship = self.delegated_access.ensure_relationship(
            owner_subject_id=self.owner,
            family_member_id=self.family_member_id,
            member_subject_id=self.member,
            status="accepted",
        )
        OwnerTruthSourceCommandService(self.store).create_text_source(
            command=CreateTextSourceCommand(
                command_id="owner-bootstrap-source",
                source_id=str(uuid4()),
                expected_version=0,
                text="Owner private source establishes the Vault.",
                metadata={"origin": "test"},
            ),
            context=self.owner_context(),
        )

    def owner_context(self) -> OwnerTruthCommandContext:
        return OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner,
            actor_subject_id=self.owner,
        )

    def member_context(self) -> OwnerTruthCommandContext:
        return OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner,
            actor_subject_id=self.member,
        )

    def seed_access_and_contributions(self) -> tuple[dict, dict, str]:
        access_grant = self.delegated_access.grant_access(
            AccessGrantCommand(
                grantorSubjectId=self.owner,
                relationshipId=self.relationship["id"],
                granteeSubjectId=self.member,
                purpose=AccessGrantPurpose.FAMILY_PERSONA,
                resourceType=ResourceScopeType.FAMILY_MEMBER,
                resourceId=self.family_member_id,
                operations=[GrantOperation.READ],
            )
        )
        contribution_grant = self.contributions.create_grant(
            command=CreateFamilyContributionGrantCommand(
                command_id="family-contribution-grant-001",
                relationship_id=self.relationship["id"],
                contributor_subject_id=self.member,
            ),
            context=self.owner_context(),
        ).grant
        pending_id = str(uuid4())
        self.contributions.submit_for_review(
            command=SubmitFamilyContributionForReviewCommand(
                command_id="family-pending-001",
                submission_id=pending_id,
                grant_id=contribution_grant["id"],
                expected_grant_version=contribution_grant["rowVersion"],
                material_kind="text",
                text="这条贡献尚未被 Owner 接受。",
            ),
            context=self.member_context(),
        )
        accepted_id = str(uuid4())
        self.contributions.submit_for_review(
            command=SubmitFamilyContributionForReviewCommand(
                command_id="family-accepted-001",
                submission_id=accepted_id,
                grant_id=contribution_grant["id"],
                expected_grant_version=contribution_grant["rowVersion"],
                material_kind="text",
                text="这条贡献已经由 Owner 接受。",
            ),
            context=self.member_context(),
        )
        accepted = self.contributions.review_submission(
            command=ReviewFamilyContributionSubmissionCommand(
                command_id="family-accepted-review-001",
                submission_id=accepted_id,
                expected_version=1,
                decision="accepted",
            ),
            context=self.owner_context(),
        )
        return access_grant, contribution_grant, str(accepted.submission["sourceId"])

    def command(self, *, actor: str, command_id: str = "terminate-family-001"):
        return FamilyRelationshipTerminationCommand(
            command_id=command_id,
            relationship_id=self.relationship["id"],
            actor_subject_id=actor,
            expected_epoch=self.relationship["relationshipEpoch"],
            second_confirmation=True,
            publication_grant_action="preserve",
        )

    def test_owner_termination_revokes_authority_but_retains_accepted_source(self) -> None:
        access_grant, contribution_grant, accepted_source_id = (
            self.seed_access_and_contributions()
        )

        result = self.service.terminate(command=self.command(actor=self.owner))

        self.assertEqual(result.outcome, "terminated")
        self.assertEqual(result.receipt["actorRole"], "owner")
        self.assertEqual(result.receipt["revokedAccessGrantCount"], 1)
        self.assertEqual(result.receipt["revokedContributionGrantCount"], 1)
        self.assertEqual(result.receipt["withdrawnPendingContributionCount"], 1)
        self.assertEqual(result.receipt["retainedAcceptedSourceCount"], 1)
        self.assertEqual(
            result.receipt["publicationGrantDisposition"],
            "preservedRequiresOwnerAction",
        )
        self.assertFalse(result.receipt["accountsDeleted"])
        relationship = self.store.get_family_relationship(
            self.owner,
            self.relationship["id"],
        )
        self.assertEqual(relationship["status"], "revoked")
        member = self.store.list_family_members(self.owner)[0]
        self.assertEqual(member["accessStatus"], "revoked")
        self.assertEqual(member["invitationStatus"], "revoked")
        grants = self.store.list_access_grants(owner_subject_id=self.owner)
        self.assertEqual(next(item for item in grants if item["id"] == access_grant["id"])["status"], "revoked")
        contribution = self.store.get_owner_truth_family_contribution_grant(
            self.vault_id,
            contribution_grant["id"],
        )
        self.assertEqual(contribution["status"], "revoked")
        submissions = self.store.list_owner_truth_family_contribution_submissions(
            owner_subject_id=self.owner,
        )
        self.assertEqual(
            {item["status"] for item in submissions},
            {"accepted", "withdrawn"},
        )
        self.assertEqual(
            self.store._owner_truth_sources[(self.vault_id, accepted_source_id)]["state"],
            "active",
        )
        disposal = self.store.list_family_contribution_disposal_queue(
            relationship_id=self.relationship["id"],
        )
        self.assertEqual(len(disposal), 1)
        self.assertEqual(disposal[0]["state"], "pending")
        with self.assertRaisesRegex(
            OwnerTruthFamilyContributionError,
            "familyContributionRelationshipInactive|familyContributionGrantInactive",
        ):
            self.contributions.submit_for_review(
                command=SubmitFamilyContributionForReviewCommand(
                    command_id="family-after-termination",
                    submission_id=str(uuid4()),
                    grant_id=contribution_grant["id"],
                    expected_grant_version=contribution_grant["rowVersion"] + 1,
                    material_kind="text",
                    text="解除后不得继续贡献。",
                ),
                context=self.member_context(),
            )

    def test_member_can_terminate_and_same_command_is_idempotent(self) -> None:
        self.seed_access_and_contributions()
        command = self.command(actor=self.member, command_id="member-terminate-001")

        first = self.service.terminate(command=command)
        replay = self.service.terminate(command=command)

        self.assertEqual(first.outcome, "terminated")
        self.assertEqual(first.receipt["actorRole"], "member")
        self.assertEqual(replay.outcome, "deduplicated")
        self.assertEqual(first.receipt["receiptId"], replay.receipt["receiptId"])

    def test_owner_and_member_concurrent_commands_converge_once(self) -> None:
        self.seed_access_and_contributions()
        commands = [
            self.command(actor=self.owner, command_id="owner-concurrent-terminate"),
            self.command(actor=self.member, command_id="member-concurrent-terminate"),
        ]

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda command: self.service.terminate(command=command),
                    commands,
                )
            )

        self.assertEqual(
            sorted(result.outcome for result in results),
            ["alreadyTerminated", "terminated"],
        )
        self.assertEqual(
            sum(result.receipt["revokedAccessGrantCount"] for result in results),
            1,
        )
        self.assertEqual(
            sum(result.receipt["withdrawnPendingContributionCount"] for result in results),
            1,
        )

    def test_requires_participant_second_confirmation_and_preserve_action(self) -> None:
        with self.assertRaisesRegex(
            FamilyRelationshipTerminationError,
            "relationshipParticipantRequired",
        ):
            self.service.terminate(command=self.command(actor=self.outsider))
        with self.assertRaisesRegex(
            ValueError,
            "second_confirmation",
        ):
            FamilyRelationshipTerminationCommand(
                command_id="terminate-without-confirmation",
                relationship_id=self.relationship["id"],
                actor_subject_id=self.owner,
                expected_epoch=self.relationship["relationshipEpoch"],
                second_confirmation=False,
                publication_grant_action="preserve",
            )


class FamilyRelationshipTerminationAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = main_module.store
        self.previous_backend_token = main_module.BACKEND_API_TOKEN
        self.previous_route_mode = main_module.AUTH_ROUTE_MODE
        self.previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
        self.previous_legacy_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        self.previous_release_policy_service = main_module.RELEASE_POLICY_SERVICE
        self.previous_release_policy_gate = main_module.RELEASE_POLICY_COMMAND_GATE
        self.previous_delegated_api = main_module.DELEGATED_ACCESS_CONTRACT_API_ENABLED
        main_module.store = InMemoryStore()
        main_module.BACKEND_API_TOKEN = "family-termination-machine-token"
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.DELEGATED_ACCESS_CONTRACT_API_ENABLED = True
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
        main_module.DELEGATED_ACCESS_CONTRACT_API_ENABLED = self.previous_delegated_api

    @staticmethod
    def login(phone: str, nickname: str) -> dict:
        response = client.post(
            "/auth/login",
            json={"phone": phone, "nickname": nickname, "password": "password123"},
        )
        if response.status_code != 200:
            raise AssertionError(response.text)
        return response.json()

    @staticmethod
    def headers(login_body: dict) -> dict[str, str]:
        return {"Authorization": f"Bearer {login_body['auth']['accessToken']}"}

    def accepted_relationship(self) -> tuple[dict, dict, dict]:
        owner = self.login("13800139011", "owner")
        member = self.login("13800139012", "member")
        invited = client.post(
            "/family/invite",
            headers=self.headers(owner),
            json={
                "userId": owner["user"]["id"],
                "name": "家庭成员",
                "relation": "亲属",
                "phone": "13800139012",
            },
        )
        self.assertEqual(invited.status_code, 200, invited.text)
        accepted = client.post(
            f"/family/invitations/{invited.json()['member']['invitationCode']}/accept",
            headers=self.headers(member),
            json={"phone": "13800139012"},
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        return owner, member, accepted.json()["member"]

    def test_member_termination_is_authenticated_idempotent_and_keeps_accounts(self) -> None:
        owner, member, relationship = self.accepted_relationship()
        owner_id = owner["user"]["id"]
        member_id = member["user"]["id"]
        memberships = client.get(
            "/family/relationships",
            headers=self.headers(member),
        )
        self.assertEqual(memberships.status_code, 200, memberships.text)
        self.assertEqual(len(memberships.json()["relationships"]), 1)
        self.assertEqual(
            memberships.json()["relationships"][0]["relationshipId"],
            relationship["relationshipId"],
        )
        self.assertEqual(
            memberships.json()["relationships"][0]["memberSubjectId"],
            member_id,
        )
        access = client.post(
            "/family/access-grants",
            headers=self.headers(owner),
            json={
                "userId": owner_id,
                "relationshipId": relationship["relationshipId"],
                "granteeSubjectId": member_id,
                "purpose": "family.persona",
                "resourceType": "familyMember",
                "resourceId": relationship["id"],
                "operations": ["read"],
            },
        )
        self.assertEqual(access.status_code, 200, access.text)
        payload = {
            "commandId": "member-api-terminate-001",
            "expectedEpoch": relationship["relationshipEpoch"],
            "secondConfirmation": True,
            "publicationGrantAction": "preserve",
        }
        path = f"/family/relationships/{relationship['relationshipId']}/terminate"

        terminated = client.post(path, headers=self.headers(member), json=payload)
        replay = client.post(path, headers=self.headers(member), json=payload)

        self.assertEqual(terminated.status_code, 200, terminated.text)
        self.assertEqual(terminated.json()["status"], "terminated")
        self.assertEqual(terminated.json()["receipt"]["actorRole"], "member")
        self.assertEqual(
            terminated.json()["receipt"]["publicationGrantDisposition"],
            "preservedRequiresOwnerAction",
        )
        self.assertFalse(terminated.json()["receipt"]["accountsDeleted"])
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["status"], "deduplicated")
        refreshed = client.get(
            f"/family/members/{owner_id}",
            headers=self.headers(owner),
        )
        self.assertEqual(refreshed.status_code, 200, refreshed.text)
        self.assertEqual(refreshed.json()["members"][0]["relationshipStatus"], "revoked")
        self.assertEqual(refreshed.json()["members"][0]["accessStatus"], "revoked")
        self.assertEqual(
            self.login("13800139011", "owner")["user"]["id"],
            owner_id,
        )
        self.assertEqual(
            self.login("13800139012", "member")["user"]["id"],
            member_id,
        )

    def test_outsider_cannot_discover_or_terminate_relationship(self) -> None:
        _, _, relationship = self.accepted_relationship()
        outsider = self.login("13800139013", "outsider")
        response = client.post(
            f"/family/relationships/{relationship['relationshipId']}/terminate",
            headers=self.headers(outsider),
            json={
                "commandId": "outsider-api-terminate-001",
                "expectedEpoch": relationship["relationshipEpoch"],
                "secondConfirmation": True,
                "publicationGrantAction": "preserve",
            },
        )
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"]["code"], "relationshipParticipantRequired")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
