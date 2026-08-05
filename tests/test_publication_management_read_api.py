from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.services.in_memory_store import InMemoryStore
from app.services.publication_authority import PublicationAuthorityMemoryVersion


client = TestClient(app)


class PublicationManagementReadAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = main_module.store
        self.previous_backend_token = main_module.BACKEND_API_TOKEN
        self.previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        self.previous_route_mode = main_module.AUTH_ROUTE_MODE
        self.previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
        self.previous_authority_qa_enabled = main_module.PUBLICATION_AUTHORITY_QA_ENABLED
        self.previous_visitor_qa_enabled = main_module.PUBLICATION_VISITOR_ACCESS_QA_ENABLED
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

    @staticmethod
    def _login(phone: str) -> tuple[str, dict[str, str], dict[str, str]]:
        response = client.post(
            "/auth/login",
            json={"phone": phone, "nickname": "发布管理读取测试", "password": "password123"},
        )
        if response.status_code != 200:
            raise AssertionError(response.text)
        payload = response.json()
        authorization = {"Authorization": f"Bearer {payload['auth']['accessToken']}"}
        return (
            str(payload["user"]["id"]),
            {**authorization, "X-DreamJourney-QA-Publication": "1"},
            {**authorization, "X-DreamJourney-QA-Visitor-Access": "1"},
        )

    @staticmethod
    def _draft_payload(memory_version_id: str) -> dict[str, object]:
        return {
            "commandId": str(uuid4()),
            "memoryVersionId": memory_version_id,
            "publicTitle": "院子里的雨声",
            "publicBody": "我愿意分享这段已经确认的回忆。",
        }

    def _seed_memory(self, *, vault_id: str, owner_id: str) -> str:
        memory_version_id = str(uuid4())
        main_module.store.publication_authority_repository().seed_memory_version(
            PublicationAuthorityMemoryVersion(
                memory_version_id=memory_version_id,
                memory_id=str(uuid4()),
                vault_id=vault_id,
                owner_subject_id=owner_id,
                authority_epoch=0,
                content_hash="a" * 64,
                is_current=True,
                memory_state="active",
                source_state="active",
                decision="accepted",
                decision_receipt_id=str(uuid4()),
            )
        )
        return memory_version_id

    def _create_confirmed_publication(
        self,
        *,
        vault_id: str,
        owner_id: str,
        authority_headers: dict[str, str],
    ) -> dict[str, object]:
        memory_version_id = self._seed_memory(vault_id=vault_id, owner_id=owner_id)
        created = client.post(
            f"/v2/internal/owner-authority/vaults/{vault_id}/drafts",
            headers=authority_headers,
            json=self._draft_payload(memory_version_id),
        )
        self.assertEqual(created.status_code, 201, created.text)
        draft = created.json()
        confirmed = client.post(
            f"/v2/internal/owner-authority/vaults/{vault_id}/drafts/{draft['draftId']}/confirm/{draft['publicationId']}",
            headers=authority_headers,
            json={
                "commandId": str(uuid4()),
                "expectedDraftRevision": draft["expectedDraftRevision"],
                "expectedDraftSnapshotHash": draft["expectedDraftSnapshotHash"],
                "secondConfirmation": True,
            },
        )
        self.assertEqual(confirmed.status_code, 201, confirmed.text)
        return {"draft": draft, "confirmed": confirmed.json()}

    def test_default_off_hides_owner_management_reads_before_authentication(self) -> None:
        main_module.PUBLICATION_AUTHORITY_QA_ENABLED = False
        main_module.PUBLICATION_VISITOR_ACCESS_QA_ENABLED = False

        authority_response = client.get(
            "/v2/internal/owner-authority/vaults/publication-management-hidden/publications"
        )
        grant_response = client.get(
            "/v2/internal/publication-access/vaults/publication-management-hidden/grants"
        )

        self.assertEqual(authority_response.status_code, 404, authority_response.text)
        self.assertEqual(grant_response.status_code, 404, grant_response.text)

    def test_owner_reads_redacted_publication_and_grant_summaries_without_credentials(self) -> None:
        owner_id, authority_headers, visitor_headers = self._login("13800139271")
        vault_id = "publication-management-owner"
        created = self._create_confirmed_publication(
            vault_id=vault_id,
            owner_id=owner_id,
            authority_headers=authority_headers,
        )
        confirmed = created["confirmed"]
        self.assertIsInstance(confirmed, dict)

        grant = client.post(
            f"/v2/internal/publication-access/vaults/{vault_id}/grants",
            headers=visitor_headers,
            json={
                "commandId": str(uuid4()),
                "publicationId": confirmed["publicationId"],
                "publicationVersionId": confirmed["publicationVersionId"],
                "granteeUserId": "publication-management-recipient",
                "expiresAt": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                "useLimit": 3,
            },
        )
        self.assertEqual(grant.status_code, 201, grant.text)

        publications = client.get(
            f"/v2/internal/owner-authority/vaults/{vault_id}/publications",
            headers=authority_headers,
        )
        grants = client.get(
            f"/v2/internal/publication-access/vaults/{vault_id}/grants",
            headers=visitor_headers,
        )

        self.assertEqual(publications.status_code, 200, publications.text)
        self.assertEqual(grants.status_code, 200, grants.text)
        publication_payload = publications.json()
        grant_payload = grants.json()
        self.assertEqual(publication_payload["schemaVersion"], "publication-owner-management-v1")
        self.assertEqual(grant_payload["schemaVersion"], "publication-owner-grant-list-v1")
        self.assertEqual(len(publication_payload["publications"]), 1)
        self.assertEqual(len(grant_payload["grants"]), 1)

        publication = publication_payload["publications"][0]
        grant_summary = grant_payload["grants"][0]
        self.assertEqual(publication["publicationId"], confirmed["publicationId"])
        self.assertEqual(publication["publicationVersionId"], confirmed["publicationVersionId"])
        self.assertEqual(publication["publicationState"], "confirmed")
        self.assertEqual(publication["projectionState"], "active")
        self.assertEqual(publication["preview"]["title"], "院子里的雨声")
        self.assertTrue(publication["aiDisclosureRequired"])
        self.assertFalse(publication["requiresSecondConfirmation"])
        self.assertEqual(grant_summary["grantId"], grant.json()["grantId"])
        self.assertEqual(grant_summary["publicationId"], confirmed["publicationId"])
        self.assertEqual(grant_summary["publicationVersionId"], confirmed["publicationVersionId"])
        self.assertEqual(grant_summary["state"], "active")
        self.assertEqual(grant_summary["useRemaining"], 3)

        serialized = f"{publication_payload}|{grant_payload}"
        for forbidden in (
            "grantCredential",
            "granteeUserId",
            "granteeSubjectHash",
            "memoryVersionId",
            "sourceObject",
            "KBLite",
            "voiceProfile",
            "digitalHuman",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_cross_owner_management_reads_are_denied(self) -> None:
        owner_id, authority_headers, visitor_headers = self._login("13800139272")
        _, other_authority_headers, other_visitor_headers = self._login("13800139273")
        vault_id = "publication-management-owner-fence"
        self._create_confirmed_publication(
            vault_id=vault_id,
            owner_id=owner_id,
            authority_headers=authority_headers,
        )

        publication_response = client.get(
            f"/v2/internal/owner-authority/vaults/{vault_id}/publications",
            headers=other_authority_headers,
        )
        grant_response = client.get(
            f"/v2/internal/publication-access/vaults/{vault_id}/grants",
            headers=other_visitor_headers,
        )

        self.assertEqual(publication_response.status_code, 403, publication_response.text)
        self.assertEqual(grant_response.status_code, 403, grant_response.text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
