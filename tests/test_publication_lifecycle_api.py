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


class PublicationLifecycleAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = main_module.store
        self.previous_backend_token = main_module.BACKEND_API_TOKEN
        self.previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        self.previous_route_mode = main_module.AUTH_ROUTE_MODE
        self.previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
        self.previous_authority_qa_enabled = main_module.PUBLICATION_AUTHORITY_QA_ENABLED
        self.previous_visitor_qa_enabled = main_module.PUBLICATION_VISITOR_ACCESS_QA_ENABLED
        self.previous_lifecycle_qa_enabled = getattr(
            main_module,
            "PUBLICATION_LIFECYCLE_QA_ENABLED",
            False,
        )
        self.previous_eligibility_resolver = main_module.PUBLICATION_VISITOR_ELIGIBILITY_RESOLVER
        main_module.store = InMemoryStore()
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        main_module.PUBLICATION_AUTHORITY_QA_ENABLED = True
        main_module.PUBLICATION_VISITOR_ACCESS_QA_ENABLED = True
        main_module.PUBLICATION_LIFECYCLE_QA_ENABLED = True

    def tearDown(self) -> None:
        main_module.store = self.previous_store
        main_module.BACKEND_API_TOKEN = self.previous_backend_token
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = self.previous_legacy_phone_login
        main_module.AUTH_ROUTE_MODE = self.previous_route_mode
        main_module.AUTH_OWNERSHIP_MODE = self.previous_ownership_mode
        main_module.PUBLICATION_AUTHORITY_QA_ENABLED = self.previous_authority_qa_enabled
        main_module.PUBLICATION_VISITOR_ACCESS_QA_ENABLED = self.previous_visitor_qa_enabled
        main_module.PUBLICATION_LIFECYCLE_QA_ENABLED = self.previous_lifecycle_qa_enabled
        main_module.PUBLICATION_VISITOR_ELIGIBILITY_RESOLVER = self.previous_eligibility_resolver

    @staticmethod
    def _login(phone: str) -> tuple[str, dict[str, str]]:
        response = client.post(
            "/auth/login",
            json={"phone": phone, "nickname": "发布生命周期测试", "password": "password123"},
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

    @classmethod
    def _lifecycle_headers(cls, headers: dict[str, str]) -> dict[str, str]:
        return {
            **headers,
            "X-DreamJourney-QA-Publication": "1",
            "X-DreamJourney-QA-Visitor-Access": "1",
            "X-DreamJourney-QA-Publication-Lifecycle": "1",
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
                content_hash="c" * 64,
                is_current=True,
                memory_state="active",
                source_state="active",
                decision="accepted",
                decision_receipt_id=str(uuid4()),
            )
        )
        return memory_version_id

    @staticmethod
    def _verified_direct() -> PublicationVisitorEligibility:
        return PublicationVisitorEligibility(
            adult_verification=PublicationAdultVerificationState.VERIFIED,
            relationship_origin=PublicationVisitorRelationshipOrigin.DIRECT,
        )

    def _active_projection_with_visitor_session(self) -> dict[str, object]:
        owner_id, owner_headers = self._login("13800139611")
        visitor_id, visitor_headers = self._login("13800139612")
        vault_id = "publication-lifecycle-owner"
        memory_version_id = self._seed_memory(vault_id=vault_id, owner_id=owner_id)
        draft = client.post(
            f"/v2/internal/owner-authority/vaults/{vault_id}/drafts",
            headers=self._authority_headers(owner_headers),
            json={
                "commandId": str(uuid4()),
                "memoryVersionId": memory_version_id,
                "publicTitle": "雨后的院子",
                "publicBody": "这是经过本人确认后才允许分享的公开记忆。",
            },
        )
        self.assertEqual(draft.status_code, 201, draft.text)
        draft_payload = draft.json()
        confirmed = client.post(
            f"/v2/internal/owner-authority/vaults/{vault_id}/drafts/"
            f"{draft_payload['draftId']}/confirm/{draft_payload['publicationId']}",
            headers=self._authority_headers(owner_headers),
            json={
                "commandId": str(uuid4()),
                "expectedDraftRevision": draft_payload["expectedDraftRevision"],
                "expectedDraftSnapshotHash": draft_payload["expectedDraftSnapshotHash"],
                "secondConfirmation": True,
            },
        )
        self.assertEqual(confirmed.status_code, 201, confirmed.text)
        publication = confirmed.json()

        main_module.PUBLICATION_VISITOR_ELIGIBILITY_RESOLVER = StaticPublicationVisitorEligibilityResolver(
            {visitor_id: self._verified_direct()}
        )
        issued = client.post(
            f"/v2/internal/publication-access/vaults/{vault_id}/grants",
            headers=self._visitor_headers(owner_headers),
            json={
                "commandId": str(uuid4()),
                "publicationId": publication["publicationId"],
                "publicationVersionId": publication["publicationVersionId"],
                "granteeUserId": visitor_id,
                "expiresAt": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                "useLimit": 2,
            },
        )
        self.assertEqual(issued.status_code, 201, issued.text)
        grant = issued.json()
        session_credential = "publication-lifecycle-session-" + "s" * 32
        admitted = client.post(
            f"/v2/internal/publication-access/grants/{grant['grantId']}/sessions",
            headers=self._visitor_headers(visitor_headers),
            json={
                "commandId": str(uuid4()),
                "grantCredential": grant["grantCredential"],
                "sessionCredential": session_credential,
            },
        )
        self.assertEqual(admitted.status_code, 201, admitted.text)
        return {
            "ownerHeaders": owner_headers,
            "visitorHeaders": visitor_headers,
            "vaultId": vault_id,
            "publication": publication,
            "grant": grant,
            "visitorSessionId": admitted.json()["visitorSessionId"],
            "sessionCredential": session_credential,
        }

    def test_default_off_hides_lifecycle_command_before_authentication(self) -> None:
        main_module.PUBLICATION_LIFECYCLE_QA_ENABLED = False

        response = client.post(
            f"/v2/internal/publication-lifecycle/vaults/default-off/publications/{uuid4()}/withdraw",
            json={"commandId": str(uuid4()), "expectedAuthorityEpoch": 0},
        )

        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"]["code"], "publicationLifecycleUnavailable")

    def test_owner_withdrawal_denies_visitor_and_returns_idempotent_redacted_receipt(self) -> None:
        state = self._active_projection_with_visitor_session()
        publication = state["publication"]
        command_id = str(uuid4())

        withdrawn = client.post(
            f"/v2/internal/publication-lifecycle/vaults/{state['vaultId']}/publications/"
            f"{publication['publicationId']}/withdraw",
            headers=self._lifecycle_headers(state["ownerHeaders"]),
            json={"commandId": command_id, "expectedAuthorityEpoch": 0},
        )
        self.assertEqual(withdrawn.status_code, 200, withdrawn.text)
        receipt = withdrawn.json()
        self.assertEqual(receipt["schemaVersion"], "publication-lifecycle-v1")
        self.assertEqual(receipt["outcome"], "withdrawn")
        self.assertEqual(receipt["publicationState"], "withdrawn")
        self.assertEqual(receipt["projectionState"], "withdrawn")
        self.assertFalse(receipt["conflictHold"])
        self.assertEqual(receipt["revokedGrantCount"], 1)
        self.assertEqual(receipt["revokedVisitorSessionCount"], 1)
        self.assertEqual(receipt["receipt"]["accessDenyState"], "completed")
        self.assertEqual(receipt["receipt"]["publicIndexCleanupState"], "pending")
        self.assertEqual(receipt["receipt"]["runtimeCleanupState"], "notApplicable")
        self.assertEqual(
            {item["domain"] for item in receipt["externalCleanup"]},
            {"publicIndex", "cache", "digitalHumanSession", "providerVoice", "objectStorage"},
        )
        self.assertTrue(
            all(item["state"] == "pending" for item in receipt["externalCleanup"])
        )
        self.assertTrue(
            all(not item["providerReceiptPresent"] for item in receipt["externalCleanup"])
        )
        self.assertNotIn("grantCredential", str(receipt))
        self.assertNotIn("sessionCredential", str(receipt))
        self.assertNotIn(str(publication["publicationId"]), str(receipt["externalCleanup"]))
        self.assertNotIn(state["vaultId"], str(receipt["externalCleanup"]))

        rejected = client.post(
            f"/v2/internal/publication-access/sessions/{state['visitorSessionId']}/projection",
            headers=self._visitor_headers(state["visitorHeaders"]),
            json={"sessionCredential": state["sessionCredential"]},
        )
        self.assertEqual(rejected.status_code, 409, rejected.text)
        self.assertEqual(
            rejected.json()["detail"]["code"],
            "publicationVisitorAccessUnavailable",
        )

        replay = client.post(
            f"/v2/internal/publication-lifecycle/vaults/{state['vaultId']}/publications/"
            f"{publication['publicationId']}/withdraw",
            headers=self._lifecycle_headers(state["ownerHeaders"]),
            json={"commandId": command_id, "expectedAuthorityEpoch": 0},
        )
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["outcome"], "deduplicated")
        self.assertEqual(replay.json()["receipt"]["receiptId"], receipt["receipt"]["receiptId"])
        self.assertEqual(replay.json()["externalCleanup"], receipt["externalCleanup"])

    def test_stale_authority_epoch_keeps_existing_visitor_access_intact(self) -> None:
        state = self._active_projection_with_visitor_session()
        publication = state["publication"]

        stale = client.post(
            f"/v2/internal/publication-lifecycle/vaults/{state['vaultId']}/publications/"
            f"{publication['publicationId']}/withdraw",
            headers=self._lifecycle_headers(state["ownerHeaders"]),
            json={"commandId": str(uuid4()), "expectedAuthorityEpoch": 1},
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(
            stale.json()["detail"]["code"],
            "publicationLifecycleConflict",
        )

        still_readable = client.post(
            f"/v2/internal/publication-access/sessions/{state['visitorSessionId']}/projection",
            headers=self._visitor_headers(state["visitorHeaders"]),
            json={"sessionCredential": state["sessionCredential"]},
        )
        self.assertEqual(still_readable.status_code, 200, still_readable.text)

    def test_third_party_objection_enters_irreversible_conflict_hold(self) -> None:
        state = self._active_projection_with_visitor_session()
        publication = state["publication"]

        suspended = client.post(
            f"/v2/internal/publication-lifecycle/vaults/{state['vaultId']}/publications/"
            f"{publication['publicationId']}/suspend",
            headers=self._lifecycle_headers(state["ownerHeaders"]),
            json={"commandId": str(uuid4()), "expectedAuthorityEpoch": 0},
        )
        self.assertEqual(suspended.status_code, 200, suspended.text)
        payload = suspended.json()
        self.assertEqual(payload["outcome"], "suspended")
        self.assertEqual(payload["publicationState"], "suspended")
        self.assertEqual(payload["projectionState"], "suspended")
        self.assertTrue(payload["conflictHold"])
        self.assertEqual(payload["receipt"]["reasonCode"], "thirdPartyObjection")

        no_restore = client.post(
            f"/v2/internal/publication-lifecycle/vaults/{state['vaultId']}/publications/"
            f"{publication['publicationId']}/restore",
            headers=self._lifecycle_headers(state["ownerHeaders"]),
            json={"commandId": str(uuid4()), "expectedAuthorityEpoch": 0},
        )
        # The backend treats unclassified routes as a deployment safety fault
        # in enforce mode. Either way, no restore handler is admitted.
        self.assertEqual(no_restore.status_code, 503, no_restore.text)
        self.assertEqual(
            no_restore.json()["detail"]["reason"],
            "routeNotClassified",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
