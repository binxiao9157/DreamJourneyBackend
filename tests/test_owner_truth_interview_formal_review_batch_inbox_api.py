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
        self.previous_closed_pilot_owner_ids = (
            main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS
        )
        self.previous_review_batch_automation_enabled = (
            main_module.OWNER_TRUTH_INTERVIEW_REVIEW_BATCH_AUTOMATION_ENABLED
        )
        self.previous_candidate_review_qa_enabled = (
            main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED
        )
        self.store = InMemoryStore()
        main_module.store = self.store
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = frozenset()
        main_module.OWNER_TRUTH_INTERVIEW_REVIEW_BATCH_AUTOMATION_ENABLED = False
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = False

    def tearDown(self) -> None:
        main_module.store = self.previous_store
        main_module.BACKEND_API_TOKEN = self.previous_backend_token
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = self.previous_legacy_phone_login
        main_module.AUTH_ROUTE_MODE = self.previous_route_mode
        main_module.AUTH_OWNERSHIP_MODE = self.previous_ownership_mode
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = (
            self.previous_closed_pilot_owner_ids
        )
        main_module.OWNER_TRUTH_INTERVIEW_REVIEW_BATCH_AUTOMATION_ENABLED = (
            self.previous_review_batch_automation_enabled
        )
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = (
            self.previous_candidate_review_qa_enabled
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
        owner_id = str(payload["user"]["id"])
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = frozenset(
            set(main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS) | {owner_id}
        )
        return (
            owner_id,
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
    def _with_candidate_review_capture(
        headers: dict[str, str],
        *,
        auth_session_id: str,
        decision_id: str,
    ) -> dict[str, str]:
        captured = dict(headers)
        captured.update(
            {
                "X-DreamJourney-Feature": "ownerTruthCandidateReview",
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

    @staticmethod
    def _candidate_proposal_admission_path(vault_id: str, review_batch_id: str) -> str:
        return (
            f"/v2/vaults/{vault_id}/interview-review-batches/"
            f"{review_batch_id}/candidate-proposal/admit"
        )

    @staticmethod
    def _candidate_proposal_status_path(vault_id: str, review_batch_id: str) -> str:
        return (
            f"/v2/vaults/{vault_id}/interview-review-batches/"
            f"{review_batch_id}/candidate-proposal/status"
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

    def test_formal_candidate_proposal_admission_persists_its_own_capture(self) -> None:
        _owner_id, auth_headers, auth_session_id = self._login("13800139626")
        echo_headers = self._with_echo_capture(
            auth_headers,
            auth_session_id=auth_session_id,
            decision_id="decision-formal-candidate-proposal-echo",
        )
        vault_id = "vault-formal-candidate-proposal-admission"
        thread_id, session_id, _session_version = self._create_formal_pending_batch(
            vault_id=vault_id,
            headers=echo_headers,
        )
        pending = client.get(self._pending_path(vault_id), headers=echo_headers)
        self.assertEqual(pending.status_code, 200, pending.text)
        item = pending.json()["reviewBatches"][0]
        review_batch_id = str(item["reviewBatchId"])
        acknowledged = client.post(
            self._acknowledgement_path(vault_id, review_batch_id),
            headers=echo_headers,
            json={
                "commandId": str(uuid4()),
                "threadId": thread_id,
                "sessionId": session_id,
                "expectedSessionVersion": int(item["sessionVersion"]),
                "expectedReviewBatchVersion": int(item["reviewBatchVersion"]),
            },
        )
        self.assertEqual(acknowledged.status_code, 201, acknowledged.text)
        review_batch_version = int(acknowledged.json()["reviewBatch"]["rowVersion"])
        admission_path = self._candidate_proposal_admission_path(vault_id, review_batch_id)
        status_path = self._candidate_proposal_status_path(vault_id, review_batch_id)
        command_id = str(uuid4())
        payload = {
            "commandId": command_id,
            "expectedReviewBatchVersion": review_batch_version,
        }

        missing = client.post(admission_path, headers=auth_headers, json=payload)
        self.assertEqual(missing.status_code, 403, missing.text)
        self.assertEqual(missing.json()["detail"]["code"], "release_policy_denied")
        wrong_feature = client.post(admission_path, headers=echo_headers, json=payload)
        self.assertEqual(wrong_feature.status_code, 403, wrong_feature.text)
        self.assertEqual(wrong_feature.json()["detail"]["code"], "release_policy_denied")

        missing_status = client.get(status_path, headers=auth_headers)
        self.assertEqual(missing_status.status_code, 403, missing_status.text)
        self.assertEqual(
            missing_status.json()["detail"]["code"],
            "release_policy_denied",
        )
        qa_header_only_status = client.get(
            status_path,
            headers={**auth_headers, "X-DreamJourney-QA-Owner-Truth": "1"},
        )
        self.assertEqual(qa_header_only_status.status_code, 404, qa_header_only_status.text)
        self.assertEqual(
            qa_header_only_status.json()["detail"]["code"],
            "ownerTruthCandidateReviewUnavailable",
        )

        policy_service = main_module.RELEASE_POLICY_SERVICE
        previous_visible = set(policy_service._CLOSED_PILOT_OWNER_VISIBLE)
        policy_service._CLOSED_PILOT_OWNER_VISIBLE = previous_visible | {
            "ownerTruthCandidateReview"
        }
        try:
            formal_headers = self._with_candidate_review_capture(
                auth_headers,
                auth_session_id=auth_session_id,
                decision_id="decision-formal-candidate-proposal-admit",
            )
            admitted = client.post(admission_path, headers=formal_headers, json=payload)
            self.assertEqual(admitted.status_code, 201, admitted.text)
            body = admitted.json()
            self.assertEqual(body["status"], "created")
            self.assertEqual(body["source"], {"status": "admitted", "kind": "conversation", "version": 1})
            self.assertEqual(body["candidate"], {"status": "notCreated"})
            rendered = json.dumps(body, ensure_ascii=False, sort_keys=True)
            for forbidden in (
                "正式批次收件箱私有叙述",
                "sourceId",
                "effectOperationId",
                "candidateId",
                "memoryVersionId",
                "provider",
                "authorization",
            ):
                self.assertNotIn(forbidden, rendered)

            admissions = self.store.owner_truth_interview_candidate_proposal_repository().snapshot()
            self.assertEqual(len(admissions["admissionsByBatch"]), 1)
            admission = next(iter(admissions["admissionsByBatch"].values()))
            capture = admission["authorizationCapture"]
            self.assertIsNotNone(capture)
            self.assertEqual(capture.feature, "ownerTruthCandidateReview")
            evidence = str(capture.value_minimized_payload())
            self.assertNotIn(auth_session_id, evidence)
            self.assertNotIn("decision-formal-candidate-proposal-admit", evidence)

            source = next(
                source
                for (source_vault_id, _source_id), source in self.store._owner_truth_sources.items()
                if source_vault_id == vault_id
            )
            self.assertNotIn("authorizationCapture", source["metadata"])
            self.assertNotIn("ownerTruthCandidateReview", str(source["metadata"]))
            effect = next(iter(self.store.effect_kernel_repository().snapshot().values()))["intent"]
            self.assertNotIn("ownerTruthCandidateReview", str(effect))

            status = client.get(
                status_path,
                headers=self._with_candidate_review_capture(
                    auth_headers,
                    auth_session_id=auth_session_id,
                    decision_id="decision-formal-candidate-proposal-status",
                ),
            )
            self.assertEqual(status.status_code, 200, status.text)
            self.assertEqual(status.headers["cache-control"], "no-store")
            status_body = status.json()
            self.assertEqual(
                status_body["schemaVersion"],
                "owner-truth-interview-candidate-proposal-status-v1",
            )
            self.assertEqual(status_body["candidateProposal"], {"status": "admitted"})
            self.assertEqual(status_body["source"], {"status": "admitted"})
            self.assertEqual(status_body["candidateExtraction"], {"status": "requested"})
            self.assertEqual(status_body["effectExecution"], {"status": "disabled"})
            self.assertEqual(status_body["candidateReview"], {"status": "notReady"})
            rendered_status = json.dumps(status_body, ensure_ascii=False, sort_keys=True)
            for forbidden in (
                "正式批次收件箱私有叙述",
                "sourceId",
                "effectOperationId",
                "candidateId",
                "memoryVersionId",
                "provider",
                "authorization",
            ):
                self.assertNotIn(forbidden, rendered_status)

            _other_owner_id, other_headers, other_session_id = self._login("13800139627")
            other_status = client.get(
                status_path,
                headers=self._with_candidate_review_capture(
                    other_headers,
                    auth_session_id=other_session_id,
                    decision_id="decision-formal-candidate-proposal-status-other-owner",
                ),
            )
            self.assertEqual(other_status.status_code, 403, other_status.text)
            self.assertEqual(
                other_status.json()["detail"]["code"],
                "ownerTruthInterviewCandidateProposalDenied",
            )

            replay = client.post(
                admission_path,
                headers=self._with_candidate_review_capture(
                    auth_headers,
                    auth_session_id=auth_session_id,
                    decision_id="decision-formal-candidate-proposal-retry",
                ),
                json=payload,
            )
            self.assertEqual(replay.status_code, 200, replay.text)
            self.assertEqual(replay.json()["status"], "deduplicated")

            main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = True
            qa_cross_replay = client.post(
                admission_path,
                headers={**auth_headers, "X-DreamJourney-QA-Owner-Truth": "1"},
                json=payload,
            )
            self.assertEqual(qa_cross_replay.status_code, 409, qa_cross_replay.text)
            self.assertEqual(
                qa_cross_replay.json()["detail"]["code"],
                "ownerTruthInterviewCandidateProposalConflict",
            )
        finally:
            policy_service._CLOSED_PILOT_OWNER_VISIBLE = previous_visible


if __name__ == "__main__":
    unittest.main()
