from __future__ import annotations

from hashlib import sha256
import json
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main_module
from app.domain.owner_truth.candidate_decisions import (
    CandidateReviewAction,
    OwnerTruthCandidateReviewCommand,
    OwnerTruthCandidateSnapshot,
)
from app.domain.owner_truth.contracts import (
    CandidateDecision,
    EpistemicStatus,
    MemoryKind,
    PerspectiveType,
    SensitivityLevel,
)
from app.domain.owner_truth.ontology import OWNER_TRUTH_SCHEMA_VERSION
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.main import app
from app.services.in_memory_store import InMemoryStore
from app.services.owner_truth_candidate_review import OwnerTruthCandidateReviewService
from app.services.owner_truth_memory_projection import OwnerTruthMemoryProjectionService


client = TestClient(app)


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class OwnerTruthKnowledgeDimensionConfirmationAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = main_module.store
        self.previous_backend_token = main_module.BACKEND_API_TOKEN
        self.previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        self.previous_route_mode = main_module.AUTH_ROUTE_MODE
        self.previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
        self.previous_candidate_qa = main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED
        self.previous_confirmation_qa = (
            main_module.OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_QA_ENABLED
        )
        main_module.store = InMemoryStore()
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = True
        main_module.OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_QA_ENABLED = True

    def tearDown(self) -> None:
        main_module.store = self.previous_store
        main_module.BACKEND_API_TOKEN = self.previous_backend_token
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = self.previous_legacy_phone_login
        main_module.AUTH_ROUTE_MODE = self.previous_route_mode
        main_module.AUTH_OWNERSHIP_MODE = self.previous_ownership_mode
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = self.previous_candidate_qa
        main_module.OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_QA_ENABLED = (
            self.previous_confirmation_qa
        )

    @staticmethod
    def _login(phone: str) -> tuple[str, dict[str, str]]:
        response = client.post(
            "/auth/login",
            json={"phone": phone, "nickname": "维度确认测试", "password": "password123"},
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
        content = {"claim": "我选择每天保留晚上时间陪伴家人。"}
        return OwnerTruthCandidateSnapshot(
            candidate_id=str(uuid4()),
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            source_id=source_id,
            memory_kind=MemoryKind.KNOWLEDGE,
            perspective_type=PerspectiveType.FIRST_PERSON,
            epistemic_status=EpistemicStatus.RECALLED,
            sensitivity=SensitivityLevel.STANDARD,
            decision=CandidateDecision.PENDING,
            policy_version=OWNER_TRUTH_SCHEMA_VERSION,
            authority_epoch=0,
            row_version=1,
            content_hash=_hash(content),
            content_schema_version=OWNER_TRUTH_SCHEMA_VERSION,
            payload={
                "content": content,
                "contentSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION,
                "evidenceRefs": [{"sourceId": source_id, "sourceVersion": 1}],
                "reviewMode": "single",
                "schemaVersion": "owner-truth-candidate-proposal-v1",
            },
        )

    def _activate_memory(self, *, vault_id: str, owner_id: str) -> tuple[str, str]:
        candidate = self._candidate(vault_id=vault_id, owner_subject_id=owner_id)
        store = main_module.store
        store.owner_truth_candidate_review_repository().seed(candidate)
        context = OwnerTruthCommandContext(
            vault_id=vault_id,
            owner_subject_id=owner_id,
            actor_subject_id=owner_id,
        )
        OwnerTruthCandidateReviewService(store).decide_and_activate(
            command=OwnerTruthCandidateReviewCommand(
                command_id="api-dimension-memory-accept-001",
                candidate_id=candidate.candidate_id,
                expected_candidate_version=1,
                action=CandidateReviewAction.ACCEPT,
                corrected_value=None,
                corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION,
                reason_code="ownerReviewed",
            ),
            context=context,
        )
        OwnerTruthMemoryProjectionService(store).rebuild(context=context)
        snapshot = store.owner_truth_memory_projection_repository().read(context=context)
        entry = snapshot["entries"][0]
        return entry["citation"]["memoryVersionId"], entry["citation"]["contentHash"]

    def test_contract_is_hidden_when_its_separate_flag_is_disabled(self) -> None:
        owner_id, headers = self._login("13800139351")
        main_module.OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_QA_ENABLED = False
        response = client.post(
            "/v2/vaults/vault-hidden/memory-versions/00000000-0000-0000-0000-000000000001/knowledge-dimension-confirmations",
            headers=headers,
            json={},
        )

        self.assertTrue(owner_id.startswith("user_"))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json()["detail"]["code"],
            "ownerTruthKnowledgeDimensionConfirmationUnavailable",
        )

    def test_owner_can_append_replay_and_receive_no_memory_text(self) -> None:
        owner_id, headers = self._login("13800139352")
        vault_id = "vault-api-dimension-confirmation"
        memory_version_id, content_hash = self._activate_memory(
            vault_id=vault_id,
            owner_id=owner_id,
        )
        payload = {
            "commandId": "api-dimension-confirm-001",
            "expectedContentHash": content_hash,
            "dimension": "keyDecisions",
            "coveredFacets": ["choice", "reason"],
        }
        path = (
            f"/v2/vaults/{vault_id}/memory-versions/{memory_version_id}"
            "/knowledge-dimension-confirmations"
        )

        created = client.post(path, headers=headers, json=payload)
        replay = client.post(path, headers=headers, json=payload)

        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(created.headers["cache-control"], "no-store")
        self.assertEqual(created.json()["confirmation"]["dimension"], "keyDecisions")
        self.assertEqual(created.json()["confirmation"]["coveredFacets"], ["choice", "reason"])
        self.assertNotIn("每天保留", created.text)
        self.assertNotIn("claim", created.text)

    def test_stale_hash_returns_a_stable_conflict(self) -> None:
        owner_id, headers = self._login("13800139353")
        vault_id = "vault-api-dimension-stale"
        memory_version_id, _content_hash = self._activate_memory(vault_id=vault_id, owner_id=owner_id)
        response = client.post(
            f"/v2/vaults/{vault_id}/memory-versions/{memory_version_id}/knowledge-dimension-confirmations",
            headers=headers,
            json={
                "commandId": "api-dimension-stale-001",
                "expectedContentHash": _hash("superseded"),
                "dimension": "keyDecisions",
                "coveredFacets": ["choice"],
            },
        )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "ownerTruthKnowledgeDimensionConfirmationStaleMemory",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
