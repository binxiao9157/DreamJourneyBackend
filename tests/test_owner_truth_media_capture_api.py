from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main_module
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.main import app
from app.services.in_memory_store import InMemoryStore
from app.services.owner_truth_media_source_object import (
    ClamAVDaemonMediaContentSafetyScanner,
    DisabledMediaContentSafetyScanner,
    FilesystemPrivateMediaObjectStore,
    MediaDeletionCommand,
    MediaUploadIntentCommand,
    OwnerTruthMediaAccessRevoked,
    OwnerTruthMediaCaptureUnavailable,
    OwnerTruthMediaIngestionService,
    OwnerTruthMediaUploadConflict,
    OwnerTruthMediaUploadInvalid,
    S3PrivateMediaObjectStore,
    TestOnlyCleanMediaContentSafetyScanner,
    build_private_media_object_store,
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
        self.previous_capability_resolver = (
            main_module.RELEASE_POLICY_SERVICE.capability_resolver
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
            "ownerMediaCaptureV1",
            "ownerMediaProcessingV1",
        }
        main_module.RELEASE_POLICY_SERVICE.capability_resolver = lambda capability: capability in {
            "ownerTruthMediaStorage",
            "ownerTruthMediaProcessing",
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
        main_module.RELEASE_POLICY_SERVICE.capability_resolver = (
            self.previous_capability_resolver
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
    def _capture_headers(
        headers: dict[str, str],
        *,
        session_id: str,
        feature: str = "ownerMediaCaptureV1",
    ) -> dict[str, str]:
        captured = dict(headers)
        captured.update(
            {
                "X-DreamJourney-Feature": feature,
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

    @staticmethod
    def _deletion_payload(*, command_id: str | None = None) -> dict[str, object]:
        return {
            "commandId": command_id or str(uuid4()),
            "expectedAuthorityEpoch": 0,
            "clientRequestedAt": "2026-08-05T00:00:00Z",
        }

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

    def test_markdown_upload_accepts_utf8_magic_alias_and_rejects_disguised_pdf(self) -> None:
        owner_id, auth_headers, session_id = self._login("13800139712")
        self._allow_owner(owner_id)
        headers = self._capture_headers(auth_headers, session_id=session_id)
        vault_id = "vault-markdown-upload"
        markdown = b"# Family memory\n\nA sourced paragraph."
        payload = {
            **self._intent_payload(body=markdown),
            "fileName": "family-memory.md",
            "contentType": "text/markdown",
        }

        created = self.client.post(self._intent_path(vault_id), headers=headers, json=payload)

        self.assertEqual(created.status_code, 201, created.text)
        upload_intent = created.json()["uploadIntent"]
        uploaded = self.client.put(
            f"{self._intent_path(vault_id)}/{upload_intent['uploadIntentId']}/content",
            headers={
                **headers,
                "X-DreamJourney-Upload-Token": upload_intent["uploadToken"],
                "Content-Type": "text/markdown",
            },
            content=markdown,
        )
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        source_object = uploaded.json()["sourceObject"]
        self.assertEqual(source_object["contentType"], "text/markdown")
        self.assertEqual(source_object["magicMime"], "text/plain")
        self.assertFalse(source_object["externalProcessingAllowed"])

        disguised_pdf = b"%PDF-1.4\n%%EOF\n"
        disguised_payload = {
            **self._intent_payload(body=disguised_pdf),
            "fileName": "disguised.md",
            "contentType": "text/markdown",
        }
        disguised_created = self.client.post(
            self._intent_path(vault_id),
            headers=headers,
            json=disguised_payload,
        )
        self.assertEqual(disguised_created.status_code, 201, disguised_created.text)
        disguised_intent = disguised_created.json()["uploadIntent"]
        rejected = self.client.put(
            f"{self._intent_path(vault_id)}/{disguised_intent['uploadIntentId']}/content",
            headers={
                **headers,
                "X-DreamJourney-Upload-Token": disguised_intent["uploadToken"],
                "Content-Type": "text/markdown",
            },
            content=disguised_pdf,
        )
        self.assertEqual(rejected.status_code, 400, rejected.text)
        self.assertEqual(rejected.json()["detail"]["code"], "ownerTruthMediaUploadInvalid")

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

        delete_cross_owner = self.client.post(
            f"/v2/vaults/{vault_id}/source-objects/{object_id}/deletions",
            headers=owner_b_headers,
            json=self._deletion_payload(),
        )
        self.assertEqual(delete_cross_owner.status_code, 404, delete_cross_owner.text)
        self.assertEqual(
            delete_cross_owner.json()["detail"]["code"],
            "ownerTruthMediaVaultNotFound",
        )

        delete_retry_cross_owner = self.client.post(
            f"/v2/vaults/{vault_id}/source-objects/{object_id}/deletion-retries",
            headers=owner_b_headers,
            json=self._deletion_payload(),
        )
        self.assertEqual(delete_retry_cross_owner.status_code, 404, delete_retry_cross_owner.text)
        self.assertEqual(
            delete_retry_cross_owner.json()["detail"]["code"],
            "ownerTruthMediaVaultNotFound",
        )

    def test_deletion_revokes_access_cancels_processing_and_returns_only_sanitized_status(self) -> None:
        owner_id, auth_headers, session_id = self._login("13800139731")
        self._allow_owner(owner_id)
        headers = self._capture_headers(auth_headers, session_id=session_id)
        vault_id = "vault-media-delete"
        body = b"private media must be revoked before physical deletion"
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
        object_id = uploaded.json()["sourceObject"]["sourceObjectId"]
        deletion_payload = self._deletion_payload()

        deleted = self.client.post(
            f"/v2/vaults/{vault_id}/source-objects/{object_id}/deletions",
            headers=headers,
            json=deletion_payload,
        )

        self.assertEqual(deleted.status_code, 202, deleted.text)
        body_json = deleted.json()
        self.assertEqual(body_json["schemaVersion"], "owner-truth-media-deletion-response-v1")
        self.assertEqual(body_json["status"], "deletionRequested")
        self.assertEqual(body_json["sourceObject"]["state"], "deleted")
        self.assertEqual(body_json["sourceObject"]["processingStatus"], "blocked")
        self.assertEqual(
            set(body_json["deletion"]),
            {"accessState", "deletionStatus", "retryable", "failureCode", "updatedAt"},
        )
        self.assertEqual(body_json["deletion"]["accessState"], "accessRevoked")
        self.assertEqual(body_json["deletion"]["deletionStatus"], "pending")
        self.assertTrue(body_json["deletion"]["retryable"])
        rendered = json.dumps(body_json, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("storageKey", rendered)
        self.assertNotIn("storageProvider", rendered)
        self.assertNotIn(body.decode("utf-8"), rendered)
        # P0-S1 is revocation-first: the later worker owns physical deletion.
        self.assertEqual(len(list(Path(self.media_root.name).rglob("*.bin"))), 1)
        self.assertEqual(self.store.effect_kernel_repository().record_count(), 1)

        replay = self.client.post(
            f"/v2/vaults/{vault_id}/source-objects/{object_id}/deletions",
            headers=headers,
            json=deletion_payload,
        )
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["status"], "deletionDeduplicated")

        retry = self.client.post(
            f"/v2/vaults/{vault_id}/source-objects/{object_id}/processing-retries",
            headers=self._capture_headers(
                auth_headers,
                session_id=session_id,
                feature="ownerMediaProcessingV1",
            ),
        )
        self.assertEqual(retry.status_code, 409, retry.text)
        self.assertEqual(retry.json()["detail"]["code"], "ownerTruthMediaAccessRevoked")

        fetched = self.client.get(
            f"/v2/vaults/{vault_id}/source-objects/{object_id}",
            headers=headers,
        )
        self.assertEqual(fetched.status_code, 200, fetched.text)
        self.assertEqual(fetched.json()["sourceObject"]["state"], "deleted")
        self.assertEqual(fetched.json()["sourceObject"]["processingStatus"], "blocked")

    def test_retryable_deletion_requeues_only_the_deletion_effect(self) -> None:
        owner_id, auth_headers, session_id = self._login("13800139733")
        self._allow_owner(owner_id)
        headers = self._capture_headers(auth_headers, session_id=session_id)
        vault_id = "vault-media-deletion-retry"
        body = b"the deletion retry must never restore private media access"
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
        object_id = uploaded.json()["sourceObject"]["sourceObjectId"]
        deleted = self.client.post(
            f"/v2/vaults/{vault_id}/source-objects/{object_id}/deletions",
            headers=headers,
            json=self._deletion_payload(),
        )
        self.assertEqual(deleted.status_code, 202, deleted.text)
        repository = self.store.owner_truth_media_source_object_repository()
        pending = repository.get_source_object(
            vault_id=vault_id,
            source_object_id=object_id,
            owner_subject_id=owner_id,
        )
        partial = repository.record_deletion_outcome(
            vault_id=vault_id,
            source_object_id=object_id,
            owner_subject_id=owner_id,
            deletion_generation=int(pending["deletionGeneration"]),
            outcome="partial",
            retryable=True,
            failure_code="objectStorageUnavailable",
        )
        self.assertEqual(partial["state"], "deleted")
        self.assertEqual(partial["deletionStatus"], "partial")
        self.assertTrue(partial["deletionRetryable"])

        retry_payload = self._deletion_payload()
        retried = self.client.post(
            f"/v2/vaults/{vault_id}/source-objects/{object_id}/deletion-retries",
            headers=headers,
            json=retry_payload,
        )
        self.assertEqual(retried.status_code, 202, retried.text)
        retried_body = retried.json()
        self.assertEqual(retried_body["status"], "deletionRetryRequested")
        self.assertEqual(retried_body["sourceObject"]["state"], "deleted")
        self.assertEqual(retried_body["sourceObject"]["processingStatus"], "blocked")
        self.assertEqual(retried_body["deletion"]["deletionStatus"], "pending")
        self.assertTrue(retried_body["deletion"]["retryable"])
        self.assertEqual(self.store.effect_kernel_repository().record_count(), 2)

        replay = self.client.post(
            f"/v2/vaults/{vault_id}/source-objects/{object_id}/deletion-retries",
            headers=headers,
            json=retry_payload,
        )
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["status"], "deletionDeduplicated")
        self.assertEqual(self.store.effect_kernel_repository().record_count(), 2)

        with self.assertRaises(OwnerTruthMediaUploadConflict):
            repository.record_deletion_outcome(
                vault_id=vault_id,
                source_object_id=object_id,
                owner_subject_id=owner_id,
                deletion_generation=int(pending["deletionGeneration"]),
                outcome="completed",
                retryable=False,
            )
        final = repository.get_source_object(
            vault_id=vault_id,
            source_object_id=object_id,
            owner_subject_id=owner_id,
        )
        self.assertEqual(final["state"], "deleted")
        self.assertEqual(final["accessState"], "accessRevoked")
        self.assertEqual(final["processingStatus"], "blocked")

    def test_deletion_commit_fence_blocks_inflight_processing_result(self) -> None:
        owner_id, auth_headers, session_id = self._login("13800139732")
        self._allow_owner(owner_id)
        headers = self._capture_headers(auth_headers, session_id=session_id)
        vault_id = "vault-media-delete-race"
        body = b"do not allow a deleted file to create a candidate"
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
        repository = self.store.owner_truth_media_source_object_repository()
        queued = repository.queue_processing(
            vault_id=vault_id,
            source_object_id=source_object["sourceObjectId"],
            owner_subject_id=owner_id,
        )
        begun = repository.begin_processing(
            vault_id=vault_id,
            source_object_id=source_object["sourceObjectId"],
            owner_subject_id=owner_id,
            expected_authority_epoch=0,
            expected_processing_generation=int(queued["processingGeneration"]),
            attempt=1,
        )
        context = OwnerTruthCommandContext(
            vault_id=vault_id,
            owner_subject_id=owner_id,
            actor_subject_id=owner_id,
        )
        deletion = repository.request_deletion(
            context=context,
            source_object_id=source_object["sourceObjectId"],
            command=MediaDeletionCommand.from_payload(self._deletion_payload()),
        )
        self.assertEqual(deletion.source_object["state"], "deleted")
        with self.assertRaises(OwnerTruthMediaAccessRevoked):
            repository.assert_processing_commit_allowed(
                vault_id=vault_id,
                source_object_id=source_object["sourceObjectId"],
                owner_subject_id=owner_id,
                expected_processing_generation=int(begun["processingGeneration"]),
            )
        with self.assertRaises(OwnerTruthMediaAccessRevoked):
            repository.record_processing_outcome(
                vault_id=vault_id,
                source_object_id=source_object["sourceObjectId"],
                owner_subject_id=owner_id,
                processing_generation=int(begun["processingGeneration"]),
                attempt=1,
                processor_id="localDocumentText",
                processor_version="v1",
                outcome="succeeded",
                result_hash=sha256(b"deleted-media-must-not-commit").hexdigest(),
                extracted_text_sha256=sha256(body).hexdigest(),
                derived_source_id=str(uuid4()),
            )
        final = repository.get_source_object(
            vault_id=vault_id,
            source_object_id=source_object["sourceObjectId"],
            owner_subject_id=owner_id,
        )
        self.assertEqual(final["state"], "deleted")
        self.assertEqual(final["processingStatus"], "blocked")
        self.assertIsNone(final["derivedSourceId"])

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

    def test_clamav_sidecar_timeout_quarantines_before_object_store_write(self) -> None:
        unavailable_root = TemporaryDirectory()
        try:
            main_module.OWNER_TRUTH_MEDIA_INGESTION_SERVICE = OwnerTruthMediaIngestionService(
                store=self.store,
                object_store=FilesystemPrivateMediaObjectStore(root=unavailable_root.name),
                safety_scanner=ClamAVDaemonMediaContentSafetyScanner(
                    host="clamav",
                    timeout_seconds=1,
                ),
                enabled=True,
                max_upload_bytes=1024 * 1024,
                upload_intent_ttl_seconds=900,
            )
            owner_id, auth_headers, session_id = self._login("13800139714")
            self._allow_owner(owner_id)
            headers = self._capture_headers(auth_headers, session_id=session_id)
            vault_id = "vault-media-sidecar-timeout"
            body = b"sidecar timeout must not persist bytes"
            created = self.client.post(
                self._intent_path(vault_id),
                headers=headers,
                json=self._intent_payload(body=body),
            )
            self.assertEqual(created.status_code, 201, created.text)
            intent = created.json()["uploadIntent"]

            with patch(
                "app.services.owner_truth_media_source_object.socket.create_connection",
                side_effect=socket.timeout(),
            ):
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
            headers=self._capture_headers(
                auth_headers,
                session_id=session_id,
                feature="ownerMediaProcessingV1",
            ),
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
            headers=self._capture_headers(
                auth_headers,
                session_id=session_id,
                feature="ownerMediaProcessingV1",
            ),
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
            provider_name="cos",
            bucket="dreamjourney-private-media",
            prefix="owner-truth/v1",
            region="ap-shanghai",
            endpoint_url="https://cos.ap-shanghai.myqcloud.com",
            server_side_encryption="AES256",
            client=client,
        )

        payload = b"private-bytes"
        adapter.write(
            storage_key="vault-a/object-a.bin",
            payload=payload,
            content_type="text/plain",
            content_sha256=sha256(payload).hexdigest(),
        )
        adapter.verify_upload(
            storage_key="vault-a/object-a.bin",
            expected_file_size_bytes=len(payload),
            expected_content_type="text/plain",
            expected_content_sha256=sha256(payload).hexdigest(),
        )

        self.assertEqual(adapter.read(storage_key="vault-a/object-a.bin"), b"private-bytes")
        with self.assertRaises(OwnerTruthMediaCaptureUnavailable):
            adapter.read(storage_key="vault-a/object-a.bin", max_bytes=4)
        self.assertEqual(client.put_requests[0]["Bucket"], "dreamjourney-private-media")
        self.assertEqual(client.put_requests[0]["Key"], "owner-truth/v1/vault-a/object-a.bin")
        self.assertEqual(client.put_requests[0]["ContentType"], "text/plain")
        self.assertEqual(
            client.put_requests[0]["Metadata"],
            {"dreamjourney-sha256": sha256(payload).hexdigest()},
        )
        self.assertEqual(client.put_requests[0]["ServerSideEncryption"], "AES256")
        self.assertNotIn("ACL", client.put_requests[0])
        self.assertEqual(client.head_requests[0]["Key"], "owner-truth/v1/vault-a/object-a.bin")
        adapter.delete(storage_key="vault-a/object-a.bin")
        self.assertEqual(client.delete_requests[0]["Key"], "owner-truth/v1/vault-a/object-a.bin")

    def test_cos_factory_fails_closed_until_explicit_sse_and_https_endpoint_exist(self) -> None:
        disabled = build_private_media_object_store(
            provider="cos",
            root=self.media_root.name,
            s3_bucket="fixture-private-media-1250000000",
            s3_region="ap-shanghai",
            s3_endpoint_url="http://cos.ap-shanghai.myqcloud.com",
            s3_access_key_id="fixture-access",
            s3_secret_access_key="fixture-secret",
            s3_server_side_encryption="AES256",
        )
        self.assertEqual(disabled.provider_name, "disabled")

        adapter = build_private_media_object_store(
            provider="cos",
            root=self.media_root.name,
            s3_bucket="fixture-private-media-1250000000",
            s3_region="ap-shanghai",
            s3_endpoint_url="https://cos.ap-shanghai.myqcloud.com",
            s3_access_key_id="fixture-access",
            s3_secret_access_key="fixture-secret",
            s3_server_side_encryption="AES256",
        )
        self.assertEqual(adapter.provider_name, "cos")

        mismatched_region = build_private_media_object_store(
            provider="cos",
            root=self.media_root.name,
            s3_bucket="fixture-private-media-1250000000",
            s3_region="ap-guangzhou",
            s3_endpoint_url="https://cos.ap-shanghai.myqcloud.com",
            s3_access_key_id="fixture-access",
            s3_secret_access_key="fixture-secret",
            s3_server_side_encryption="AES256",
        )
        self.assertEqual(mismatched_region.provider_name, "disabled")

    def test_cos_delete_requires_provider_acknowledgement_and_absence_verification(self) -> None:
        client = _FakeS3Client()
        client.delete_removes_object = False
        adapter = S3PrivateMediaObjectStore(
            provider_name="cos",
            bucket="dreamjourney-private-media",
            prefix="owner-truth/v1",
            region="ap-shanghai",
            endpoint_url="https://cos.ap-shanghai.myqcloud.com",
            server_side_encryption="AES256",
            client=client,
        )
        payload = b"private-delete-verification"
        storage_key = "vault-a/object-delete.bin"
        adapter.write(
            storage_key=storage_key,
            payload=payload,
            content_type="text/plain",
            content_sha256=sha256(payload).hexdigest(),
        )

        client.delete_response = {}
        with self.assertRaises(OwnerTruthMediaCaptureUnavailable):
            adapter.delete(storage_key=storage_key)

        client.delete_response = {"ResponseMetadata": {"HTTPStatusCode": 204}}
        with self.assertRaises(OwnerTruthMediaCaptureUnavailable):
            adapter.delete(storage_key=storage_key)

        client.delete_removes_object = True
        adapter.delete(storage_key=storage_key)
        self.assertEqual(len(client.delete_requests), 3)

    def test_cos_head_mismatch_keeps_source_object_unverified(self) -> None:
        payload = b"private source text"
        client = _FakeS3Client()
        client.head_override = {"ContentLength": len(payload) + 1}
        service = OwnerTruthMediaIngestionService(
            store=InMemoryStore(),
            object_store=S3PrivateMediaObjectStore(
                provider_name="cos",
                bucket="dreamjourney-private-media",
                prefix="owner-truth/v1",
                region="ap-shanghai",
                endpoint_url="https://cos.ap-shanghai.myqcloud.com",
                server_side_encryption="AES256",
                client=client,
            ),
            safety_scanner=TestOnlyCleanMediaContentSafetyScanner(),
            enabled=True,
            max_upload_bytes=1024 * 1024,
            upload_intent_ttl_seconds=900,
        )
        context = OwnerTruthCommandContext(
            vault_id="vault-cos-head-mismatch",
            owner_subject_id="owner-cos-head-mismatch",
            actor_subject_id="owner-cos-head-mismatch",
        )
        intent = service.create_upload_intent(
            context=context,
            command=MediaUploadIntentCommand.from_payload(self._intent_payload(body=payload)),
        )

        with self.assertRaises(OwnerTruthMediaCaptureUnavailable):
            service.upload_content(
                context=context,
                intent_id=str(intent.upload_intent["uploadIntentId"]),
                upload_token=str(intent.upload_token),
                payload=payload,
                request_content_type="text/plain",
            )

        source_object = service.get_source_object(
            context=context,
            source_object_id=str(intent.source_object["sourceObjectId"]),
        )
        self.assertEqual(source_object["state"], "uploadPending")
        self.assertEqual(len(client.delete_requests), 1)

    def test_authorized_owner_can_read_private_content_without_storage_details(self) -> None:
        owner_id, auth_headers, session_id = self._login("13800139720")
        self._allow_owner(owner_id)
        headers = self._capture_headers(auth_headers, session_id=session_id)
        vault_id = "vault-media-content-read"
        payload = b"private media content"
        created = self.client.post(
            self._intent_path(vault_id),
            headers=headers,
            json=self._intent_payload(body=payload),
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
            content=payload,
        )
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        source_object_id = uploaded.json()["sourceObject"]["sourceObjectId"]

        downloaded = self.client.get(
            f"/v2/vaults/{vault_id}/source-objects/{source_object_id}/content",
            headers=headers,
        )

        self.assertEqual(downloaded.status_code, 200, downloaded.text)
        self.assertEqual(downloaded.content, payload)
        self.assertEqual(downloaded.headers["content-type"], "text/plain; charset=utf-8")
        self.assertEqual(downloaded.headers["cache-control"], "no-store")
        self.assertEqual(downloaded.headers["content-disposition"], "attachment")
        self.assertNotIn("storageKey", downloaded.text)

        _, other_headers, other_session = self._login("13800139721")
        other_response = self.client.get(
            f"/v2/vaults/{vault_id}/source-objects/{source_object_id}/content",
            headers=self._capture_headers(other_headers, session_id=other_session),
        )
        self.assertIn(other_response.status_code, {403, 404})


class _FakeS3Body:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self, size: int = -1) -> bytes:
        return self._payload if size < 0 else self._payload[:size]


class _FakeS3ClientError(Exception):
    def __init__(self, *, status: int, code: str) -> None:
        super().__init__(code)
        self.response = {
            "ResponseMetadata": {"HTTPStatusCode": status},
            "Error": {"Code": code},
        }


class _FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.object_metadata: dict[tuple[str, str], dict[str, object]] = {}
        self.put_requests: list[dict[str, object]] = []
        self.head_requests: list[dict[str, object]] = []
        self.delete_requests: list[dict[str, object]] = []
        self.head_override: dict[str, object] | None = None
        self.delete_response: dict[str, object] = {
            "ResponseMetadata": {"HTTPStatusCode": 204},
        }
        self.delete_removes_object = True

    def put_object(self, **kwargs: object) -> None:
        self.put_requests.append(dict(kwargs))
        identifier = (str(kwargs["Bucket"]), str(kwargs["Key"]))
        self.objects[identifier] = bytes(kwargs["Body"])
        self.object_metadata[identifier] = {
            "ContentType": str(kwargs.get("ContentType") or "application/octet-stream"),
            "Metadata": dict(kwargs.get("Metadata") or {}),
            "ServerSideEncryption": kwargs.get("ServerSideEncryption"),
        }

    def head_object(self, **kwargs: object) -> dict[str, object]:
        self.head_requests.append(dict(kwargs))
        identifier = (str(kwargs["Bucket"]), str(kwargs["Key"]))
        if identifier not in self.objects:
            raise _FakeS3ClientError(status=404, code="NoSuchKey")
        response = {
            "ContentLength": len(self.objects[identifier]),
            **self.object_metadata[identifier],
        }
        response.update(self.head_override or {})
        return response

    def get_object(self, **kwargs: object) -> dict[str, object]:
        payload = self.objects[(str(kwargs["Bucket"]), str(kwargs["Key"]))]
        return {"Body": _FakeS3Body(payload)}

    def delete_object(self, **kwargs: object) -> dict[str, object]:
        self.delete_requests.append(dict(kwargs))
        identifier = (str(kwargs["Bucket"]), str(kwargs["Key"]))
        if self.delete_removes_object:
            self.objects.pop(identifier, None)
            self.object_metadata.pop(identifier, None)
        return dict(self.delete_response)
