from __future__ import annotations

import json
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.services.in_memory_store import InMemoryStore


client = TestClient(app)


class OwnerTruthInterviewInputAPITests(unittest.TestCase):
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
                "nickname": "访谈自然输入测试",
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

    def _start_session(
        self,
        *,
        vault_id: str,
        headers: dict[str, str],
        command_id: str | None = None,
        thread_id: str | None = None,
        session_id: str | None = None,
    ):
        return client.post(
            self._start_path(vault_id),
            headers=headers,
            json={
                "commandId": command_id or str(uuid4()),
                "threadId": thread_id or str(uuid4()),
                "sessionId": session_id or str(uuid4()),
            },
        )

    def test_contract_is_default_hidden(self) -> None:
        _, headers = self._login("13800139601")
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = False

        response = self._start_session(
            vault_id="vault-interview-input-hidden",
            headers=headers,
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json()["detail"]["code"],
            "ownerTruthCandidateReviewUnavailable",
        )

    def test_owner_can_start_and_append_without_receipt_echoing_message_content(self) -> None:
        owner_id, headers = self._login("13800139602")
        vault_id = "vault-interview-input-owner"
        thread_id = str(uuid4())
        session_id = str(uuid4())
        start = self._start_session(
            vault_id=vault_id,
            headers=headers,
            thread_id=thread_id,
            session_id=session_id,
        )

        self.assertTrue(owner_id.startswith("user_"))
        self.assertEqual(start.status_code, 201)
        self.assertEqual(start.headers["cache-control"], "no-store")
        self.assertEqual(
            start.json(),
            {
                "schemaVersion": "owner-truth-interview-session-command-v1",
                "vaultId": vault_id,
                "receipt": {
                    "status": "created",
                    "threadId": thread_id,
                    "sessionId": session_id,
                    "threadVersion": 1,
                    "sessionVersion": 1,
                    "state": "active",
                    "boundary": "open",
                },
            },
        )

        text = "小时候下雨天，我会在院子里听家人讲故事。"
        append_command_id = str(uuid4())
        message_id = str(uuid4())
        append = client.post(
            self._append_path(vault_id, session_id),
            headers=headers,
            json={
                "commandId": append_command_id,
                "threadId": thread_id,
                "messageId": message_id,
                "expectedThreadVersion": 1,
                "expectedSessionVersion": 1,
                "text": text,
            },
        )

        self.assertEqual(append.status_code, 201)
        self.assertEqual(append.headers["cache-control"], "no-store")
        payload = append.json()
        self.assertEqual(
            payload,
            {
                "schemaVersion": "owner-truth-interview-session-command-v1",
                "vaultId": vault_id,
                "receipt": {
                    "status": "created",
                    "threadId": thread_id,
                    "sessionId": session_id,
                    "threadVersion": 2,
                    "sessionVersion": 2,
                    "state": "active",
                    "boundary": "open",
                    "messageId": message_id,
                    "messageSequence": 1,
                },
            },
        )
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        self.assertNotIn(text, serialized)
        for forbidden in ("candidate", "memory", "source", "authorityEffects"):
            self.assertNotIn(forbidden, serialized)

        replay = client.post(
            self._append_path(vault_id, session_id),
            headers=headers,
            json={
                "commandId": append_command_id,
                "threadId": thread_id,
                "messageId": message_id,
                "expectedThreadVersion": 1,
                "expectedSessionVersion": 1,
                "text": text,
            },
        )
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()["receipt"]["status"], "deduplicated")
        self.assertEqual(replay.json()["receipt"]["threadVersion"], 2)
        self.assertEqual(replay.json()["receipt"]["sessionVersion"], 2)

    def test_other_owner_and_stale_versions_cannot_append(self) -> None:
        owner_id, owner_headers = self._login("13800139603")
        vault_id = "vault-interview-input-boundary"
        thread_id = str(uuid4())
        session_id = str(uuid4())
        start = self._start_session(
            vault_id=vault_id,
            headers=owner_headers,
            thread_id=thread_id,
            session_id=session_id,
        )
        self.assertEqual(start.status_code, 201)
        _, other_headers = self._login("13800139604")

        other = client.post(
            self._append_path(vault_id, session_id),
            headers=other_headers,
            json={
                "commandId": str(uuid4()),
                "threadId": thread_id,
                "messageId": str(uuid4()),
                "expectedThreadVersion": 1,
                "expectedSessionVersion": 1,
                "text": "另一位用户不能写入。",
            },
        )
        self.assertEqual(other.status_code, 403)
        self.assertEqual(
            other.json()["detail"]["code"],
            "ownerTruthInterviewSessionDenied",
        )

        stale = client.post(
            self._append_path(vault_id, session_id),
            headers=owner_headers,
            json={
                "commandId": str(uuid4()),
                "threadId": thread_id,
                "messageId": str(uuid4()),
                "expectedThreadVersion": 9,
                "expectedSessionVersion": 9,
                "text": "旧版本不得覆盖会话。",
            },
        )
        self.assertTrue(owner_id.startswith("user_"))
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(
            stale.json()["detail"]["code"],
            "ownerTruthInterviewSessionConflict",
        )


if __name__ == "__main__":
    unittest.main()
