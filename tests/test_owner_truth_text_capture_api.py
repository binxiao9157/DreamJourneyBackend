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
        self.previous_authenticated_owner_v4_enabled = (
            main_module.RELEASE_POLICY_SERVICE.authenticated_owner_v4_enabled
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
        main_module.RELEASE_POLICY_SERVICE.authenticated_owner_v4_enabled = True

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
        main_module.RELEASE_POLICY_SERVICE.authenticated_owner_v4_enabled = (
            self.previous_authenticated_owner_v4_enabled
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
    def _state_path(vault_id: str) -> str:
        return f"/v2/vaults/{vault_id}/source-capture-state"

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

    def test_authenticated_owner_can_capture_without_pilot_but_anonymous_cannot(self) -> None:
        _owner_id, auth_headers, session_id = self._login("13800139952")
        authenticated = client.post(
            self._path("vault-forged-owner-text-capture"),
            headers={
                **self._capture_headers(auth_headers, session_id=session_id),
                "X-DreamJourney-QA-Owner-Truth": "1",
            },
            json=self._payload(),
        )

        self.assertEqual(authenticated.status_code, 201, authenticated.text)
        self.assertEqual(self.store.owner_truth_source_count("vault-forged-owner-text-capture"), 1)

        anonymous = client.post(
            self._path("vault-anonymous-owner-text-capture"),
            headers={"X-DreamJourney-QA-Owner-Truth": "1"},
            json=self._payload(),
        )
        self.assertEqual(anonymous.status_code, 401, anonymous.text)
        self.assertEqual(
            anonymous.json()["detail"]["code"],
            "route_authentication_denied",
        )
        self.assertEqual(self.store.owner_truth_source_count("vault-anonymous-owner-text-capture"), 0)

    def test_v2_authority_owner_cannot_create_new_legacy_archive_authority(self) -> None:
        owner_id, auth_headers, _session_id = self._login("13800139962")
        self._allow_owner(owner_id)

        for kind in ("text", "photo", "audio", "video"):
            with self.subTest(kind=kind):
                response = client.post(
                    "/archive/items",
                    headers=auth_headers,
                    json={
                        "id": f"legacy-{kind}-must-not-write",
                        "userId": owner_id,
                        "kind": kind,
                        "note": "不得形成第二套权威数据",
                    },
                )
                if kind in {"audio", "video"}:
                    self.assertEqual(response.status_code, 403, response.text)
                    detail = response.json()["detail"]
                    self.assertEqual(detail["code"], "release_policy_denied")
                    self.assertEqual(detail["reason"], "productClosed")
                    continue
                self.assertEqual(response.status_code, 409, response.text)
                detail = response.json()["detail"]
                self.assertEqual(detail["code"], "legacyArchiveAuthorityRetired")
                self.assertEqual(detail["authority"], "ownerTruthV2")
                self.assertEqual(detail["requiredRoute"], "/v2/vaults/{vaultId}/sources")

        self.assertEqual(self.store.list_archive_items(owner_id), [])

    def test_v2_authority_switch_does_not_retire_time_letter_contract(self) -> None:
        owner_id, _auth_headers, _session_id = self._login("13800139963")
        self._allow_owner(owner_id)

        self.assertIsNone(
            main_module._legacy_archive_v2_authority_retirement(
                owner_user_id=owner_id,
                payload={"kind": "timeLetter"},
            )
        )
        self.assertEqual(
            main_module._legacy_archive_v2_authority_retirement(
                owner_user_id=owner_id,
                payload={"kind": "photo"},
            ),
            {
                "code": "legacyArchiveAuthorityRetired",
                "authority": "ownerTruthV2",
                "feature": "ownerTextCaptureV1",
                "requiredRoute": "/v2/vaults/{vaultId}/sources",
                "retryable": False,
            },
        )

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

    def test_authorized_owner_reads_value_minimized_source_capture_state(self) -> None:
        owner_a, headers_a, session_a = self._login("13800139955")
        owner_b, headers_b, session_b = self._login("13800139956")
        self._allow_owner(owner_a)
        self._allow_owner(owner_b)
        vault_id = "vault-text-capture-state"

        initial = client.get(
            self._state_path(vault_id),
            headers=self._capture_headers(headers_a, session_id=session_a),
        )
        self.assertEqual(initial.status_code, 200, initial.text)
        self.assertEqual(
            initial.json(),
            {
                "schemaVersion": "owner-truth-text-capture-state-v1",
                "vaultId": vault_id,
                "authorityEpoch": 0,
            },
        )

        created = client.post(
            self._path(vault_id),
            headers=self._capture_headers(headers_a, session_id=session_a),
            json=self._payload(),
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.store._owner_truth_vaults[vault_id]["authorityEpoch"] = 2

        current = client.get(
            self._state_path(vault_id),
            headers=self._capture_headers(headers_a, session_id=session_a),
        )
        self.assertEqual(current.status_code, 200, current.text)
        self.assertEqual(
            current.json(),
            {
                "schemaVersion": "owner-truth-text-capture-state-v1",
                "vaultId": vault_id,
                "authorityEpoch": 2,
            },
        )
        self.assertNotIn(owner_a, json.dumps(current.json(), ensure_ascii=False))

        cross_owner = client.get(
            self._state_path(vault_id),
            headers=self._capture_headers(headers_b, session_id=session_b),
        )
        self.assertEqual(cross_owner.status_code, 404, cross_owner.text)
        self.assertEqual(cross_owner.json()["detail"]["code"], "ownerTruthVaultNotFound")

        qa_only = client.get(
            self._state_path(vault_id),
            headers={
                **headers_b,
                "X-DreamJourney-QA-Owner-Truth": "1",
            },
        )
        self.assertEqual(qa_only.status_code, 403, qa_only.text)
        self.assertEqual(qa_only.json()["detail"]["code"], "release_policy_denied")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
