from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
import json
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import sleep
import unittest
from uuid import uuid4

import httpx

from app.async_effects.consumer_repository import InMemoryAsyncEffectConsumerRepository
from app.async_effects.dead_letter_repository import InMemoryAsyncEffectDeadLetterRepository
from app.async_effects.lease_repository import InMemoryAsyncEffectLeaseRepository
from app.async_effects.owner_truth_media_processing_worker import (
    OwnerTruthMediaProcessingWorkerRuntime,
)
from app.core.config import Settings
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.in_memory_store import InMemoryStore
from app.services.owner_truth_media_processing import (
    MediaTextExtraction,
    OwnerTruthMediaProcessingCoordinator,
    OwnerTruthMediaProcessingTerminalError,
    OwnerTruthMediaProcessorRouter,
)
from app.services.owner_truth_media_source_object import (
    FilesystemPrivateMediaObjectStore,
    MediaUploadIntentCommand,
    OwnerTruthMediaIngestionService,
    OwnerTruthMediaUploadConflict,
    TestOnlyCleanMediaContentSafetyScanner,
)


class _MediaWorkerStore(InMemoryStore):
    """Adds the typed worker-only repositories to the normal semantic store."""

    def __init__(self) -> None:
        super().__init__()
        self._lease_repository = InMemoryAsyncEffectLeaseRepository()
        self._consumer_repository = InMemoryAsyncEffectConsumerRepository()
        self._dead_letter_repository = InMemoryAsyncEffectDeadLetterRepository()
        self.unit_of_work_depth = 0

    @contextmanager
    def request_unit_of_work(self, **kwargs):
        self.unit_of_work_depth += 1
        try:
            with super().request_unit_of_work(**kwargs) as unit_of_work:
                yield unit_of_work
        finally:
            self.unit_of_work_depth -= 1

    def readiness_probe(self):
        return {"status": "ready"}

    def async_effect_lease_repository(self):
        return self._lease_repository

    def async_effect_consumer_repository(self):
        return self._consumer_repository

    def async_effect_dead_letter_repository(self):
        return self._dead_letter_repository


