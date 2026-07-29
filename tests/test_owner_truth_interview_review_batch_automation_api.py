from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.services.in_memory_store import InMemoryStore


client = TestClient(app)


class OwnerTruthInterviewReviewBatchAutomationAPITests(unittest.TestCase):
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
            json={"phone": phone, "nickname": "批次自动化 QA", "password": "password123"},
        )
        if response.status_code != 200:
            raise AssertionError(response.text)
        body = response.json()
        return str(body["user"]["id"]), {
            "Authorization": f"Bearer {body['auth']['accessToken']}",
            "X-DreamJourney-QA-Owner-Truth": "1",
        }

    @staticmethod
    def _session_path(vault_id: str, session_id: str) -> str:
        return f"/v2/vaults/{vault_id}/interview-sessions/{session_id}"

    def _start(self, *, vault_id: str, headers: dict[str, str]) -> tuple[str, str, int, int]:
        thread_id = str(uuid4())
        session_id = str(uuid4())
        response = client.post(
            f"/v2/vaults/{vault_id}/interview-sessions",
            headers=headers,
            json={
                "commandId": str(uuid4()),
                "threadId": thread_id,
                "sessionId": session_id,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        receipt = response.json()["receipt"]
        return (
            thread_id,
            session_id,
            int(receipt["threadVersion"]),
            int(receipt["sessionVersion"]),
        )

    def _append(
        self,
        *,
        vault_id: str,
        session_id: str,
        thread_id: str,
        thread_version: int,
        session_version: int,
        index: int,
        headers: dict[str, str],
        command_id: str | None = None,
        message_id: str | None = None,
    ) -> tuple[dict[str, object], str, str]:
        command_id = command_id or str(uuid4())
        message_id = message_id or str(uuid4())
        private_message = f"自动批次 API 私有叙述 {index + 1}，不得进入响应。"
        response = client.post(
            f"{self._session_path(vault_id, session_id)}/messages",
            headers=headers,
            json={
                "commandId": command_id,
                "threadId": thread_id,
                "messageId": message_id,
                "expectedThreadVersion": thread_version,
                "expectedSessionVersion": session_version,
                "text": private_message,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertNotIn(private_message, json.dumps(body, ensure_ascii=False))
        return body, command_id, message_id

    def test_fifth_qa_narrative_creates_one_value_minimized_review_batch(self) -> None:
        _owner_id, headers = self._login("13800139731")
        vault_id = "vault-review-batch-automation-api"
        thread_id, session_id, thread_version, session_version = self._start(
            vault_id=vault_id,
            headers=headers,
        )

        fifth_body: dict[str, object] | None = None
        fifth_command_id = ""
        fifth_message_id = ""
        fifth_expected_thread_version = 0
        fifth_expected_session_version = 0
        for index in range(5):
            expected_thread_version = thread_version
            expected_session_version = session_version
            body, command_id, message_id = self._append(
                vault_id=vault_id,
                session_id=session_id,
                thread_id=thread_id,
                thread_version=thread_version,
                session_version=session_version,
                index=index,
                headers=headers,
            )
            receipt = body["receipt"]
            self.assertIsInstance(receipt, dict)
            thread_version = int(receipt["threadVersion"])
            session_version = int(receipt["sessionVersion"])
            if index < 4:
                self.assertNotIn("reviewBatchAutomation", body)
            fifth_body = body
            fifth_command_id = command_id
            fifth_message_id = message_id
            fifth_expected_thread_version = expected_thread_version
            fifth_expected_session_version = expected_session_version

        assert fifth_body is not None
        automation = fifth_body["reviewBatchAutomation"]
        self.assertIsInstance(automation, dict)
        self.assertEqual(automation["state"], "created")
        self.assertTrue(automation["reviewBatchCreated"])
        self.assertEqual(automation["sessionVersion"], 7)
        self.assertEqual(automation["reviewBatch"]["trigger"], "turnThreshold")
        self.assertEqual(automation["reviewBatch"]["capturedCandidateBatchTurnCount"], 5)
        self.assertNotIn("自动批次 API 私有叙述", json.dumps(automation, ensure_ascii=False))

        sixth_body, _sixth_command_id, _sixth_message_id = self._append(
            vault_id=vault_id,
            session_id=session_id,
            thread_id=thread_id,
            thread_version=thread_version,
            session_version=session_version,
            index=5,
            headers=headers,
        )
        sixth_automation = sixth_body["reviewBatchAutomation"]
        self.assertIsInstance(sixth_automation, dict)
        self.assertEqual(sixth_automation["state"], "alreadyPending")
        self.assertEqual(
            sixth_automation["reviewBatch"]["reviewBatchId"],
            automation["reviewBatch"]["reviewBatchId"],
        )
        self.assertEqual(sixth_automation["sessionVersion"], 8)

        replay = client.post(
            f"{self._session_path(vault_id, session_id)}/messages",
            headers=headers,
            json={
                "commandId": fifth_command_id,
                "threadId": thread_id,
                "messageId": fifth_message_id,
                "expectedThreadVersion": fifth_expected_thread_version,
                "expectedSessionVersion": fifth_expected_session_version,
                "text": "自动批次 API 私有叙述 5，不得进入响应。",
            },
        )
        self.assertEqual(replay.status_code, 200, replay.text)
        replay_automation = replay.json()["reviewBatchAutomation"]
        self.assertEqual(replay_automation["state"], "alreadyPending")
        self.assertEqual(
            replay_automation["sessionVersion"],
            sixth_automation["sessionVersion"],
        )
        self.assertEqual(
            replay_automation["reviewBatch"]["reviewBatchId"],
            automation["reviewBatch"]["reviewBatchId"],
        )

    def test_non_qa_request_never_invokes_review_batch_automation(self) -> None:
        result = main_module._owner_truth_review_batch_automation_after_qa_transition(
            request=SimpleNamespace(headers={}),
            session_id=str(uuid4()),
            transition_command_id=str(uuid4()),
            context=main_module.OwnerTruthCommandContext(
                vault_id="vault-no-qa-review-batch-automation",
                owner_subject_id="owner-no-qa-review-batch-automation",
                actor_subject_id="owner-no-qa-review-batch-automation",
            ),
        )

        self.assertIsNone(result)

    def test_qa_exit_with_less_than_threshold_creates_session_exit_batch(self) -> None:
        _owner_id, headers = self._login("13800139732")
        vault_id = "vault-review-batch-automation-exit-api"
        thread_id, session_id, thread_version, session_version = self._start(
            vault_id=vault_id,
            headers=headers,
        )
        for index in range(4):
            body, _command_id, _message_id = self._append(
                vault_id=vault_id,
                session_id=session_id,
                thread_id=thread_id,
                thread_version=thread_version,
                session_version=session_version,
                index=index,
                headers=headers,
            )
            receipt = body["receipt"]
            self.assertIsInstance(receipt, dict)
            thread_version = int(receipt["threadVersion"])
            session_version = int(receipt["sessionVersion"])

        boundary = client.post(
            f"{self._session_path(vault_id, session_id)}/boundary",
            headers=headers,
            json={
                "commandId": str(uuid4()),
                "threadId": thread_id,
                "expectedSessionVersion": session_version,
                "boundary": "doNotAsk",
            },
        )
        self.assertEqual(boundary.status_code, 201, boundary.text)
        automation = boundary.json()["reviewBatchAutomation"]
        self.assertEqual(automation["state"], "created")
        self.assertEqual(automation["reviewBatch"]["trigger"], "sessionExit")
        self.assertEqual(automation["reviewBatch"]["capturedCandidateBatchTurnCount"], 4)


if __name__ == "__main__":
    unittest.main()
