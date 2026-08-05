from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from app.async_effects.consumer_repository import InMemoryAsyncEffectConsumerRepository
from app.async_effects.lease_repository import InMemoryAsyncEffectLeaseRepository
from app.async_effects.owner_truth_media_deletion_worker import (
    OwnerTruthMediaDeletionWorkerRuntime,
)
from app.core.config import Settings
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.in_memory_store import InMemoryStore
from app.services.owner_truth_media_deletion import (
    OwnerTruthMediaDeletionCoordinator,
    build_media_source_object_deletion_effect_intent,
)
from app.services.owner_truth_media_source_object import (
    FilesystemPrivateMediaObjectStore,
    MediaDeletionCommand,
    MediaUploadIntentCommand,
    OwnerTruthMediaCaptureUnavailable,
    OwnerTruthMediaIngestionService,
    TestOnlyCleanMediaContentSafetyScanner,
)


class _MediaDeletionWorkerStore(InMemoryStore):
    """Adds the typed worker-only repositories to the normal semantic store."""

    def __init__(self) -> None:
        super().__init__()
        self._lease_repository = InMemoryAsyncEffectLeaseRepository()
        self._consumer_repository = InMemoryAsyncEffectConsumerRepository()

    def readiness_probe(self):
        return {"status": "ready"}

    def async_effect_lease_repository(self):
        return self._lease_repository

    def async_effect_consumer_repository(self):
        return self._consumer_repository


class _UnavailableDeleteStore(FilesystemPrivateMediaObjectStore):
    def delete(self, *, storage_key: str) -> None:
        del storage_key
        raise OwnerTruthMediaCaptureUnavailable("test object store is unavailable")


class _MismatchedProviderStore(FilesystemPrivateMediaObjectStore):
    provider_name = "mismatched"


