from __future__ import annotations

import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.services.in_memory_store import InMemoryStore
from app.services.publication_authority import PublicationAuthorityMemoryVersion


client = TestClient(app)


class PublicationAuthorityAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = main_module.store
        self.previous_backend_token = main_module.BACKEND_API_TOKEN
        self.previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        self.previous_route_mode = main_module.AUTH_ROUTE_MODE
        self.previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
        self.previous_qa_enabled = main_module.PUBLICATION_AUTHORITY_QA_ENABLED
        main_module.store = InMemoryStore()
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        main_module.PUBLICATION_AUTHORITY_QA_ENABLED = True

    def tearDown(self) -> None:
        main_module.store = self.previous_store
        main_module.BACKEND_API_TOKEN = self.previous_backend_token
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = self.previous_legacy_phone_login
        main_module.AUTH_ROUTE_MODE = self.previous_route_mode
        main_module.AUTH_OWNERSHIP_MODE = self.previous_ownership_mode
        main_module.PUBLICATION_AUTHORITY_QA_ENABLED = self.previous_qa_enabled

    @staticmethod
    def _login(phone: str) -> tuple[str, dict[str, str]]:
        response = client.post(
            "/auth/login",
            json={"phone": phone, "nickname": "发布授权测试", "password": "password123"},
        )
        if response.status_code != 200:
            raise AssertionError(response.text)
        payload = response.json()
        return str(payload["user"]["id"]), {
            "Authorization": f"Bearer {payload['auth']['accessToken']}",
            "X-DreamJourney-QA-Publication": "1",
        }

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

    def test_default_off_hides_writer_from_an_authenticated_owner(self) -> None:
        owner_id, headers = self._login("13800139171")
        memory_version_id = self._seed_memory(vault_id="publication-hidden", owner_id=owner_id)
        main_module.PUBLICATION_AUTHORITY_QA_ENABLED = False

        response = client.post(
            "/v2/internal/owner-authority/vaults/publication-hidden/drafts",
            headers=headers,
            json=self._draft_payload(memory_version_id),
        )

        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"]["code"], "publicationAuthorityUnavailable")

    def test_default_off_hides_writer_before_backend_token_authentication(self) -> None:
        main_module.BACKEND_API_TOKEN = "configured-server-token"
        main_module.PUBLICATION_AUTHORITY_QA_ENABLED = False

        response = client.post(
            "/v2/internal/owner-authority/vaults/default-off/drafts",
            json={},
        )

        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"]["code"], "publicationAuthorityUnavailable")

    def test_owner_can_create_draft_and_second_confirm_independent_projection(self) -> None:
        owner_id, headers = self._login("13800139172")
        vault_id = "publication-owner"
        memory_version_id = self._seed_memory(vault_id=vault_id, owner_id=owner_id)

        created = client.post(
            f"/v2/internal/owner-authority/vaults/{vault_id}/drafts",
            headers=headers,
            json=self._draft_payload(memory_version_id),
        )
        self.assertEqual(created.status_code, 201, created.text)
        draft = created.json()
        self.assertEqual(draft["state"], "draft")
        self.assertTrue(draft["requiresSecondConfirmation"])

        confirmed = client.post(
            f"/v2/internal/owner-authority/vaults/{vault_id}/drafts/{draft['draftId']}/confirm/{draft['publicationId']}",
            headers=headers,
            json={
                "commandId": str(uuid4()),
                "expectedDraftRevision": draft["expectedDraftRevision"],
                "expectedDraftSnapshotHash": draft["expectedDraftSnapshotHash"],
                "secondConfirmation": True,
            },
        )
        self.assertEqual(confirmed.status_code, 201, confirmed.text)
        result = confirmed.json()
        self.assertEqual(result["publicationState"], "confirmed")
        self.assertEqual(result["projectionState"], "active")
        self.assertNotEqual(result["publicProjectionHash"], "a" * 64)
        self.assertEqual(
            main_module.store.publication_authority_repository().public_projection_count(),
            1,
        )

    def test_direct_identifier_in_public_copy_fails_closed(self) -> None:
        owner_id, headers = self._login("13800139175")
        vault_id = "publication-direct-identifier"
        memory_version_id = self._seed_memory(vault_id=vault_id, owner_id=owner_id)

        response = client.post(
            f"/v2/internal/owner-authority/vaults/{vault_id}/drafts",
            headers=headers,
            json={
                **self._draft_payload(memory_version_id),
                "publicBody": "公开联系电话 13800138000。",
            },
        )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "publicationNotPublishable")

    def test_cross_owner_and_unexpected_payload_fail_closed(self) -> None:
        owner_id, headers = self._login("13800139173")
        _, other_headers = self._login("13800139174")
        vault_id = "publication-owner-fence"
        memory_version_id = self._seed_memory(vault_id=vault_id, owner_id=owner_id)

        cross_owner = client.post(
            f"/v2/internal/owner-authority/vaults/{vault_id}/drafts",
            headers=other_headers,
            json=self._draft_payload(memory_version_id),
        )
        self.assertEqual(cross_owner.status_code, 403, cross_owner.text)

        invalid_shape = client.post(
            f"/v2/internal/owner-authority/vaults/{vault_id}/drafts",
            headers=headers,
            json={**self._draft_payload(memory_version_id), "sourcePayload": "forbidden"},
        )
        self.assertEqual(invalid_shape.status_code, 400, invalid_shape.text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
