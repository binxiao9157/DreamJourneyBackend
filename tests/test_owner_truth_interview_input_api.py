from __future__ import annotations

import hashlib
import json
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.in_memory_store import InMemoryStore
from app.services.owner_truth_conversation import OwnerTruthConversationService
from app.services.owner_truth_interview_decision_audit import (
    OwnerTruthInterviewDecisionAuditCommand,
)


client = TestClient(app)


class OwnerTruthInterviewInputAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = main_module.store
        self.previous_backend_token = main_module.BACKEND_API_TOKEN
        self.previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        self.previous_route_mode = main_module.AUTH_ROUTE_MODE
        self.previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
        self.previous_closed_pilot_owner_ids = (
            main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS
        )
        self.previous_qa_enabled = main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED
        self.previous_decision_audit_enabled = (
            main_module.OWNER_TRUTH_INTERVIEW_DECISION_AUDIT_ENABLED
        )
        self.previous_topic_shift_shadow_enabled = (
            main_module.OWNER_TRUTH_TOPIC_SHIFT_SHADOW_ENABLED
        )
        self.previous_topic_shift_preflight_qa_enabled = (
            main_module.OWNER_TRUTH_TOPIC_SHIFT_PREFLIGHT_QA_ENABLED
        )
        self.previous_do_not_ask_reactivation_preflight_enabled = (
            main_module.OWNER_TRUTH_DO_NOT_ASK_REACTIVATION_PREFLIGHT_ENABLED
        )
        self.previous_review_batch_automation_enabled = (
            main_module.OWNER_TRUTH_INTERVIEW_REVIEW_BATCH_AUTOMATION_ENABLED
        )
        self.store = InMemoryStore()
        main_module.store = self.store
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = frozenset()
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = True
        main_module.OWNER_TRUTH_INTERVIEW_DECISION_AUDIT_ENABLED = False
        main_module.OWNER_TRUTH_TOPIC_SHIFT_SHADOW_ENABLED = False
        main_module.OWNER_TRUTH_TOPIC_SHIFT_PREFLIGHT_QA_ENABLED = False
        main_module.OWNER_TRUTH_DO_NOT_ASK_REACTIVATION_PREFLIGHT_ENABLED = False
        main_module.OWNER_TRUTH_INTERVIEW_REVIEW_BATCH_AUTOMATION_ENABLED = False

    def tearDown(self) -> None:
        main_module.store = self.previous_store
        main_module.BACKEND_API_TOKEN = self.previous_backend_token
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = self.previous_legacy_phone_login
        main_module.AUTH_ROUTE_MODE = self.previous_route_mode
        main_module.AUTH_OWNERSHIP_MODE = self.previous_ownership_mode
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = (
            self.previous_closed_pilot_owner_ids
        )
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = self.previous_qa_enabled
        main_module.OWNER_TRUTH_INTERVIEW_DECISION_AUDIT_ENABLED = (
            self.previous_decision_audit_enabled
        )
        main_module.OWNER_TRUTH_TOPIC_SHIFT_SHADOW_ENABLED = (
            self.previous_topic_shift_shadow_enabled
        )
        main_module.OWNER_TRUTH_TOPIC_SHIFT_PREFLIGHT_QA_ENABLED = (
            self.previous_topic_shift_preflight_qa_enabled
        )
        main_module.OWNER_TRUTH_DO_NOT_ASK_REACTIVATION_PREFLIGHT_ENABLED = (
            self.previous_do_not_ask_reactivation_preflight_enabled
        )
        main_module.OWNER_TRUTH_INTERVIEW_REVIEW_BATCH_AUTOMATION_ENABLED = (
            self.previous_review_batch_automation_enabled
        )

    @staticmethod
    def _login(phone: str, *, qa: bool = True) -> tuple[str, dict[str, str], str]:
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
        owner_id = str(payload["user"]["id"])
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = frozenset(
            set(main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS) | {owner_id}
        )
        headers = {
            "Authorization": f"Bearer {payload['auth']['accessToken']}",
        }
        if qa:
            headers["X-DreamJourney-QA-Owner-Truth"] = "1"
        return owner_id, headers, payload["auth"]["sessionId"]

    @staticmethod
    def _start_path(vault_id: str) -> str:
        return f"/v2/vaults/{vault_id}/interview-sessions"

    @staticmethod
    def _current_path(vault_id: str) -> str:
        return f"/v2/vaults/{vault_id}/interview-sessions/current"

    @staticmethod
    def _append_path(vault_id: str, session_id: str) -> str:
        return f"/v2/vaults/{vault_id}/interview-sessions/{session_id}/messages"

    @staticmethod
    def _boundary_path(vault_id: str, session_id: str) -> str:
        return f"/v2/vaults/{vault_id}/interview-sessions/{session_id}/boundary"

    @staticmethod
    def _end_path(vault_id: str, session_id: str) -> str:
        return f"/v2/vaults/{vault_id}/interview-sessions/{session_id}/end"

    @staticmethod
    def _topic_switch_path(vault_id: str, session_id: str) -> str:
        return (
            f"/v2/vaults/{vault_id}/interview-sessions/"
            f"{session_id}/pause-for-topic-switch"
        )

    @staticmethod
    def _restore_do_not_ask_path(vault_id: str, session_id: str) -> str:
        return f"/v2/vaults/{vault_id}/interview-sessions/{session_id}/restore-do-not-ask"

    @staticmethod
    def _presentation_path(vault_id: str, session_id: str) -> str:
        return f"/v2/vaults/{vault_id}/interview-sessions/{session_id}/presentation"

    def _start_session(
        self,
        *,
        vault_id: str,
        headers: dict[str, str],
        command_id: str | None = None,
        thread_id: str | None = None,
        session_id: str | None = None,
        entry_mode: str | None = None,
    ):
        payload = {
            "commandId": command_id or str(uuid4()),
            "threadId": thread_id or str(uuid4()),
            "sessionId": session_id or str(uuid4()),
        }
        if entry_mode is not None:
            payload["entryMode"] = entry_mode
        return client.post(
            self._start_path(vault_id),
            headers=headers,
            json=payload,
        )

    def _set_boundary(
        self,
        *,
        vault_id: str,
        session_id: str,
        thread_id: str,
        expected_session_version: int,
        boundary: str,
        headers: dict[str, str],
        command_id: str | None = None,
        extra: dict[str, object] | None = None,
    ):
        payload: dict[str, object] = {
            "commandId": command_id or str(uuid4()),
            "threadId": thread_id,
            "expectedSessionVersion": expected_session_version,
            "boundary": boundary,
        }
        if extra:
            payload.update(extra)
        return client.post(
            self._boundary_path(vault_id, session_id),
            headers=headers,
            json=payload,
        )

    def _restore_do_not_ask(
        self,
        *,
        vault_id: str,
        session_id: str,
        thread_id: str,
        expected_session_version: int,
        headers: dict[str, str],
        command_id: str | None = None,
        confirmed: bool = True,
    ):
        return client.post(
            self._restore_do_not_ask_path(vault_id, session_id),
            headers=headers,
            json={
                "commandId": command_id or str(uuid4()),
                "threadId": thread_id,
                "expectedSessionVersion": expected_session_version,
                "confirmed": confirmed,
            },
        )

    def _end_session(
        self,
        *,
        vault_id: str,
        session_id: str,
        thread_id: str,
        expected_thread_version: int,
        expected_session_version: int,
        headers: dict[str, str],
        command_id: str | None = None,
        extra: dict[str, object] | None = None,
    ):
        payload: dict[str, object] = {
            "commandId": command_id or str(uuid4()),
            "threadId": thread_id,
            "expectedThreadVersion": expected_thread_version,
            "expectedSessionVersion": expected_session_version,
        }
        if extra:
            payload.update(extra)
        return client.post(
            self._end_path(vault_id, session_id),
            headers=headers,
            json=payload,
        )

    def _pause_for_topic_switch(
        self,
        *,
        vault_id: str,
        session_id: str,
        thread_id: str,
        expected_thread_version: int,
        expected_session_version: int,
        headers: dict[str, str],
        command_id: str | None = None,
        extra: dict[str, object] | None = None,
    ):
        payload: dict[str, object] = {
            "commandId": command_id or str(uuid4()),
            "threadId": thread_id,
            "expectedThreadVersion": expected_thread_version,
            "expectedSessionVersion": expected_session_version,
        }
        if extra:
            payload.update(extra)
        return client.post(
            self._topic_switch_path(vault_id, session_id),
            headers=headers,
            json=payload,
        )

    def test_contract_is_default_hidden(self) -> None:
        _, headers, _ = self._login("13800139601")
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

        current = client.get(
            self._current_path("vault-interview-input-hidden"),
            headers=headers,
        )
        self.assertEqual(current.status_code, 404)
        self.assertEqual(
            current.json()["detail"]["code"],
            "ownerTruthCandidateReviewUnavailable",
        )

        restore = self._restore_do_not_ask(
            vault_id="vault-interview-input-hidden",
            session_id=str(uuid4()),
            thread_id=str(uuid4()),
            expected_session_version=1,
            headers=headers,
        )
        self.assertEqual(restore.status_code, 404)
        self.assertEqual(
            restore.json()["detail"]["code"],
            "ownerTruthCandidateReviewUnavailable",
        )

        ended = self._end_session(
            vault_id="vault-interview-input-hidden",
            session_id=str(uuid4()),
            thread_id=str(uuid4()),
            expected_thread_version=1,
            expected_session_version=1,
            headers=headers,
        )
        self.assertEqual(ended.status_code, 404)
        self.assertEqual(
            ended.json()["detail"]["code"],
            "ownerTruthCandidateReviewUnavailable",
        )

        paused = self._pause_for_topic_switch(
            vault_id="vault-interview-input-hidden",
            session_id=str(uuid4()),
            thread_id=str(uuid4()),
            expected_thread_version=1,
            expected_session_version=1,
            headers=headers,
        )
        self.assertEqual(paused.status_code, 404)
        self.assertEqual(
            paused.json()["detail"]["code"],
            "ownerTruthCandidateReviewUnavailable",
        )

    def test_owner_can_end_session_once_and_create_one_session_exit_review_batch(self) -> None:
        _, headers, _ = self._login("13800139618")
        vault_id = "vault-interview-explicit-end"
        thread_id = str(uuid4())
        session_id = str(uuid4())
        started = self._start_session(
            vault_id=vault_id,
            headers=headers,
            thread_id=thread_id,
            session_id=session_id,
        )
        self.assertEqual(started.status_code, 201, started.text)
        appended = client.post(
            self._append_path(vault_id, session_id),
            headers=headers,
            json={
                "commandId": str(uuid4()),
                "threadId": thread_id,
                "messageId": str(uuid4()),
                "expectedThreadVersion": 1,
                "expectedSessionVersion": 1,
                "text": "本轮结束前的私有叙述不应出现在结束回执。",
            },
        )
        self.assertEqual(appended.status_code, 201, appended.text)

        command_id = str(uuid4())
        ended = self._end_session(
            vault_id=vault_id,
            session_id=session_id,
            thread_id=thread_id,
            expected_thread_version=2,
            expected_session_version=2,
            headers=headers,
            command_id=command_id,
        )

        self.assertEqual(ended.status_code, 201, ended.text)
        payload = ended.json()
        self.assertEqual(payload["receipt"]["status"], "created")
        self.assertEqual(payload["receipt"]["threadVersion"], 3)
        self.assertEqual(payload["receipt"]["sessionVersion"], 4)
        self.assertEqual(payload["receipt"]["state"], "ended")
        self.assertEqual(payload["reviewBatchAutomation"]["state"], "created")
        self.assertEqual(payload["reviewBatchAutomation"]["reviewBatch"]["trigger"], "sessionExit")
        self.assertEqual(
            payload["reviewBatchAutomation"]["reviewBatch"]["capturedCandidateBatchTurnCount"],
            1,
        )
        rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("本轮结束前", rendered)
        self.assertNotIn("candidate", rendered)
        self.assertNotIn("memory", rendered)

        replayed = self._end_session(
            vault_id=vault_id,
            session_id=session_id,
            thread_id=thread_id,
            expected_thread_version=2,
            expected_session_version=2,
            headers=headers,
            command_id=command_id,
        )
        self.assertEqual(replayed.status_code, 200, replayed.text)
        self.assertEqual(replayed.json()["receipt"]["status"], "deduplicated")
        self.assertEqual(replayed.json()["receipt"]["sessionVersion"], 4)
        self.assertEqual(replayed.json()["reviewBatchAutomation"]["state"], "alreadyPending")

        blocked_append = client.post(
            self._append_path(vault_id, session_id),
            headers=headers,
            json={
                "commandId": str(uuid4()),
                "threadId": thread_id,
                "messageId": str(uuid4()),
                "expectedThreadVersion": 3,
                "expectedSessionVersion": 4,
                "text": "结束后不能继续写入。",
            },
        )
        self.assertEqual(blocked_append.status_code, 409, blocked_append.text)
        self.assertEqual(
            blocked_append.json()["detail"]["code"],
            "ownerTruthInterviewSessionConflict",
        )
        current = client.get(self._current_path(vault_id), headers=headers)
        self.assertEqual(current.status_code, 200, current.text)
        self.assertIsNone(current.json()["currentSession"])

    def test_live_assistant_turn_is_context_only_and_batch_waits_for_explicit_end(self) -> None:
        owner_id, headers, _ = self._login("13800139718")
        vault_id = "vault-interview-live-context"
        thread_id = str(uuid4())
        session_id = str(uuid4())
        started = self._start_session(
            vault_id=vault_id,
            headers=headers,
            thread_id=thread_id,
            session_id=session_id,
            entry_mode="live",
        )
        self.assertEqual(started.status_code, 201, started.text)
        main_module.OWNER_TRUTH_INTERVIEW_REVIEW_BATCH_AUTOMATION_ENABLED = True

        thread_version = 1
        session_version = 1
        turns = (
            ("owner", "我小时候住在河边。"),
            ("assistant", "那条河给你留下了什么印象？"),
            ("owner", "河水很安静，我常和外公去散步。"),
            ("assistant", "和外公散步时，你最记得什么声音？"),
            ("owner", "我最记得风吹过芦苇的声音。"),
            ("owner", "夏天河边还有很多萤火虫。"),
            ("owner", "这些画面我到现在都记得。"),
        )
        for role, text in turns:
            response = client.post(
                self._append_path(vault_id, session_id),
                headers=headers,
                json={
                    "commandId": str(uuid4()),
                    "threadId": thread_id,
                    "messageId": str(uuid4()),
                    "expectedThreadVersion": thread_version,
                    "expectedSessionVersion": session_version,
                    "text": text,
                    "role": role,
                    "captureMode": "live",
                },
            )
            self.assertEqual(response.status_code, 201, response.text)
            self.assertNotIn("reviewBatchAutomation", response.json())
            receipt = response.json()["receipt"]
            thread_version = int(receipt["threadVersion"])
            session_version = int(receipt["sessionVersion"])

        context = OwnerTruthCommandContext(
            vault_id=vault_id,
            owner_subject_id=owner_id,
            actor_subject_id=owner_id,
        )
        conversation = OwnerTruthConversationService(
            self.store.owner_truth_conversation_repository()
        )
        self.assertEqual(
            conversation.list_review_batches(session_id=session_id, context=context),
            (),
        )
        state = client.get(
            f"{self._start_path(vault_id)}/{session_id}/state",
            headers=headers,
        )
        self.assertEqual(state.status_code, 200, state.text)
        self.assertEqual(state.json()["session"]["ownerTurnCount"], 5)

        ended = self._end_session(
            vault_id=vault_id,
            session_id=session_id,
            thread_id=thread_id,
            expected_thread_version=thread_version,
            expected_session_version=session_version,
            headers=headers,
            command_id=str(uuid4()),
        )
        self.assertEqual(ended.status_code, 201, ended.text)
        self.assertEqual(ended.json()["reviewBatchAutomation"]["state"], "created")
        batches = conversation.list_review_batches(session_id=session_id, context=context)
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0].captured_candidate_batch_turn_count, 5)

    def test_end_requires_owner_current_versions_and_exact_payload(self) -> None:
        _, owner_headers, _ = self._login("13800139619")
        vault_id = "vault-interview-explicit-end-controls"
        thread_id = str(uuid4())
        session_id = str(uuid4())
        started = self._start_session(
            vault_id=vault_id,
            headers=owner_headers,
            thread_id=thread_id,
            session_id=session_id,
        )
        self.assertEqual(started.status_code, 201, started.text)
        _, other_headers, _ = self._login("13800139620")

        denied = self._end_session(
            vault_id=vault_id,
            session_id=session_id,
            thread_id=thread_id,
            expected_thread_version=1,
            expected_session_version=1,
            headers=other_headers,
        )
        stale = self._end_session(
            vault_id=vault_id,
            session_id=session_id,
            thread_id=thread_id,
            expected_thread_version=9,
            expected_session_version=9,
            headers=owner_headers,
        )
        malformed = self._end_session(
            vault_id=vault_id,
            session_id=session_id,
            thread_id=thread_id,
            expected_thread_version=1,
            expected_session_version=1,
            headers=owner_headers,
            extra={"reason": "free-form end reasons are not accepted"},
        )

        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(denied.json()["detail"]["code"], "ownerTruthInterviewSessionDenied")
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["detail"]["code"], "ownerTruthInterviewSessionConflict")
        self.assertEqual(malformed.status_code, 400, malformed.text)
        self.assertEqual(malformed.json()["detail"]["code"], "ownerTruthInterviewSessionInvalid")

    def test_owner_can_pause_topic_switch_once_then_start_a_new_private_thread(self) -> None:
        _, headers, _ = self._login("13800139632")
        vault_id = "vault-interview-topic-switch"
        old_thread_id = str(uuid4())
        old_session_id = str(uuid4())
        started = self._start_session(
            vault_id=vault_id,
            headers=headers,
            thread_id=old_thread_id,
            session_id=old_session_id,
        )
        self.assertEqual(started.status_code, 201, started.text)
        private_text = "旧主题的私人叙述不能出现在切换回执里。"
        appended = client.post(
            self._append_path(vault_id, old_session_id),
            headers=headers,
            json={
                "commandId": str(uuid4()),
                "threadId": old_thread_id,
                "messageId": str(uuid4()),
                "expectedThreadVersion": 1,
                "expectedSessionVersion": 1,
                "text": private_text,
            },
        )
        self.assertEqual(appended.status_code, 201, appended.text)

        command_id = str(uuid4())
        paused = self._pause_for_topic_switch(
            vault_id=vault_id,
            session_id=old_session_id,
            thread_id=old_thread_id,
            expected_thread_version=2,
            expected_session_version=2,
            headers=headers,
            command_id=command_id,
        )
        self.assertEqual(paused.status_code, 201, paused.text)
        self.assertEqual(paused.headers["cache-control"], "no-store")
        payload = paused.json()
        self.assertEqual(payload["receipt"]["status"], "created")
        self.assertEqual(payload["receipt"]["threadId"], old_thread_id)
        self.assertEqual(payload["receipt"]["sessionId"], old_session_id)
        self.assertEqual(payload["receipt"]["threadVersion"], 3)
        self.assertEqual(payload["receipt"]["sessionVersion"], 4)
        self.assertEqual(payload["receipt"]["state"], "paused")
        self.assertEqual(payload["receipt"]["boundary"], "open")
        self.assertEqual(payload["reviewBatchAutomation"]["state"], "created")
        self.assertEqual(payload["reviewBatchAutomation"]["reviewBatch"]["trigger"], "sessionExit")
        rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        for forbidden in (
            private_text,
            "客户端不得发送主题文本",
            "topicId",
            "candidate",
            "memory",
            "source",
            "provider",
        ):
            self.assertNotIn(forbidden, rendered)

        replayed = self._pause_for_topic_switch(
            vault_id=vault_id,
            session_id=old_session_id,
            thread_id=old_thread_id,
            expected_thread_version=2,
            expected_session_version=2,
            headers=headers,
            command_id=command_id,
        )
        self.assertEqual(replayed.status_code, 200, replayed.text)
        self.assertEqual(replayed.json()["receipt"]["status"], "deduplicated")
        self.assertEqual(replayed.json()["receipt"]["sessionVersion"], 4)
        self.assertEqual(replayed.json()["reviewBatchAutomation"]["state"], "alreadyPending")

        blocked_append = client.post(
            self._append_path(vault_id, old_session_id),
            headers=headers,
            json={
                "commandId": str(uuid4()),
                "threadId": old_thread_id,
                "messageId": str(uuid4()),
                "expectedThreadVersion": 3,
                "expectedSessionVersion": 4,
                "text": "已暂停的旧主题不能继续写入。",
            },
        )
        self.assertEqual(blocked_append.status_code, 409, blocked_append.text)
        self.assertEqual(
            blocked_append.json()["detail"]["code"],
            "ownerTruthInterviewSessionConflict",
        )

        new_thread_id = str(uuid4())
        new_session_id = str(uuid4())
        new_session = self._start_session(
            vault_id=vault_id,
            headers=headers,
            thread_id=new_thread_id,
            session_id=new_session_id,
        )
        self.assertEqual(new_session.status_code, 201, new_session.text)
        self.assertEqual(new_session.json()["receipt"]["threadId"], new_thread_id)
        self.assertEqual(new_session.json()["receipt"]["sessionId"], new_session_id)

    def test_topic_switch_requires_owner_current_versions_and_exact_value_free_payload(self) -> None:
        _, owner_headers, _ = self._login("13800139633")
        vault_id = "vault-interview-topic-switch-controls"
        thread_id = str(uuid4())
        session_id = str(uuid4())
        started = self._start_session(
            vault_id=vault_id,
            headers=owner_headers,
            thread_id=thread_id,
            session_id=session_id,
        )
        self.assertEqual(started.status_code, 201, started.text)
        _, other_headers, _ = self._login("13800139634")

        denied = self._pause_for_topic_switch(
            vault_id=vault_id,
            session_id=session_id,
            thread_id=thread_id,
            expected_thread_version=1,
            expected_session_version=1,
            headers=other_headers,
        )
        stale = self._pause_for_topic_switch(
            vault_id=vault_id,
            session_id=session_id,
            thread_id=thread_id,
            expected_thread_version=9,
            expected_session_version=9,
            headers=owner_headers,
        )
        malformed = self._pause_for_topic_switch(
            vault_id=vault_id,
            session_id=session_id,
            thread_id=thread_id,
            expected_thread_version=1,
            expected_session_version=1,
            headers=owner_headers,
            extra={"topic": "客户端不得发送主题文本"},
        )

        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(denied.json()["detail"]["code"], "ownerTruthInterviewSessionDenied")
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["detail"]["code"], "ownerTruthInterviewSessionConflict")
        self.assertEqual(malformed.status_code, 400, malformed.text)
        self.assertEqual(
            malformed.json()["detail"]["code"],
            "ownerTruthInterviewSessionInvalid",
        )

    def test_owner_can_resume_only_current_active_session_without_content(self) -> None:
        _, headers, _ = self._login("13800139615")
        vault_id = "vault-interview-current-session"
        thread_id = str(uuid4())
        session_id = str(uuid4())
        start = self._start_session(
            vault_id=vault_id,
            headers=headers,
            thread_id=thread_id,
            session_id=session_id,
        )
        self.assertEqual(start.status_code, 201, start.text)

        private_text = "这段私有叙述只能留在访谈会话中。"
        append = client.post(
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
        self.assertEqual(append.status_code, 201, append.text)

        current = client.get(self._current_path(vault_id), headers=headers)

        self.assertEqual(current.status_code, 200, current.text)
        self.assertEqual(current.headers["cache-control"], "no-store")
        self.assertEqual(
            current.json(),
            {
                "schemaVersion": "owner-truth-interview-current-session-v1",
                "vaultId": vault_id,
                "currentSession": {
                    "status": "resumed",
                    "threadId": thread_id,
                    "sessionId": session_id,
                    "threadVersion": 2,
                    "sessionVersion": 2,
                    "state": "active",
                    "boundary": "open",
                    "entryMode": "naturalInput",
                },
            },
        )
        rendered = json.dumps(current.json(), ensure_ascii=False, sort_keys=True)
        for forbidden in (
            private_text,
            "candidate",
            "memory",
            "review",
            "ownerSubjectId",
            "authorityEpoch",
            "fatigue",
            "turnCount",
            "messageSequence",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_live_session_mode_is_explicit_and_invalid_modes_fail_closed(self) -> None:
        _, headers, _ = self._login("13800139721")
        vault_id = "vault-interview-live-entry-mode"
        thread_id = str(uuid4())
        session_id = str(uuid4())
        started = self._start_session(
            vault_id=vault_id,
            headers=headers,
            thread_id=thread_id,
            session_id=session_id,
            entry_mode="live",
        )
        self.assertEqual(started.status_code, 201, started.text)

        current = client.get(self._current_path(vault_id), headers=headers)
        self.assertEqual(current.status_code, 200, current.text)
        self.assertEqual(current.json()["currentSession"]["entryMode"], "live")

        invalid = self._start_session(
            vault_id="vault-interview-invalid-entry-mode",
            headers=headers,
            entry_mode="providerRealtime",
        )
        self.assertEqual(invalid.status_code, 400, invalid.text)
        self.assertEqual(
            invalid.json()["detail"]["code"],
            "ownerTruthInterviewSessionInvalid",
        )

    def test_current_session_is_owner_bound_and_ignores_paused_history(self) -> None:
        _, owner_headers, _ = self._login("13800139616")
        vault_id = "vault-interview-current-owner-boundary"
        thread_id = str(uuid4())
        session_id = str(uuid4())
        start = self._start_session(
            vault_id=vault_id,
            headers=owner_headers,
            thread_id=thread_id,
            session_id=session_id,
        )
        self.assertEqual(start.status_code, 201, start.text)

        _, other_headers, _ = self._login("13800139617")
        other = client.get(self._current_path(vault_id), headers=other_headers)
        self.assertEqual(other.status_code, 403, other.text)
        self.assertEqual(other.json()["detail"]["code"], "ownerTruthInterviewSessionDenied")

        paused = self._set_boundary(
            vault_id=vault_id,
            session_id=session_id,
            thread_id=thread_id,
            expected_session_version=1,
            boundary="doNotAsk",
            headers=owner_headers,
        )
        self.assertEqual(paused.status_code, 201, paused.text)

        current = client.get(self._current_path(vault_id), headers=owner_headers)
        self.assertEqual(current.status_code, 200, current.text)
        self.assertEqual(
            current.json(),
            {
                "schemaVersion": "owner-truth-interview-current-session-v1",
                "vaultId": vault_id,
                "currentSession": None,
            },
        )

    def test_owner_can_start_and_append_without_receipt_echoing_message_content(self) -> None:
        owner_id, headers, _ = self._login("13800139602")
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

    def test_crisis_narrative_is_interrupted_before_interview_persistence(self) -> None:
        owner_id, headers, _ = self._login("13800139614")
        vault_id = "vault-interview-safety-override"
        thread_id = str(uuid4())
        session_id = str(uuid4())
        start = self._start_session(
            vault_id=vault_id,
            headers=headers,
            thread_id=thread_id,
            session_id=session_id,
        )
        self.assertEqual(start.status_code, 201, start.text)

        crisis_text = "我真的撑不住了。"
        crisis_command_id = str(uuid4())
        crisis_message_id = str(uuid4())
        main_module.OWNER_TRUTH_INTERVIEW_DECISION_AUDIT_ENABLED = True
        interrupted = client.post(
            self._append_path(vault_id, session_id),
            headers=headers,
            json={
                "commandId": crisis_command_id,
                "threadId": thread_id,
                "messageId": crisis_message_id,
                "expectedThreadVersion": 1,
                "expectedSessionVersion": 1,
                "text": crisis_text,
            },
        )

        self.assertEqual(interrupted.status_code, 409, interrupted.text)
        self.assertEqual(interrupted.headers["cache-control"], "no-store")
        interruption = interrupted.json()
        self.assertEqual(
            interruption["schemaVersion"],
            "owner-truth-interview-safety-override-v1",
        )
        self.assertEqual(interruption["vaultId"], vault_id)
        self.assertEqual(interruption["status"], "safetyOverride")
        self.assertFalse(interruption["persisted"])
        self.assertFalse(interruption["retryable"])
        decision = interruption["safetyDecision"]
        self.assertEqual(decision["riskClass"], "highDistress")
        self.assertEqual(decision["action"], "respondWithNeutralSafetyText")
        self.assertEqual(
            decision["neutralResponse"]["message"],
            "我注意到你可能正处在危险中。请立即联系身边可信任的人；如有紧迫危险，请联系当地紧急服务。",
        )
        self.assertFalse(decision["effects"]["providerEffectsAllowed"])
        self.assertNotIn(crisis_text, interrupted.text)

        state = client.get(
            f"{self._start_path(vault_id)}/{session_id}/state",
            headers=headers,
        )
        self.assertEqual(state.status_code, 200, state.text)
        self.assertEqual(state.json()["session"]["ownerTurnCount"], 0)
        self.assertEqual(state.json()["session"]["threadVersion"], 1)
        self.assertEqual(state.json()["session"]["rowVersion"], 1)
        blocked_audit_command = OwnerTruthInterviewDecisionAuditCommand(
            command_id=(
                "owner-truth-interview-append-decision-audit:"
                f"{crisis_command_id}"
            ),
            thread_id=thread_id,
            session_id=session_id,
            message_id=crisis_message_id,
            expected_session_version=1,
        )
        self.assertIsNone(
            self.store.owner_truth_interview_decision_audit_repository().find_by_command(
                context=OwnerTruthCommandContext(
                    vault_id=vault_id,
                    owner_subject_id=owner_id,
                    actor_subject_id=owner_id,
                ),
                command_id_hash=blocked_audit_command.command_id_hash,
                request_payload_hash=blocked_audit_command.request_payload_hash,
            )
        )

        normal = client.post(
            self._append_path(vault_id, session_id),
            headers=headers,
            json={
                "commandId": str(uuid4()),
                "threadId": thread_id,
                "messageId": str(uuid4()),
                "expectedThreadVersion": 1,
                "expectedSessionVersion": 1,
                "text": "我想从小时候在院子里听故事的经历讲起。",
            },
        )
        self.assertEqual(normal.status_code, 201, normal.text)
        self.assertEqual(normal.json()["receipt"]["messageSequence"], 1)

    def test_owner_can_persist_boundary_with_idempotent_value_minimized_receipt(self) -> None:
        owner_id, headers, _ = self._login("13800139608")
        vault_id = "vault-interview-boundary-owner"
        thread_id = str(uuid4())
        session_id = str(uuid4())
        start = self._start_session(
            vault_id=vault_id,
            headers=headers,
            thread_id=thread_id,
            session_id=session_id,
        )
        self.assertEqual(start.status_code, 201)

        command_id = str(uuid4())
        response = self._set_boundary(
            vault_id=vault_id,
            session_id=session_id,
            thread_id=thread_id,
            expected_session_version=1,
            boundary="cooldown",
            headers=headers,
            command_id=command_id,
        )

        self.assertTrue(owner_id.startswith("user_"))
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(
            response.json(),
            {
                "schemaVersion": "owner-truth-interview-session-command-v1",
                "vaultId": vault_id,
                "receipt": {
                    "status": "created",
                    "threadId": thread_id,
                    "sessionId": session_id,
                    "threadVersion": 1,
                    "sessionVersion": 2,
                    "state": "paused",
                    "boundary": "cooldown",
                },
            },
        )
        serialized = json.dumps(response.json(), ensure_ascii=False, sort_keys=True)
        for forbidden in ("message", "source", "candidate", "memory", "fatigue", "text"):
            self.assertNotIn(forbidden, serialized)

        replay = self._set_boundary(
            vault_id=vault_id,
            session_id=session_id,
            thread_id=thread_id,
            expected_session_version=1,
            boundary="cooldown",
            headers=headers,
            command_id=command_id,
        )
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["receipt"]["status"], "deduplicated")
        self.assertEqual(replay.json()["receipt"]["sessionVersion"], 2)

    def test_skip_once_is_consumed_by_the_next_owner_narrative(self) -> None:
        _, headers, _ = self._login("13800139612")
        vault_id = "vault-interview-boundary-skip-once-consumed"
        thread_id = str(uuid4())
        session_id = str(uuid4())
        start = self._start_session(
            vault_id=vault_id,
            headers=headers,
            thread_id=thread_id,
            session_id=session_id,
        )
        self.assertEqual(start.status_code, 201, start.text)

        boundary = self._set_boundary(
            vault_id=vault_id,
            session_id=session_id,
            thread_id=thread_id,
            expected_session_version=1,
            boundary="skipOnce",
            headers=headers,
        )
        self.assertEqual(boundary.status_code, 201, boundary.text)
        self.assertEqual(boundary.json()["receipt"]["boundary"], "skipOnce")
        self.assertEqual(boundary.json()["receipt"]["sessionVersion"], 2)

        command_id = str(uuid4())
        message_id = str(uuid4())
        append_payload = {
            "commandId": command_id,
            "threadId": thread_id,
            "messageId": message_id,
            "expectedThreadVersion": 1,
            "expectedSessionVersion": 2,
            "text": "本轮不需要继续追问，我先补充这一段私人叙述。",
        }
        append = client.post(
            self._append_path(vault_id, session_id),
            headers=headers,
            json=append_payload,
        )

        self.assertEqual(append.status_code, 201, append.text)
        self.assertEqual(append.json()["receipt"]["state"], "active")
        self.assertEqual(append.json()["receipt"]["boundary"], "open")
        self.assertEqual(append.json()["receipt"]["sessionVersion"], 3)
        self.assertEqual(append.json()["receipt"]["messageSequence"], 1)

        replay = client.post(
            self._append_path(vault_id, session_id),
            headers=headers,
            json=append_payload,
        )
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["receipt"]["status"], "deduplicated")
        self.assertEqual(replay.json()["receipt"]["boundary"], "open")
        self.assertEqual(replay.json()["receipt"]["sessionVersion"], 3)

        presentation = client.get(
            self._presentation_path(vault_id, session_id),
            headers=headers,
        )
        self.assertEqual(presentation.status_code, 200, presentation.text)
        self.assertEqual(presentation.json()["presentation"]["state"], "narrativeRecorded")
        self.assertTrue(presentation.json()["presentation"]["canContinue"])

    def test_do_not_ask_requires_explicit_confirmation_before_the_owner_can_restore(self) -> None:
        _, headers, _ = self._login("13800139613")
        vault_id = "vault-interview-do-not-ask-restore"
        thread_id = str(uuid4())
        session_id = str(uuid4())
        start = self._start_session(
            vault_id=vault_id,
            headers=headers,
            thread_id=thread_id,
            session_id=session_id,
        )
        self.assertEqual(start.status_code, 201, start.text)

        paused = self._set_boundary(
            vault_id=vault_id,
            session_id=session_id,
            thread_id=thread_id,
            expected_session_version=1,
            boundary="doNotAsk",
            headers=headers,
        )
        self.assertEqual(paused.status_code, 201, paused.text)
        self.assertEqual(paused.json()["receipt"]["state"], "paused")
        self.assertEqual(paused.json()["receipt"]["boundary"], "doNotAsk")

        unconfirmed = self._restore_do_not_ask(
            vault_id=vault_id,
            session_id=session_id,
            thread_id=thread_id,
            expected_session_version=2,
            headers=headers,
            confirmed=False,
        )
        self.assertEqual(unconfirmed.status_code, 400, unconfirmed.text)
        self.assertEqual(
            unconfirmed.json()["detail"]["code"],
            "ownerTruthInterviewSessionInvalid",
        )

        command_id = str(uuid4())
        restored = self._restore_do_not_ask(
            vault_id=vault_id,
            session_id=session_id,
            thread_id=thread_id,
            expected_session_version=2,
            headers=headers,
            command_id=command_id,
        )
        self.assertEqual(restored.status_code, 201, restored.text)
        self.assertEqual(restored.headers["cache-control"], "no-store")
        self.assertEqual(
            restored.json()["receipt"],
            {
                "status": "created",
                "threadId": thread_id,
                "sessionId": session_id,
                "threadVersion": 1,
                "sessionVersion": 3,
                "state": "active",
                "boundary": "open",
            },
        )

        replay = self._restore_do_not_ask(
            vault_id=vault_id,
            session_id=session_id,
            thread_id=thread_id,
            expected_session_version=2,
            headers=headers,
            command_id=command_id,
        )
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["receipt"]["status"], "deduplicated")
        self.assertEqual(replay.json()["receipt"]["boundary"], "open")
        self.assertEqual(replay.json()["receipt"]["sessionVersion"], 3)

    def test_boundary_requires_owner_current_version_and_supported_control(self) -> None:
        owner_id, owner_headers, _ = self._login("13800139609")
        vault_id = "vault-interview-boundary-controls"
        thread_id = str(uuid4())
        session_id = str(uuid4())
        start = self._start_session(
            vault_id=vault_id,
            headers=owner_headers,
            thread_id=thread_id,
            session_id=session_id,
        )
        self.assertEqual(start.status_code, 201)
        _, other_headers, _ = self._login("13800139610")

        other = self._set_boundary(
            vault_id=vault_id,
            session_id=session_id,
            thread_id=thread_id,
            expected_session_version=1,
            boundary="doNotAsk",
            headers=other_headers,
        )
        stale = self._set_boundary(
            vault_id=vault_id,
            session_id=session_id,
            thread_id=thread_id,
            expected_session_version=9,
            boundary="doNotAsk",
            headers=owner_headers,
        )
        unsupported = self._set_boundary(
            vault_id=vault_id,
            session_id=session_id,
            thread_id=thread_id,
            expected_session_version=1,
            boundary="open",
            headers=owner_headers,
        )
        unexpected = self._set_boundary(
            vault_id=vault_id,
            session_id=session_id,
            thread_id=thread_id,
            expected_session_version=1,
            boundary="skipOnce",
            headers=owner_headers,
            extra={"reason": "do not accept free-form policy reasons"},
        )

        self.assertTrue(owner_id.startswith("user_"))
        self.assertEqual(other.status_code, 403, other.text)
        self.assertEqual(other.json()["detail"]["code"], "ownerTruthInterviewSessionDenied")
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["detail"]["code"], "ownerTruthInterviewSessionConflict")
        self.assertEqual(unsupported.status_code, 400, unsupported.text)
        self.assertEqual(unsupported.json()["detail"]["code"], "ownerTruthInterviewSessionInvalid")
        self.assertEqual(unexpected.status_code, 400, unexpected.text)
        self.assertEqual(unexpected.json()["detail"]["code"], "ownerTruthInterviewSessionInvalid")

    def test_other_owner_and_stale_versions_cannot_append(self) -> None:
        owner_id, owner_headers, _ = self._login("13800139603")
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
        _, other_headers, _ = self._login("13800139604")

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

    def test_formal_natural_input_requires_captured_release_policy(self) -> None:
        _, headers, _ = self._login("13800139605", qa=False)

        response = self._start_session(
            vault_id="vault-interview-input-policy-denied",
            headers=headers,
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["code"], "release_policy_denied")
        self.assertEqual(response.json()["detail"]["feature"], "echoTextInput")
        self.assertEqual(response.json()["detail"]["reason"], "missingCapturedPolicy")

    def test_formal_natural_input_accepts_matching_release_policy_capture(self) -> None:
        owner_id, headers, session_id = self._login("13800139606", qa=False)
        headers.update(
            {
                "X-DreamJourney-Feature": "echoTextInput",
                "X-DreamJourney-Feature-Decision-Id": "decision-interview-natural-input",
                "X-DreamJourney-Feature-Allowed": "true",
                "X-DreamJourney-Policy-Version": "release-policy-v1",
                "X-DreamJourney-Policy-Revision": "1",
                "X-DreamJourney-Account-Generation": hashlib.sha256(
                    session_id.encode("utf-8")
                ).hexdigest()[:24],
            }
        )
        vault_id = "vault-interview-input-policy-allowed"
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

        append = client.post(
            self._append_path(vault_id, session_id),
            headers=headers,
            json={
                "commandId": str(uuid4()),
                "threadId": thread_id,
                "messageId": str(uuid4()),
                "expectedThreadVersion": 1,
                "expectedSessionVersion": 1,
                "text": "通过正式发布策略写入的自然输入。",
            },
        )
        self.assertEqual(append.status_code, 201)
        self.assertEqual(append.json()["receipt"]["messageSequence"], 1)

    def test_formal_natural_input_writes_value_free_audit_only_when_enabled(self) -> None:
        owner_id, headers, auth_session_id = self._login("13800139620", qa=False)
        headers.update(
            {
                "X-DreamJourney-Feature": "echoTextInput",
                "X-DreamJourney-Feature-Decision-Id": "decision-interview-append-audit",
                "X-DreamJourney-Feature-Allowed": "true",
                "X-DreamJourney-Policy-Version": "release-policy-v1",
                "X-DreamJourney-Policy-Revision": "1",
                "X-DreamJourney-Account-Generation": hashlib.sha256(
                    auth_session_id.encode("utf-8")
                ).hexdigest()[:24],
            }
        )
        vault_id = "vault-interview-append-decision-audit"
        thread_id = str(uuid4())
        session_id = str(uuid4())
        start = self._start_session(
            vault_id=vault_id,
            headers=headers,
            thread_id=thread_id,
            session_id=session_id,
        )
        self.assertEqual(start.status_code, 201, start.text)

        default_off_command_id = str(uuid4())
        default_off_message_id = str(uuid4())
        default_off = client.post(
            self._append_path(vault_id, session_id),
            headers=headers,
            json={
                "commandId": default_off_command_id,
                "threadId": thread_id,
                "messageId": default_off_message_id,
                "expectedThreadVersion": 1,
                "expectedSessionVersion": 1,
                "text": "开关关闭时只追加私有叙述。",
            },
        )
        self.assertEqual(default_off.status_code, 201, default_off.text)
        default_off_audit_command = OwnerTruthInterviewDecisionAuditCommand(
            command_id=(
                "owner-truth-interview-append-decision-audit:"
                f"{default_off_command_id}"
            ),
            thread_id=thread_id,
            session_id=session_id,
            message_id=default_off_message_id,
            expected_session_version=2,
        )
        audit_context = OwnerTruthCommandContext(
            vault_id=vault_id,
            owner_subject_id=owner_id,
            actor_subject_id=owner_id,
            policy_version="release-policy-v1",
        )
        repository = self.store.owner_truth_interview_decision_audit_repository()
        self.assertIsNone(
            repository.find_by_command(
                context=audit_context,
                command_id_hash=default_off_audit_command.command_id_hash,
                request_payload_hash=default_off_audit_command.request_payload_hash,
            )
        )

        append_command_id = str(uuid4())
        message_id = str(uuid4())
        private_text = "这段叙述只能写入私有会话，不能进入审计正文。"
        main_module.OWNER_TRUTH_INTERVIEW_DECISION_AUDIT_ENABLED = True
        append = client.post(
            self._append_path(vault_id, session_id),
            headers=headers,
            json={
                "commandId": append_command_id,
                "threadId": thread_id,
                "messageId": message_id,
                "expectedThreadVersion": 2,
                "expectedSessionVersion": 2,
                "text": private_text,
            },
        )

        self.assertEqual(append.status_code, 201, append.text)
        rendered_response = json.dumps(append.json(), ensure_ascii=False, sort_keys=True)
        self.assertNotIn(private_text, rendered_response)
        self.assertNotIn("decisionAudit", rendered_response)

        audit_command = OwnerTruthInterviewDecisionAuditCommand(
            command_id=(
                "owner-truth-interview-append-decision-audit:"
                f"{append_command_id}"
            ),
            thread_id=thread_id,
            session_id=session_id,
            message_id=message_id,
            expected_session_version=3,
        )
        audit = repository.find_by_command(
            context=audit_context,
            command_id_hash=audit_command.command_id_hash,
            request_payload_hash=audit_command.request_payload_hash,
        )
        self.assertIsNotNone(audit)
        assert audit is not None
        self.assertEqual(audit.outcome, "deduplicated")
        self.assertEqual(audit.action.value, "listen")
        self.assertEqual(audit.reason_code, "noSafePrimaryQuestion")
        self.assertEqual(audit.session_version, 3)
        rendered_audit = json.dumps(audit.value_free_summary(), ensure_ascii=False, sort_keys=True)
        self.assertNotIn(private_text, rendered_audit)
        self.assertNotIn(thread_id, rendered_audit)
        self.assertNotIn(message_id, rendered_audit)

    def test_formal_natural_input_topic_shift_shadow_is_independent_and_non_mutating(self) -> None:
        owner_id, headers, auth_session_id = self._login("13800139626", qa=False)
        headers.update(
            {
                "X-DreamJourney-Feature": "echoTextInput",
                "X-DreamJourney-Feature-Decision-Id": "decision-interview-topic-shift-shadow",
                "X-DreamJourney-Feature-Allowed": "true",
                "X-DreamJourney-Policy-Version": "release-policy-v1",
                "X-DreamJourney-Policy-Revision": "1",
                "X-DreamJourney-Account-Generation": hashlib.sha256(
                    auth_session_id.encode("utf-8")
                ).hexdigest()[:24],
            }
        )
        vault_id = "vault-interview-topic-shift-shadow"
        thread_id = str(uuid4())
        session_id = str(uuid4())
        started = self._start_session(
            vault_id=vault_id,
            headers=headers,
            thread_id=thread_id,
            session_id=session_id,
        )
        self.assertEqual(started.status_code, 201, started.text)

        audit_context = OwnerTruthCommandContext(
            vault_id=vault_id,
            owner_subject_id=owner_id,
            actor_subject_id=owner_id,
            policy_version="release-policy-v1",
        )
        repository = self.store.owner_truth_interview_decision_audit_repository()
        main_module.OWNER_TRUTH_INTERVIEW_DECISION_AUDIT_ENABLED = True

        disabled_text = "我们换个话题吧，聊聊我第一份工作的经历。"
        disabled_command_id = str(uuid4())
        disabled_message_id = str(uuid4())
        disabled = client.post(
            self._append_path(vault_id, session_id),
            headers=headers,
            json={
                "commandId": disabled_command_id,
                "threadId": thread_id,
                "messageId": disabled_message_id,
                "expectedThreadVersion": 1,
                "expectedSessionVersion": 1,
                "text": disabled_text,
            },
        )
        self.assertEqual(disabled.status_code, 201, disabled.text)
        disabled_audit_command = OwnerTruthInterviewDecisionAuditCommand(
            command_id=(
                "owner-truth-interview-append-decision-audit:"
                f"{disabled_command_id}"
            ),
            thread_id=thread_id,
            session_id=session_id,
            message_id=disabled_message_id,
            expected_session_version=2,
        )
        disabled_audit = repository.find_by_command(
            context=audit_context,
            command_id_hash=disabled_audit_command.command_id_hash,
            request_payload_hash=disabled_audit_command.request_payload_hash,
        )
        self.assertIsNotNone(disabled_audit)
        assert disabled_audit is not None
        self.assertEqual(disabled_audit.action.value, "listen")
        self.assertEqual(disabled_audit.reason_code, "noSafePrimaryQuestion")

        main_module.OWNER_TRUTH_TOPIC_SHIFT_SHADOW_ENABLED = True
        shadow_text = "先不聊这个了，我想说说外婆年轻时的故事。"
        shadow_command_id = str(uuid4())
        shadow_message_id = str(uuid4())
        shadow = client.post(
            self._append_path(vault_id, session_id),
            headers=headers,
            json={
                "commandId": shadow_command_id,
                "threadId": thread_id,
                "messageId": shadow_message_id,
                "expectedThreadVersion": 2,
                "expectedSessionVersion": 2,
                "text": shadow_text,
            },
        )
        self.assertEqual(shadow.status_code, 201, shadow.text)
        self.assertNotIn(shadow_text, shadow.text)
        shadow_audit_command = OwnerTruthInterviewDecisionAuditCommand(
            command_id=(
                "owner-truth-interview-append-decision-audit:"
                f"{shadow_command_id}"
            ),
            thread_id=thread_id,
            session_id=session_id,
            message_id=shadow_message_id,
            expected_session_version=3,
        )
        shadow_audit = repository.find_by_command(
            context=audit_context,
            command_id_hash=shadow_audit_command.command_id_hash,
            request_payload_hash=shadow_audit_command.request_payload_hash,
        )
        self.assertIsNotNone(shadow_audit)
        assert shadow_audit is not None
        self.assertEqual(shadow_audit.action.value, "pause")
        self.assertEqual(shadow_audit.reason_code, "topicChanged")
        rendered_audit = json.dumps(
            shadow_audit.value_free_summary(),
            ensure_ascii=False,
            sort_keys=True,
        )
        self.assertNotIn(shadow_text, rendered_audit)
        self.assertNotIn(thread_id, rendered_audit)
        self.assertNotIn(session_id, rendered_audit)

        # Shadowing only audits the next policy action. The existing session
        # remains active until a later, explicitly gated product command owns
        # the actual pause/create-thread transition.
        state = client.get(
            f"{self._start_path(vault_id)}/{session_id}/state",
            headers=headers,
        )
        self.assertEqual(state.status_code, 200, state.text)
        self.assertEqual(state.json()["session"]["state"], "active")
        self.assertEqual(state.json()["session"]["rowVersion"], 3)

    def test_topic_shift_preflight_is_default_off_qa_only_and_write_free(self) -> None:
        _, qa_headers, _ = self._login("13800139628")
        qa_vault_id = "vault-interview-topic-shift-preflight-qa"
        qa_thread_id = str(uuid4())
        qa_session_id = str(uuid4())
        started = self._start_session(
            vault_id=qa_vault_id,
            headers=qa_headers,
            thread_id=qa_thread_id,
            session_id=qa_session_id,
        )
        self.assertEqual(started.status_code, 201, started.text)
        topic_change_text = "我们换个话题吧，聊聊我第一份工作的经历。"

        default_off = client.post(
            self._append_path(qa_vault_id, qa_session_id),
            headers=qa_headers,
            json={
                "commandId": str(uuid4()),
                "threadId": qa_thread_id,
                "messageId": str(uuid4()),
                "expectedThreadVersion": 1,
                "expectedSessionVersion": 1,
                "text": topic_change_text,
            },
        )
        self.assertEqual(default_off.status_code, 201, default_off.text)

        preflight_vault_id = "vault-interview-topic-shift-preflight-enabled"
        preflight_thread_id = str(uuid4())
        preflight_session_id = str(uuid4())
        started_preflight = self._start_session(
            vault_id=preflight_vault_id,
            headers=qa_headers,
            thread_id=preflight_thread_id,
            session_id=preflight_session_id,
        )
        self.assertEqual(started_preflight.status_code, 201, started_preflight.text)
        main_module.OWNER_TRUTH_TOPIC_SHIFT_PREFLIGHT_QA_ENABLED = True
        blocked = client.post(
            self._append_path(preflight_vault_id, preflight_session_id),
            headers=qa_headers,
            json={
                "commandId": str(uuid4()),
                "threadId": preflight_thread_id,
                "messageId": str(uuid4()),
                "expectedThreadVersion": 1,
                "expectedSessionVersion": 1,
                "text": topic_change_text,
            },
        )
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertEqual(blocked.headers["cache-control"], "no-store")
        self.assertNotIn(topic_change_text, blocked.text)
        self.assertEqual(
            blocked.json(),
            {
                "schemaVersion": (
                    "owner-truth-interview-topic-switch-preflight-response-v1"
                ),
                "vaultId": preflight_vault_id,
                "persisted": False,
                "retryable": False,
                "nextAction": "pauseForTopicSwitch",
                "preflight": {
                    "schemaVersion": "owner-truth-topic-shift-preflight-v1",
                    "status": "topicSwitchRequired",
                    "requiresTopicSwitch": True,
                    "reasonCode": "explicitTopicSwitchRequired",
                },
            },
        )
        preflight_state = client.get(
            f"{self._start_path(preflight_vault_id)}/{preflight_session_id}/state",
            headers=qa_headers,
        )
        self.assertEqual(preflight_state.status_code, 200, preflight_state.text)
        self.assertEqual(preflight_state.json()["session"]["state"], "active")
        self.assertEqual(preflight_state.json()["session"]["rowVersion"], 1)
        self.assertEqual(preflight_state.json()["session"]["ownerTurnCount"], 0)

        _, formal_headers, formal_auth_session_id = self._login("13800139629", qa=False)
        formal_headers.update(
            {
                "X-DreamJourney-Feature": "echoTextInput",
                "X-DreamJourney-Feature-Decision-Id": (
                    "decision-interview-topic-shift-preflight-formal"
                ),
                "X-DreamJourney-Feature-Allowed": "true",
                "X-DreamJourney-Policy-Version": "release-policy-v1",
                "X-DreamJourney-Policy-Revision": "1",
                "X-DreamJourney-Account-Generation": hashlib.sha256(
                    formal_auth_session_id.encode("utf-8")
                ).hexdigest()[:24],
            }
        )
        formal_vault_id = "vault-interview-topic-shift-preflight-formal"
        formal_thread_id = str(uuid4())
        formal_session_id = str(uuid4())
        formal_started = self._start_session(
            vault_id=formal_vault_id,
            headers=formal_headers,
            thread_id=formal_thread_id,
            session_id=formal_session_id,
        )
        self.assertEqual(formal_started.status_code, 201, formal_started.text)
        formal_append = client.post(
            self._append_path(formal_vault_id, formal_session_id),
            headers=formal_headers,
            json={
                "commandId": str(uuid4()),
                "threadId": formal_thread_id,
                "messageId": str(uuid4()),
                "expectedThreadVersion": 1,
                "expectedSessionVersion": 1,
                "text": topic_change_text,
            },
        )
        self.assertEqual(formal_append.status_code, 201, formal_append.text)

    def test_natural_input_do_not_ask_reactivation_preflight_is_default_off_and_write_free(self) -> None:
        _, headers, auth_session_id = self._login("13800139627", qa=False)
        headers.update(
            {
                "X-DreamJourney-Feature": "echoTextInput",
                "X-DreamJourney-Feature-Decision-Id": "decision-interview-do-not-ask-preflight",
                "X-DreamJourney-Feature-Allowed": "true",
                "X-DreamJourney-Policy-Version": "release-policy-v1",
                "X-DreamJourney-Policy-Revision": "1",
                "X-DreamJourney-Account-Generation": hashlib.sha256(
                    auth_session_id.encode("utf-8")
                ).hexdigest()[:24],
            }
        )
        vault_id = "vault-interview-do-not-ask-preflight"
        thread_id = str(uuid4())
        session_id = str(uuid4())
        started = self._start_session(
            vault_id=vault_id,
            headers=headers,
            thread_id=thread_id,
            session_id=session_id,
        )
        self.assertEqual(started.status_code, 201, started.text)
        paused = self._set_boundary(
            vault_id=vault_id,
            session_id=session_id,
            thread_id=thread_id,
            expected_session_version=1,
            boundary="doNotAsk",
            headers=headers,
        )
        self.assertEqual(paused.status_code, 201, paused.text)
        self.assertEqual(paused.json()["receipt"]["state"], "paused")
        self.assertEqual(paused.json()["receipt"]["boundary"], "doNotAsk")

        attempted_text = "我愿意重新聊这个话题。"
        default_off = client.post(
            self._append_path(vault_id, session_id),
            headers=headers,
            json={
                "commandId": str(uuid4()),
                "threadId": thread_id,
                "messageId": str(uuid4()),
                "expectedThreadVersion": 2,
                "expectedSessionVersion": 2,
                "text": attempted_text,
            },
        )
        self.assertEqual(default_off.status_code, 409, default_off.text)
        self.assertEqual(
            default_off.json()["detail"]["code"],
            "ownerTruthInterviewSessionConflict",
        )

        main_module.OWNER_TRUTH_DO_NOT_ASK_REACTIVATION_PREFLIGHT_ENABLED = True
        payload = {
            "commandId": str(uuid4()),
            "threadId": thread_id,
            "messageId": str(uuid4()),
            "expectedThreadVersion": 2,
            "expectedSessionVersion": 2,
            "text": attempted_text,
        }
        preflight = client.post(
            self._append_path(vault_id, session_id),
            headers=headers,
            json=payload,
        )
        self.assertEqual(preflight.status_code, 409, preflight.text)
        self.assertEqual(preflight.headers["cache-control"], "no-store")
        self.assertNotIn(attempted_text, preflight.text)
        self.assertEqual(
            preflight.json(),
            {
                "schemaVersion": "owner-truth-interview-do-not-ask-reopen-response-v1",
                "vaultId": vault_id,
                "persisted": False,
                "retryable": False,
                "nextAction": "restoreDoNotAsk",
                "preflight": {
                    "schemaVersion": "owner-truth-do-not-ask-reactivation-preflight-v1",
                    "status": "confirmationRequired",
                    "requiresConfirmation": True,
                    "reasonCode": "doNotAskRestoreConfirmationRequired",
                },
            },
        )

        replay = client.post(
            self._append_path(vault_id, session_id),
            headers=headers,
            json=payload,
        )
        self.assertEqual(replay.status_code, 409, replay.text)
        self.assertEqual(replay.json(), preflight.json())
        state = client.get(
            f"{self._start_path(vault_id)}/{session_id}/state",
            headers=headers,
        )
        self.assertEqual(state.status_code, 200, state.text)
        self.assertEqual(state.json()["session"]["state"], "paused")
        self.assertEqual(state.json()["session"]["boundary"], "doNotAsk")
        self.assertEqual(state.json()["session"]["rowVersion"], 2)
        self.assertEqual(state.json()["session"]["ownerTurnCount"], 0)

    def test_formal_review_batch_automation_is_default_off_and_value_free_when_enabled(self) -> None:
        owner_id, headers, auth_session_id = self._login("13800139621", qa=False)
        headers.update(
            {
                "X-DreamJourney-Feature": "echoTextInput",
                "X-DreamJourney-Feature-Decision-Id": "decision-interview-review-batch",
                "X-DreamJourney-Feature-Allowed": "true",
                "X-DreamJourney-Policy-Version": "release-policy-v1",
                "X-DreamJourney-Policy-Revision": "1",
                "X-DreamJourney-Account-Generation": hashlib.sha256(
                    auth_session_id.encode("utf-8")
                ).hexdigest()[:24],
            }
        )
        vault_id = "vault-interview-formal-review-batch"
        thread_id = str(uuid4())
        session_id = str(uuid4())
        started = self._start_session(
            vault_id=vault_id,
            headers=headers,
            thread_id=thread_id,
            session_id=session_id,
        )
        self.assertEqual(started.status_code, 201, started.text)

        thread_version = 1
        session_version = 1
        for index in range(5):
            response = client.post(
                self._append_path(vault_id, session_id),
                headers=headers,
                json={
                    "commandId": str(uuid4()),
                    "threadId": thread_id,
                    "messageId": str(uuid4()),
                    "expectedThreadVersion": thread_version,
                    "expectedSessionVersion": session_version,
                    "text": f"默认关闭的正式私有叙述 {index + 1}，不得出现在回执。",
                },
            )
            self.assertEqual(response.status_code, 201, response.text)
            payload = response.json()
            self.assertNotIn("reviewBatchAutomation", payload)
            receipt = payload["receipt"]
            thread_version = int(receipt["threadVersion"])
            session_version = int(receipt["sessionVersion"])

        context = OwnerTruthCommandContext(
            vault_id=vault_id,
            owner_subject_id=owner_id,
            actor_subject_id=owner_id,
        )
        conversation = OwnerTruthConversationService(
            self.store.owner_truth_conversation_repository()
        )
        self.assertEqual(
            conversation.list_review_batches(session_id=session_id, context=context),
            (),
        )

        # A new formal session proves the enabled path independently; the
        # first default-off session remains untouched.
        enabled_vault_id = "vault-interview-formal-review-batch-enabled"
        enabled_thread_id = str(uuid4())
        enabled_session_id = str(uuid4())
        started_enabled = self._start_session(
            vault_id=enabled_vault_id,
            headers=headers,
            thread_id=enabled_thread_id,
            session_id=enabled_session_id,
        )
        self.assertEqual(started_enabled.status_code, 201, started_enabled.text)
        main_module.OWNER_TRUTH_INTERVIEW_REVIEW_BATCH_AUTOMATION_ENABLED = True
        thread_version = 1
        session_version = 1
        private_text = "第五段正式私有叙述不得泄露到批次或回执。"
        for index in range(5):
            text = private_text if index == 4 else f"启用后的正式私有叙述 {index + 1}。"
            response = client.post(
                self._append_path(enabled_vault_id, enabled_session_id),
                headers=headers,
                json={
                    "commandId": str(uuid4()),
                    "threadId": enabled_thread_id,
                    "messageId": str(uuid4()),
                    "expectedThreadVersion": thread_version,
                    "expectedSessionVersion": session_version,
                    "text": text,
                },
            )
            self.assertEqual(response.status_code, 201, response.text)
            payload = response.json()
            self.assertNotIn("reviewBatchAutomation", payload)
            self.assertNotIn(private_text, json.dumps(payload, ensure_ascii=False))
            receipt = payload["receipt"]
            thread_version = int(receipt["threadVersion"])
            session_version = int(receipt["sessionVersion"])

        self.assertEqual(thread_version, 6)
        # The fifth append creates a hidden ReviewBatch in the same command
        # UoW, so the formal receipt returns the usable post-batch version.
        self.assertEqual(session_version, 7)
        batches = conversation.list_review_batches(
            session_id=enabled_session_id,
            context=OwnerTruthCommandContext(
                vault_id=enabled_vault_id,
                owner_subject_id=owner_id,
                actor_subject_id=owner_id,
            ),
        )
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0].trigger.value, "turnThreshold")
        self.assertEqual(batches[0].captured_candidate_batch_turn_count, 5)

        sixth = client.post(
            self._append_path(enabled_vault_id, enabled_session_id),
            headers=headers,
            json={
                "commandId": str(uuid4()),
                "threadId": enabled_thread_id,
                "messageId": str(uuid4()),
                "expectedThreadVersion": thread_version,
                "expectedSessionVersion": session_version,
                "text": "已有待确认批次后不能再创建第二个批次。",
            },
        )
        self.assertEqual(sixth.status_code, 201, sixth.text)
        self.assertNotIn("reviewBatchAutomation", sixth.json())
        self.assertEqual(
            len(
                conversation.list_review_batches(
                    session_id=enabled_session_id,
                    context=OwnerTruthCommandContext(
                        vault_id=enabled_vault_id,
                        owner_subject_id=owner_id,
                        actor_subject_id=owner_id,
                    ),
                )
            ),
            1,
        )

    def test_formal_end_requires_captured_policy_and_creates_hidden_session_exit_batch(self) -> None:
        owner_id, headers, auth_session_id = self._login("13800139623", qa=False)
        headers.update(
            {
                "X-DreamJourney-Feature": "echoTextInput",
                "X-DreamJourney-Feature-Decision-Id": "decision-interview-formal-end",
                "X-DreamJourney-Feature-Allowed": "true",
                "X-DreamJourney-Policy-Version": "release-policy-v1",
                "X-DreamJourney-Policy-Revision": "1",
                "X-DreamJourney-Account-Generation": hashlib.sha256(
                    auth_session_id.encode("utf-8")
                ).hexdigest()[:24],
            }
        )
        vault_id = "vault-interview-formal-end"
        thread_id = str(uuid4())
        session_id = str(uuid4())

        self.assertEqual(
            self._start_session(
                vault_id=vault_id,
                headers=headers,
                thread_id=thread_id,
                session_id=session_id,
            ).status_code,
            201,
        )
        appended = client.post(
            self._append_path(vault_id, session_id),
            headers=headers,
            json={
                "commandId": str(uuid4()),
                "threadId": thread_id,
                "messageId": str(uuid4()),
                "expectedThreadVersion": 1,
                "expectedSessionVersion": 1,
                "text": "正式结束前的私有叙述不得出现在结束回执。",
            },
        )
        self.assertEqual(appended.status_code, 201, appended.text)

        missing_capture_headers = {
            key: value
            for key, value in headers.items()
            if not key.lower().startswith("x-dreamjourney-feature")
            and key not in {
                "X-DreamJourney-Policy-Version",
                "X-DreamJourney-Policy-Revision",
                "X-DreamJourney-Account-Generation",
            }
        }
        missing_capture = self._end_session(
            vault_id=vault_id,
            session_id=session_id,
            thread_id=thread_id,
            expected_thread_version=2,
            expected_session_version=2,
            headers=missing_capture_headers,
        )
        self.assertEqual(missing_capture.status_code, 403, missing_capture.text)
        self.assertEqual(
            missing_capture.json()["detail"]["code"],
            "release_policy_denied",
        )

        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = False
        main_module.OWNER_TRUTH_INTERVIEW_REVIEW_BATCH_AUTOMATION_ENABLED = True
        ended = self._end_session(
            vault_id=vault_id,
            session_id=session_id,
            thread_id=thread_id,
            expected_thread_version=2,
            expected_session_version=2,
            headers=headers,
        )
        self.assertEqual(ended.status_code, 201, ended.text)
        payload = ended.json()
        self.assertEqual(payload["receipt"]["state"], "ended")
        self.assertEqual(payload["receipt"]["threadVersion"], 3)
        self.assertEqual(payload["receipt"]["sessionVersion"], 4)
        self.assertNotIn("reviewBatchAutomation", payload)
        rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("正式结束前", rendered)
        self.assertNotIn("candidate", rendered.lower())
        self.assertNotIn("memory", rendered.lower())

        batches = OwnerTruthConversationService(
            self.store.owner_truth_conversation_repository()
        ).list_review_batches(
            session_id=session_id,
            context=OwnerTruthCommandContext(
                vault_id=vault_id,
                owner_subject_id=owner_id,
                actor_subject_id=owner_id,
            ),
        )
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0].trigger.value, "sessionExit")
        self.assertEqual(batches[0].captured_candidate_batch_turn_count, 1)

    def test_formal_topic_switch_requires_policy_and_creates_hidden_exit_batch(self) -> None:
        owner_id, headers, auth_session_id = self._login("13800139635", qa=False)
        headers.update(
            {
                "X-DreamJourney-Feature": "echoTextInput",
                "X-DreamJourney-Feature-Decision-Id": "decision-interview-formal-topic-switch",
                "X-DreamJourney-Feature-Allowed": "true",
                "X-DreamJourney-Policy-Version": "release-policy-v1",
                "X-DreamJourney-Policy-Revision": "1",
                "X-DreamJourney-Account-Generation": hashlib.sha256(
                    auth_session_id.encode("utf-8")
                ).hexdigest()[:24],
            }
        )
        vault_id = "vault-interview-formal-topic-switch"
        thread_id = str(uuid4())
        session_id = str(uuid4())
        self.assertEqual(
            self._start_session(
                vault_id=vault_id,
                headers=headers,
                thread_id=thread_id,
                session_id=session_id,
            ).status_code,
            201,
        )
        appended = client.post(
            self._append_path(vault_id, session_id),
            headers=headers,
            json={
                "commandId": str(uuid4()),
                "threadId": thread_id,
                "messageId": str(uuid4()),
                "expectedThreadVersion": 1,
                "expectedSessionVersion": 1,
                "text": "切换到 Live 前保留的私人叙述。",
            },
        )
        self.assertEqual(appended.status_code, 201, appended.text)

        missing_capture_headers = {
            key: value
            for key, value in headers.items()
            if not key.lower().startswith("x-dreamjourney-feature")
            and key not in {
                "X-DreamJourney-Policy-Version",
                "X-DreamJourney-Policy-Revision",
                "X-DreamJourney-Account-Generation",
            }
        }
        denied = self._pause_for_topic_switch(
            vault_id=vault_id,
            session_id=session_id,
            thread_id=thread_id,
            expected_thread_version=2,
            expected_session_version=2,
            headers=missing_capture_headers,
        )
        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(denied.json()["detail"]["code"], "release_policy_denied")

        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = False
        main_module.OWNER_TRUTH_INTERVIEW_REVIEW_BATCH_AUTOMATION_ENABLED = True
        paused = self._pause_for_topic_switch(
            vault_id=vault_id,
            session_id=session_id,
            thread_id=thread_id,
            expected_thread_version=2,
            expected_session_version=2,
            headers=headers,
        )
        self.assertEqual(paused.status_code, 201, paused.text)
        payload = paused.json()
        self.assertEqual(payload["receipt"]["state"], "paused")
        self.assertEqual(payload["receipt"]["threadVersion"], 3)
        self.assertEqual(payload["receipt"]["sessionVersion"], 4)
        self.assertNotIn("reviewBatchAutomation", payload)
        self.assertNotIn("切换到 Live", json.dumps(payload, ensure_ascii=False))

        batches = OwnerTruthConversationService(
            self.store.owner_truth_conversation_repository()
        ).list_review_batches(
            session_id=session_id,
            context=OwnerTruthCommandContext(
                vault_id=vault_id,
                owner_subject_id=owner_id,
                actor_subject_id=owner_id,
            ),
        )
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0].trigger.value, "sessionExit")
        self.assertEqual(batches[0].captured_candidate_batch_turn_count, 1)

    def test_formal_pause_boundary_creates_hidden_session_exit_batch_when_enabled(self) -> None:
        owner_id, headers, auth_session_id = self._login("13800139622", qa=False)
        headers.update(
            {
                "X-DreamJourney-Feature": "echoTextInput",
                "X-DreamJourney-Feature-Decision-Id": "decision-interview-review-boundary",
                "X-DreamJourney-Feature-Allowed": "true",
                "X-DreamJourney-Policy-Version": "release-policy-v1",
                "X-DreamJourney-Policy-Revision": "1",
                "X-DreamJourney-Account-Generation": hashlib.sha256(
                    auth_session_id.encode("utf-8")
                ).hexdigest()[:24],
            }
        )
        vault_id = "vault-interview-formal-review-boundary"
        thread_id = str(uuid4())
        session_id = str(uuid4())
        self.assertEqual(
            self._start_session(
                vault_id=vault_id,
                headers=headers,
                thread_id=thread_id,
                session_id=session_id,
            ).status_code,
            201,
        )
        appended = client.post(
            self._append_path(vault_id, session_id),
            headers=headers,
            json={
                "commandId": str(uuid4()),
                "threadId": thread_id,
                "messageId": str(uuid4()),
                "expectedThreadVersion": 1,
                "expectedSessionVersion": 1,
                "text": "暂停前的正式私有叙述。",
            },
        )
        self.assertEqual(appended.status_code, 201, appended.text)

        main_module.OWNER_TRUTH_INTERVIEW_REVIEW_BATCH_AUTOMATION_ENABLED = True
        paused = self._set_boundary(
            vault_id=vault_id,
            session_id=session_id,
            thread_id=thread_id,
            expected_session_version=2,
            boundary="doNotAsk",
            headers=headers,
        )
        self.assertEqual(paused.status_code, 201, paused.text)
        payload = paused.json()
        self.assertNotIn("reviewBatchAutomation", payload)
        self.assertEqual(payload["receipt"]["state"], "paused")
        self.assertEqual(payload["receipt"]["boundary"], "doNotAsk")
        self.assertEqual(payload["receipt"]["sessionVersion"], 4)

        batches = OwnerTruthConversationService(
            self.store.owner_truth_conversation_repository()
        ).list_review_batches(
            session_id=session_id,
            context=OwnerTruthCommandContext(
                vault_id=vault_id,
                owner_subject_id=owner_id,
                actor_subject_id=owner_id,
            ),
        )
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0].trigger.value, "sessionExit")
        self.assertEqual(batches[0].captured_candidate_batch_turn_count, 1)

    def test_formal_boundary_requires_captured_echo_policy(self) -> None:
        _, headers, auth_session_id = self._login("13800139611", qa=False)
        vault_id = "vault-interview-boundary-policy"
        thread_id = str(uuid4())
        session_id = str(uuid4())

        missing_capture = self._set_boundary(
            vault_id=vault_id,
            session_id=session_id,
            thread_id=thread_id,
            expected_session_version=1,
            boundary="doNotAsk",
            headers=headers,
        )
        self.assertEqual(missing_capture.status_code, 403)
        self.assertEqual(missing_capture.json()["detail"]["code"], "release_policy_denied")
        self.assertEqual(missing_capture.json()["detail"]["reason"], "missingCapturedPolicy")

        headers.update(
            {
                "X-DreamJourney-Feature": "echoTextInput",
                "X-DreamJourney-Feature-Decision-Id": "decision-interview-boundary",
                "X-DreamJourney-Feature-Allowed": "true",
                "X-DreamJourney-Policy-Version": "release-policy-v1",
                "X-DreamJourney-Policy-Revision": "1",
                "X-DreamJourney-Account-Generation": hashlib.sha256(
                    auth_session_id.encode("utf-8")
                ).hexdigest()[:24],
            }
        )
        start = self._start_session(
            vault_id=vault_id,
            headers=headers,
            thread_id=thread_id,
            session_id=session_id,
        )
        self.assertEqual(start.status_code, 201, start.text)

        allowed = self._set_boundary(
            vault_id=vault_id,
            session_id=session_id,
            thread_id=thread_id,
            expected_session_version=1,
            boundary="doNotAsk",
            headers=headers,
        )
        self.assertEqual(allowed.status_code, 201, allowed.text)
        self.assertEqual(allowed.json()["receipt"]["boundary"], "doNotAsk")
        self.assertEqual(allowed.json()["receipt"]["state"], "paused")

    def test_formal_presentation_is_policy_bound_and_content_free(self) -> None:
        _, headers, auth_session_id = self._login("13800139607", qa=False)
        headers.update(
            {
                "X-DreamJourney-Feature": "echoTextInput",
                "X-DreamJourney-Feature-Decision-Id": "decision-interview-presentation",
                "X-DreamJourney-Feature-Allowed": "true",
                "X-DreamJourney-Policy-Version": "release-policy-v1",
                "X-DreamJourney-Policy-Revision": "1",
                "X-DreamJourney-Account-Generation": hashlib.sha256(
                    auth_session_id.encode("utf-8")
                ).hexdigest()[:24],
            }
        )
        vault_id = "vault-interview-presentation"
        thread_id = str(uuid4())
        interview_session_id = str(uuid4())
        start = self._start_session(
            vault_id=vault_id,
            headers=headers,
            thread_id=thread_id,
            session_id=interview_session_id,
        )
        self.assertEqual(start.status_code, 201)

        text = "这段私人叙述不能进入产品呈现合同。"
        append = client.post(
            self._append_path(vault_id, interview_session_id),
            headers=headers,
            json={
                "commandId": str(uuid4()),
                "threadId": thread_id,
                "messageId": str(uuid4()),
                "expectedThreadVersion": 1,
                "expectedSessionVersion": 1,
                "text": text,
            },
        )
        self.assertEqual(append.status_code, 201)

        response = client.get(
            self._presentation_path(vault_id, interview_session_id),
            headers=headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(
            response.json(),
            {
                "schemaVersion": "owner-truth-interview-session-presentation-v1",
                "vaultId": vault_id,
                "presentation": {
                    "state": "narrativeRecorded",
                    "canContinue": True,
                    "canContinueLater": True,
                },
            },
        )
        rendered = json.dumps(response.json(), ensure_ascii=False, sort_keys=True)
        for forbidden in (
            text,
            "threadId",
            "sessionId",
            "candidate",
            "memory",
            "fatigue",
            "ownerTurnCount",
            "pendingReviewBatchId",
        ):
            self.assertNotIn(forbidden, rendered)

        denied = client.get(
            self._presentation_path(vault_id, interview_session_id),
            headers={"Authorization": headers["Authorization"]},
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json()["detail"]["code"], "release_policy_denied")
        self.assertEqual(denied.json()["detail"]["reason"], "missingCapturedPolicy")


if __name__ == "__main__":
    unittest.main()