class _RecordingMetricRecorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def record_attempt(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        return {"sinkOutcome": "notConfigured"}


class OwnerTruthMediaDeletionWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.media_root = TemporaryDirectory()
        self.store = _MediaDeletionWorkerStore()
        self.object_store = FilesystemPrivateMediaObjectStore(root=self.media_root.name)
        self.context = OwnerTruthCommandContext(
            vault_id="vault-media-deletion",
            owner_subject_id="owner-media-deletion",
            actor_subject_id="owner-media-deletion",
        )
        self.service = OwnerTruthMediaIngestionService(
            store=self.store,
            object_store=self.object_store,
            safety_scanner=TestOnlyCleanMediaContentSafetyScanner(),
            enabled=True,
            max_upload_bytes=1024 * 1024,
            upload_intent_ttl_seconds=900,
        )
        self.settings = Settings(
            async_effect_v1_enabled=True,
            async_effect_worker_enabled=True,
            owner_truth_media_capture_enabled=True,
            owner_truth_media_deletion_worker_enabled=True,
            owner_truth_media_storage_provider="filesystem",
            owner_truth_media_storage_root=self.media_root.name,
        )

    def tearDown(self) -> None:
        self.media_root.cleanup()

    def test_worker_physically_deletes_only_after_revocation_and_writes_completion_receipt(self) -> None:
        source_object, intent = self._upload_and_enqueue_deletion()
        recorder = _RecordingMetricRecorder()

        result = self._worker(operation_metric_recorder=recorder).run_once()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["reason"], "privateMediaDeletionCompleted")
        self.assertEqual(result["deletionStatus"], "completed")
        self.assertFalse(result["deletionRetryable"])
        self.assertEqual(result["consumerOutcome"], "accepted")
        self.assertEqual(result["businessOutcome"], "completed")
        self.assertEqual(result["jobState"], "succeeded")
        self.assertEqual(self._private_file_count(), 0)
        completed = self._source_object(source_object)
        self.assertEqual(completed["state"], "deleted")
        self.assertEqual(completed["accessState"], "accessRevoked")
        self.assertEqual(completed["deletionStatus"], "completed")
        self.assertFalse(completed["deletionRetryable"])
        self.assertEqual(self.store._lease_repository.attempt_state(intent.job_id, 1), "succeeded")
        self.assertEqual(len(recorder.calls), 1)
        self.assertEqual(recorder.calls[0]["component_id"], "ownerTruthMediaDeletionWorker")
        self.assertNotIn("storageKey", str(recorder.calls[0]))
        self.assertEqual(self._worker().run_once()["status"], "idle")

    def test_unavailable_object_store_keeps_revocation_and_returns_retryable_partial(self) -> None:
        source_object, intent = self._upload_and_enqueue_deletion()

        result = self._worker(
            object_store=_UnavailableDeleteStore(root=self.media_root.name)
        ).run_once()

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "privateMediaDeletionUnavailable")
        self.assertEqual(result["deletionStatus"], "partial")
        self.assertTrue(result["deletionRetryable"])
        self.assertEqual(result["jobState"], "failed")
        self.assertEqual(self._private_file_count(), 1)
        partial = self._source_object(source_object)
        self.assertEqual(partial["accessState"], "accessRevoked")
        self.assertEqual(partial["deletionStatus"], "partial")
        self.assertTrue(partial["deletionRetryable"])
        self.assertEqual(partial["deletionFailureCode"], "privateMediaDeletionUnavailable")
        self.assertEqual(self.store._lease_repository.attempt_state(intent.job_id, 1), "terminalFailed")

    def test_storage_provider_mismatch_is_terminal_without_touching_bytes(self) -> None:
        source_object, _intent = self._upload_and_enqueue_deletion()

        result = self._worker(
            object_store=_MismatchedProviderStore(root=self.media_root.name)
        ).run_once()

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "privateMediaDeletionStorageProviderMismatch")
        self.assertEqual(result["deletionStatus"], "unsupported")
        self.assertFalse(result["deletionRetryable"])
        self.assertEqual(self._private_file_count(), 1)
        unsupported = self._source_object(source_object)
        self.assertEqual(unsupported["accessState"], "accessRevoked")
        self.assertEqual(unsupported["deletionStatus"], "unsupported")
        self.assertFalse(unsupported["deletionRetryable"])

    def test_stale_generation_is_blocked_before_provider_delete_and_current_retry_can_finish(self) -> None:
        source_object, old_intent = self._upload_and_enqueue_deletion(seed=False)
        repository = self.store.owner_truth_media_source_object_repository()
        repository.record_deletion_outcome(
            vault_id=self.context.vault_id,
            source_object_id=str(source_object["sourceObjectId"]),
            owner_subject_id=self.context.owner_subject_id,
            deletion_generation=int(source_object["deletionGeneration"]),
            outcome="partial",
            retryable=True,
            failure_code="privateMediaDeletionUnavailable",
        )
        retry = self.service.retry_deletion(
            context=self.context,
            source_object_id=str(source_object["sourceObjectId"]),
            command=self._deletion_command(),
        )
        with self.store.request_unit_of_work(
            correlation_id="test-media-deletion-retry-enqueue",
            command_id=f"testMediaDeletionRetry:{source_object['sourceObjectId']}",
        ):
            enqueued = OwnerTruthMediaDeletionCoordinator(self.store).enqueue_accepted_deletion(
                context=self.context,
                result=retry,
            )
        current_intent = build_media_source_object_deletion_effect_intent(
            source_object=enqueued.source_object
        )
        self.store._lease_repository.seed(old_intent)

        stale = self._worker().run_once()

        self.assertEqual(stale["status"], "blocked")
        self.assertEqual(stale["reason"], "mediaDeletionStale")
        self.assertEqual(self._private_file_count(), 1)
        pending = self._source_object(source_object)
        self.assertEqual(pending["deletionGeneration"], 2)
        self.assertEqual(pending["deletionStatus"], "pending")
        self.store._lease_repository.seed(current_intent)

        current = self._worker().run_once()

        self.assertEqual(current["status"], "completed")
        self.assertEqual(self._private_file_count(), 0)
        self.assertEqual(self._source_object(source_object)["deletionStatus"], "completed")

    def test_worker_fails_closed_until_deletion_lane_is_explicitly_enabled(self) -> None:
        worker = OwnerTruthMediaDeletionWorkerRuntime(
            settings=Settings(
                async_effect_v1_enabled=True,
                async_effect_worker_enabled=True,
                owner_truth_media_capture_enabled=True,
                owner_truth_media_storage_provider="filesystem",
                owner_truth_media_storage_root=self.media_root.name,
            ),
            store=self.store,
            object_store=self.object_store,
        )

        self.assertEqual(worker.run_once()["reason"], "ownerTruthMediaDeletionWorkerDisabled")

    def _worker(
        self,
        *,
        object_store: FilesystemPrivateMediaObjectStore | None = None,
        operation_metric_recorder: object | None = None,
    ) -> OwnerTruthMediaDeletionWorkerRuntime:
        return OwnerTruthMediaDeletionWorkerRuntime(
            settings=self.settings,
            store=self.store,
            worker_id="owner-truth-media-deletion-test-worker",
            object_store=object_store or self.object_store,
            operation_metric_recorder=operation_metric_recorder,
        )

    def _upload_and_enqueue_deletion(self, *, seed: bool = True):
        payload = b"A private media deletion worker test payload."
        upload = MediaUploadIntentCommand.from_payload(
            {
                "commandId": str(uuid4()),
                "expectedAuthorityEpoch": 0,
                "mediaKind": "document",
                "fileName": "private-memory.txt",
                "contentType": "text/plain",
                "fileSizeBytes": len(payload),
                "contentSha256": sha256(payload).hexdigest(),
                "purpose": "memoryCapture",
                "clientCreatedAt": "2026-08-05T00:00:00Z",
            }
        )
        created = self.service.create_upload_intent(context=self.context, command=upload)
        outcome, verified = self.service.upload_content(
            context=self.context,
            intent_id=str(created.upload_intent["uploadIntentId"]),
            upload_token=str(created.upload_token),
            payload=payload,
            request_content_type="text/plain",
        )
        self.assertEqual(outcome, "uploaded")
        deletion = self.service.request_deletion(
            context=self.context,
            source_object_id=str(verified["sourceObjectId"]),
            command=self._deletion_command(),
        )
        with self.store.request_unit_of_work(
            correlation_id="test-media-deletion-enqueue",
            command_id=f"testMediaDeletion:{verified['sourceObjectId']}",
        ):
            enqueued = OwnerTruthMediaDeletionCoordinator(self.store).enqueue_accepted_deletion(
                context=self.context,
                result=deletion,
            )
        intent = build_media_source_object_deletion_effect_intent(source_object=enqueued.source_object)
        if seed:
            self.store._lease_repository.seed(intent)
        return enqueued.source_object, intent

    def _deletion_command(self) -> MediaDeletionCommand:
        return MediaDeletionCommand.from_payload(
            {
                "commandId": str(uuid4()),
                "expectedAuthorityEpoch": 0,
                "clientRequestedAt": "2026-08-05T00:00:00Z",
            }
        )

    def _source_object(self, source_object):
        return self.store.owner_truth_media_source_object_repository().get_source_object(
            vault_id=self.context.vault_id,
            source_object_id=str(source_object["sourceObjectId"]),
            owner_subject_id=self.context.owner_subject_id,
        )

    def _private_file_count(self) -> int:
        return sum(1 for path in Path(self.media_root.name).rglob("*") if path.is_file())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
