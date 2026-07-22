from __future__ import annotations

import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.services.in_memory_store import InMemoryStore


client = TestClient(app)


class OwnerTruthThreadPreferenceAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = main_module.store
        self.previous_backend_token = main_module.BACKEND_API_TOKEN
        self.previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        self.previous_route_mode = main_module.AUTH_ROUTE_MODE
        self.previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
        self.previous_candidate_qa = main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED
        self.previous_thread_preference_qa = main_module.OWNER_TRUTH_THREAD_PREFERENCE_QA_ENABLED
        self.previous_cooldown_seconds = main_module.OWNER_TRUTH_THREAD_COOLDOWN_SECONDS
        self.store = InMemoryStore()
        main_module.store = self.store
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = True
        main_module.OWNER_TRUTH_THREAD_PREFERENCE_QA_ENABLED = False
        main_module.OWNER_TRUTH_THREAD_COOLDOWN_SECONDS = 60

    def tearDown(self) -> None:
        main_module.store = self.previous_store
        main_module.BACKEND_API_TOKEN = self.previous_backend_token
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = self.previous_legacy_phone_login
        main_module.AUTH_ROUTE_MODE = self.previous_route_mode
        main_module.AUTH_OWNERSHIP_MODE = self.previous_ownership_mode
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = self.previous_candidate_qa
        main_module.OWNER_TRUTH_THREAD_PREFERENCE_QA_ENABLED = self.previous_thread_preference_qa
        main_module.OWNER_TRUTH_THREAD_COOLDOWN_SECONDS = self.previous_cooldown_seconds

    @staticmethod
    def _login(phone: str) -> tuple[str, dict[str, str]]:
        response = client.post(
            "/auth/login",
            json={"phone": phone, "nickname": "线程偏好测试", "password": "password123"},
        )
        if response.status_code != 200:
            raise AssertionError(response.text)
        body = response.json()
        return str(body["user"]["id"]), {
            "Authorization": f"Bearer {body['auth']['accessToken']}",
            "X-DreamJourney-QA-Owner-Truth": "1",
        }

    @staticmethod
    def _start_path(vault_id: str) -> str:
        return f"/v2/vaults/{vault_id}/interview-sessions"

    @staticmethod
    def _boundary_path(vault_id: str, session_id: str) -> str:
        return f"/v2/vaults/{vault_id}/interview-sessions/{session_id}/boundary"

    @staticmethod
    def _restore_cooldown_path(vault_id: str, session_id: str) -> str:
        return f"/v2/vaults/{vault_id}/interview-sessions/{session_id}/restore-cooldown"

    @staticmethod
    def _restore_do_not_ask_path(vault_id: str, session_id: str) -> str:
        return f"/v2/vaults/{vault_id}/interview-sessions/{session_id}/restore-do-not-ask"

    def _start(self, *, vault_id: str, headers: dict[str, str]) -> tuple[str, str]:
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
        return thread_id, session_id

    def test_cooldown_is_hidden_by_default_then_uses_server_owned_preference(self) -> None:
        owner_id, headers = self._login("13900000401")
        vault_id = "vault-thread-preference-api"
        thread_id, session_id = self._start(vault_id=vault_id, headers=headers)
        cooldown_payload = {
            "commandId": str(uuid4()),
            "threadId": thread_id,
            "expectedSessionVersion": 1,
            "boundary": "cooldown",
        }

        hidden = client.post(
            self._restore_cooldown_path(vault_id, session_id),
            headers=headers,
            json={
                "commandId": str(uuid4()),
                "threadId": thread_id,
                "expectedSessionVersion": 1,
            },
        )
        self.assertEqual(hidden.status_code, 404)
        self.assertEqual(
            hidden.json()["detail"]["code"],
            "ownerTruthThreadPreferenceUnavailable",
        )

        main_module.OWNER_TRUTH_THREAD_PREFERENCE_QA_ENABLED = True
        injected = client.post(
            self._boundary_path(vault_id, session_id),
            headers=headers,
            json={**cooldown_payload, "cooldownUntil": "client-controlled"},
        )
        self.assertEqual(injected.status_code, 400)
        self.assertEqual(
            injected.json()["detail"]["code"],
            "ownerTruthInterviewSessionInvalid",
        )

        paused = client.post(
            self._boundary_path(vault_id, session_id),
            headers=headers,
            json=cooldown_payload,
        )
        self.assertEqual(paused.status_code, 201, paused.text)
        self.assertEqual(paused.json()["receipt"]["boundary"], "cooldown")

        replayed = client.post(
            self._boundary_path(vault_id, session_id),
            headers=headers,
            json=cooldown_payload,
        )
        self.assertEqual(replayed.status_code, 200, replayed.text)
        self.assertEqual(replayed.json()["receipt"]["status"], "deduplicated")
        self.assertEqual(
            replayed.json()["receipt"]["sessionVersion"],
            paused.json()["receipt"]["sessionVersion"],
        )

        from app.domain.owner_truth.source_commands import OwnerTruthCommandContext

        preference = self.store.owner_truth_thread_preference_repository().read(
            context=OwnerTruthCommandContext(
                vault_id=vault_id,
                owner_subject_id=owner_id,
                actor_subject_id=owner_id,
            ),
            thread_id=thread_id,
        )
        self.assertIsNotNone(preference)
        assert preference is not None
        self.assertEqual(preference.preference.value, "cooldown")
        self.assertIsNotNone(preference.cooldown_until)

        early = client.post(
            self._restore_cooldown_path(vault_id, session_id),
            headers=headers,
            json={
                "commandId": str(uuid4()),
                "threadId": thread_id,
                "expectedSessionVersion": 2,
            },
        )
        self.assertEqual(early.status_code, 409)
        self.assertEqual(early.json()["detail"]["code"], "ownerTruthThreadCooldownActive")

    def test_do_not_ask_restore_clears_the_same_thread_preference(self) -> None:
        owner_id, headers = self._login("13900000402")
        vault_id = "vault-thread-preference-do-not-ask-api"
        thread_id, session_id = self._start(vault_id=vault_id, headers=headers)
        main_module.OWNER_TRUTH_THREAD_PREFERENCE_QA_ENABLED = True
        paused = client.post(
            self._boundary_path(vault_id, session_id),
            headers=headers,
            json={
                "commandId": str(uuid4()),
                "threadId": thread_id,
                "expectedSessionVersion": 1,
                "boundary": "doNotAsk",
            },
        )
        self.assertEqual(paused.status_code, 201, paused.text)

        restored = client.post(
            self._restore_do_not_ask_path(vault_id, session_id),
            headers=headers,
            json={
                "commandId": str(uuid4()),
                "threadId": thread_id,
                "expectedSessionVersion": 2,
                "confirmed": True,
            },
        )
        self.assertEqual(restored.status_code, 201, restored.text)
        self.assertEqual(restored.json()["receipt"]["boundary"], "open")

        from app.domain.owner_truth.source_commands import OwnerTruthCommandContext

        preference = self.store.owner_truth_thread_preference_repository().read(
            context=OwnerTruthCommandContext(
                vault_id=vault_id,
                owner_subject_id=owner_id,
                actor_subject_id=owner_id,
            ),
            thread_id=thread_id,
        )
        self.assertIsNotNone(preference)
        assert preference is not None
        self.assertEqual(preference.preference.value, "open")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
