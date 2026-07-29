from __future__ import annotations

import json
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.services.in_memory_store import InMemoryStore


client = TestClient(app)


class OwnerTruthInterviewReviewBatchAcknowledgementAPITests(unittest.TestCase):
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
            json={"phone": phone, "nickname": "批次确认 QA", "password": "password123"},
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

    @staticmethod
    def _acknowledgement_path(vault_id: str, review_batch_id: str) -> str:
        return (
            f"/v2/vaults/{vault_id}/interview-review-batches/"
            f"{review_batch_id}/acknowledgement"
        )

    def _start_and_create_pending_batch(
        self,
        *,
        vault_id: str,
        headers: dict[str, str],
    ) -> tuple[str, str, int, int, str, int]:
        thread_id = str(uuid4())
        session_id = str(uuid4())
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
        receipt = started.json()["receipt"]
        thread_version = int(receipt["threadVersion"])
        session_version = int(receipt["sessionVersion"])

        pending_body: dict[str, object] | None = None
        for index in range(5):
            private_text = f"批次确认 API 私有叙述 {index + 1}，不得回显。"
            appended = client.post(
                f"{self._session_path(vault_id, session_id)}/messages",
                headers=headers,
                json={
                    "commandId": str(uuid4()),
                    "threadId": thread_id,
                    "messageId": str(uuid4()),
                    "expectedThreadVersion": thread_version,
                    "expectedSessionVersion": session_version,
                    "text": private_text,
                },
            )
            self.assertEqual(appended.status_code, 201, appended.text)
            body = appended.json()
            self.assertNotIn(private_text, json.dumps(body, ensure_ascii=False))
            updated_receipt = body["receipt"]
            thread_version = int(updated_receipt["threadVersion"])
            session_version = int(updated_receipt["sessionVersion"])
            if index == 4:
                pending_body = body

        assert pending_body is not None
        automation = pending_body["reviewBatchAutomation"]
        self.assertIsInstance(automation, dict)
        review_batch = automation["reviewBatch"]
        self.assertIsInstance(review_batch, dict)
        self.assertEqual(review_batch["state"], "pendingAcknowledgement")
        return (
            thread_id,
            session_id,
            thread_version,
            session_version,
            str(review_batch["reviewBatchId"]),
            int(review_batch["rowVersion"]),
        )

    def test_owner_can_acknowledge_pending_batch_without_creating_source_candidate_or_memory(self) -> None:
        owner_id, headers = self._login("13800139741")
        vault_id = "vault-review-batch-acknowledgement"
        (
            thread_id,
            session_id,
            _thread_version,
            session_version,
            review_batch_id,
            review_batch_version,
        ) = self._start_and_create_pending_batch(vault_id=vault_id, headers=headers)
        command_id = str(uuid4())

        response = client.post(
            self._acknowledgement_path(vault_id, review_batch_id),
            headers=headers,
            json={
                "commandId": command_id,
                "threadId": thread_id,
                "sessionId": session_id,
                "expectedSessionVersion": session_version,
                "expectedReviewBatchVersion": review_batch_version,
            },
        )

        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(
            body["schemaVersion"],
            "owner-truth-interview-review-batch-acknowledgement-response-v1",
        )
        self.assertEqual(body["status"], "acknowledged")
        self.assertEqual(body["session"]["sessionId"], session_id)
        self.assertEqual(body["session"]["sessionVersion"], session_version + 1)
        self.assertEqual(body["reviewBatch"]["reviewBatchId"], review_batch_id)
        self.assertEqual(body["reviewBatch"]["state"], "acknowledged")
        self.assertEqual(body["reviewBatch"]["rowVersion"], review_batch_version + 1)
        rendered = json.dumps(body, ensure_ascii=False, sort_keys=True)
        for forbidden in ("sourceId", "candidateId", "memoryVersionId", "provider", "批次确认 API 私有叙述"):
            self.assertNotIn(forbidden, rendered)
        self.assertEqual(self.store.owner_truth_source_count(vault_id), 0)

        replay = client.post(
            self._acknowledgement_path(vault_id, review_batch_id),
            headers=headers,
            json={
                "commandId": command_id,
                "threadId": thread_id,
                "sessionId": session_id,
                "expectedSessionVersion": session_version,
                "expectedReviewBatchVersion": review_batch_version,
            },
        )
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["status"], "deduplicated")
        self.assertEqual(replay.json()["reviewBatch"], body["reviewBatch"])
        self.assertEqual(self.store.owner_truth_source_count(vault_id), 0)
        self.assertTrue(owner_id.startswith("user_"))

    def test_contract_is_hidden_without_qa_gate_and_other_owner_cannot_acknowledge(self) -> None:
        _owner_id, owner_headers = self._login("13800139742")
        vault_id = "vault-review-batch-acknowledgement-access"
        (
            thread_id,
            session_id,
            _thread_version,
            session_version,
            review_batch_id,
            review_batch_version,
        ) = self._start_and_create_pending_batch(vault_id=vault_id, headers=owner_headers)
        payload = {
            "commandId": str(uuid4()),
            "threadId": thread_id,
            "sessionId": session_id,
            "expectedSessionVersion": session_version,
            "expectedReviewBatchVersion": review_batch_version,
        }

        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = False
        hidden = client.post(
            self._acknowledgement_path(vault_id, review_batch_id),
            headers=owner_headers,
            json=payload,
        )
        self.assertEqual(hidden.status_code, 404)
        self.assertEqual(hidden.json()["detail"]["code"], "ownerTruthCandidateReviewUnavailable")

        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = True
        _other_id, other_headers = self._login("13800139743")
        denied = client.post(
            self._acknowledgement_path(vault_id, review_batch_id),
            headers=other_headers,
            json=payload,
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json()["detail"]["code"], "ownerTruthInterviewSessionDenied")
        self.assertEqual(self.store.owner_truth_source_count(vault_id), 0)


if __name__ == "__main__":
    unittest.main()
