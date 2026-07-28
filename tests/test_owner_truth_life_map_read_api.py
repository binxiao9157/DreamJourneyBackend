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


class OwnerTruthLifeMapReadAPITests(unittest.TestCase):
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
        self.previous_life_map_qa = main_module.OWNER_TRUTH_LIFE_MAP_READ_QA_ENABLED
        self.store = InMemoryStore()
        main_module.store = self.store
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = True
        main_module.OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_QA_ENABLED = True
        main_module.OWNER_TRUTH_LIFE_MAP_READ_QA_ENABLED = True

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
        main_module.OWNER_TRUTH_LIFE_MAP_READ_QA_ENABLED = self.previous_life_map_qa

    @staticmethod
    def _login(phone: str) -> tuple[str, dict[str, str]]:
        response = client.post(
            "/auth/login",
            json={"phone": phone, "nickname": "人生地图 QA", "password": "password123"},
        )
        if response.status_code != 200:
            raise AssertionError(response.text)
        body = response.json()
        return str(body["user"]["id"]), {
            "Authorization": f"Bearer {body['auth']['accessToken']}",
            "X-DreamJourney-QA-Owner-Truth": "1",
        }

    def _activate_memory(self, *, vault_id: str, owner_id: str) -> tuple[str, str]:
        source_id = str(uuid4())
        content = {"claim": "private life-map API content must not leak"}
        candidate = OwnerTruthCandidateSnapshot(
            candidate_id=str(uuid4()),
            vault_id=vault_id,
            owner_subject_id=owner_id,
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
        self.store.owner_truth_candidate_review_repository().seed(candidate)
        context = OwnerTruthCommandContext(
            vault_id=vault_id,
            owner_subject_id=owner_id,
            actor_subject_id=owner_id,
        )
        OwnerTruthCandidateReviewService(self.store).decide_and_activate(
            command=OwnerTruthCandidateReviewCommand(
                command_id="life-map-api-memory-accept-001",
                candidate_id=candidate.candidate_id,
                expected_candidate_version=1,
                action=CandidateReviewAction.ACCEPT,
                corrected_value=None,
                corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION,
                reason_code="ownerReviewed",
            ),
            context=context,
        )
        OwnerTruthMemoryProjectionService(self.store).rebuild(context=context)
        snapshot = self.store.owner_truth_memory_projection_repository().read(context=context)
        entry = snapshot["entries"][0]
        return str(entry["citation"]["memoryVersionId"]), str(entry["citation"]["contentHash"])

    def test_contract_is_hidden_when_its_separate_flag_is_disabled(self) -> None:
        _owner_id, headers = self._login("13800139601")
        main_module.OWNER_TRUTH_LIFE_MAP_READ_QA_ENABLED = False

        response = client.post(
            "/v2/vaults/vault-life-map-hidden/life-map/read",
            headers=headers,
            json={},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "ownerTruthLifeMapReadUnavailable")

    def test_owner_reads_value_free_confirmed_life_map(self) -> None:
        owner_id, headers = self._login("13800139602")
        vault_id = "vault-life-map-api"
        memory_version_id, content_hash = self._activate_memory(
            vault_id=vault_id,
            owner_id=owner_id,
        )
        confirmation = client.post(
            f"/v2/vaults/{vault_id}/memory-versions/{memory_version_id}"
            "/knowledge-dimension-confirmations",
            headers=headers,
            json={
                "commandId": "life-map-api-confirm-001",
                "expectedContentHash": content_hash,
                "dimension": "keyDecisions",
                "coveredFacets": ["choice"],
            },
        )
        self.assertEqual(confirmation.status_code, 201, confirmation.text)

        response = client.post(
            f"/v2/vaults/{vault_id}/life-map/read",
            headers=headers,
            json={},
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        body = response.json()
        self.assertEqual(body["schemaVersion"], "owner-truth-life-map-read-response-v1")
        life_map = body["lifeMap"]
        self.assertEqual(life_map["state"], "ready")
        projection = life_map["projection"]
        self.assertEqual(projection["dimensionCount"], 6)
        decision = next(
            item for item in projection["dimensions"] if item["dimension"] == "keyDecisions"
        )
        self.assertEqual(decision["evidenceCount"], 1)
        rendered = json.dumps(body, ensure_ascii=False)
        self.assertNotIn("private life-map API", rendered)
        self.assertNotIn("memoryVersionId", rendered)
        self.assertNotIn("sourceId", rendered)

    def test_payload_is_rejected_and_another_owner_cannot_read(self) -> None:
        owner_id, headers = self._login("13800139603")
        vault_id = "vault-life-map-access"
        self._activate_memory(vault_id=vault_id, owner_id=owner_id)

        invalid = client.post(
            f"/v2/vaults/{vault_id}/life-map/read",
            headers=headers,
            json={"unexpected": True},
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()["detail"]["code"], "ownerTruthLifeMapReadInvalid")

        _other_id, other_headers = self._login("13800139604")
        denied = client.post(
            f"/v2/vaults/{vault_id}/life-map/read",
            headers=other_headers,
            json={},
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json()["detail"]["code"], "ownerTruthLifeMapReadDenied")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
