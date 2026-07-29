from __future__ import annotations

import json
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.services.in_memory_store import InMemoryStore


client = TestClient(app)


class OwnerTruthInterviewCandidateProposalAPITests(unittest.TestCase):
    """Exercise the real in-memory conversation -> Source admission boundary."""

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
            json={"phone": phone, "nickname": "提案入场 QA", "password": "password123"},
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

    @staticmethod
    def _admission_path(vault_id: str, review_batch_id: str) -> str:
        return (
            f"/v2/vaults/{vault_id}/interview-review-batches/"
            f"{review_batch_id}/candidate-proposal/admit"
        )

    def _start_and_create_pending_batch(
        self,
        *,
        vault_id: str,
        headers: dict[str, str],
    ) -> tuple[str, str, int, int, str, int, tuple[str, ...]]:
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
        texts: list[str] = []
        pending_batch: dict[str, object] | None = None

        for index in range(5):
            private_text = f"候选提案 API 私有叙述第 {index + 1} 条，不得回显。"
            texts.append(private_text)
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
            updated = body["receipt"]
            thread_version = int(updated["threadVersion"])
            session_version = int(updated["sessionVersion"])
            if index == 4:
                automation = body["reviewBatchAutomation"]
                self.assertIsInstance(automation, dict)
                pending_batch = automation["reviewBatch"]
                self.assertIsInstance(pending_batch, dict)

        assert pending_batch is not None
        return (
            thread_id,
            session_id,
            thread_version,
            session_version,
            str(pending_batch["reviewBatchId"]),
            int(pending_batch["rowVersion"]),
            tuple(texts),
        )

    def _acknowledge(
        self,
        *,
        vault_id: str,
        review_batch_id: str,
        thread_id: str,
        session_id: str,
        session_version: int,
        review_batch_version: int,
        headers: dict[str, str],
    ) -> tuple[int, int]:
        response = client.post(
            self._acknowledgement_path(vault_id, review_batch_id),
            headers=headers,
            json={
                "commandId": str(uuid4()),
                "threadId": thread_id,
                "sessionId": session_id,
                "expectedSessionVersion": session_version,
                "expectedReviewBatchVersion": review_batch_version,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["status"], "acknowledged")
        return int(body["session"]["sessionVersion"]), int(body["reviewBatch"]["rowVersion"])

    def test_acknowledged_batch_admits_exact_frozen_window_without_response_leakage(self) -> None:
        _owner_id, headers = self._login("13800139751")
        vault_id = "vault-interview-candidate-proposal-api"
        (
            thread_id,
            session_id,
            thread_version,
            session_version,
            review_batch_id,
            review_batch_version,
            frozen_texts,
        ) = self._start_and_create_pending_batch(vault_id=vault_id, headers=headers)
        acknowledged_session_version, acknowledged_batch_version = self._acknowledge(
            vault_id=vault_id,
            review_batch_id=review_batch_id,
            thread_id=thread_id,
            session_id=session_id,
            session_version=session_version,
            review_batch_version=review_batch_version,
            headers=headers,
        )

        later_text = "候选提案 API 第六条后续叙述，绝不能混入已确认批次。"
        later = client.post(
            f"{self._session_path(vault_id, session_id)}/messages",
            headers=headers,
            json={
                "commandId": str(uuid4()),
                "threadId": thread_id,
                "messageId": str(uuid4()),
                "expectedThreadVersion": thread_version,
                "expectedSessionVersion": acknowledged_session_version,
                "text": later_text,
            },
        )
        self.assertEqual(later.status_code, 201, later.text)

        command_id = str(uuid4())
        response = client.post(
            self._admission_path(vault_id, review_batch_id),
            headers=headers,
            json={
                "commandId": command_id,
                "expectedReviewBatchVersion": acknowledged_batch_version,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(
            body["schemaVersion"],
            "owner-truth-interview-candidate-proposal-admission-response-v1",
        )
        self.assertEqual(body["status"], "created")
        self.assertEqual(body["reviewBatch"]["reviewBatchId"], review_batch_id)
        self.assertEqual(body["source"], {"status": "admitted", "kind": "conversation", "version": 1})
        self.assertEqual(body["candidateExtraction"], {"status": "requested", "ownerMessageCount": 5})
        self.assertEqual(body["candidate"], {"status": "notCreated"})
        self.assertEqual(body["memoryActivation"], {"status": "notApplicable"})
        rendered = json.dumps(body, ensure_ascii=False, sort_keys=True)
        for forbidden in (*frozen_texts, later_text, "sourceId", "effectOperationId", "candidateId", "memoryVersionId", "provider"):
            self.assertNotIn(forbidden, rendered)
        self.assertEqual(self.store.owner_truth_source_count(vault_id), 1)
        self.assertEqual(self.store.effect_kernel_repository().record_count(), 1)

        stored_source = next(
            source
            for (stored_vault_id, _), source in self.store._owner_truth_sources.items()
            if stored_vault_id == vault_id
        )
        self.assertEqual(
            stored_source["contentPayload"]["text"],
            "\n\n".join(frozen_texts),
        )
        self.assertNotIn(later_text, stored_source["contentPayload"]["text"])

        replay = client.post(
            self._admission_path(vault_id, review_batch_id),
            headers=headers,
            json={
                "commandId": command_id,
                "expectedReviewBatchVersion": acknowledged_batch_version,
            },
        )
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["status"], "deduplicated")
        self.assertEqual(self.store.owner_truth_source_count(vault_id), 1)
        self.assertEqual(self.store.effect_kernel_repository().record_count(), 1)

    def test_pending_hidden_or_cross_owner_batch_cannot_enter_source_lane(self) -> None:
        _owner_id, owner_headers = self._login("13800139752")
        vault_id = "vault-interview-candidate-proposal-access"
        (
            thread_id,
            session_id,
            _thread_version,
            session_version,
            review_batch_id,
            review_batch_version,
            _texts,
        ) = self._start_and_create_pending_batch(vault_id=vault_id, headers=owner_headers)
        payload = {
            "commandId": str(uuid4()),
            "expectedReviewBatchVersion": review_batch_version,
        }

        pending = client.post(
            self._admission_path(vault_id, review_batch_id),
            headers=owner_headers,
            json=payload,
        )
        self.assertEqual(pending.status_code, 409, pending.text)
        self.assertEqual(
            pending.json()["detail"]["code"],
            "ownerTruthInterviewCandidateProposalConflict",
        )
        self.assertEqual(self.store.owner_truth_source_count(vault_id), 0)
        self.assertEqual(self.store.effect_kernel_repository().record_count(), 0)

        acknowledged_session_version, acknowledged_batch_version = self._acknowledge(
            vault_id=vault_id,
            review_batch_id=review_batch_id,
            thread_id=thread_id,
            session_id=session_id,
            session_version=session_version,
            review_batch_version=review_batch_version,
            headers=owner_headers,
        )
        del acknowledged_session_version

        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = False
        hidden = client.post(
            self._admission_path(vault_id, review_batch_id),
            headers=owner_headers,
            json={
                "commandId": str(uuid4()),
                "expectedReviewBatchVersion": acknowledged_batch_version,
            },
        )
        self.assertEqual(hidden.status_code, 404)
        self.assertEqual(hidden.json()["detail"]["code"], "ownerTruthCandidateReviewUnavailable")

        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = True
        _other_id, other_headers = self._login("13800139753")
        denied = client.post(
            self._admission_path(vault_id, review_batch_id),
            headers=other_headers,
            json={
                "commandId": str(uuid4()),
                "expectedReviewBatchVersion": acknowledged_batch_version,
            },
        )
        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(
            denied.json()["detail"]["code"],
            "ownerTruthInterviewCandidateProposalDenied",
        )
        self.assertEqual(self.store.owner_truth_source_count(vault_id), 0)
        self.assertEqual(self.store.effect_kernel_repository().record_count(), 0)


if __name__ == "__main__":
    unittest.main()
