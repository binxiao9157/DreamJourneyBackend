from __future__ import annotations

import json
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main_module
from app.domain.owner_truth.conversation import InterviewBoundary, SetInterviewBoundaryCommand
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.main import app
from app.services.in_memory_store import InMemoryStore
from app.services.owner_truth_conversation import OwnerTruthConversationService


client = TestClient(app)


class OwnerTruthInterviewOrchestrationAPITests(unittest.TestCase):
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
            json={
                "phone": phone,
                "nickname": "访谈编排读取测试",
                "password": "password123",
            },
        )
        if response.status_code != 200:
            raise AssertionError(response.text)
        payload = response.json()
        return payload["user"]["id"], {
            "Authorization": f"Bearer {payload['auth']['accessToken']}",
            "X-DreamJourney-QA-Owner-Truth": "1",
        }

    @staticmethod
    def _start_path(vault_id: str) -> str:
        return f"/v2/vaults/{vault_id}/interview-sessions"

    @staticmethod
    def _append_path(vault_id: str, session_id: str) -> str:
        return f"/v2/vaults/{vault_id}/interview-sessions/{session_id}/messages"

    @staticmethod
    def _state_path(vault_id: str, session_id: str) -> str:
        return f"/v2/vaults/{vault_id}/interview-sessions/{session_id}/state"

    @staticmethod
    def _read_path(vault_id: str, session_id: str) -> str:
        return (
            f"/v2/vaults/{vault_id}/interview-sessions/"
            f"{session_id}/orchestration/read"
        )

    @staticmethod
    def _signals(**overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "topicIncomplete": False,
            "needsClarification": False,
            "userChangedTopic": False,
            "userReopenedDoNotAskTopic": False,
            "isSensitive": False,
            "acceptedBroadenRecommendation": False,
        }
        payload.update(overrides)
        return payload

    def _start_session(
        self,
        *,
        vault_id: str,
        headers: dict[str, str],
    ) -> tuple[str, str]:
        thread_id = str(uuid4())
        session_id = str(uuid4())
        response = client.post(
            self._start_path(vault_id),
            headers=headers,
            json={
                "commandId": str(uuid4()),
                "threadId": thread_id,
                "sessionId": session_id,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return thread_id, session_id

    def test_contract_is_default_hidden(self) -> None:
        _, headers = self._login("13800139701")
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = False

        response = client.post(
            self._read_path("vault-interview-orchestration-hidden", str(uuid4())),
            headers=headers,
            json=self._signals(),
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json()["detail"]["code"],
            "ownerTruthCandidateReviewUnavailable",
        )

    def test_owner_reads_value_free_policy_without_mutating_private_session(self) -> None:
        _, headers = self._login("13800139702")
        vault_id = "vault-interview-orchestration-owner"
        thread_id, session_id = self._start_session(vault_id=vault_id, headers=headers)
        private_text = "只属于本人、不能进入访谈编排读取回执的叙述。"
        appended = client.post(
            self._append_path(vault_id, session_id),
            headers=headers,
            json={
                "commandId": str(uuid4()),
                "threadId": thread_id,
                "messageId": str(uuid4()),
                "expectedThreadVersion": 1,
                "expectedSessionVersion": 1,
                "text": private_text,
            },
        )
        self.assertEqual(appended.status_code, 201, appended.text)
        state_before = client.get(self._state_path(vault_id, session_id), headers=headers)
        self.assertEqual(state_before.status_code, 200, state_before.text)

        response = client.post(
            self._read_path(vault_id, session_id),
            headers=headers,
            json=self._signals(userChangedTopic=True),
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        payload = response.json()
        self.assertEqual(
            payload["schemaVersion"],
            "owner-truth-interview-session-orchestration-read-response-v1",
        )
        self.assertEqual(payload["vaultId"], vault_id)
        orchestration = payload["orchestration"]
        self.assertEqual(
            orchestration["schemaVersion"],
            "owner-truth-interview-session-orchestration-v1",
        )
        self.assertEqual(orchestration["decision"]["action"], "pause")
        self.assertEqual(orchestration["decision"]["reasonCode"], "topicChanged")
        self.assertEqual(orchestration["decision"]["nextSessionState"], "paused")
        self.assertEqual(orchestration["persistedSession"]["state"], "active")
        self.assertEqual(orchestration["persistedSession"]["ownerTurnCount"], 1)
        self.assertEqual(
            orchestration["transientSignals"],
            "opaqueTopicAndBooleanPolicySignalsOnly",
        )
        rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        for forbidden in (
            private_text,
            thread_id,
            session_id,
            "topicId",
            "ownerSubjectId",
            "pendingReviewBatchId",
            "candidateId",
            "memory",
            "provider",
        ):
            self.assertNotIn(forbidden, rendered)

        state_after = client.get(self._state_path(vault_id, session_id), headers=headers)
        self.assertEqual(state_after.status_code, 200, state_after.text)
        self.assertEqual(state_after.json(), state_before.json())

    def test_reopened_do_not_ask_topic_requires_confirmation_without_restoring_session(self) -> None:
        owner_id, headers = self._login("13800139705")
        vault_id = "vault-interview-orchestration-do-not-ask"
        thread_id, session_id = self._start_session(vault_id=vault_id, headers=headers)
        context = OwnerTruthCommandContext(
            vault_id=vault_id,
            owner_subject_id=owner_id,
            actor_subject_id=owner_id,
        )
        with self.store.request_unit_of_work(
            correlation_id=f"orchestration-do-not-ask:{vault_id}:{session_id}",
            command_id="seed-interview-orchestration-do-not-ask",
        ):
            OwnerTruthConversationService(
                self.store.owner_truth_conversation_repository()
            ).set_boundary(
                command=SetInterviewBoundaryCommand(
                    command_id="seed-interview-orchestration-do-not-ask-boundary",
                    thread_id=thread_id,
                    session_id=session_id,
                    expected_session_version=1,
                    boundary=InterviewBoundary.DO_NOT_ASK,
                ),
                context=context,
            )

        response = client.post(
            self._read_path(vault_id, session_id),
            headers=headers,
            json=self._signals(userReopenedDoNotAskTopic=True),
        )

        self.assertEqual(response.status_code, 200, response.text)
        orchestration = response.json()["orchestration"]
        self.assertEqual(orchestration["decision"]["action"], "clarify")
        self.assertEqual(
            orchestration["decision"]["reasonCode"],
            "doNotAskRestoreConfirmationRequired",
        )
        self.assertEqual(orchestration["decision"]["nextSessionState"], "paused")
        self.assertEqual(orchestration["persistedSession"]["boundary"], "doNotAsk")
        self.assertEqual(orchestration["persistedSession"]["state"], "paused")
        self.assertEqual(
            self.store.owner_truth_conversation_repository().snapshot(vault_id=vault_id)[
                "candidateCount"
            ],
            0,
        )

    def test_owner_accepts_legacy_signal_payload_and_rejects_unknown_or_non_boolean_fields(
        self,
    ) -> None:
        _, owner_headers = self._login("13800139703")
        vault_id = "vault-interview-orchestration-controls"
        _, session_id = self._start_session(vault_id=vault_id, headers=owner_headers)
        _, other_headers = self._login("13800139704")

        denied = client.post(
            self._read_path(vault_id, session_id),
            headers=other_headers,
            json=self._signals(),
        )
        legacy_payload = self._signals()
        legacy_payload.pop("userReopenedDoNotAskTopic")
        legacy = client.post(
            self._read_path(vault_id, session_id),
            headers=owner_headers,
            json=legacy_payload,
        )
        malformed = client.post(
            self._read_path(vault_id, session_id),
            headers=owner_headers,
            json=self._signals(topicId="client-cannot-send-topic"),
        )
        non_boolean = client.post(
            self._read_path(vault_id, session_id),
            headers=owner_headers,
            json=self._signals(needsClarification="true"),
        )

        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(
            denied.json()["detail"]["code"],
            "ownerTruthInterviewOrchestrationDenied",
        )
        self.assertEqual(legacy.status_code, 200, legacy.text)
        self.assertEqual(legacy.json()["orchestration"]["decision"]["action"], "listen")
        self.assertEqual(malformed.status_code, 400, malformed.text)
        self.assertEqual(
            malformed.json()["detail"]["code"],
            "ownerTruthInterviewOrchestrationInvalid",
        )
        self.assertEqual(non_boolean.status_code, 400, non_boolean.text)
        self.assertEqual(
            non_boolean.json()["detail"]["code"],
            "ownerTruthInterviewOrchestrationInvalid",
        )


if __name__ == "__main__":
    unittest.main()
