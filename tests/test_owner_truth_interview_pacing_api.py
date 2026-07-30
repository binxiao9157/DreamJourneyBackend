from __future__ import annotations

import json
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.services.in_memory_store import InMemoryStore


client = TestClient(app)


class OwnerTruthInterviewPacingAPITests(unittest.TestCase):
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
                "nickname": "访谈节奏状态测试",
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
    def _pacing_path(vault_id: str, session_id: str) -> str:
        return f"/v2/vaults/{vault_id}/interview-sessions/{session_id}/pacing"

    @staticmethod
    def _read_path(vault_id: str, session_id: str) -> str:
        return (
            f"/v2/vaults/{vault_id}/interview-sessions/"
            f"{session_id}/orchestration/read"
        )

    @staticmethod
    def _state_path(vault_id: str, session_id: str) -> str:
        return f"/v2/vaults/{vault_id}/interview-sessions/{session_id}/state"

    @staticmethod
    def _signals() -> dict[str, bool]:
        return {
            "topicIncomplete": True,
            "needsClarification": False,
            "userChangedTopic": False,
            "isSensitive": False,
            "acceptedBroadenRecommendation": False,
        }

    def _start_session(
        self,
        *,
        vault_id: str,
        headers: dict[str, str],
    ) -> tuple[str, str, int]:
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
        return thread_id, session_id, response.json()["receipt"]["sessionVersion"]

    @staticmethod
    def _pacing_payload(
        *,
        command_id: str,
        thread_id: str,
        expected_session_version: int,
        event: str,
    ) -> dict[str, object]:
        return {
            "commandId": command_id,
            "threadId": thread_id,
            "expectedSessionVersion": expected_session_version,
            "event": event,
        }

    def test_contract_is_default_hidden(self) -> None:
        _, headers = self._login("13800139801")
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = False

        response = client.post(
            self._pacing_path("vault-interview-pacing-hidden", str(uuid4())),
            headers=headers,
            json=self._pacing_payload(
                command_id=str(uuid4()),
                thread_id=str(uuid4()),
                expected_session_version=1,
                event="deepeningCompleted",
            ),
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json()["detail"]["code"],
            "ownerTruthCandidateReviewUnavailable",
        )

    def test_owner_records_bounded_pacing_then_summary_resets_follow_up_budget(self) -> None:
        _, headers = self._login("13800139802")
        vault_id = "vault-interview-pacing-owner"
        thread_id, session_id, session_version = self._start_session(
            vault_id=vault_id,
            headers=headers,
        )

        for index in range(4):
            response = client.post(
                self._pacing_path(vault_id, session_id),
                headers=headers,
                json=self._pacing_payload(
                    command_id=str(uuid4()),
                    thread_id=thread_id,
                    expected_session_version=session_version,
                    event="deepeningCompleted",
                ),
            )
            self.assertEqual(response.status_code, 201, response.text)
            session_version = response.json()["receipt"]["sessionVersion"]
            self.assertEqual(response.headers["cache-control"], "no-store")

        summary_recommendation = client.post(
            self._read_path(vault_id, session_id),
            headers=headers,
            json=self._signals(),
        )
        self.assertEqual(summary_recommendation.status_code, 200, summary_recommendation.text)
        self.assertEqual(
            summary_recommendation.json()["orchestration"]["decision"]["action"],
            "summarize",
        )

        summary = client.post(
            self._pacing_path(vault_id, session_id),
            headers=headers,
            json=self._pacing_payload(
                command_id=str(uuid4()),
                thread_id=thread_id,
                expected_session_version=session_version,
                event="summaryCompleted",
            ),
        )
        self.assertEqual(summary.status_code, 201, summary.text)

        state = client.get(self._state_path(vault_id, session_id), headers=headers)
        self.assertEqual(state.status_code, 200, state.text)
        self.assertEqual(state.json()["session"]["deepeningTurnCount"], 0)
        rendered = json.dumps(summary.json(), ensure_ascii=False, sort_keys=True)
        for forbidden in ("candidate", "memory", "provider", "text"):
            self.assertNotIn(forbidden, rendered)
        self.assertNotIn("messageId", summary.json()["receipt"])
        self.assertNotIn("messageSequence", summary.json()["receipt"])
        conversation_snapshot = self.store.owner_truth_conversation_repository().snapshot(
            vault_id=vault_id
        )
        self.assertEqual(conversation_snapshot["candidateCount"], 0)
        self.assertEqual(conversation_snapshot["memoryVersionCount"], 0)

        deepen_again = client.post(
            self._read_path(vault_id, session_id),
            headers=headers,
            json=self._signals(),
        )
        self.assertEqual(deepen_again.status_code, 200, deepen_again.text)
        self.assertEqual(
            deepen_again.json()["orchestration"]["decision"]["action"],
            "deepen",
        )

    def test_requires_exact_payload_replays_idempotently_and_rejects_other_owner(self) -> None:
        _, owner_headers = self._login("13800139803")
        vault_id = "vault-interview-pacing-controls"
        thread_id, session_id, session_version = self._start_session(
            vault_id=vault_id,
            headers=owner_headers,
        )
        command_id = str(uuid4())
        payload = self._pacing_payload(
            command_id=command_id,
            thread_id=thread_id,
            expected_session_version=session_version,
            event="deepeningCompleted",
        )
        created = client.post(
            self._pacing_path(vault_id, session_id),
            headers=owner_headers,
            json=payload,
        )
        replayed = client.post(
            self._pacing_path(vault_id, session_id),
            headers=owner_headers,
            json=payload,
        )
        _, other_headers = self._login("13800139804")
        denied = client.post(
            self._pacing_path(vault_id, session_id),
            headers=other_headers,
            json=self._pacing_payload(
                command_id=str(uuid4()),
                thread_id=thread_id,
                expected_session_version=created.json()["receipt"]["sessionVersion"],
                event="deepeningCompleted",
            ),
        )
        malformed = client.post(
            self._pacing_path(vault_id, session_id),
            headers=owner_headers,
            json={**payload, "topicId": "client-cannot-send-topic"},
        )
        unsupported_event = client.post(
            self._pacing_path(vault_id, session_id),
            headers=owner_headers,
            json=self._pacing_payload(
                command_id=str(uuid4()),
                thread_id=thread_id,
                expected_session_version=created.json()["receipt"]["sessionVersion"],
                event="fatigueGuarded",
            ),
        )
        summary_too_early = client.post(
            self._pacing_path(vault_id, session_id),
            headers=owner_headers,
            json=self._pacing_payload(
                command_id=str(uuid4()),
                thread_id=thread_id,
                expected_session_version=created.json()["receipt"]["sessionVersion"],
                event="summaryCompleted",
            ),
        )

        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(replayed.status_code, 200, replayed.text)
        self.assertEqual(created.json()["receipt"]["status"], "created")
        self.assertEqual(replayed.json()["receipt"]["status"], "deduplicated")
        self.assertEqual(
            {key: value for key, value in created.json()["receipt"].items() if key != "status"},
            {key: value for key, value in replayed.json()["receipt"].items() if key != "status"},
        )
        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(denied.json()["detail"]["code"], "ownerTruthInterviewSessionDenied")
        self.assertEqual(malformed.status_code, 400, malformed.text)
        self.assertEqual(malformed.json()["detail"]["code"], "ownerTruthInterviewSessionInvalid")
        self.assertEqual(unsupported_event.status_code, 400, unsupported_event.text)
        self.assertEqual(unsupported_event.json()["detail"]["code"], "ownerTruthInterviewSessionInvalid")
        self.assertEqual(summary_too_early.status_code, 409, summary_too_early.text)
        self.assertEqual(summary_too_early.json()["detail"]["code"], "ownerTruthInterviewSessionConflict")


if __name__ == "__main__":
    unittest.main()
