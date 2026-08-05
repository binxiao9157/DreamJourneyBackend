from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main_module
from app.domain.publication.share_grant_session import (
    PublicationAdultVerificationState,
    PublicationVisitorRelationshipOrigin,
)
from app.main import app
from app.services.in_memory_store import InMemoryStore
from app.services.publication_authority import PublicationAuthorityMemoryVersion
from app.services.publication_visitor_access import (
    PublicationVisitorEligibility,
    StaticPublicationVisitorEligibilityResolver,
)


client = TestClient(app)


class PublicationVisitorAccessAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = main_module.store
        self.previous_backend_token = main_module.BACKEND_API_TOKEN
        self.previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        self.previous_route_mode = main_module.AUTH_ROUTE_MODE
        self.previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
        self.previous_authority_qa_enabled = main_module.PUBLICATION_AUTHORITY_QA_ENABLED
        self.previous_visitor_qa_enabled = main_module.PUBLICATION_VISITOR_ACCESS_QA_ENABLED
        self.previous_eligibility_resolver = main_module.PUBLICATION_VISITOR_ELIGIBILITY_RESOLVER
        main_module.store = InMemoryStore()
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        main_module.PUBLICATION_AUTHORITY_QA_ENABLED = True
        main_module.PUBLICATION_VISITOR_ACCESS_QA_ENABLED = True

    def tearDown(self) -> None:
        main_module.store = self.previous_store
        main_module.BACKEND_API_TOKEN = self.previous_backend_token
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = self.previous_legacy_phone_login
        main_module.AUTH_ROUTE_MODE = self.previous_route_mode
        main_module.AUTH_OWNERSHIP_MODE = self.previous_ownership_mode
        main_module.PUBLICATION_AUTHORITY_QA_ENABLED = self.previous_authority_qa_enabled
        main_module.PUBLICATION_VISITOR_ACCESS_QA_ENABLED = self.previous_visitor_qa_enabled
        main_module.PUBLICATION_VISITOR_ELIGIBILITY_RESOLVER = self.previous_eligibility_resolver

    @staticmethod
    def _login(phone: str) -> tuple[str, dict[str, str]]:
        response = client.post(
            "/auth/login",
            json={"phone": phone, "nickname": "访客授权测试", "password": "password123"},
        )
        if response.status_code != 200:
            raise AssertionError(response.text)
        payload = response.json()
        return str(payload["user"]["id"]), {
            "Authorization": f"Bearer {payload['auth']['accessToken']}"
        }

    @staticmethod
    def _authority_headers(headers: dict[str, str]) -> dict[str, str]:
        return {**headers, "X-DreamJourney-QA-Publication": "1"}

    @staticmethod
    def _visitor_headers(headers: dict[str, str]) -> dict[str, str]:
        return {**headers, "X-DreamJourney-QA-Visitor-Access": "1"}

    def _seed_memory(self, *, vault_id: str, owner_id: str) -> str:
        memory_version_id = str(uuid4())
        main_module.store.publication_authority_repository().seed_memory_version(
            PublicationAuthorityMemoryVersion(
                memory_version_id=memory_version_id,
                memory_id=str(uuid4()),
                vault_id=vault_id,
                owner_subject_id=owner_id,
                authority_epoch=0,
                content_hash="b" * 64,
                is_current=True,
                memory_state="active",
                source_state="active",
                decision="accepted",
                decision_receipt_id=str(uuid4()),
            )
        )
        return memory_version_id

    def _active_projection(self, *, vault_id: str, owner_id: str, headers: dict[str, str]) -> dict[str, object]:
        memory_version_id = self._seed_memory(vault_id=vault_id, owner_id=owner_id)
        draft = client.post(
            f"/v2/internal/owner-authority/vaults/{vault_id}/drafts",
            headers=self._authority_headers(headers),
            json={
                "commandId": str(uuid4()),
                "memoryVersionId": memory_version_id,
                "publicTitle": "散步时的回忆",
                "publicBody": "这是经过本人确认、可以分享的回忆。",
            },
        )
        self.assertEqual(draft.status_code, 201, draft.text)
        draft_payload = draft.json()
        confirmed = client.post(
            f"/v2/internal/owner-authority/vaults/{vault_id}/drafts/"
            f"{draft_payload['draftId']}/confirm/{draft_payload['publicationId']}",
            headers=self._authority_headers(headers),
            json={
                "commandId": str(uuid4()),
                "expectedDraftRevision": draft_payload["expectedDraftRevision"],
                "expectedDraftSnapshotHash": draft_payload["expectedDraftSnapshotHash"],
                "secondConfirmation": True,
            },
        )
        self.assertEqual(confirmed.status_code, 201, confirmed.text)
        return confirmed.json()

    @staticmethod
    def _verified_direct() -> PublicationVisitorEligibility:
        return PublicationVisitorEligibility(
            adult_verification=PublicationAdultVerificationState.VERIFIED,
            relationship_origin=PublicationVisitorRelationshipOrigin.DIRECT,
        )

    def test_default_off_hides_visitor_admission_before_backend_token_authentication(self) -> None:
        main_module.BACKEND_API_TOKEN = "configured-server-token"
        main_module.PUBLICATION_VISITOR_ACCESS_QA_ENABLED = False

        response = client.post(
            f"/v2/internal/publication-access/grants/{uuid4()}/sessions",
            json={},
        )

        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"]["code"], "publicationVisitorAccessUnavailable")

    def test_owner_can_issue_once_admit_verified_visitor_and_revoke(self) -> None:
        owner_id, owner_headers = self._login("13800139501")
        visitor_id, visitor_headers = self._login("13800139502")
        vault_id = "publication-visitor-owner"
        projection = self._active_projection(
            vault_id=vault_id,
            owner_id=owner_id,
            headers=owner_headers,
        )
        main_module.PUBLICATION_VISITOR_ELIGIBILITY_RESOLVER = StaticPublicationVisitorEligibilityResolver(
            {visitor_id: self._verified_direct()}
        )
        issue_payload = {
            "commandId": str(uuid4()),
            "publicationId": projection["publicationId"],
            "publicationVersionId": projection["publicationVersionId"],
            "granteeUserId": visitor_id,
            "expiresAt": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "useLimit": 1,
        }
        issued = client.post(
            f"/v2/internal/publication-access/vaults/{vault_id}/grants",
            headers=self._visitor_headers(owner_headers),
            json=issue_payload,
        )
        self.assertEqual(issued.status_code, 201, issued.text)
        grant = issued.json()
        self.assertTrue(grant["credentialIssued"])
        self.assertIn("grantCredential", grant)
        self.assertEqual(issued.headers.get("cache-control"), "no-store")

        replay = client.post(
            f"/v2/internal/publication-access/vaults/{vault_id}/grants",
            headers=self._visitor_headers(owner_headers),
            json=issue_payload,
        )
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertFalse(replay.json()["credentialIssued"])
        self.assertNotIn("grantCredential", replay.json())

        admitted = client.post(
            f"/v2/internal/publication-access/grants/{grant['grantId']}/sessions",
            headers=self._visitor_headers(visitor_headers),
            json={
                "commandId": str(uuid4()),
                "grantCredential": grant["grantCredential"],
                "sessionCredential": "visitor-session-" + "s" * 32,
            },
        )
        self.assertEqual(admitted.status_code, 201, admitted.text)
        self.assertEqual(admitted.json()["useRemaining"], 0)

        revoked = client.post(
            f"/v2/internal/publication-access/vaults/{vault_id}/grants/{grant['grantId']}/revoke",
            headers=self._visitor_headers(owner_headers),
            json={"commandId": str(uuid4())},
        )
        self.assertEqual(revoked.status_code, 200, revoked.text)
        self.assertEqual(revoked.json()["revokedSessionCount"], 1)

        rejected_after_revoke = client.post(
            f"/v2/internal/publication-access/grants/{grant['grantId']}/sessions",
            headers=self._visitor_headers(visitor_headers),
            json={
                "commandId": str(uuid4()),
                "grantCredential": grant["grantCredential"],
                "sessionCredential": "visitor-session-after-revoke-" + "s" * 32,
            },
        )
        self.assertEqual(rejected_after_revoke.status_code, 409, rejected_after_revoke.text)
        self.assertEqual(
            rejected_after_revoke.json()["detail"]["code"],
            "publicationVisitorAccessUnavailable",
        )

    def test_unknown_minor_or_family_derived_visitor_is_denied_server_side(self) -> None:
        owner_id, owner_headers = self._login("13800139503")
        visitor_id, visitor_headers = self._login("13800139504")
        vault_id = "publication-visitor-eligibility"
        projection = self._active_projection(
            vault_id=vault_id,
            owner_id=owner_id,
            headers=owner_headers,
        )
        main_module.PUBLICATION_VISITOR_ELIGIBILITY_RESOLVER = StaticPublicationVisitorEligibilityResolver(
            {visitor_id: self._verified_direct()}
        )
        issued = client.post(
            f"/v2/internal/publication-access/vaults/{vault_id}/grants",
            headers=self._visitor_headers(owner_headers),
            json={
                "commandId": str(uuid4()),
                "publicationId": projection["publicationId"],
                "publicationVersionId": projection["publicationVersionId"],
                "granteeUserId": visitor_id,
                "expiresAt": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                "useLimit": 1,
            },
        )
        self.assertEqual(issued.status_code, 201, issued.text)
        grant = issued.json()

        for adult, relationship in (
            (PublicationAdultVerificationState.UNKNOWN, PublicationVisitorRelationshipOrigin.DIRECT),
            (PublicationAdultVerificationState.MINOR, PublicationVisitorRelationshipOrigin.DIRECT),
            (PublicationAdultVerificationState.VERIFIED, PublicationVisitorRelationshipOrigin.FAMILY_DERIVED),
        ):
            with self.subTest(adult=adult, relationship=relationship):
                main_module.PUBLICATION_VISITOR_ELIGIBILITY_RESOLVER = StaticPublicationVisitorEligibilityResolver(
                    {
                        visitor_id: PublicationVisitorEligibility(
                            adult_verification=adult,
                            relationship_origin=relationship,
                        )
                    }
                )
                denied = client.post(
                    f"/v2/internal/publication-access/grants/{grant['grantId']}/sessions",
                    headers=self._visitor_headers(visitor_headers),
                    json={
                        "commandId": str(uuid4()),
                        "grantCredential": grant["grantCredential"],
                        "sessionCredential": "visitor-session-denied-" + "s" * 32,
                    },
                )
                self.assertEqual(denied.status_code, 409, denied.text)
                self.assertEqual(
                    denied.json()["detail"]["code"],
                    "publicationVisitorAdultVerificationRequired",
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
