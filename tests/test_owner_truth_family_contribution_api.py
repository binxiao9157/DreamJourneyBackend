from __future__ import annotations

import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main_module
from app.domain.owner_truth.source_commands import (
    CreateTextSourceCommand,
    OwnerTruthCommandContext,
)
from app.main import app
from app.services.delegated_access import DelegatedAccessService
from app.services.in_memory_store import InMemoryStore
from app.services.owner_truth_source import OwnerTruthSourceCommandService


client = TestClient(app)


class OwnerTruthFamilyContributionAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = main_module.store
        self.previous_backend_token = main_module.BACKEND_API_TOKEN
        self.previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        self.previous_route_mode = main_module.AUTH_ROUTE_MODE
        self.previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
        self.previous_qa_enabled = main_module.OWNER_TRUTH_FAMILY_CONTRIBUTION_QA_ENABLED
        main_module.store = InMemoryStore()
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        main_module.OWNER_TRUTH_FAMILY_CONTRIBUTION_QA_ENABLED = True
        self.vault_id = "vault-family-contribution-api"

    def tearDown(self) -> None:
        main_module.store = self.previous_store
        main_module.BACKEND_API_TOKEN = self.previous_backend_token
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = self.previous_legacy_phone_login
        main_module.AUTH_ROUTE_MODE = self.previous_route_mode
        main_module.AUTH_OWNERSHIP_MODE = self.previous_ownership_mode
        main_module.OWNER_TRUTH_FAMILY_CONTRIBUTION_QA_ENABLED = self.previous_qa_enabled

    @staticmethod
    def login(phone: str, nickname: str) -> tuple[str, dict[str, str]]:
        response = client.post(
            "/auth/login",
            json={"phone": phone, "nickname": nickname, "password": "password123"},
        )
        if response.status_code != 200:
            raise AssertionError(response.text)
        payload = response.json()
        return payload["user"]["id"], {
            "Authorization": f"Bearer {payload['auth']['accessToken']}",
            "X-DreamJourney-QA-Owner-Truth": "1",
        }

    def seed_owner_vault(self, owner_id: str) -> None:
        OwnerTruthSourceCommandService(main_module.store).create_text_source(
            command=CreateTextSourceCommand(
                command_id="api-owner-vault-bootstrap",
                source_id=str(uuid4()),
                expected_version=0,
                text="Owner source establishes the private Vault.",
                metadata={"origin": "test"},
            ),
            context=OwnerTruthCommandContext(
                vault_id=self.vault_id,
                owner_subject_id=owner_id,
                actor_subject_id=owner_id,
            ),
        )

    def accepted_relationship(self):
        owner_id, owner_headers = self.login("13800139701", "Owner")
        member_id, member_headers = self.login("13800139702", "Member")
        self.seed_owner_vault(owner_id)
        relationship = DelegatedAccessService(main_module.store).ensure_relationship(
            owner_subject_id=owner_id,
            family_member_id="family-api-member-1",
            member_subject_id=member_id,
            status="accepted",
        )
        return owner_id, owner_headers, member_id, member_headers, relationship

    def test_default_hidden_then_owner_grants_member_submits_and_owner_revokes(self) -> None:
        owner_id, owner_headers, member_id, member_headers, relationship = self.accepted_relationship()
        create_path = f"/v2/vaults/{self.vault_id}/family-contribution-grants"

        main_module.OWNER_TRUTH_FAMILY_CONTRIBUTION_QA_ENABLED = False
        hidden = client.post(
            create_path,
            headers=owner_headers,
            json={
                "commandId": "family-api-hidden",
                "relationshipId": relationship["id"],
                "contributorSubjectId": member_id,
            },
        )
        self.assertEqual(hidden.status_code, 404)
        self.assertEqual(hidden.json()["detail"]["code"], "ownerTruthFamilyContributionUnavailable")

        main_module.OWNER_TRUTH_FAMILY_CONTRIBUTION_QA_ENABLED = True
        created = client.post(
            create_path,
            headers=owner_headers,
            json={
                "commandId": "family-api-grant-001",
                "relationshipId": relationship["id"],
                "contributorSubjectId": member_id,
            },
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.headers["cache-control"], "no-store")
        grant = created.json()["grant"]
        self.assertEqual(grant["scope"], "submitTextSource")
        self.assertEqual(grant["ownerSubjectId"], owner_id)
        self.assertEqual(grant["contributorSubjectId"], member_id)

        submitted = client.post(
            f"{create_path}/{grant['grantId']}/sources",
            headers=member_headers,
            json={
                "expectedGrantVersion": grant["rowVersion"],
                "sourceCommandId": "family-api-source-001",
                "sourceId": str(uuid4()),
                "text": "这是一条由家人补充的静态记忆材料。",
            },
        )
        self.assertEqual(submitted.status_code, 201)
        self.assertNotIn("静态记忆材料", str(submitted.json()))
        self.assertEqual(
            submitted.json()["candidateExtraction"],
            {"status": "notRequested"},
        )

        revoked = client.post(
            f"{create_path}/{grant['grantId']}/revoke",
            headers=owner_headers,
            json={"commandId": "family-api-revoke-001", "expectedVersion": grant["rowVersion"]},
        )
        self.assertEqual(revoked.status_code, 200)
        self.assertEqual(revoked.json()["grant"]["status"], "revoked")
        blocked = client.post(
            f"{create_path}/{grant['grantId']}/sources",
            headers=member_headers,
            json={
                "expectedGrantVersion": grant["rowVersion"],
                "sourceCommandId": "family-api-source-after-revoke",
                "sourceId": str(uuid4()),
                "text": "撤销后不得继续提交。",
            },
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()["detail"]["code"], "familyContributionGrantInactive")

    def test_cross_account_owner_cannot_grant_or_revoke_another_vault(self) -> None:
        owner_id, owner_headers, member_id, _member_headers, relationship = self.accepted_relationship()
        other_id, other_headers = self.login("13800139703", "Other")
        create_path = f"/v2/vaults/{self.vault_id}/family-contribution-grants"
        denied_create = client.post(
            create_path,
            headers=other_headers,
            json={
                "commandId": "family-api-cross-owner",
                "relationshipId": relationship["id"],
                "contributorSubjectId": member_id,
            },
        )
        self.assertEqual(owner_id.startswith("user_"), True)
        self.assertEqual(other_id.startswith("user_"), True)
        self.assertEqual(denied_create.status_code, 403)
        self.assertEqual(
            denied_create.json()["detail"]["code"],
            "familyContributionVaultOwnerMismatch",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
