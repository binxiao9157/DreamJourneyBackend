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


class OwnerTruthTextCaptureAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = main_module.store
        self.previous_backend_token = main_module.BACKEND_API_TOKEN
        self.previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        self.previous_route_mode = main_module.AUTH_ROUTE_MODE
        self.previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
        self.previous_closed_pilot_owner_ids = main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS
        self.previous_closed_pilot_features = set(
            main_module.RELEASE_POLICY_SERVICE.closed_pilot_enabled_features
        )
        self.store = InMemoryStore()
        main_module.store = self.store
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = frozenset()
        main_module.RELEASE_POLICY_SERVICE.closed_pilot_enabled_features = {
            "ownerTextCaptureV1"
        }

    def tearDown(self) -> None:
        main_module.store = self.previous_store
        main_module.BACKEND_API_TOKEN = self.previous_backend_token
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = self.previous_legacy_phone_login
        main_module.AUTH_ROUTE_MODE = self.previous_route_mode
        main_module.AUTH_OWNERSHIP_MODE = self.previous_ownership_mode
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = self.previous_closed_pilot_owner_ids
        main_module.RELEASE_POLICY_SERVICE.closed_pilot_enabled_features = (
            self.previous_closed_pilot_features
        )

    @staticmethod
    def _login(phone: str) -> tuple[str, dict[str, str], str]:
        response = client.post(
            "/auth/login",
            json={"phone": phone, "nickname": "文字 Source 闭环测试", "password": "password123"},
        )
        if response.status_code != 200:
            raise AssertionError(response.text)
        body = response.json()
        return (
            str(body["user"]["id"]),
            {"Authorization": f"Bearer {body['auth']['accessToken']}"},
            str(body["auth"]["sessionId"]),
        )

    @staticmethod
    def _capture_headers(
        headers: dict[str, str],
        *,
        session_id: str,
        feature: str = "ownerTextCaptureV1",
    ) -> dict[str, str]:
        captured = dict(headers)
        captured.update(
            {
                "X-DreamJourney-Feature": feature,
                "X-DreamJourney-Feature-Decision-Id": f"decision-{uuid4()}",
                "X-DreamJourney-Feature-Allowed": "true",
                "X-DreamJourney-Policy-Version": "release-policy-v1",
                "X-DreamJourney-Policy-Revision": "1",
                "X-DreamJourney-Account-Generation": hashlib.sha256(
                    session_id.encode("utf-8")
                ).hexdigest()[:24],
            }
        )
        return captured

    @staticmethod
    def _path(vault_id: str) -> str:
        return f"/v2/vaults/{vault_id}/sources"

    @staticmethod
    def _payload(*, command_id: str | None = None, expected_epoch: int = 0) -> dict[str, object]:
        return {
            "commandId": command_id or str(uuid4()),
            "expectedAuthorityEpoch": expected_epoch,
            "kind": "text",
            "content": "爷爷总会在傍晚带我去河边散步。",
            "purpose": "memoryCapture",
            "clientCreatedAt": "2026-08-02T12:00:00Z",
        }

    def _allow_owner(self, owner_id: str) -> None:
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = frozenset(
            set(main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS) | {owner_id}
        )

    def test_server_authorized_owner_creates_one_source_and_replays_without_qa_header(self) -> None:
        owner_id, auth_headers, session_id = self._login("13800139951")
        self._allow_owner(owner_id)
        vault_id = "vault-owner-text-capture"
        payload = self._payload()

        response = client.post(
            self._path(vault_id),
            headers=self._capture_headers(auth_headers, session_id=session_id),
            json=payload,
        )

        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["schemaVersion"], "owner-truth-text-capture-response-v1")
        self.assertEqual(body["vaultId"], vault_id)
        self.assertEqual(body["source"]["status"], "created")
        self.assertEqual(body["source"]["sourceVersion"], 1)
        self.assertEqual(body["source"]["authorityEpoch"], 0)
        self.assertEqual(body["candidateExtraction"], {"status": "requested"})
        self.assertIn("acceptedAt", body)
        rendered = json.dumps(body, ensure_ascii=False, sort_keys=True)
        self.assertNotIn(str(payload["content"]), rendered)
        self.assertNotIn("X-DreamJourney-QA-Owner-Truth", rendered)
        self.assertEqual(self.store.owner_truth_source_count(vault_id), 1)
        self.assertEqual(self.store.effect_kernel_repository().record_count(), 1)

        replay = client.post(
            self._path(vault_id),
            headers=self._capture_headers(auth_headers, session_id=session_id),
            json=payload,
        )
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["source"]["status"], "deduplicated")
        self.assertEqual(self.store.owner_truth_source_count(vault_id), 1)
        self.assertEqual(self.store.effect_kernel_repository().record_count(), 1)

    def test_forged_client_capture_and_qa_header_do_not_grant_text_capture(self) -> None:
        _owner_id, auth_headers, session_id = self._login("13800139952")
        response = client.post(
            self._path("vault-forged-owner-text-capture"),
            headers={
                **self._capture_headers(auth_headers, session_id=session_id),
                "X-DreamJourney-QA-Owner-Truth": "1",
            },
            json=self._payload(),
        )

        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["detail"]["code"], "release_policy_denied")
        self.assertEqual(self.store.owner_truth_source_count("vault-forged-owner-text-capture"), 0)

    def test_cross_owner_and_stale_epoch_cannot_create_another_source(self) -> None:
        owner_a, headers_a, session_a = self._login("13800139953")
        owner_b, headers_b, session_b = self._login("13800139954")
        self._allow_owner(owner_a)
        self._allow_owner(owner_b)
        vault_id = "vault-text-capture-isolation"

        created = client.post(
            self._path(vault_id),
            headers=self._capture_headers(headers_a, session_id=session_a),
            json=self._payload(),
        )
        self.assertEqual(created.status_code, 201, created.text)

        cross_owner = client.post(
            self._path(vault_id),
            headers=self._capture_headers(headers_b, session_id=session_b),
            json=self._payload(),
        )
        self.assertEqual(cross_owner.status_code, 404, cross_owner.text)
        self.assertEqual(cross_owner.json()["detail"]["code"], "ownerTruthVaultNotFound")

        self.store._owner_truth_vaults[vault_id]["authorityEpoch"] = 1
        stale = client.post(
            self._path(vault_id),
            headers=self._capture_headers(headers_a, session_id=session_a),
            json=self._payload(expected_epoch=0),
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(
            stale.json()["detail"]["code"],
            "ownerTruthSourceAuthorityEpochConflict",
        )
        self.assertEqual(self.store.owner_truth_source_count(vault_id), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
