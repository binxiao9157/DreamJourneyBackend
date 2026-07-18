from __future__ import annotations

from hashlib import sha256
import json
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main_module
from app.domain.owner_truth.candidate_decisions import OwnerTruthCandidateSnapshot
from app.domain.owner_truth.contracts import (
    CandidateDecision,
    EpistemicStatus,
    MemoryKind,
    PerspectiveType,
    SensitivityLevel,
)
from app.domain.owner_truth.ontology import OWNER_TRUTH_SCHEMA_VERSION
from app.main import app
from app.services.in_memory_store import InMemoryStore


client = TestClient(app)


def _content_hash(content: dict[str, object]) -> str:
    return sha256(
        json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


class OwnerTruthCandidateReviewAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = main_module.store
        self.previous_backend_token = main_module.BACKEND_API_TOKEN
        self.previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        self.previous_route_mode = main_module.AUTH_ROUTE_MODE
        self.previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
        self.previous_qa_enabled = main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED
        main_module.store = InMemoryStore()
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = True

    def tearDown(self) -> None:
        main_module.store = self.previous_store
        main_module.BACKEND_API_TOKEN = self.previous_backend_token
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = self.previous_legacy_phone_login
        main_module.AUTH_ROUTE_MODE = self.previous_route_mode
        main_module.AUTH_OWNERSHIP_MODE = self.previous_ownership_mode
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = self.previous_qa_enabled

    @staticmethod
    def _login(phone: str) -> tuple[str, dict[str, str]]:
        response = client.post(
            "/auth/login",
            json={"phone": phone, "nickname": "候选审核测试", "password": "password123"},
        )
        if response.status_code != 200:
            raise AssertionError(response.text)
        payload = response.json()
        return payload["user"]["id"], {
            "Authorization": f"Bearer {payload['auth']['accessToken']}",
            "X-DreamJourney-QA-Owner-Truth": "1",
        }

    @staticmethod
    def _candidate(*, vault_id: str, owner_subject_id: str) -> OwnerTruthCandidateSnapshot:
        source_id = str(uuid4())
        content = {"summary": "小时候在院子里听雨"}
        return OwnerTruthCandidateSnapshot(
            candidate_id=str(uuid4()),
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            source_id=source_id,
            memory_kind=MemoryKind.EXPERIENCE,
            perspective_type=PerspectiveType.FIRST_PERSON,
            epistemic_status=EpistemicStatus.RECALLED,
            sensitivity=SensitivityLevel.STANDARD,
            decision=CandidateDecision.PENDING,
            policy_version=OWNER_TRUTH_SCHEMA_VERSION,
            authority_epoch=0,
            row_version=1,
            content_hash=_content_hash(content),
            content_schema_version=OWNER_TRUTH_SCHEMA_VERSION,
            payload={
                "content": content,
                "contentSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION,
                "evidenceRefs": [
                    {
                        "sourceId": source_id,
                        "sourceVersion": 1,
                        "span": {"start": 0, "end": 10},
                    }
                ],
                "reviewMode": "single",
                "schemaVersion": "owner-truth-candidate-proposal-v1",
            },
        )

    def _seed(self, candidate: OwnerTruthCandidateSnapshot) -> None:
        main_module.store.owner_truth_candidate_review_repository().seed(candidate)

    def test_contract_is_default_hidden_even_for_an_authenticated_owner(self) -> None:
        owner_id, headers = self._login("13800139101")
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = False

        response = client.get(f"/v2/vaults/vault-hidden/candidates", headers=headers)

        self.assertEqual(owner_id.startswith("user_"), True)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "ownerTruthCandidateReviewUnavailable")

    def test_owner_can_list_decide_activate_memory_and_replay(self) -> None:
        owner_id, headers = self._login("13800139102")
        vault_id = "vault-api-owner-review"
        candidate = self._candidate(vault_id=vault_id, owner_subject_id=owner_id)
        self._seed(candidate)

        inbox = client.get(f"/v2/vaults/{vault_id}/candidates", headers=headers)
        self.assertEqual(inbox.status_code, 200)
        self.assertEqual(inbox.headers["cache-control"], "no-store")
        self.assertEqual(inbox.json()["schemaVersion"], "owner-truth-candidate-inbox-v1")
        self.assertEqual(inbox.json()["candidates"][0]["candidateId"], candidate.candidate_id)
        self.assertEqual(inbox.json()["candidates"][0]["candidateVersion"], 1)

        command = {
            "commandId": "candidate-api-accept-001",
            "expectedCandidateVersion": 1,
            "action": "accept",
            "reasonCode": "ownerReviewed",
        }
        created = client.post(
            f"/v2/vaults/{vault_id}/candidates/{candidate.candidate_id}/decisions",
            headers=headers,
            json=command,
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["status"], "created")
        self.assertEqual(
            created.json()["schemaVersion"],
            "owner-truth-candidate-decision-memory-v1",
        )
        self.assertEqual(created.json()["memoryActivation"]["status"], "created")
        self.assertTrue(created.json()["memoryActivation"]["memoryId"])
        self.assertTrue(created.json()["memoryActivation"]["memoryVersionId"])
        self.assertEqual(created.json()["receipt"]["decision"], "accepted")

        replay = client.post(
            f"/v2/vaults/{vault_id}/candidates/{candidate.candidate_id}/decisions",
            headers=headers,
            json=command,
        )
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()["status"], "deduplicated")
        self.assertEqual(
            replay.json()["receipt"]["receiptId"],
            created.json()["receipt"]["receiptId"],
        )
        self.assertEqual(replay.json()["memoryActivation"]["status"], "deduplicated")
        self.assertEqual(
            client.get(f"/v2/vaults/{vault_id}/candidates", headers=headers).json()["candidates"],
            [],
        )

    def test_cross_vault_stale_and_corrected_value_boundaries(self) -> None:
        owner_id, headers = self._login("13800139103")
        vault_id = "vault-api-correct-review"
        candidate = self._candidate(vault_id=vault_id, owner_subject_id=owner_id)
        self._seed(candidate)

        denied = client.get("/v2/vaults/vault-other-owner/candidates", headers=headers)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json()["detail"]["code"], "ownerTruthCandidateReviewDenied")

        stale = client.post(
            f"/v2/vaults/{vault_id}/candidates/{candidate.candidate_id}/decisions",
            headers=headers,
            json={
                "commandId": "candidate-api-stale-001",
                "expectedCandidateVersion": 2,
                "action": "reject",
                "reasonCode": "ownerReviewed",
            },
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["detail"]["code"], "ownerTruthCandidateVersionConflict")

        corrected_value = {"summary": "小时候在院子里听父亲讲故事"}
        corrected = client.post(
            f"/v2/vaults/{vault_id}/candidates/{candidate.candidate_id}/decisions",
            headers=headers,
            json={
                "commandId": "candidate-api-correct-001",
                "expectedCandidateVersion": 1,
                "action": "correct",
                "correctedValue": corrected_value,
                "correctedValueSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION,
                "reasonCode": "ownerCorrected",
            },
        )
        self.assertEqual(corrected.status_code, 201)
        body = corrected.json()
        self.assertEqual(body["receipt"]["decision"], "corrected")
        self.assertIsNotNone(body["receipt"]["correctedValueId"])
        self.assertNotIn("correctedValue", body["receipt"])
        self.assertNotIn(corrected_value["summary"], str(body))


if __name__ == "__main__":
    unittest.main()
