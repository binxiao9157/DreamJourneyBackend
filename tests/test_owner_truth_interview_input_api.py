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
        headers = {
            "Authorization": f"Bearer {payload['auth']['accessToken']}",
        }
        if qa:
            headers["X-DreamJourney-QA-Owner-Truth"] = "1"
        return payload["user"]["id"], headers, payload["auth"]["sessionId"]

    @staticmethod
    def _start_path(vault_id: str) -> str:
        return f"/v2/vaults/{vault_id}/interview-sessions"

    @staticmethod
    def _append_path(vault_id: str, session_id: str) -> str:
        return f"/v2/vaults/{vault_id}/interview-sessions/{session_id}/messages"

    @staticmethod
    def _boundary_path(vault_id: str, session_id: str) -> str:
        return f"/v2/vaults/{vault_id}/interview-sessions/{session_id}/boundary"

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
        _, headers, _ = self._login("13800139614")
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
        interrupted = client.post(
            self._append_path(vault_id, session_id),
            headers=headers,
            json={
                "commandId": str(uuid4()),
                "threadId": thread_id,
                "messageId": str(uuid4()),
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
