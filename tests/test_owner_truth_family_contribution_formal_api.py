from __future__ import annotations

from hashlib import sha256
import json
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


class OwnerTruthFamilyContributionFormalAPITests(unittest.TestCase):
    """The closed-pilot lane must never inherit the QA-only bypass."""

    def setUp(self) -> None:
        self.previous_store = main_module.store
        self.previous_backend_token = main_module.BACKEND_API_TOKEN
        self.previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        self.previous_route_mode = main_module.AUTH_ROUTE_MODE
        self.previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
        self.previous_qa_enabled = main_module.OWNER_TRUTH_FAMILY_CONTRIBUTION_QA_ENABLED
        self.previous_closed_pilot_owner_ids = main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS
        self.previous_closed_pilot_features = set(
            main_module.RELEASE_POLICY_SERVICE.closed_pilot_enabled_features
        )
        main_module.store = InMemoryStore()
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        main_module.OWNER_TRUTH_FAMILY_CONTRIBUTION_QA_ENABLED = False
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = frozenset()
        main_module.RELEASE_POLICY_SERVICE.closed_pilot_enabled_features.discard(
            "ownerTruthFamilyContribution"
        )
        self.vault_id = "vault-family-contribution-formal-api"

    def tearDown(self) -> None:
        main_module.store = self.previous_store
        main_module.BACKEND_API_TOKEN = self.previous_backend_token
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = self.previous_legacy_phone_login
        main_module.AUTH_ROUTE_MODE = self.previous_route_mode
        main_module.AUTH_OWNERSHIP_MODE = self.previous_ownership_mode
        main_module.OWNER_TRUTH_FAMILY_CONTRIBUTION_QA_ENABLED = self.previous_qa_enabled
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = self.previous_closed_pilot_owner_ids
        main_module.RELEASE_POLICY_SERVICE.closed_pilot_enabled_features = (
            self.previous_closed_pilot_features
        )

    @staticmethod
    def _login(phone: str, nickname: str) -> tuple[str, dict[str, str], str]:
        response = client.post(
            "/auth/login",
            json={"phone": phone, "nickname": nickname, "password": "password123"},
        )
        if response.status_code != 200:
            raise AssertionError(response.text)
        payload = response.json()
        return (
            str(payload["user"]["id"]),
            {"Authorization": f"Bearer {payload['auth']['accessToken']}"},
            str(payload["auth"]["sessionId"]),
        )

    @staticmethod
    def _product_headers(
        headers: dict[str, str],
        *,
        session_id: str,
        decision_id: str,
    ) -> dict[str, str]:
        return {
            **headers,
            "X-DreamJourney-Feature": "ownerTruthFamilyContribution",
            "X-DreamJourney-Feature-Decision-Id": decision_id,
            "X-DreamJourney-Feature-Allowed": "true",
            "X-DreamJourney-Policy-Version": "release-policy-v1",
            "X-DreamJourney-Policy-Revision": "1",
            "X-DreamJourney-Account-Generation": sha256(
                session_id.encode("utf-8")
            ).hexdigest()[:24],
        }

    def _seed_owner_vault(self, owner_id: str) -> None:
        OwnerTruthSourceCommandService(main_module.store).create_text_source(
            command=CreateTextSourceCommand(
                command_id="formal-family-owner-vault-bootstrap",
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

    def _accepted_relationship(self):
        owner_id, owner_headers, owner_session_id = self._login("13800139801", "Owner")
        member_id, member_headers, _member_session_id = self._login("13800139802", "Member")
        other_id, other_headers, _other_session_id = self._login("13800139803", "Other")
        self._seed_owner_vault(owner_id)
        relationship = DelegatedAccessService(main_module.store).ensure_relationship(
            owner_subject_id=owner_id,
            family_member_id="family-formal-member-1",
            member_subject_id=member_id,
            status="accepted",
        )
        return (
            owner_id,
            owner_headers,
            owner_session_id,
            member_id,
            member_headers,
            other_id,
            other_headers,
            relationship,
        )

    def test_closed_pilot_grant_accepts_only_static_submission_and_can_be_revoked(self) -> None:
        (
            owner_id,
            owner_headers,
            owner_session_id,
            member_id,
            member_headers,
            _other_id,
            other_headers,
            relationship,
        ) = self._accepted_relationship()
        create_path = f"/v2/vaults/{self.vault_id}/family-contribution/grants"
        payload = {
            "commandId": "formal-family-grant-001",
            "relationshipId": relationship["id"],
            "contributorSubjectId": member_id,
        }

        default_closed = client.post(create_path, headers=owner_headers, json=payload)
        self.assertEqual(default_closed.status_code, 403)
        self.assertEqual(default_closed.json()["detail"]["code"], "release_policy_denied")

        # A QA header does not promote a normal product request.
        qa_header_only = client.post(
            create_path,
            headers={**owner_headers, "X-DreamJourney-QA-Owner-Truth": "1"},
            json=payload,
        )
        self.assertEqual(qa_header_only.status_code, 403)

        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = frozenset({owner_id})
        main_module.RELEASE_POLICY_SERVICE.closed_pilot_enabled_features.add(
            "ownerTruthFamilyContribution"
        )
        policy_headers = self._product_headers(
            owner_headers,
            session_id=owner_session_id,
            decision_id="formal-family-decision-001",
        )
        created = client.post(create_path, headers=policy_headers, json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.headers["cache-control"], "no-store")
        grant = created.json()["grant"]
        self.assertEqual(grant["admissionMode"], "closedPilot")
        self.assertEqual(grant["scope"], "submitTextSource")
        self.assertNotIn("authorizationEvidence", json.dumps(created.json()))

        submitted = client.post(
            f"{create_path}/{grant['grantId']}/sources",
            headers=member_headers,
            json={
                "expectedGrantVersion": grant["rowVersion"],
                "sourceCommandId": "formal-family-source-001",
                "sourceId": str(uuid4()),
                "text": "这是一条由受邀家人提交的静态材料。",
            },
        )
        self.assertEqual(submitted.status_code, 201, submitted.text)
        self.assertEqual(submitted.json()["candidateExtraction"], {"status": "notRequested"})
        self.assertNotIn("静态材料", json.dumps(submitted.json(), ensure_ascii=False))

        cross_account = client.post(
            f"{create_path}/{grant['grantId']}/sources",
            headers=other_headers,
            json={
                "expectedGrantVersion": grant["rowVersion"],
                "sourceCommandId": "formal-family-source-cross-account",
                "sourceId": str(uuid4()),
                "text": "其他人不应提交。",
            },
        )
        self.assertEqual(cross_account.status_code, 403)
        self.assertEqual(
            cross_account.json()["detail"]["code"],
            "familyContributionGrantContributorMismatch",
        )

        revoked = client.post(
            f"{create_path}/{grant['grantId']}/revoke",
            headers=policy_headers,
            json={"commandId": "formal-family-revoke-001", "expectedVersion": grant["rowVersion"]},
        )
        self.assertEqual(revoked.status_code, 200, revoked.text)
        self.assertEqual(revoked.json()["grant"]["status"], "revoked")

        blocked = client.post(
            f"{create_path}/{grant['grantId']}/sources",
            headers=member_headers,
            json={
                "expectedGrantVersion": grant["rowVersion"],
                "sourceCommandId": "formal-family-source-after-revoke",
                "sourceId": str(uuid4()),
                "text": "撤销后不得继续提交。",
            },
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(
            blocked.json()["detail"]["code"], "familyContributionGrantInactive"
        )

    def test_formal_route_rejects_a_qa_grant_and_owner_toggle_stops_old_formal_grant(self) -> None:
        (
            owner_id,
            owner_headers,
            owner_session_id,
            member_id,
            member_headers,
            _other_id,
            _other_headers,
            relationship,
        ) = self._accepted_relationship()
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = frozenset({owner_id})
        main_module.RELEASE_POLICY_SERVICE.closed_pilot_enabled_features.add(
            "ownerTruthFamilyContribution"
        )
        formal_path = f"/v2/vaults/{self.vault_id}/family-contribution/grants"
        policy_headers = self._product_headers(
            owner_headers,
            session_id=owner_session_id,
            decision_id="formal-family-decision-002",
        )
        formal = client.post(
            formal_path,
            headers=policy_headers,
            json={
                "commandId": "formal-family-grant-002",
                "relationshipId": relationship["id"],
                "contributorSubjectId": member_id,
            },
        )
        self.assertEqual(formal.status_code, 201, formal.text)
        formal_grant = formal.json()["grant"]

        main_module.RELEASE_POLICY_SERVICE.closed_pilot_enabled_features.discard(
            "ownerTruthFamilyContribution"
        )
        disabled = client.post(
            f"{formal_path}/{formal_grant['grantId']}/sources",
            headers=member_headers,
            json={
                "expectedGrantVersion": formal_grant["rowVersion"],
                "sourceCommandId": "formal-family-source-after-toggle-off",
                "sourceId": str(uuid4()),
                "text": "关闭后不得提交。",
            },
        )
        self.assertEqual(disabled.status_code, 404)
        self.assertEqual(
            disabled.json()["detail"]["code"], "ownerTruthFamilyContributionUnavailable"
        )

        # Revoke the formal grant, then create an old QA fixture for the same
        # relationship. The product contributor route must never accept it.
        main_module.RELEASE_POLICY_SERVICE.closed_pilot_enabled_features.add(
            "ownerTruthFamilyContribution"
        )
        revoke = client.post(
            f"{formal_path}/{formal_grant['grantId']}/revoke",
            headers=policy_headers,
            json={"commandId": "formal-family-revoke-002", "expectedVersion": 1},
        )
        self.assertEqual(revoke.status_code, 200, revoke.text)
        main_module.OWNER_TRUTH_FAMILY_CONTRIBUTION_QA_ENABLED = True
        qa_path = f"/v2/vaults/{self.vault_id}/family-contribution-grants"
        qa_grant = client.post(
            qa_path,
            headers={**owner_headers, "X-DreamJourney-QA-Owner-Truth": "1"},
            json={
                "commandId": "qa-family-grant-001",
                "relationshipId": relationship["id"],
                "contributorSubjectId": member_id,
            },
        )
        self.assertEqual(qa_grant.status_code, 201, qa_grant.text)
        rejected_qa_grant = client.post(
            f"{formal_path}/{qa_grant.json()['grant']['grantId']}/sources",
            headers=member_headers,
            json={
                "expectedGrantVersion": 1,
                "sourceCommandId": "formal-family-source-qa-grant",
                "sourceId": str(uuid4()),
                "text": "QA grant must not enter product route.",
            },
        )
        self.assertEqual(rejected_qa_grant.status_code, 409)
        self.assertEqual(
            rejected_qa_grant.json()["detail"]["code"],
            "familyContributionGrantAdmissionModeMismatch",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
