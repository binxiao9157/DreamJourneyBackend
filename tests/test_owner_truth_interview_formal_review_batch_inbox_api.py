from __future__ import annotations

import hashlib
import json
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.services.in_memory_store import InMemoryStore


client = TestClient(app)


class OwnerTruthInterviewFormalReviewBatchInboxAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = main_module.store
        self.previous_backend_token = main_module.BACKEND_API_TOKEN
        self.previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        self.previous_route_mode = main_module.AUTH_ROUTE_MODE
        self.previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
        self.previous_review_batch_automation_enabled = (
            main_module.OWNER_TRUTH_INTERVIEW_REVIEW_BATCH_AUTOMATION_ENABLED
        )
        self.store = InMemoryStore()
        main_module.store = self.store
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        main_module.OWNER_TRUTH_INTERVIEW_REVIEW_BATCH_AUTOMATION_ENABLED = False

    def tearDown(self) -> None:
        main_module.store = self.previous_store
        main_module.BACKEND_API_TOKEN = self.previous_backend_token
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = self.previous_legacy_phone_login
        main_module.AUTH_ROUTE_MODE = self.previous_route_mode
        main_module.AUTH_OWNERSHIP_MODE = self.previous_ownership_mode
        main_module.OWNER_TRUTH_INTERVIEW_REVIEW_BATCH_AUTOMATION_ENABLED = (
            self.previous_review_batch_automation_enabled
        )

    @staticmethod
    def _login(phone: str) -> tuple[str, dict[str, str], str]:
        response = client.post(
            "/auth/login",
            json={
                "phone": phone,
                "nickname": "正式批次收件箱测试",
                "password": "password123",
            },
        )
        if response.status_code != 200:
            raise AssertionError(response.text)
        payload = response.json()
        return (
            str(payload["user"]["id"]),
            {"Authorization": f"Bearer {payload['auth']['accessToken']}"},
            str(payload["auth"]["sessionId"]),
        )

    @staticmethod
    def _with_echo_capture(
        headers: dict[str, str],
        *,
        auth_session_id: str,
        decision_id: str,
    ) -> dict[str, str]:
        captured = dict(headers)
        captured.update(
            {
                "X-DreamJourney-Feature": "echoTextInput",
                "X-DreamJourney-Feature-Decision-Id": decision_id,
                "X-DreamJourney-Feature-Allowed": "true",
                "X-DreamJourney-Policy-Version": "release-policy-v1",
                "X-DreamJourney-Policy-Revision": "1",
                "X-DreamJourney-Account-Generation": hashlib.sha256(
                    auth_session_id.encode("utf-8")
                ).hexdigest()[:24],
            }
        )
        return captured

    @staticmethod
    def _start_path(vault_id: str) -> str:
        return f"/v2/vaults/{vault_id}/interview-sessions"

    @staticmethod
    def _message_path(vault_id: str, session_id: str) -> str:
        return f"/v2/vaults/{vault_id}/interview-sessions/{session_id}/messages"

    @staticmethod
    def _pending_path(vault_id: str) -> str:
        return f"/v2/vaults/{vault_id}/interview-review-batches/pending"

    @staticmethod
    def _acknowledgement_path(vault_id: str, review_batch_id: str) -> str:
        return (
            f"/v2/vaults/{vault_id}/interview-review-batches/"
            f"{review_batch_id}/acknowledgement"
        )

    def _create_formal_pending_batch(
        self,
        *,
        vault_id: str,
        headers: dict[str, str],
    ) -> tuple[str, str, int]:
        thread_id = str(uuid4())
        session_id = str(uuid4())
        started = client.post(
            self._start_path(vault_id),
            headers=headers,
            json={
                "commandId": str(uuid4()),
                "threadId": thread_id,
                "sessionId": session_id,
            },
        )
        self.assertEqual(started.status_code, 201, started.text)
        main_module.OWNER_TRUTH_INTERVIEW_REVIEW_BATCH_AUTOMATION_ENABLED = True
        thread_version = 1
        session_version = 1
        for index in range(5):
            appended = client.post(
                self._message_path(vault_id, session_id),
                headers=headers,
                json={
                    "commandId": str(uuid4()),
                    "threadId": thread_id,
                    "messageId": str(uuid4()),
                    "expectedThreadVersion": thread_version,
                    "expectedSessionVersion": session_version,
                    "text": f"正式批次收件箱私有叙述 {index + 1}。",
                },
            )
            self.assertEqual(appended.status_code, 201, appended.text)
            receipt = appended.json()["receipt"]
            thread_version = int(receipt["threadVersion"])
            session_version = int(receipt["sessionVersion"])
        self.assertEqual(thread_version, 6)
        self.assertEqual(session_version, 7)
        return thread_id, session_id, session_version

    def test_formal_pending_inbox_requires_capture_and_acknowledges_without_candidate_effects(self) -> None:
        owner_id, auth_headers, auth_session_id = self._login("13800139623")
        headers = self._with_echo_capture(
            auth_headers,
            auth_session_id=auth_session_id,
            decision_id="decision-formal-review-batch-inbox",
        )
        vault_id = "vault-formal-review-batch-inbox"
        thread_id, session_id, session_version = self._create_formal_pending_batch(
            vault_id=vault_id,
            headers=headers,
        )

        missing_capture = client.get(self._pending_path(vault_id), headers=auth_headers)
        self.assertEqual(missing_capture.status_code, 403, missing_capture.text)
        self.assertEqual(missing_capture.json()["detail"]["code"], "release_policy_denied")

        qa_header_only = client.get(
            self._pending_path(vault_id),
            headers={**auth_headers, "X-DreamJourney-QA-Owner-Truth": "1"},
        )
        self.assertEqual(qa_header_only.status_code, 403, qa_header_only.text)
        self.assertEqual(qa_header_only.json()["detail"]["code"], "release_policy_denied")

        inbox = client.get(self._pending_path(vault_id), headers=headers)
        self.assertEqual(inbox.status_code, 200, inbox.text)
        payload = inbox.json()
        self.assertEqual(
            payload["schemaVersion"],
            "owner-truth-interview-pending-review-batch-inbox-v1",
        )
        self.assertEqual(payload["vaultId"], vault_id)
        self.assertEqual(len(payload["reviewBatches"]), 1)
        item = payload["reviewBatches"][0]
        self.assertEqual(item["threadId"], thread_id)
        self.assertEqual(item["sessionId"], session_id)
        self.assertEqual(item["sessionVersion"], session_version)
        self.assertEqual(item["trigger"], "turnThreshold")
        self.assertEqual(item["capturedCandidateBatchTurnCount"], 5)
        rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("正式批次收件箱私有叙述", rendered)
        for forbidden in (
            "candidateid",
            "memoryversion",
            "sourceid",
            "providerpayload",
        ):
            self.assertNotIn(forbidden, rendered.lower())

        command_id = str(uuid4())
        acknowledgement_payload = {
            "commandId": command_id,
            "threadId": thread_id,
            "sessionId": session_id,
            "expectedSessionVersion": int(item["sessionVersion"]),
            "expectedReviewBatchVersion": int(item["reviewBatchVersion"]),
        }
        missing_acknowledgement_capture = client.post(
            self._acknowledgement_path(vault_id, str(item["reviewBatchId"])),
            headers=auth_headers,
            json=acknowledgement_payload,
        )
        self.assertEqual(
            missing_acknowledgement_capture.status_code,
            403,
            missing_acknowledgement_capture.text,
        )
        self.assertEqual(
            missing_acknowledgement_capture.json()["detail"]["code"],
            "release_policy_denied",
        )
        acknowledgement = client.post(
            self._acknowledgement_path(vault_id, str(item["reviewBatchId"])),
            headers=headers,
            json=acknowledgement_payload,
        )
        self.assertEqual(acknowledgement.status_code, 201, acknowledgement.text)
        acknowledgement_response = acknowledgement.json()
        self.assertEqual(acknowledgement_response["status"], "acknowledged")
        self.assertEqual(acknowledgement_response["candidateProposal"]["status"], "notStarted")
        self.assertEqual(acknowledgement_response["memoryActivation"]["status"], "notApplicable")
        self.assertNotIn("正式批次收件箱私有叙述", acknowledgement.text)

        replay = client.post(
            self._acknowledgement_path(vault_id, str(item["reviewBatchId"])),
            headers=headers,
            json=acknowledgement_payload,
        )
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["status"], "deduplicated")

        cleared = client.get(self._pending_path(vault_id), headers=headers)
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertEqual(cleared.json()["reviewBatches"], [])
        snapshot = self.store.owner_truth_conversation_repository().snapshot(vault_id=vault_id)
        self.assertEqual(snapshot["candidateCount"], 0)
        self.assertEqual(snapshot["memoryVersionCount"], 0)
        self.assertTrue(owner_id.startswith("user_"))

    def test_formal_pending_inbox_rejects_another_owner(self) -> None:
        _owner_id, auth_headers, auth_session_id = self._login("13800139624")
        headers = self._with_echo_capture(
            auth_headers,
            auth_session_id=auth_session_id,
            decision_id="decision-formal-review-batch-owner",
        )
        vault_id = "vault-formal-review-batch-owner"
        self._create_formal_pending_batch(vault_id=vault_id, headers=headers)

        _other_id, other_auth_headers, other_auth_session_id = self._login("13800139625")
        other_headers = self._with_echo_capture(
            other_auth_headers,
            auth_session_id=other_auth_session_id,
            decision_id="decision-formal-review-batch-other-owner",
        )
        denied = client.get(self._pending_path(vault_id), headers=other_headers)
        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(denied.json()["detail"]["code"], "ownerTruthInterviewSessionDenied")


if __name__ == "__main__":
    unittest.main()
