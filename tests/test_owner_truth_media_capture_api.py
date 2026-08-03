from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main_module
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.main import app
from app.services.in_memory_store import InMemoryStore
from app.services.owner_truth_media_source_object import (
    DisabledMediaContentSafetyScanner,
    FilesystemPrivateMediaObjectStore,
    MediaUploadIntentCommand,
    OwnerTruthMediaIngestionService,
    OwnerTruthMediaUploadInvalid,
    TestOnlyCleanMediaContentSafetyScanner,
)


class OwnerTruthMediaCaptureAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = main_module.store
        self.previous_service = main_module.OWNER_TRUTH_MEDIA_INGESTION_SERVICE
        self.previous_backend_token = main_module.BACKEND_API_TOKEN
        self.previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        self.previous_route_mode = main_module.AUTH_ROUTE_MODE
        self.previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
        self.previous_closed_pilot_owner_ids = main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS
        self.previous_closed_pilot_features = set(
            main_module.RELEASE_POLICY_SERVICE.closed_pilot_enabled_features
        )
        self.media_root = TemporaryDirectory()
        self.store = InMemoryStore()
        main_module.store = self.store
        main_module.OWNER_TRUTH_MEDIA_INGESTION_SERVICE = OwnerTruthMediaIngestionService(
            store=self.store,
            object_store=FilesystemPrivateMediaObjectStore(root=self.media_root.name),
            safety_scanner=TestOnlyCleanMediaContentSafetyScanner(),
            enabled=True,
            max_upload_bytes=1024 * 1024,
            upload_intent_ttl_seconds=900,
        )
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = frozenset()
        main_module.RELEASE_POLICY_SERVICE.closed_pilot_enabled_features = {
            "ownerMediaCaptureV1"
        }
        self.client = TestClient(app)

    def tearDown(self) -> None:
        main_module.store = self.previous_store
        main_module.OWNER_TRUTH_MEDIA_INGESTION_SERVICE = self.previous_service
        main_module.BACKEND_API_TOKEN = self.previous_backend_token
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = self.previous_legacy_phone_login
        main_module.AUTH_ROUTE_MODE = self.previous_route_mode
        main_module.AUTH_OWNERSHIP_MODE = self.previous_ownership_mode
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = self.previous_closed_pilot_owner_ids
        main_module.RELEASE_POLICY_SERVICE.closed_pilot_enabled_features = (
            self.previous_closed_pilot_features
        )
        self.media_root.cleanup()

    def _login(self, phone: str) -> tuple[str, dict[str, str], str]:
        response = self.client.post(
            "/auth/login",
            json={"phone": phone, "nickname": "媒体 Source 测试", "password": "password123"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        return (
            str(body["user"]["id"]),
            {"Authorization": f"Bearer {body['auth']['accessToken']}"},
            str(body["auth"]["sessionId"]),
        )

    @staticmethod
    def _capture_headers(headers: dict[str, str], *, session_id: str) -> dict[str, str]:
        captured = dict(headers)
        captured.update(
            {
                "X-DreamJourney-Feature": "ownerMediaCaptureV1",
                "X-DreamJourney-Feature-Decision-Id": f"decision-{uuid4()}",
                "X-DreamJourney-Feature-Allowed": "true",
                "X-DreamJourney-Policy-Version": "release-policy-v1",
                "X-DreamJourney-Policy-Revision": "1",
                "X-DreamJourney-Account-Generation": sha256(
                    session_id.encode("utf-8")
                ).hexdigest()[:24],
            }
        )
        return captured

    @staticmethod
    def _intent_payload(*, body: bytes, command_id: str | None = None) -> dict[str, object]:
        return {
            "commandId": command_id or str(uuid4()),
            "expectedAuthorityEpoch": 0,
            "mediaKind": "document",
            "fileName": "memo.txt",
            "contentType": "text/plain",
            "fileSizeBytes": len(body),
            "contentSha256": sha256(body).hexdigest(),
            "purpose": "memoryCapture",
            "clientCreatedAt": "2026-08-03T00:00:00Z",
        }

    @staticmethod
    def _intent_path(vault_id: str) -> str:
        return f"/v2/vaults/{vault_id}/source-objects/upload-intents"

    def _allow_owner(self, owner_id: str) -> None:
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = frozenset(
            set(main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS) | {owner_id}
        )

    def test_authorized_owner_uploads_private_media_and_replay_does_not_leak_token(self) -> None:
        owner_id, auth_headers, session_id = self._login("13800139701")
        self._allow_owner(owner_id)
        headers = self._capture_headers(auth_headers, session_id=session_id)
        vault_id = "vault-media-upload"
        body = b"grandpa wrote this memory"
        payload = self._intent_payload(body=body)

        created = self.client.post(self._intent_path(vault_id), headers=headers, json=payload)

        self.assertEqual(created.status_code, 201, created.text)
        created_body = created.json()
        upload_intent = created_body["uploadIntent"]
        self.assertEqual(created_body["schemaVersion"], "owner-truth-media-upload-intent-v1")
        self.assertEqual(created_body["sourceObject"]["state"], "uploadPending")
        self.assertTrue(upload_intent["requiresClientUpload"])
        self.assertIn("uploadToken", upload_intent)
        self.assertNotIn(body.decode("utf-8"), json.dumps(created_body, ensure_ascii=False))

        uploaded = self.client.put(
            f"{self._intent_path(vault_id)}/{upload_intent['uploadIntentId']}/content",
            headers={
                **headers,
                "X-DreamJourney-Upload-Token": upload_intent["uploadToken"],
                "Content-Type": "text/plain",
            },
            content=body,
        )
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        source_object = uploaded.json()["sourceObject"]
        self.assertEqual(source_object["state"], "verified")
        self.assertEqual(source_object["magicMime"], "text/plain")
        self.assertEqual(source_object["safetyStatus"], "clean")
        self.assertEqual(source_object["processingStatus"], "notQueued")
        self.assertNotIn("storageKey", source_object)

        replay = self.client.post(self._intent_path(vault_id), headers=headers, json=payload)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["status"], "deduplicated")
        self.assertNotIn("uploadToken", replay.json()["uploadIntent"])

        object_id = source_object["sourceObjectId"]
        fetched = self.client.get(
            f"/v2/vaults/{vault_id}/source-objects/{object_id}",
            headers=headers,
        )
        self.assertEqual(fetched.status_code, 200, fetched.text)
        self.assertEqual(fetched.json()["sourceObject"]["sourceObjectId"], object_id)
        self.assertEqual(len(list(Path(self.media_root.name).rglob("*.bin"))), 1)

    def test_cross_owner_cannot_read_or_upload_another_owner_media(self) -> None:
        owner_a, headers_a, session_a = self._login("13800139702")
        owner_b, headers_b, session_b = self._login("13800139703")
        self._allow_owner(owner_a)
        self._allow_owner(owner_b)
        owner_a_headers = self._capture_headers(headers_a, session_id=session_a)
        owner_b_headers = self._capture_headers(headers_b, session_id=session_b)
        vault_id = "vault-media-isolation"
        body = b"owner a private media"

        created = self.client.post(
            self._intent_path(vault_id),
            headers=owner_a_headers,
            json=self._intent_payload(body=body),
        )
        self.assertEqual(created.status_code, 201, created.text)
        object_id = created.json()["sourceObject"]["sourceObjectId"]
        intent_id = created.json()["uploadIntent"]["uploadIntentId"]

        read_cross_owner = self.client.get(
            f"/v2/vaults/{vault_id}/source-objects/{object_id}",
            headers=owner_b_headers,
        )
        self.assertEqual(read_cross_owner.status_code, 404, read_cross_owner.text)
        self.assertEqual(read_cross_owner.json()["detail"]["code"], "ownerTruthMediaVaultNotFound")

        upload_cross_owner = self.client.put(
            f"{self._intent_path(vault_id)}/{intent_id}/content",
            headers={
                **owner_b_headers,
                "X-DreamJourney-Upload-Token": created.json()["uploadIntent"]["uploadToken"],
                "Content-Type": "text/plain",
            },
            content=body,
        )
        self.assertEqual(upload_cross_owner.status_code, 404, upload_cross_owner.text)

    def test_scanner_unavailable_quarantines_without_persisting_unscanned_bytes(self) -> None:
        unavailable_root = TemporaryDirectory()
        try:
            main_module.OWNER_TRUTH_MEDIA_INGESTION_SERVICE = OwnerTruthMediaIngestionService(
                store=self.store,
                object_store=FilesystemPrivateMediaObjectStore(root=unavailable_root.name),
                safety_scanner=DisabledMediaContentSafetyScanner(),
                enabled=True,
                max_upload_bytes=1024 * 1024,
                upload_intent_ttl_seconds=900,
            )
            owner_id, auth_headers, session_id = self._login("13800139704")
            self._allow_owner(owner_id)
            headers = self._capture_headers(auth_headers, session_id=session_id)
            vault_id = "vault-media-quarantine"
            body = b"requires a real safety scanner"
            created = self.client.post(
                self._intent_path(vault_id),
                headers=headers,
                json=self._intent_payload(body=body),
            )
            self.assertEqual(created.status_code, 201, created.text)
            intent = created.json()["uploadIntent"]

            uploaded = self.client.put(
                f"{self._intent_path(vault_id)}/{intent['uploadIntentId']}/content",
                headers={
                    **headers,
                    "X-DreamJourney-Upload-Token": intent["uploadToken"],
                    "Content-Type": "text/plain",
                },
                content=body,
            )
            self.assertEqual(uploaded.status_code, 202, uploaded.text)
            source_object = uploaded.json()["sourceObject"]
            self.assertEqual(source_object["state"], "quarantined")
            self.assertEqual(source_object["safetyStatus"], "unavailable")
            self.assertTrue(source_object["retryable"])
            self.assertEqual(source_object["failureCode"], "contentSafetyScannerUnavailable")
            self.assertEqual(list(Path(unavailable_root.name).rglob("*.bin")), [])
        finally:
            unavailable_root.cleanup()

    def test_magic_mime_mismatch_rejects_before_private_write(self) -> None:
        body = b"not a pdf"
        command = MediaUploadIntentCommand.from_payload(
            {
                **self._intent_payload(body=body),
                "mediaKind": "document",
                "fileName": "memo.pdf",
                "contentType": "application/pdf",
            }
        )
        context = OwnerTruthCommandContext(
            vault_id="vault-magic-mismatch",
            owner_subject_id="owner-magic-mismatch",
            actor_subject_id="owner-magic-mismatch",
        )
        service = main_module.OWNER_TRUTH_MEDIA_INGESTION_SERVICE
        intent = service.create_upload_intent(context=context, command=command)
        with self.assertRaises(OwnerTruthMediaUploadInvalid):
            service.upload_content(
                context=context,
                intent_id=str(intent.upload_intent["uploadIntentId"]),
                upload_token=str(intent.upload_token),
                payload=body,
                request_content_type="application/pdf",
            )
        self.assertEqual(list(Path(self.media_root.name).rglob("*.bin")), [])

