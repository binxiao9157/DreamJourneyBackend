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


class OwnerTruthInterviewTurnContextAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = main_module.store
        self.previous_backend_token = main_module.BACKEND_API_TOKEN
        self.previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        self.previous_route_mode = main_module.AUTH_ROUTE_MODE
        self.previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
        self.previous_qa_enabled = main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED
        self.store = InMemoryStore()
        main_module.store = self.store
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
            json={"phone": phone, "nickname": "回合上下文测试", "password": "password123"},
        )
        if response.status_code != 200:
            raise AssertionError(response.text)
        payload = response.json()
        return payload["user"]["id"], {
            "Authorization": f"Bearer {payload['auth']['accessToken']}",
            "X-DreamJourney-QA-Owner-Truth": "1",
        }

    @staticmethod
    def _candidate(
        *,
        vault_id: str,
        owner_subject_id: str,
        summary: str = "确认记忆只应在私有回合上下文内物化",
        perspective_type: PerspectiveType = PerspectiveType.FIRST_PERSON,
        epistemic_status: EpistemicStatus = EpistemicStatus.RECALLED,
    ) -> OwnerTruthCandidateSnapshot:
        source_id = str(uuid4())
        content = {"summary": summary}
        return OwnerTruthCandidateSnapshot(
            candidate_id=str(uuid4()),
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            source_id=source_id,
            memory_kind=MemoryKind.EXPERIENCE,
            perspective_type=perspective_type,
            epistemic_status=epistemic_status,
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
                "evidenceRefs": [
                    {
                        "sourceId": source_id,
                        "sourceVersion": 1,
                        "span": {"start": 0, "end": 12},
                    }
                ],
                "reviewMode": "single",
                "schemaVersion": "owner-truth-candidate-proposal-v1",
            },
        )

    def _activate_projection(
        self,
        *,
        vault_id: str,
        owner_id: str,
        summary: str = "确认记忆只应在私有回合上下文内物化",
        perspective_type: PerspectiveType = PerspectiveType.FIRST_PERSON,
        epistemic_status: EpistemicStatus = EpistemicStatus.RECALLED,
    ) -> str:
        candidate = self._candidate(
            vault_id=vault_id,
            owner_subject_id=owner_id,
            summary=summary,
            perspective_type=perspective_type,
            epistemic_status=epistemic_status,
        )
        self.store.owner_truth_candidate_review_repository().seed(candidate)
        context = OwnerTruthCommandContext(
            vault_id=vault_id,
            owner_subject_id=owner_id,
            actor_subject_id=owner_id,
        )
        OwnerTruthCandidateReviewService(self.store).decide_and_activate(
            command=OwnerTruthCandidateReviewCommand(
                command_id="turn-context-api-activate",
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
        return candidate.payload["content"]["summary"]

    @staticmethod
    def _prepare_path(vault_id: str, session_id: str) -> str:
        return f"/v2/vaults/{vault_id}/interview-sessions/{session_id}/turn-context/prepare"

    def test_contract_is_hidden_without_owner_truth_qa_gate(self) -> None:
        _, headers = self._login("13800139721")
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = False

        response = client.post(
            self._prepare_path("vault-turn-context-hidden", str(uuid4())),
            headers=headers,
            json={
                "messageId": str(uuid4()),
                "expectedSessionVersion": 1,
                "query": "不得公开",
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json()["detail"]["code"],
            "ownerTruthContextShadowUnavailable",
        )

    def test_owner_prepares_current_turn_context_without_receiving_query_or_memory_text(self) -> None:
        owner_id, headers = self._login("13800139722")
        vault_id = "vault-turn-context-api"
        confirmed_summary = self._activate_projection(vault_id=vault_id, owner_id=owner_id)
        thread_id = str(uuid4())
        session_id = str(uuid4())
        message_id = str(uuid4())
        owner_message = "这段访谈输入只保存到私有消息中"
        query = "请继续聊确认过的回忆"

        started = client.post(
            f"/v2/vaults/{vault_id}/interview-sessions",
            headers=headers,
            json={
                "commandId": str(uuid4()),
                "threadId": thread_id,
                "sessionId": session_id,
            },
        )
        self.assertEqual(started.status_code, 201, started.text)
        appended = client.post(
            f"/v2/vaults/{vault_id}/interview-sessions/{session_id}/messages",
            headers=headers,
            json={
                "commandId": str(uuid4()),
                "threadId": thread_id,
                "messageId": message_id,
                "expectedThreadVersion": 1,
                "expectedSessionVersion": 1,
                "text": owner_message,
            },
        )
        self.assertEqual(appended.status_code, 201, appended.text)

        prepared = client.post(
            self._prepare_path(vault_id, session_id),
            headers=headers,
            json={
                "messageId": message_id,
                "expectedSessionVersion": 2,
                "query": query,
            },
        )

        self.assertEqual(prepared.status_code, 200, prepared.text)
        self.assertEqual(prepared.headers["cache-control"], "no-store")
        self.assertEqual(
            prepared.json()["schemaVersion"],
            "owner-truth-interview-turn-context-prepare-response-v1",
        )
        preparation = prepared.json()["interviewTurnContext"]
        self.assertEqual(preparation["state"], "ready")
        self.assertTrue(preparation["readyForServerTurn"])
        self.assertFalse(preparation["providerDispatchAllowed"])
        self.assertTrue(preparation["publicEchoUnchanged"])
        self.assertEqual(
            preparation["contextMaterialization"]["generationContext"]["sourceCount"],
            1,
        )
        rendered = json.dumps(prepared.json(), ensure_ascii=False, sort_keys=True)
        for forbidden in (confirmed_summary, owner_message, query):
            self.assertNotIn(forbidden, rendered)

        stale = client.post(
            self._prepare_path(vault_id, session_id),
            headers=headers,
            json={
                "messageId": message_id,
                "expectedSessionVersion": 1,
                "query": query,
            },
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["detail"]["code"], "ownerTruthInterviewSessionConflict")

    def test_turn_context_api_excludes_ai_only_inferred_memory(self) -> None:
        owner_id, headers = self._login("13800139723")
        vault_id = "vault-turn-context-ai-only"
        inferred_summary = "AI 推断的记忆不得进入访谈上下文"
        self._activate_projection(
            vault_id=vault_id,
            owner_id=owner_id,
            summary=inferred_summary,
            epistemic_status=EpistemicStatus.INFERRED,
        )
        thread_id = str(uuid4())
        session_id = str(uuid4())
        message_id = str(uuid4())
        owner_message = "这是一段有效的本人叙述"
        query = "不应把推断记忆提供给后续回答"

        started = client.post(
            f"/v2/vaults/{vault_id}/interview-sessions",
            headers=headers,
            json={
                "commandId": str(uuid4()),
                "threadId": thread_id,
                "sessionId": session_id,
            },
        )
        self.assertEqual(started.status_code, 201, started.text)
        appended = client.post(
            f"/v2/vaults/{vault_id}/interview-sessions/{session_id}/messages",
            headers=headers,
            json={
                "commandId": str(uuid4()),
                "threadId": thread_id,
                "messageId": message_id,
                "expectedThreadVersion": 1,
                "expectedSessionVersion": 1,
                "text": owner_message,
            },
        )
        self.assertEqual(appended.status_code, 201, appended.text)

        prepared = client.post(
            self._prepare_path(vault_id, session_id),
            headers=headers,
            json={
                "messageId": message_id,
                "expectedSessionVersion": 2,
                "query": query,
            },
        )

        self.assertEqual(prepared.status_code, 200, prepared.text)
        preparation = prepared.json()["interviewTurnContext"]
        self.assertTrue(preparation["readyForServerTurn"])
        materialization = preparation["contextMaterialization"]
        self.assertEqual(materialization["generationContext"]["sourceCount"], 0)
        self.assertEqual(materialization["selectedContext"], [])
        self.assertEqual(
            materialization["filteredContext"][0]["reason"],
            "ai_only_epistemic_status_not_context_eligible",
        )
        rendered = json.dumps(prepared.json(), ensure_ascii=False, sort_keys=True)
        for forbidden in (inferred_summary, owner_message, query):
            self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()
