from __future__ import annotations

import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main_module
from app.domain.owner_truth.conversation import (
    InterviewBoundary,
    SetInterviewBoundaryCommand,
    StartInterviewSessionCommand,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.main import app
from app.services.in_memory_store import InMemoryStore
from app.services.owner_truth_conversation import OwnerTruthConversationService


client = TestClient(app)


class OwnerTruthInterviewSessionStateAPITests(unittest.TestCase):
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
                "nickname": "访谈会话状态测试",
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

    def _seed_paused_session(
        self,
        *,
        vault_id: str,
        owner_subject_id: str,
    ) -> str:
        thread_id = str(uuid4())
        session_id = str(uuid4())
        context = OwnerTruthCommandContext(
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            actor_subject_id=owner_subject_id,
        )
        with self.store.request_unit_of_work(
            correlation_id=f"session-state-test:{vault_id}:{session_id}",
            command_id="seed-interview-session-state",
        ):
            service = OwnerTruthConversationService(
                self.store.owner_truth_conversation_repository()
            )
            service.start_session(
                command=StartInterviewSessionCommand(
                    command_id="seed-interview-session",
                    thread_id=thread_id,
                    session_id=session_id,
                    expected_thread_version=0,
                    entry_mode="naturalInput",
                ),
                context=context,
            )
            service.set_boundary(
                command=SetInterviewBoundaryCommand(
                    command_id="seed-interview-session-boundary",
                    thread_id=thread_id,
                    session_id=session_id,
                    expected_session_version=1,
                    boundary=InterviewBoundary.DO_NOT_ASK,
                ),
                context=context,
            )
        return session_id

    @staticmethod
    def _read_path(vault_id: str, session_id: str) -> str:
        return f"/v2/vaults/{vault_id}/interview-sessions/{session_id}/state"

    def test_contract_is_default_hidden(self) -> None:
        owner_id, headers = self._login("13800139501")
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = False

        response = client.get(
            self._read_path("vault-hidden-session", str(uuid4())),
            headers=headers,
        )

        self.assertTrue(owner_id.startswith("user_"))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json()["detail"]["code"],
            "ownerTruthCandidateReviewUnavailable",
        )

    def test_owner_can_read_value_minimized_interview_state_without_message_content(self) -> None:
        owner_id, headers = self._login("13800139502")
        vault_id = "vault-interview-session-state"
        session_id = self._seed_paused_session(
            vault_id=vault_id,
            owner_subject_id=owner_id,
        )

        response = client.get(self._read_path(vault_id, session_id), headers=headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        payload = response.json()
        self.assertEqual(
            payload["schemaVersion"],
            "owner-truth-interview-session-state-read-v1",
        )
        self.assertEqual(payload["vaultId"], vault_id)
        self.assertEqual(
            payload["session"],
            {
                "state": "paused",
                "boundary": "doNotAsk",
                "rowVersion": 2,
                "threadVersion": 1,
                "ownerTurnCount": 0,
                "deepeningTurnCount": 0,
                "candidateBatchTurnCount": 0,
                "fatigue": "normal",
                "hasPendingReviewBatch": False,
                "authorityEpoch": 0,
            },
        )
        for forbidden in (
            "sessionId",
            "threadId",
            "ownerSubjectId",
            "pendingReviewBatchId",
            "message",
            "source",
            "memory",
        ):
            self.assertNotIn(forbidden, payload["session"])

    def test_other_owner_cannot_read_session_state(self) -> None:
        owner_id, _ = self._login("13800139503")
        vault_id = "vault-interview-session-owner-boundary"
        session_id = self._seed_paused_session(
            vault_id=vault_id,
            owner_subject_id=owner_id,
        )
        _, other_headers = self._login("13800139504")

        response = client.get(self._read_path(vault_id, session_id), headers=other_headers)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json()["detail"]["code"],
            "ownerTruthInterviewSessionDenied",
        )


if __name__ == "__main__":
    unittest.main()