class _RecordingMetricRecorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def record_attempt(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        return {"sinkOutcome": "notConfigured"}


class _FailingMetricRecorder:
    def record_attempt(self, **_kwargs: object) -> dict[str, object]:
        raise RuntimeError("metric sink unavailable")


class _BlockingProcessorRouter:
    """Test-only processor that keeps an external call open past a short lease."""

    def __init__(self, *, started: Event, release: Event) -> None:
        self._started = started
        self._release = release

    def extract(self, *, source_object, payload) -> MediaTextExtraction:
        del source_object, payload
        self._started.set()
        if not self._release.wait(timeout=5.0):
            raise TimeoutError("test processor release was not signalled")
        return MediaTextExtraction(
            processor_id="blockingProcessor",
            processor_version="v1",
            extracted_text="The private provider returned after its lease heartbeat.",
        )

    def identity_for(self, _source_object):
        return "blockingProcessor", "v1"


class _TerminalProcessorRouter:
    """Test-only processor that rejects an otherwise valid private image."""

    def extract(self, *, source_object, payload) -> MediaTextExtraction:
        del source_object, payload
        raise OwnerTruthMediaProcessingTerminalError("mediaProcessorRejected")

    def identity_for(self, _source_object):
        return "terminalProcessor", "v1"


class _TransactionBoundaryProcessorRouter:
    def __init__(self, store: _MediaWorkerStore) -> None:
        self._store = store
        self.observed_depths: list[int] = []

    def extract(self, *, source_object, payload) -> MediaTextExtraction:
        del source_object, payload
        self.observed_depths.append(self._store.unit_of_work_depth)
        return MediaTextExtraction(
            processor_id="transactionBoundaryProcessor",
            processor_version="v1",
            extracted_text="Private parsing runs outside a database transaction.",
        )

    def identity_for(self, _source_object):
        return "transactionBoundaryProcessor", "v1"


class OwnerTruthMediaProcessingWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.media_root = TemporaryDirectory()
        self.store = _MediaWorkerStore()
        self.object_store = FilesystemPrivateMediaObjectStore(root=self.media_root.name)
        self.context = OwnerTruthCommandContext(
            vault_id="vault-media-processing",
            owner_subject_id="owner-media-processing",
            actor_subject_id="owner-media-processing",
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
            owner_truth_media_processing_worker_enabled=True,
            owner_truth_media_storage_provider="filesystem",
            owner_truth_media_storage_root=self.media_root.name,
        )

    def tearDown(self) -> None:
        self.media_root.cleanup()

    def test_text_media_creates_private_import_source_and_candidate_effect(self) -> None:
        source_object, intent = self._upload_and_queue(
            payload=b"A private memory written in a document.",
            media_kind="document",
            content_type="text/plain",
            file_name="memory.txt",
        )

        result = self._worker().run_once()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["reason"], "mediaTextExtracted")
        self.assertEqual(result["processingStatus"], "succeeded")
        self.assertEqual(result["candidateExtractionRequested"], "accepted")
        completed = self.store.owner_truth_media_source_object_repository().get_source_object(
            vault_id=self.context.vault_id,
            source_object_id=str(source_object["sourceObjectId"]),
            owner_subject_id=self.context.owner_subject_id,
        )
        self.assertEqual(completed["state"], "processed")
        self.assertEqual(completed["processingStatus"], "succeeded")
        self.assertFalse(completed["retryable"])
        self.assertIsNotNone(completed["derivedSourceId"])
        self.assertEqual(self.store._lease_repository.attempt_state(intent.job_id, 1), "succeeded")
        # One effect for media work and one independent effect for the derived
        # Source's owner-reviewed Candidate extraction; neither contains text.
        self.assertEqual(self.store.effect_kernel_repository().record_count(), 2)

        source = self.store._owner_truth_sources[
            (self.context.vault_id, str(completed["derivedSourceId"]))
        ]
        metadata = source["metadata"]
        self.assertEqual(metadata["processingGeneration"], 1)
        self.assertEqual(metadata["storageVersion"], 1)
        self.assertEqual(metadata["fragmentEvidence"][0]["locatorType"], "line")
        self.assertEqual(len(metadata["fragmentEvidenceHash"]), 64)
        self.assertNotIn("A private memory", json.dumps(metadata, sort_keys=True))

    def test_private_read_and_parse_run_outside_database_transaction(self) -> None:
        self._upload_and_queue(
            payload=b"A private memory written in a document.",
            media_kind="document",
            content_type="text/plain",
            file_name="memory.txt",
        )
        router = _TransactionBoundaryProcessorRouter(self.store)

        result = self._worker(processor_router=router).run_once()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(router.observed_depths, [0])
        self.assertEqual(self.store.unit_of_work_depth, 0)

    def test_expired_lease_attempt_can_resume_processing_after_worker_crash(self) -> None:
        source_object, _intent = self._upload_and_queue(
            payload=b"A private memory whose first worker crashes.",
            media_kind="document",
            content_type="text/plain",
            file_name="worker-crash.txt",
        )
        repository = self.store.owner_truth_media_source_object_repository()
        first = repository.begin_processing(
            vault_id=self.context.vault_id,
            source_object_id=str(source_object["sourceObjectId"]),
            owner_subject_id=self.context.owner_subject_id,
            expected_authority_epoch=0,
            expected_processing_generation=int(source_object["processingGeneration"]),
            attempt=1,
        )

        recovered = repository.begin_processing(
            vault_id=self.context.vault_id,
            source_object_id=str(source_object["sourceObjectId"]),
            owner_subject_id=self.context.owner_subject_id,
            expected_authority_epoch=0,
            expected_processing_generation=int(source_object["processingGeneration"]),
            attempt=2,
        )

        self.assertEqual(first["processingAttempt"], 1)
        self.assertEqual(recovered["processingAttempt"], 2)
        self.assertEqual(recovered["processingStatus"], "processing")
        with self.assertRaises(OwnerTruthMediaUploadConflict):
            repository.begin_processing(
                vault_id=self.context.vault_id,
                source_object_id=str(source_object["sourceObjectId"]),
                owner_subject_id=self.context.owner_subject_id,
                expected_authority_epoch=0,
                expected_processing_generation=int(source_object["processingGeneration"]),
                attempt=2,
            )

    def test_missing_private_object_is_terminal_and_not_retried(self) -> None:
        source_object, intent = self._upload_and_queue(
            payload=b"A private object removed before processing.",
            media_kind="document",
            content_type="text/plain",
            file_name="missing.txt",
        )
        self.object_store.delete(storage_key=str(source_object["storageKey"]))

        result = self._worker().run_once()

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "privateMediaObjectMissing")
        self.assertEqual(
            self.store._lease_repository.attempt_state(intent.job_id, 1),
            "terminalFailed",
        )

    def test_claimed_job_records_a_redacted_worker_metric(self) -> None:
        source_text = "A private memory written in a document."
        _source_object, _intent = self._upload_and_queue(
            payload=source_text.encode("utf-8"),
            media_kind="document",
            content_type="text/plain",
            file_name="memory.txt",
        )
        recorder = _RecordingMetricRecorder()

        result = self._worker(operation_metric_recorder=recorder).run_once()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(recorder.calls), 1)
        call = recorder.calls[0]
        self.assertEqual(call["component_kind"], "worker")
        self.assertEqual(call["component_id"], "ownerTruthMediaProcessingWorker")
        self.assertEqual(call["operation"], "ownerTruthMediaProcessing")
        self.assertEqual(call["outcome"], "succeeded")
        rendered = json.dumps(call, ensure_ascii=False, sort_keys=True)
        self.assertNotIn(source_text, rendered)
        self.assertNotIn("storageKey", rendered)

    def test_metric_failure_does_not_change_private_media_processing(self) -> None:
        _source_object, _intent = self._upload_and_queue(
            payload=b"A private memory written in a document.",
            media_kind="document",
            content_type="text/plain",
            file_name="memory.txt",
        )

        result = self._worker(operation_metric_recorder=_FailingMetricRecorder()).run_once()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["processingStatus"], "succeeded")

    def test_disabled_image_ocr_retries_then_terminalizes_without_fabricating_text(self) -> None:
        png_payload = b"\x89PNG\r\n\x1a\nprivate-image-payload"
        source_object, intent = self._upload_and_queue(
            payload=png_payload,
            media_kind="image",
            content_type="image/png",
            file_name="memory.png",
        )
        worker = self._worker()

        first = worker.run_once()
        second = worker.run_once()
        third = worker.run_once()

        self.assertEqual(first["status"], "retryWait")
        self.assertEqual(second["status"], "retryWait")
        self.assertEqual(third["status"], "failed")
        self.assertEqual(third["reason"], "mediaProcessingRetriesExhausted")
        self.assertEqual(third["deadLetterOutcome"], "admitted")
        self.assertEqual(third["deadLetterCause"], "maxAttemptsExceeded")
        self.assertEqual(third["deadLetterState"], "open")
        self.assertEqual(third["deadLetterNextAction"], "authorizedReplayRequired")
        completed = self.store.owner_truth_media_source_object_repository().get_source_object(
            vault_id=self.context.vault_id,
            source_object_id=str(source_object["sourceObjectId"]),
            owner_subject_id=self.context.owner_subject_id,
        )
        self.assertEqual(completed["processingStatus"], "failed")
        self.assertFalse(completed["retryable"])
        self.assertEqual(completed["failureCode"], "mediaProcessingRetriesExhausted")
        self.assertIsNone(completed["derivedSourceId"])
        self.assertEqual(completed["processingGeneration"], 1)
        self.assertEqual(self.store._lease_repository.attempt_state(intent.job_id, 1), "retryableFailed")
        self.assertEqual(self.store._lease_repository.attempt_state(intent.job_id, 2), "retryableFailed")
        self.assertEqual(self.store._lease_repository.attempt_state(intent.job_id, 3), "terminalFailed")
        self.assertEqual(self.store.effect_kernel_repository().record_count(), 1)
        self.assertEqual(self.store._dead_letter_repository.record_count(), 1)
        admission = self.store._dead_letter_repository.load(third["deadLetterId"])
        self.assertEqual(admission.intent, intent)
        self.assertEqual(admission.cause.value, "maxAttemptsExceeded")
        self.assertEqual(admission.next_action, "authorizedReplayRequired")
        self.assertEqual(self._worker().run_once()["status"], "idle")
        self.assertEqual(self.store._dead_letter_repository.record_count(), 1)

        with self.store.request_unit_of_work(
            correlation_id="test-media-processing-manual-retry",
            command_id=f"testMediaProcessingRetry:{completed['sourceObjectId']}",
        ):
            retry = OwnerTruthMediaProcessingCoordinator(self.store).queue_verified_source_object(
                context=self.context,
                source_object=completed,
            )

        self.assertTrue(retry.queued)
        self.assertIsNotNone(retry.intent)
        self.assertEqual(retry.source_object["state"], "verified")
        self.assertEqual(retry.source_object["processingStatus"], "queued")
        self.assertEqual(retry.source_object["processingGeneration"], 2)
        self.assertNotEqual(retry.intent.job_id, intent.job_id)
        self.assertEqual(self.store.effect_kernel_repository().record_count(), 2)
        self.assertEqual(self.store._dead_letter_repository.record_count(), 1)

    def test_terminal_processor_failure_records_manual_intervention_dead_letter(self) -> None:
        source_object, intent = self._upload_and_queue(
            payload=b"\x89PNG\r\n\x1a\nprivate-terminal-image",
            media_kind="image",
            content_type="image/png",
            file_name="terminal-image.png",
        )

        result = self._worker(processor_router=_TerminalProcessorRouter()).run_once()

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "mediaProcessorRejected")
        self.assertEqual(result["deadLetterOutcome"], "admitted")
        self.assertEqual(result["deadLetterCause"], "manualInterventionRequired")
        self.assertEqual(result["deadLetterNextAction"], "manualInterventionRequired")
        admission = self.store._dead_letter_repository.load(result["deadLetterId"])
        self.assertEqual(admission.intent, intent)
        self.assertEqual(admission.attempt, 1)
        self.assertEqual(admission.cause.value, "manualInterventionRequired")
        failed = self.store.owner_truth_media_source_object_repository().get_source_object(
            vault_id=self.context.vault_id,
            source_object_id=str(source_object["sourceObjectId"]),
            owner_subject_id=self.context.owner_subject_id,
        )
        self.assertEqual(failed["processingStatus"], "failed")
        self.assertFalse(failed["retryable"])
        self.assertEqual(self.store._lease_repository.attempt_state(intent.job_id, 1), "terminalFailed")

    def test_consented_image_ocr_provider_creates_private_import_source_through_worker(self) -> None:
        observed_requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            observed_requests.append(request)
            return httpx.Response(200, json={"text": "照片里有一张家人的生日卡片"})

        processor_router = OwnerTruthMediaProcessorRouter.from_settings(
            Settings(
                owner_truth_media_image_ocr_provider="httpJson",
                owner_truth_media_image_ocr_url="https://ocr.private.example.test/extract",
                owner_truth_media_image_ocr_api_key="private-test-key",
            ),
            client_factory=lambda **kwargs: httpx.Client(
                transport=httpx.MockTransport(handler),
                **kwargs,
            ),
        )
        source_object, _intent = self._upload_and_queue(
            payload=b"\x89PNG\r\n\x1a\nprivate-image-payload",
            media_kind="image",
            content_type="image/png",
            file_name="memory.png",
            allow_external_processing=True,
        )

        result = self._worker(processor_router=processor_router).run_once()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["processingStatus"], "succeeded")
        self.assertEqual(len(observed_requests), 1)
        self.assertNotIn("照片里有一张家人的生日卡片", str(result))
        completed = self.store.owner_truth_media_source_object_repository().get_source_object(
            vault_id=self.context.vault_id,
            source_object_id=str(source_object["sourceObjectId"]),
            owner_subject_id=self.context.owner_subject_id,
        )
        self.assertEqual(completed["processingStatus"], "succeeded")
        self.assertIsNotNone(completed["derivedSourceId"])
        self.assertIsNotNone(completed["lastProcessingResultId"])

    def test_slow_processor_heartbeats_lease_and_blocks_second_worker(self) -> None:
        source_object, intent = self._upload_and_queue(
            payload=b"A private memory written in a document.",
            media_kind="document",
            content_type="text/plain",
            file_name="memory.txt",
        )
        started = Event()
        release = Event()
        processor_router = _BlockingProcessorRouter(started=started, release=release)
        first_worker = self._worker(
            processor_router=processor_router,
            worker_id="owner-truth-media-processing-first",
            lease_seconds=1,
            heartbeat_interval_seconds=0.02,
        )
        first_result: dict[str, object] = {}
        first_thread = Thread(
            target=lambda: first_result.update(first_worker.run_once()),
            name="media-processing-first-worker-test",
        )
        first_thread.start()
        self.assertTrue(started.wait(timeout=1.0))

        # Wait beyond the initial one-second lease. The first worker renews
        # independently while the processor blocks, so a second worker cannot
        # take ownership of the same effect.
        sleep(1.1)
        contender = self._worker(
            worker_id="owner-truth-media-processing-contender",
            lease_seconds=1,
        ).run_once()
        self.assertEqual(contender["status"], "idle")

        release.set()
        first_thread.join(timeout=3.0)
        self.assertFalse(first_thread.is_alive())
        self.assertEqual(first_result["status"], "completed")
        completed = self.store.owner_truth_media_source_object_repository().get_source_object(
            vault_id=self.context.vault_id,
            source_object_id=str(source_object["sourceObjectId"]),
            owner_subject_id=self.context.owner_subject_id,
        )
        self.assertEqual(completed["processingStatus"], "succeeded")
        self.assertEqual(self.store._lease_repository.attempt_state(intent.job_id, 1), "succeeded")

    def test_lease_heartbeat_failure_fails_closed_without_committing_provider_result(self) -> None:
        _source_object, intent = self._upload_and_queue(
            payload=b"A private memory written in a document.",
            media_kind="document",
            content_type="text/plain",
            file_name="memory.txt",
        )
        started = Event()
        release = Event()
        heartbeat_attempted = Event()
        original_heartbeat = self.store._lease_repository.heartbeat

        def fail_heartbeat(*_args, **_kwargs):
            heartbeat_attempted.set()
            raise RuntimeError("test lease heartbeat transaction failed")

        self.store._lease_repository.heartbeat = fail_heartbeat
        worker = self._worker(
            processor_router=_BlockingProcessorRouter(started=started, release=release),
            lease_seconds=1,
            heartbeat_interval_seconds=0.01,
        )
        result: dict[str, object] = {}
        thread = Thread(
            target=lambda: result.update(worker.run_once()),
            name="media-processing-heartbeat-failure-test",
        )
        try:
            thread.start()
            self.assertTrue(started.wait(timeout=1.0))
            self.assertTrue(heartbeat_attempted.wait(timeout=1.0))
            release.set()
            thread.join(timeout=3.0)
        finally:
            self.store._lease_repository.heartbeat = original_heartbeat
            release.set()
            thread.join(timeout=3.0)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result["status"], "lost")
        self.assertEqual(result["reason"], "mediaProcessingLeaseLost")
        self.assertNotEqual(self.store._lease_repository.attempt_state(intent.job_id, 1), "succeeded")
        # The only effect is the original media-processing request. No derived
        # Source/Candidate effect can be committed after lease ownership is
        # uncertain.
        self.assertEqual(self.store.effect_kernel_repository().record_count(), 1)

    def test_lease_heartbeat_uses_bounded_third_by_default_and_allows_test_injection(self) -> None:
        self.assertAlmostEqual(self._worker(lease_seconds=3)._heartbeat_interval_seconds, 1.0)
        self.assertAlmostEqual(self._worker(lease_seconds=180)._heartbeat_interval_seconds, 30.0)
        self.assertAlmostEqual(
            self._worker(lease_seconds=1, heartbeat_interval_seconds=0.02)._heartbeat_interval_seconds,
            0.02,
        )

    def test_docx_is_parsed_inside_the_private_worker_before_candidate_review(self) -> None:
        from docx import Document

        buffer = BytesIO()
        document = Document()
        document.add_paragraph("A DOCX memory stays private until review.")
        document.save(buffer)
        source_object, _intent = self._upload_and_queue(
            payload=buffer.getvalue(),
            media_kind="document",
            content_type=(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
            file_name="memory.docx",
        )

        result = self._worker().run_once()

        self.assertEqual(result["status"], "completed")
        completed = self.store.owner_truth_media_source_object_repository().get_source_object(
            vault_id=self.context.vault_id,
            source_object_id=str(source_object["sourceObjectId"]),
            owner_subject_id=self.context.owner_subject_id,
        )
        self.assertEqual(completed["processingStatus"], "succeeded")
        self.assertIsNotNone(completed["derivedSourceId"])

    def test_pdf_is_parsed_inside_the_private_worker_before_candidate_review(self) -> None:
        source_object, _intent = self._upload_and_queue(
            payload=_minimal_pdf("A PDF memory stays private until review."),
            media_kind="document",
            content_type="application/pdf",
            file_name="memory.pdf",
        )

        result = self._worker().run_once()

        self.assertEqual(result["status"], "completed")
        completed = self.store.owner_truth_media_source_object_repository().get_source_object(
            vault_id=self.context.vault_id,
            source_object_id=str(source_object["sourceObjectId"]),
            owner_subject_id=self.context.owner_subject_id,
        )
        self.assertEqual(completed["processingStatus"], "succeeded")
        self.assertIsNotNone(completed["derivedSourceId"])

    def test_video_is_stored_but_not_queued_for_content_understanding(self) -> None:
        source_object, intent = self._upload_and_queue(
            payload=b"\x00\x00\x00\x18ftypisomprivate-video-payload",
            media_kind="video",
            content_type="video/mp4",
            file_name="memory.mp4",
        )

        self.assertIsNone(intent)
        self.assertEqual(source_object["processingStatus"], "notApplicable")
        self.assertEqual(source_object["state"], "verified")
        self.assertEqual(self._worker().run_once()["status"], "idle")

    def test_worker_fails_closed_until_the_explicit_media_worker_flag_is_enabled(self) -> None:
        worker = OwnerTruthMediaProcessingWorkerRuntime(
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

        self.assertEqual(worker.run_once()["reason"], "ownerTruthMediaProcessingWorkerDisabled")

    def _worker(
        self,
        *,
        processor_router: OwnerTruthMediaProcessorRouter | None = None,
        operation_metric_recorder: object | None = None,
        worker_id: str = "owner-truth-media-test-worker",
        lease_seconds: int = 120,
        heartbeat_interval_seconds: float | None = None,
    ) -> OwnerTruthMediaProcessingWorkerRuntime:
        return OwnerTruthMediaProcessingWorkerRuntime(
            settings=self.settings,
            store=self.store,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            retry_seconds=0,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
            object_store=self.object_store,
            processor_router=processor_router,
            operation_metric_recorder=operation_metric_recorder,
        )

    def _upload_and_queue(
        self,
        *,
        payload: bytes,
        media_kind: str,
        content_type: str,
        file_name: str,
        allow_external_processing: bool = False,
    ):
        command = MediaUploadIntentCommand.from_payload(
            {
                "commandId": str(uuid4()),
                "expectedAuthorityEpoch": 0,
                "mediaKind": media_kind,
                "fileName": file_name,
                "contentType": content_type,
                "fileSizeBytes": len(payload),
                "contentSha256": sha256(payload).hexdigest(),
                "purpose": "memoryCapture",
                "clientCreatedAt": "2026-08-03T00:00:00Z",
                **({"allowExternalProcessing": True} if allow_external_processing else {}),
            }
        )
        created = self.service.create_upload_intent(context=self.context, command=command)
        outcome, verified = self.service.upload_content(
            context=self.context,
            intent_id=str(created.upload_intent["uploadIntentId"]),
            upload_token=str(created.upload_token),
            payload=payload,
            request_content_type=content_type,
        )
        self.assertEqual(outcome, "uploaded")
        with self.store.request_unit_of_work(
            correlation_id="test-media-processing-enqueue",
            command_id=f"testMediaProcessing:{verified['sourceObjectId']}",
        ):
            queued = OwnerTruthMediaProcessingCoordinator(self.store).queue_verified_source_object(
                context=self.context,
                source_object=verified,
            )
        if queued.intent is not None:
            self.store._lease_repository.seed(queued.intent)
        return queued.source_object, queued.intent


def _minimal_pdf(text: str) -> bytes:
    """Build a tiny valid text PDF without adding a rendering test dependency."""

    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT\n/F1 14 Tf\n72 720 Td\n({escaped}) Tj\nET\n".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    chunks = [b"%PDF-1.4\n"]
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(sum(len(chunk) for chunk in chunks))
        chunks.append(f"{index} 0 obj\n".encode("ascii") + body + b"\nendobj\n")
    xref_offset = sum(len(chunk) for chunk in chunks)
    chunks.append(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    chunks.append(b"0000000000 65535 f \n")
    chunks.extend(f"{offset:010d} 00000 n \n".encode("ascii") for offset in offsets[1:])
    chunks.append(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return b"".join(chunks)


if __name__ == "__main__":
    unittest.main()
