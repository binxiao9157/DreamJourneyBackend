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
    S3PrivateMediaObjectStore,
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
        self.assertFalse(source_object["externalProcessingAllowed"])
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

    def test_image_upload_intent_persists_explicit_external_processing_permission(self) -> None:
        owner_id, auth_headers, session_id = self._login("13800139711")
        self._allow_owner(owner_id)
        headers = self._capture_headers(auth_headers, session_id=session_id)
        vault_id = "vault-media-external-consent"
        body = b"\x89PNG\r\n\x1a\nprivate-image-content"
        payload = {
            **self._intent_payload(body=body),
            "mediaKind": "image",
            "fileName": "private.png",
            "contentType": "image/png",
            "allowExternalProcessing": True,
        }

        created = self.client.post(self._intent_path(vault_id), headers=headers, json=payload)

        self.assertEqual(created.status_code, 201, created.text)
        source_object = created.json()["sourceObject"]
        self.assertTrue(source_object["externalProcessingAllowed"])
        self.assertNotIn("storageKey", source_object)

        invalid_document = {
            **self._intent_payload(body=b"private text"),
            "allowExternalProcessing": True,
        }
        rejected = self.client.post(self._intent_path(vault_id), headers=headers, json=invalid_document)
        self.assertEqual(rejected.status_code, 400, rejected.text)
        self.assertEqual(rejected.json()["detail"]["code"], "ownerTruthMediaUploadInvalid")

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

    def test_verified_upload_queues_private_processing_without_exposing_an_effect_id(self) -> None:
        main_module.OWNER_TRUTH_MEDIA_INGESTION_SERVICE = OwnerTruthMediaIngestionService(
            store=self.store,
            object_store=FilesystemPrivateMediaObjectStore(root=self.media_root.name),
            safety_scanner=TestOnlyCleanMediaContentSafetyScanner(),
            enabled=True,
            max_upload_bytes=1024 * 1024,
            upload_intent_ttl_seconds=900,
            on_verified=main_module._queue_verified_owner_truth_media_processing,
        )
        owner_id, auth_headers, session_id = self._login("13800139705")
        self._allow_owner(owner_id)
        headers = self._capture_headers(auth_headers, session_id=session_id)
        vault_id = "vault-media-processing-queue"
        body = b"queue this private source for processing"
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

        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        source_object = uploaded.json()["sourceObject"]
        self.assertEqual(source_object["state"], "verified")
        self.assertEqual(source_object["processingStatus"], "queued")
        self.assertEqual(source_object["processingGeneration"], 1)
        self.assertNotIn("jobId", source_object)
        self.assertNotIn("storageKey", source_object)
        self.assertEqual(self.store.effect_kernel_repository().record_count(), 1)

    def test_terminal_media_failure_can_start_a_new_private_processing_generation(self) -> None:
        main_module.OWNER_TRUTH_MEDIA_INGESTION_SERVICE = OwnerTruthMediaIngestionService(
            store=self.store,
            object_store=FilesystemPrivateMediaObjectStore(root=self.media_root.name),
            safety_scanner=TestOnlyCleanMediaContentSafetyScanner(),
            enabled=True,
            max_upload_bytes=1024 * 1024,
            upload_intent_ttl_seconds=900,
            on_verified=main_module._queue_verified_owner_truth_media_processing,
        )
        owner_id, auth_headers, session_id = self._login("13800139706")
        self._allow_owner(owner_id)
        headers = self._capture_headers(auth_headers, session_id=session_id)
        vault_id = "vault-media-processing-retry"
        body = b"retry a private processing request"
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
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        source_object = uploaded.json()["sourceObject"]
        self.store.owner_truth_media_source_object_repository().record_processing_outcome(
            vault_id=vault_id,
            source_object_id=source_object["sourceObjectId"],
            owner_subject_id=owner_id,
            processing_generation=1,
            attempt=3,
            processor_id="localDocumentText",
            processor_version="v1",
            outcome="failed",
            result_hash=sha256(b"terminal-media-processing-failure").hexdigest(),
            failure_code="documentParserUnavailable",
        )

        retried = self.client.post(
            f"/v2/vaults/{vault_id}/source-objects/{source_object['sourceObjectId']}/processing-retries",
            headers=headers,
        )

        self.assertEqual(retried.status_code, 202, retried.text)
        retried_object = retried.json()["sourceObject"]
        self.assertEqual(retried.json()["status"], "processingRequested")
        self.assertEqual(retried_object["state"], "verified")
        self.assertEqual(retried_object["processingStatus"], "queued")
        self.assertEqual(retried_object["processingGeneration"], 2)
        self.assertNotIn("jobId", retried_object)
        self.assertNotIn("storageKey", retried_object)

        duplicate = self.client.post(
            f"/v2/vaults/{vault_id}/source-objects/{source_object['sourceObjectId']}/processing-retries",
            headers=headers,
        )
        self.assertEqual(duplicate.status_code, 409, duplicate.text)

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

    def test_private_s3_adapter_keeps_keys_server_side_and_uses_no_public_acl(self) -> None:
        client = _FakeS3Client()
        adapter = S3PrivateMediaObjectStore(
            bucket="dreamjourney-private-media",
            prefix="owner-truth/v1",
            region="ap-shanghai",
            endpoint_url="https://cos.example.test",
            client=client,
        )

        adapter.write(storage_key="vault-a/object-a.bin", payload=b"private-bytes")

        self.assertEqual(adapter.read(storage_key="vault-a/object-a.bin"), b"private-bytes")
        self.assertEqual(client.put_requests[0]["Bucket"], "dreamjourney-private-media")
        self.assertEqual(client.put_requests[0]["Key"], "owner-truth/v1/vault-a/object-a.bin")
        self.assertNotIn("ACL", client.put_requests[0])
        adapter.delete(storage_key="vault-a/object-a.bin")
        self.assertEqual(client.delete_requests[0]["Key"], "owner-truth/v1/vault-a/object-a.bin")


class _FakeS3Body:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload


class _FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.put_requests: list[dict[str, object]] = []
        self.delete_requests: list[dict[str, object]] = []

    def put_object(self, **kwargs: object) -> None:
        self.put_requests.append(dict(kwargs))
        self.objects[(str(kwargs["Bucket"]), str(kwargs["Key"]))] = bytes(kwargs["Body"])

    def get_object(self, **kwargs: object) -> dict[str, object]:
        payload = self.objects[(str(kwargs["Bucket"]), str(kwargs["Key"]))]
        return {"Body": _FakeS3Body(payload)}

    def delete_object(self, **kwargs: object) -> None:
        self.delete_requests.append(dict(kwargs))
        self.objects.pop((str(kwargs["Bucket"]), str(kwargs["Key"])), None)
