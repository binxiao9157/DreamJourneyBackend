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
from app.domain.owner_truth.ontology import OWNER_TRUTH_SCHEMA_VERSION_V2
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.main import app
from app.services.in_memory_store import InMemoryStore
from app.services.owner_truth_candidate_review import OwnerTruthCandidateReviewService


client = TestClient(app)


def _digest(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _content(summary: str) -> dict[str, object]:
    return {
        "summary": summary,
        "facets": {
            "people": [{"value": "外祖父", "evidenceMode": "ownerStated", "confidence": 1.0}],
            "time": [],
            "places": [{"value": "老院子", "evidenceMode": "ownerStated", "confidence": 1.0}],
            "relationships": [],
            "emotions": [],
            "values": [],
            "personality": [],
            "confidence": 1.0,
        },
    }


class OwnerTruthFormalMemoryAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = main_module.store
        self.previous_backend_token = main_module.BACKEND_API_TOKEN
        self.previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        self.previous_route_mode = main_module.AUTH_ROUTE_MODE
        self.previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
        self.previous_qa_enabled = main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED
        self.previous_closed_pilot_owner_ids = main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS
        self.previous_closed_pilot_features = set(
            main_module.RELEASE_POLICY_SERVICE.closed_pilot_enabled_features
        )
        main_module.store = InMemoryStore()
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = True
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = frozenset()
        main_module.RELEASE_POLICY_SERVICE.closed_pilot_enabled_features = {
            "ownerTruthCandidateReview"
        }

    def tearDown(self) -> None:
        main_module.store = self.previous_store
        main_module.BACKEND_API_TOKEN = self.previous_backend_token
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = self.previous_legacy_phone_login
        main_module.AUTH_ROUTE_MODE = self.previous_route_mode
        main_module.AUTH_OWNERSHIP_MODE = self.previous_ownership_mode
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = self.previous_qa_enabled
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = self.previous_closed_pilot_owner_ids
        main_module.RELEASE_POLICY_SERVICE.closed_pilot_enabled_features = (
            self.previous_closed_pilot_features
        )

    @staticmethod
    def _login(phone: str) -> tuple[str, dict[str, str], str]:
        response = client.post(
            "/auth/login",
            json={"phone": phone, "nickname": "正式记忆测试", "password": "password123"},
        )
        if response.status_code != 200:
            raise AssertionError(response.text)
        body = response.json()
        owner_id = str(body["user"]["id"])
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = frozenset(
            set(main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS) | {owner_id}
        )
        return (
            owner_id,
            {"Authorization": f"Bearer {body['auth']['accessToken']}"},
            str(body["auth"]["sessionId"]),
        )

    @staticmethod
    def _policy_headers(headers: dict[str, str], *, session_id: str) -> dict[str, str]:
        return {
            **headers,
            "X-DreamJourney-Feature": "ownerTruthCandidateReview",
            "X-DreamJourney-Feature-Decision-Id": f"decision-{uuid4()}",
            "X-DreamJourney-Feature-Allowed": "true",
            "X-DreamJourney-Policy-Version": "release-policy-v1",
            "X-DreamJourney-Policy-Revision": "1",
            "X-DreamJourney-Account-Generation": sha256(session_id.encode("utf-8")).hexdigest()[:24],
        }

    @staticmethod
    def _activate(*, vault_id: str, owner_id: str) -> str:
        source_id = str(uuid4())
        candidate_id = str(uuid4())
        content = _content("在老院子里听外祖父讲故事")
        context = OwnerTruthCommandContext(
            vault_id=vault_id,
            owner_subject_id=owner_id,
            actor_subject_id=owner_id,
        )
        candidate = OwnerTruthCandidateSnapshot(
            candidate_id=candidate_id,
            vault_id=vault_id,
            owner_subject_id=owner_id,
            source_id=source_id,
            memory_kind=MemoryKind.EXPERIENCE,
            perspective_type=PerspectiveType.FIRST_PERSON,
            epistemic_status=EpistemicStatus.RECALLED,
            sensitivity=SensitivityLevel.STANDARD,
            decision=CandidateDecision.PENDING,
            policy_version=context.policy_version,
            authority_epoch=0,
            row_version=1,
            content_hash=_digest(content),
            content_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V2,
            payload={
                "schemaVersion": "owner-truth-candidate-proposal-v1",
                "candidateKind": "experience",
                "content": content,
                "contentSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION_V2,
                "evidenceRefs": [{"sourceId": source_id, "sourceVersion": 1}],
                "reviewMode": "single",
            },
        )
        main_module.store.owner_truth_candidate_review_repository().seed(candidate)
        result = OwnerTruthCandidateReviewService(main_module.store).decide_and_activate(
            command=OwnerTruthCandidateReviewCommand(
                command_id=f"accept-{candidate_id}",
                candidate_id=candidate_id,
                expected_candidate_version=1,
                action=CandidateReviewAction.ACCEPT,
                corrected_value=None,
                corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V2,
                reason_code="ownerReviewed",
            ),
            context=context,
        )
        return str(result.memory_activation.memory_id)

    def test_owner_can_list_filter_read_and_revise_formal_memory(self) -> None:
        owner_id, headers, session_id = self._login("13800139961")
        vault_id = f"vault-{uuid4()}"
        memory_id = self._activate(vault_id=vault_id, owner_id=owner_id)
        policy_headers = self._policy_headers(headers, session_id=session_id)

        listed = client.get(
            f"/v2/vaults/{vault_id}/memories",
            params={"kind": "experience", "query": "外祖父", "facet": "places:老院子"},
            headers=policy_headers,
        )
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual([item["memoryId"] for item in listed.json()["memories"]], [memory_id])

        profile = client.get(
            f"/v2/vaults/{vault_id}/memory-profile",
            headers=self._policy_headers(headers, session_id=session_id),
        )
        self.assertEqual(profile.status_code, 200, profile.text)
        profile_body = profile.json()
        self.assertEqual(profile_body["schemaVersion"], "owner-truth-person-memory-profile-v2")
        self.assertEqual(profile_body["memoryCount"], 1)
        self.assertEqual(
            profile_body["lifeStory"]["schemaVersion"],
            "owner-truth-biography-projection-v1",
        )
        self.assertEqual(profile_body["lifeStory"]["supportingMemoryCount"], 1)
        self.assertEqual(
            profile_body["lifeStory"]["chapters"][0]["supportingMemoryIds"],
            [memory_id],
        )
        experience = profile_body["dimensions"][0]
        self.assertEqual(experience["dimension"], "lifeEvent")
        self.assertEqual(experience["supportingMemoryIds"], [memory_id])
        self.assertEqual(len(experience["supportingMemoryVersionIds"]), 1)
        self.assertIn("外祖父", experience["narrative"])
        self.assertEqual(profile_body["memoryModel"]["memoryCount"], 1)
        self.assertEqual(
            profile_body["memoryModel"]["modelVersion"],
            profile_body["profileVersion"],
        )

        detail = client.get(
            f"/v2/vaults/{vault_id}/memories/{memory_id}",
            headers=self._policy_headers(headers, session_id=session_id),
        )
        self.assertEqual(detail.status_code, 200, detail.text)
        current = detail.json()["memory"]["currentVersion"]
        corrected = _content("在老院子里听外祖父讲童年往事")
        revision = client.post(
            f"/v2/vaults/{vault_id}/memories/{memory_id}/revisions",
            headers=self._policy_headers(headers, session_id=session_id),
            json={
                "commandId": "formal-memory-api-edit-001",
                "expectedVersion": current["versionNumber"],
                "expectedContentHash": current["contentHash"],
                "expectedContentSchemaVersion": current["contentSchemaVersion"],
                "contentSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION_V2,
                "correctedContent": corrected,
                "secondConfirmation": True,
            },
        )
        self.assertEqual(revision.status_code, 201, revision.text)
        self.assertEqual(revision.json()["revision"]["replacementVersion"], 2)

        refreshed = client.get(
            f"/v2/vaults/{vault_id}/memories/{memory_id}",
            headers=self._policy_headers(headers, session_id=session_id),
        )
        self.assertEqual(refreshed.json()["memory"]["currentVersion"]["content"], corrected)
        self.assertEqual(len(refreshed.json()["memory"]["versions"]), 2)

        refreshed_profile = client.get(
            f"/v2/vaults/{vault_id}/memory-profile",
            headers=self._policy_headers(headers, session_id=session_id),
        )
        self.assertEqual(refreshed_profile.status_code, 200, refreshed_profile.text)
        refreshed_profile_body = refreshed_profile.json()
        self.assertNotEqual(
            refreshed_profile_body["profileVersion"],
            profile_body["profileVersion"],
        )
        self.assertIn(
            corrected["summary"],
            refreshed_profile_body["lifeStory"]["chapters"][0]["text"],
        )
        self.assertEqual(
            refreshed_profile_body["lifeStory"]["chapters"][0][
                "supportingMemoryVersionIds"
            ],
            [refreshed.json()["memory"]["currentVersion"]["versionId"]],
        )

    def test_unconfirmed_stale_and_cross_owner_requests_do_not_mutate(self) -> None:
        owner_id, headers, session_id = self._login("13800139962")
        other_id, other_headers, other_session_id = self._login("13800139963")
        vault_id = f"vault-{uuid4()}"
        memory_id = self._activate(vault_id=vault_id, owner_id=owner_id)
        detail = client.get(
            f"/v2/vaults/{vault_id}/memories/{memory_id}",
            headers=self._policy_headers(headers, session_id=session_id),
        ).json()["memory"]["currentVersion"]
        base_payload = {
            "commandId": "formal-memory-api-rejected-001",
            "expectedVersion": detail["versionNumber"],
            "expectedContentHash": detail["contentHash"],
            "expectedContentSchemaVersion": detail["contentSchemaVersion"],
            "contentSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION_V2,
            "correctedContent": _content("不应保存"),
            "secondConfirmation": False,
        }
        unconfirmed = client.post(
            f"/v2/vaults/{vault_id}/memories/{memory_id}/revisions",
            headers=self._policy_headers(headers, session_id=session_id),
            json=base_payload,
        )
        self.assertEqual(unconfirmed.status_code, 400, unconfirmed.text)

        stale_payload = {**base_payload, "commandId": "formal-memory-api-stale-001", "secondConfirmation": True}
        stale_payload["expectedVersion"] = 2
        stale = client.post(
            f"/v2/vaults/{vault_id}/memories/{memory_id}/revisions",
            headers=self._policy_headers(headers, session_id=session_id),
            json=stale_payload,
        )
        self.assertEqual(stale.status_code, 409, stale.text)

        foreign = client.get(
            f"/v2/vaults/{vault_id}/memories/{memory_id}",
            headers=self._policy_headers(other_headers, session_id=other_session_id),
        )
        self.assertEqual(foreign.status_code, 403, foreign.text)
        self.assertNotEqual(owner_id, other_id)

        unchanged = client.get(
            f"/v2/vaults/{vault_id}/memories/{memory_id}",
            headers=self._policy_headers(headers, session_id=session_id),
        )
        self.assertEqual(unchanged.json()["memory"]["currentVersion"]["versionNumber"], 1)
        registered_delete_routes = {
            str(route.path)
            for route in app.routes
            if "DELETE" in (getattr(route, "methods", set()) or set())
        }
        self.assertNotIn("/v2/vaults/{vault_id}/memories/{memory_id}", registered_delete_routes)


if __name__ == "__main__":
    unittest.main()
