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
from app.services.owner_truth_memory_search_projection import (
    OwnerTruthMemorySearchDocumentProjectionService,
)


client = TestClient(app)


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class OwnerTruthMemorySearchReadAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = main_module.store
        self.previous_backend_token = main_module.BACKEND_API_TOKEN
        self.previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        self.previous_route_mode = main_module.AUTH_ROUTE_MODE
        self.previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
        self.previous_candidate_qa = main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED
        self.previous_memory_search_qa = main_module.OWNER_TRUTH_MEMORY_SEARCH_READ_QA_ENABLED
        self.previous_memory_search_projection_qa = (
            main_module.OWNER_TRUTH_MEMORY_SEARCH_PROJECTION_QA_ENABLED
        )
        self.store = InMemoryStore()
        main_module.store = self.store
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = True
        main_module.OWNER_TRUTH_MEMORY_SEARCH_READ_QA_ENABLED = True
        main_module.OWNER_TRUTH_MEMORY_SEARCH_PROJECTION_QA_ENABLED = True

    def tearDown(self) -> None:
        main_module.store = self.previous_store
        main_module.BACKEND_API_TOKEN = self.previous_backend_token
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = self.previous_legacy_phone_login
        main_module.AUTH_ROUTE_MODE = self.previous_route_mode
        main_module.AUTH_OWNERSHIP_MODE = self.previous_ownership_mode
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = self.previous_candidate_qa
        main_module.OWNER_TRUTH_MEMORY_SEARCH_READ_QA_ENABLED = self.previous_memory_search_qa
        main_module.OWNER_TRUTH_MEMORY_SEARCH_PROJECTION_QA_ENABLED = (
            self.previous_memory_search_projection_qa
        )

    @staticmethod
    def _login(phone: str) -> tuple[str, dict[str, str]]:
        response = client.post(
            "/auth/login",
            json={"phone": phone, "nickname": "检索 QA", "password": "password123"},
        )
        if response.status_code != 200:
            raise AssertionError(response.text)
        body = response.json()
        return str(body["user"]["id"]), {
            "Authorization": f"Bearer {body['auth']['accessToken']}",
            "X-DreamJourney-QA-Owner-Truth": "1",
        }

    @staticmethod
    def _login_release_policy(phone: str) -> tuple[str, dict[str, str], str]:
        response = client.post(
            "/auth/login",
            json={"phone": phone, "nickname": "回顾检索展示测试", "password": "password123"},
        )
        if response.status_code != 200:
            raise AssertionError(response.text)
        body = response.json()
        return (
            str(body["user"]["id"]),
            {"Authorization": f"Bearer {body['auth']['accessToken']}"},
            str(body["auth"]["sessionId"]),
        )

    @staticmethod
    def _memory_search_presentation_policy_headers(
        headers: dict[str, str],
        *,
        session_id: str,
        decision_id: str,
    ) -> dict[str, str]:
        return {
            **headers,
            "X-DreamJourney-Feature": "ownerTruthMemorySearch",
            "X-DreamJourney-Feature-Decision-Id": decision_id,
            "X-DreamJourney-Feature-Allowed": "true",
            "X-DreamJourney-Policy-Version": "release-policy-v1",
            "X-DreamJourney-Policy-Revision": "1",
            "X-DreamJourney-Account-Generation": sha256(
                session_id.encode("utf-8")
            ).hexdigest()[:24],
        }

    def _activate_memory(
        self,
        *,
        vault_id: str,
        owner_id: str,
        rebuild_search_projection: bool = True,
    ) -> str:
        source_id = str(uuid4())
        content = {
            "claim": "private memory search API evidence about a career choice",
            "labels": ["career", "choice"],
        }
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
                command_id="memory-search-api-memory-accept-001",
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
        if rebuild_search_projection:
            OwnerTruthMemorySearchDocumentProjectionService(self.store).rebuild(context=context)
        snapshot = self.store.owner_truth_memory_projection_repository().read(context=context)
        return str(snapshot["entries"][0]["citation"]["memoryVersionId"])

    def test_contract_is_hidden_when_its_separate_flag_is_disabled(self) -> None:
        _owner_id, headers = self._login("13800139701")
        main_module.OWNER_TRUTH_MEMORY_SEARCH_READ_QA_ENABLED = False

        response = client.post(
            "/v2/vaults/vault-memory-search-hidden/memory-search/read",
            headers=headers,
            json={"query": "career"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "ownerTruthMemorySearchReadUnavailable")

    def test_owner_searches_confirmed_memory_without_exposing_query_or_content(self) -> None:
        owner_id, headers = self._login("13800139702")
        vault_id = "vault-memory-search-api"
        memory_version_id = self._activate_memory(vault_id=vault_id, owner_id=owner_id)

        response = client.post(
            f"/v2/vaults/{vault_id}/memory-search/read",
            headers=headers,
            json={"query": "career choice", "limit": 5},
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        body = response.json()
        self.assertEqual(body["schemaVersion"], "owner-truth-memory-search-read-response-v1")
        search = body["search"]
        self.assertEqual(search["state"], "ready")
        self.assertFalse(search["queryPlan"]["semanticRankingAvailable"])
        self.assertEqual(search["queryPlan"]["retrievalMode"], "deterministicTextFallback")
        self.assertEqual(search["hits"][0]["citation"]["memoryVersionId"], memory_version_id)
        rendered = json.dumps(body, ensure_ascii=False)
        self.assertNotIn("private memory search API evidence", rendered)
        self.assertNotIn("career choice", rendered)
        self.assertNotIn("sourceId", rendered)
        self.assertNotIn('"searchText":', rendered)
        self.assertNotIn("structuredTerms", rendered)

    def test_product_memory_search_requires_its_own_policy_and_limits_its_response(
        self,
    ) -> None:
        owner_id, headers, session_id = self._login_release_policy("13800139706")
        vault_id = "vault-memory-search-presentation"
        self._activate_memory(vault_id=vault_id, owner_id=owner_id)
        path = f"/v2/vaults/{vault_id}/memory-search"

        denied = client.post(
            path,
            headers={**headers, "X-DreamJourney-QA-Owner-Truth": "1"},
            json={"query": "career"},
        )
        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(denied.json()["detail"]["code"], "release_policy_denied")
        self.assertEqual(denied.json()["detail"]["feature"], "ownerTruthMemorySearch")

        policy_service = main_module.RELEASE_POLICY_SERVICE
        previous_visible = set(policy_service._CLOSED_PILOT_OWNER_VISIBLE)
        policy_service._CLOSED_PILOT_OWNER_VISIBLE = previous_visible | {
            "ownerTruthMemorySearch"
        }
        try:
            response = client.post(
                path,
                headers=self._memory_search_presentation_policy_headers(
                    headers,
                    session_id=session_id,
                    decision_id="memory-search-presentation-owner",
                ),
                json={"query": "career"},
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.headers["cache-control"], "no-store")
            body = response.json()
            self.assertEqual(
                body["schemaVersion"],
                "owner-truth-memory-search-presentation-response-v1",
            )
            self.assertEqual(body["vaultId"], vault_id)
            search = body["memorySearch"]
            self.assertEqual(
                set(search),
                {"state", "retrievalMode", "resultCount", "results"},
            )
            self.assertEqual(search["state"], "ready")
            self.assertEqual(search["retrievalMode"], "deterministicTextFallback")
            self.assertEqual(search["resultCount"], 1)
            self.assertEqual(len(search["results"]), 1)
            result = search["results"][0]
            self.assertEqual(
                set(result),
                {
                    "rank",
                    "preview",
                    "memoryKind",
                    "perspectiveType",
                    "sensitivity",
                    "matchKind",
                },
            )
            self.assertEqual(result["rank"], 1)
            self.assertIn("career", result["preview"])
            rendered = json.dumps(body, ensure_ascii=False)
            for forbidden in (
                "memoryVersionId",
                "memoryId",
                "sourceId",
                "threadId",
                "contentHash",
                "searchText",
                "structuredTerms",
                "authorityEpoch",
                "projectionCheckpoint",
                "policyVersion",
            ):
                self.assertNotIn(forbidden, rendered)
        finally:
            policy_service._CLOSED_PILOT_OWNER_VISIBLE = previous_visible

    def test_projection_rebuild_is_independently_gated_and_required_before_search(self) -> None:
        owner_id, headers = self._login("13800139705")
        vault_id = "vault-memory-search-projection-api"
        self._activate_memory(
            vault_id=vault_id,
            owner_id=owner_id,
            rebuild_search_projection=False,
        )

        before = client.post(
            f"/v2/vaults/{vault_id}/memory-search/read",
            headers=headers,
            json={"query": "career"},
        )
        self.assertEqual(before.status_code, 200)
        self.assertEqual(before.json()["search"]["state"], "rebuilding")

        rebuilt = client.post(
            f"/v2/vaults/{vault_id}/memory-search/projection/rebuild",
            headers=headers,
            json={},
        )
        self.assertEqual(rebuilt.status_code, 200, rebuilt.text)
        self.assertEqual(rebuilt.headers["cache-control"], "no-store")
        summary = rebuilt.json()["searchProjection"]
        self.assertEqual(summary["state"], "ready")
        self.assertEqual(summary["projection"]["documentCount"], 1)
        self.assertNotIn("private memory search API evidence", json.dumps(rebuilt.json()))

        main_module.OWNER_TRUTH_MEMORY_SEARCH_PROJECTION_QA_ENABLED = False
        hidden = client.post(
            f"/v2/vaults/{vault_id}/memory-search/projection/rebuild",
            headers=headers,
            json={},
        )
        self.assertEqual(hidden.status_code, 404)
        self.assertEqual(
            hidden.json()["detail"]["code"],
            "ownerTruthMemorySearchProjectionUnavailable",
        )

    def test_invalid_payload_and_cross_owner_access_fail_closed(self) -> None:
        owner_id, headers = self._login("13800139703")
        vault_id = "vault-memory-search-access"
        self._activate_memory(vault_id=vault_id, owner_id=owner_id)

        invalid = client.post(
            f"/v2/vaults/{vault_id}/memory-search/read",
            headers=headers,
            json={"query": "career", "unexpected": True},
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()["detail"]["code"], "ownerTruthMemorySearchReadInvalid")

        _other_id, other_headers = self._login("13800139704")
        denied = client.post(
            f"/v2/vaults/{vault_id}/memory-search/read",
            headers=other_headers,
            json={"query": "career"},
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json()["detail"]["code"], "ownerTruthMemorySearchReadDenied")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
