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


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class OwnerTruthLegacyShadowParityAPITests(unittest.TestCase):
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
            json={"phone": phone, "nickname": "Shadow parity QA", "password": "password123"},
        )
        if response.status_code != 200:
            raise AssertionError(response.text)
        payload = response.json()
        return payload["user"]["id"], {
            "Authorization": f"Bearer {payload['auth']['accessToken']}",
            "X-DreamJourney-QA-Owner-Truth": "1",
        }

    @staticmethod
    def _seed_vault(owner_id: str, vault_id: str) -> None:
        source_id = str(uuid4())
        candidate = OwnerTruthCandidateSnapshot(
            candidate_id=str(uuid4()),
            vault_id=vault_id,
            owner_subject_id=owner_id,
            source_id=source_id,
            memory_kind=MemoryKind.EXPERIENCE,
            perspective_type=PerspectiveType.FIRST_PERSON,
            epistemic_status=EpistemicStatus.RECALLED,
            sensitivity=SensitivityLevel.STANDARD,
            decision=CandidateDecision.PENDING,
            policy_version=OWNER_TRUTH_SCHEMA_VERSION,
            authority_epoch=0,
            row_version=1,
            content_hash=_hash({"summary": "shadow parity seed"}),
            content_schema_version=OWNER_TRUTH_SCHEMA_VERSION,
            payload={
                "content": {"summary": "shadow parity seed"},
                "contentSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION,
                "evidenceRefs": [
                    {
                        "sourceId": source_id,
                        "sourceVersion": 1,
                        "span": {"start": 0, "end": 1},
                    }
                ],
                "reviewMode": "single",
                "schemaVersion": "owner-truth-candidate-proposal-v1",
            },
        )
        main_module.store.owner_truth_candidate_review_repository().seed(candidate)

    def test_contract_is_default_hidden(self) -> None:
        owner_id, headers = self._login("13800139331")
        self._seed_vault(owner_id, "vault-parity-hidden")
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = False

        response = client.post(
            "/v2/vaults/vault-parity-hidden/legacy-migration/shadow-parity",
            headers=headers,
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json()["detail"]["code"],
            "ownerTruthLegacyMigrationUnavailable",
        )

    def test_owner_can_observe_value_free_parity_but_never_cut_over(self) -> None:
        owner_id, headers = self._login("13800139332")
        vault_id = "vault-parity-owner"
        self._seed_vault(owner_id, vault_id)
        raw_archive_body = "这个旧档案正文绝不能出现在 shadow parity 响应"
        main_module.store.add_archive_item(
            owner_id,
            {"id": "legacy-shadow-api", "kind": "text", "note": raw_archive_body},
        )

        first = client.post(
            f"/v2/vaults/{vault_id}/legacy-migration/shadow-parity",
            headers=headers,
        )
        replay = client.post(
            f"/v2/vaults/{vault_id}/legacy-migration/shadow-parity",
            headers=headers,
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.headers["cache-control"], "no-store")
        payload = first.json()
        self.assertEqual(payload["schemaVersion"], "owner-truth-legacy-shadow-parity-v1")
        self.assertEqual(payload["comparisonStatus"], "projectionRebuilding")
        self.assertFalse(payload["cutoverAllowed"])
        self.assertFalse(payload["authorityEpochChanged"])
        self.assertFalse(payload["legacyWriterRetired"])
        self.assertEqual(payload["cutoverAdmission"]["status"], "external_go_required")
        self.assertFalse(payload["cutoverAdmission"]["cutoverAllowed"])
        self.assertIn(
            "separateProductionGoRecordRequired",
            payload["cutoverAdmission"]["reasonCodes"],
        )
        self.assertNotIn(raw_archive_body, str(payload))
        self.assertNotIn("legacy-shadow-api", str(payload))
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()["inventoryRunId"], payload["inventoryRunId"])

    def test_cross_owner_vault_is_denied_before_inventory(self) -> None:
        owner_id, _owner_headers = self._login("13800139333")
        vault_id = "vault-parity-cross-owner"
        self._seed_vault(owner_id, vault_id)
        _attacker_id, attacker_headers = self._login("13800139334")

        response = client.post(
            f"/v2/vaults/{vault_id}/legacy-migration/shadow-parity",
            headers=attacker_headers,
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json()["detail"]["code"],
            "ownerTruthLegacyMigrationDenied",
        )
        snapshot = main_module.store.owner_truth_legacy_migration_repository().snapshot()
        self.assertEqual(snapshot["runCount"], 0)


if __name__ == "__main__":
    unittest.main()
