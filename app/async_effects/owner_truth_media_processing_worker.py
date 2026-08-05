"""Default-off worker for private Owner Truth media processing.

Only metadata and hashes cross the asynchronous-effect boundary.  Private
bytes are read from the configured private object store inside the worker, and
successful extraction becomes an ``import`` Source for the existing
Owner-reviewed Candidate flow.  It never publishes a media URL, logs content,
or turns OCR/ASR/provider output into confirmed memory automatically.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import socket
from time import perf_counter, sleep
from typing import Any, Mapping, Optional

from app.async_effects.contracts import (
    AsyncEffectIntent,
    is_async_effect_store_ready,
    resolve_async_effect_runtime_status,
)
from app.async_effects.lease_repository import (
    AsyncEffectJobLease,
    AsyncEffectLeaseCancelled,
    AsyncEffectLeaseLost,
)
from app.async_effects.worker_lifecycle import WorkerDrainController
from app.core.config import Settings
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.observability.operation_metrics import OperationMetricRecorder
from app.services.owner_truth_media_processing import (
    OWNER_TRUTH_MEDIA_PROCESSING_CONSUMER,
    OWNER_TRUTH_MEDIA_PROCESSING_JOB_TYPE,
    OWNER_TRUTH_MEDIA_PROCESSING_OPERATION_TYPE,
    OwnerTruthMediaProcessingConsumerCommand,
    OwnerTruthMediaProcessingRetryableError,
    OwnerTruthMediaProcessingTerminalError,
    OwnerTruthMediaProcessorRouter,
    build_media_processing_candidate_effect,
)
from app.services.owner_truth_media_source_object import (
    OwnerTruthMediaAccessRevoked,
    OwnerTruthMediaUploadInvalid,
    PrivateMediaObjectStore,
    build_private_media_object_store,
)
from app.services.store_factory import close_store, make_store, open_store


_DEFAULT_LEASE_SECONDS = 120
_DEFAULT_RETRY_SECONDS = 30
_NOT_APPLICABLE_REASONS = {"videoProcessingNotApplicable"}
_WORKER_METRIC_COMPONENT_ID = "ownerTruthMediaProcessingWorker"


class OwnerTruthMediaProcessingWorkerError(RuntimeError):
    """The typed media worker could not safely transition its current job."""


def _result_hash(*parts: str) -> str:
    return sha256(":".join(parts).encode("utf-8")).hexdigest()


class OwnerTruthMediaProcessingWorkerRuntime:
    """Claim and process at most one closed-pilot private media job at a time."""

    def __init__(
        self,
        *,
        settings: Settings,
        store: Any,
        worker_id: Optional[str] = None,
        lease_seconds: int = _DEFAULT_LEASE_SECONDS,
        retry_seconds: int = _DEFAULT_RETRY_SECONDS,
        object_store: PrivateMediaObjectStore | None = None,
        processor_router: OwnerTruthMediaProcessorRouter | None = None,
        operation_metric_recorder: OperationMetricRecorder | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._worker_id = str(
            worker_id or f"owner-truth-media-processing-worker-{socket.gethostname()}"
        )
        self._lease_seconds = max(1, int(lease_seconds))
        self._retry_seconds = max(0, int(retry_seconds))
        self._object_store = object_store or build_private_media_object_store(
            provider=settings.owner_truth_media_storage_provider,
            root=settings.owner_truth_media_storage_root,
            s3_bucket=settings.owner_truth_media_s3_bucket,
            s3_prefix=settings.owner_truth_media_s3_prefix,
            s3_region=settings.owner_truth_media_s3_region,
            s3_endpoint_url=settings.owner_truth_media_s3_endpoint_url,
            s3_access_key_id=settings.owner_truth_media_s3_access_key_id,
            s3_secret_access_key=settings.owner_truth_media_s3_secret_access_key,
            s3_server_side_encryption=settings.owner_truth_media_s3_server_side_encryption,
            s3_kms_key_id=settings.owner_truth_media_s3_kms_key_id,
        )
        self._processor_router = processor_router or OwnerTruthMediaProcessorRouter.from_settings(settings)
        self._operation_metric_recorder = operation_metric_recorder or self._make_metric_recorder()

    def run_once(self) -> dict[str, Any]:
        started_at = perf_counter()
        reason = self._runtime_block_reason()
        if reason is not None:
            return self._payload(status="blocked", reason=reason)
        store_reason = self._worker_store_block_reason()
        if store_reason is not None:
            return self._payload(status="blocked", reason=store_reason)

        lease = self._claim_next()
        if lease is None:
            return self._payload(status="idle", reason="noEligibleMediaProcessingJob")

        try:
            with self._unit_of_work(
                correlation_id=f"owner-truth-media-processing-worker-{lease.job_id}",
                command_id=f"ownerTruthMediaProcessingWorker:{lease.operation_id}",
            ):
                result = self._consume_current_lease(lease)
        except AsyncEffectLeaseCancelled:
            result = self._payload(
                status="cancelled",
                reason="mediaProcessingCancelled",
                lease=lease,
            )
        except AsyncEffectLeaseLost:
            result = self._payload(
                status="lost",
                reason="mediaProcessingLeaseLost",
                lease=lease,
            )
        except OwnerTruthMediaAccessRevoked:
            # A delete request revokes the SourceObject before the physical
            # deletion worker runs.  Never turn that into a retryable parser
            # failure: cancel this lease and leave the tombstone authoritative.
            result = self._cancel_access_revoked_lease(lease)
        except OwnerTruthMediaProcessingRetryableError as error:
            result = self._retry_or_terminal(lease, reason=error.reason_code)
        except OwnerTruthMediaProcessingTerminalError as error:
            result = self._terminalize(lease, reason=error.reason_code)
        except FileNotFoundError:
            result = self._terminalize(lease, reason="privateMediaObjectMissing")
        except OwnerTruthMediaUploadInvalid:
            result = self._terminalize(lease, reason="privateMediaObjectInvalid")
        except Exception:
            # Do not attach exception messages: a parser/provider can contain
            # private filenames, text, or request metadata in its error string.
            result = self._retry_or_terminal(lease, reason="mediaProcessingUnexpectedFailure")
        self._record_attempt(lease=lease, result=result, started_at=started_at)
        return result

    def _make_metric_recorder(self) -> OperationMetricRecorder:
        sink = getattr(self._store, "append_evidence_event", None)
        return OperationMetricRecorder(
            environment=self._settings.environment,
            build="backend-owner-truth-media-processing-worker",
            event_sink=sink if callable(sink) else None,
            retention_days=self._settings.evidence_rollout_retention_days,
            identifier_hmac_key=self._settings.operations_evidence_hmac_key,
        )

    def _record_attempt(
        self,
        *,
        lease: AsyncEffectJobLease,
        result: Mapping[str, Any],
        started_at: float,
    ) -> None:
        # Metrics are redacted, best-effort evidence and never affect media bytes,
        # provider output, processing status, or lease state.
        try:
            outcome = {
                "completed": "succeeded",
                "blocked": "cancelled",
                "cancelled": "cancelled",
                "lost": "unknown",
                "retryWait": "failed",
                "failed": "failed",
            }.get(str(result.get("status") or "").strip(), "unknown")
            self._operation_metric_recorder.record_attempt(
                request_key=lease.job_id,
                operation_key=lease.operation_id,
                attempt=lease.attempt,
                component_kind="worker",
                component_id=_WORKER_METRIC_COMPONENT_ID,
                operation="ownerTruthMediaProcessing",
                outcome=outcome,
                feedback_state="notApplicable",
                latency_ms=max(0, int((perf_counter() - started_at) * 1000)),
                correlation_key=f"ownerTruthMediaProcessing:{lease.operation_id}",
            )
        except Exception:
            return

    def _consume_current_lease(self, lease: AsyncEffectJobLease) -> dict[str, Any]:
        lease_repository = self._store.async_effect_lease_repository()
        intent = lease_repository.load_intent(lease)
        self._assert_typed_intent(intent)
        repository = self._store.owner_truth_media_source_object_repository()
        source_object = repository.begin_processing(
            vault_id=intent.target.vault_id,
            source_object_id=intent.target.resource_id,
            owner_subject_id=intent.target.owner_subject_id,
            expected_authority_epoch=intent.target.authority_epoch,
            expected_processing_generation=intent.target.resource_version,
            attempt=lease.attempt,
        )
        storage_key = str(source_object.get("storageKey") or "").strip()
        if not storage_key:
            raise OwnerTruthMediaProcessingTerminalError("privateMediaObjectMissing")
        payload = self._object_store.read(storage_key=storage_key)
        if sha256(payload).hexdigest() != str(source_object.get("contentSha256") or ""):
            raise OwnerTruthMediaProcessingTerminalError("privateMediaObjectIntegrityMismatch")

        extraction = self._processor_router.extract(source_object=source_object, payload=payload)
        # Extraction can take long enough for an Owner delete request to land.
        # Re-read behind the repository's commit fence before creating a
        # derived Source or Candidate effect, so deleted media cannot leak
        # downstream through an in-flight worker.
        source_object = repository.assert_processing_commit_allowed(
            vault_id=intent.target.vault_id,
            source_object_id=intent.target.resource_id,
            owner_subject_id=intent.target.owner_subject_id,
            expected_processing_generation=int(source_object["processingGeneration"]),
        )
        derived_source_id, candidate_effect = build_media_processing_candidate_effect(
            context=OwnerTruthCommandContext(
                vault_id=intent.target.vault_id,
                owner_subject_id=intent.target.owner_subject_id,
                actor_subject_id=intent.target.owner_subject_id,
            ),
            source_object=source_object,
            extraction=extraction,
            store=self._store,
        )
        updated = repository.record_processing_outcome(
            vault_id=intent.target.vault_id,
            source_object_id=intent.target.resource_id,
            owner_subject_id=intent.target.owner_subject_id,
            processing_generation=int(source_object["processingGeneration"]),
            attempt=lease.attempt,
            processor_id=extraction.processor_id,
            processor_version=extraction.processor_version,
            outcome="succeeded",
            result_hash=extraction.result_hash(source_object=source_object),
            extracted_text_sha256=extraction.extracted_text_sha256,
            derived_source_id=derived_source_id,
        )
        receipt = self._store.async_effect_consumer_repository().consume(
            OwnerTruthMediaProcessingConsumerCommand(
                intent=intent,
                consumer_name=OWNER_TRUTH_MEDIA_PROCESSING_CONSUMER,
                business_target_key=intent.business_target_key,
                outcome="completed",
                reason_code="mediaTextExtracted",
                result_ref_hash=extraction.result_hash(source_object=source_object),
                processing_state="succeeded",
            )
        )
        completion = lease_repository.complete(lease, outcome="succeeded")
        return self._payload(
            status="completed",
            reason="mediaTextExtracted",
            lease=lease,
            intent=intent,
            completion=completion,
            receipt=receipt,
            source_object=updated,
            candidate_effect=candidate_effect,
        )

    def _cancel_access_revoked_lease(self, lease: AsyncEffectJobLease) -> dict[str, Any]:
        try:
            with self._unit_of_work(
                correlation_id=f"owner-truth-media-processing-worker-revoked-{lease.job_id}",
                command_id=f"ownerTruthMediaProcessingWorkerRevoked:{lease.operation_id}",
            ):
                self._store.async_effect_lease_repository().request_cancel(lease.job_id)
        except (AsyncEffectLeaseCancelled, AsyncEffectLeaseLost):
            pass
        except Exception:
            # The revocation fence in the SourceObject repository remains the
            # final guard even if a best-effort lease cancellation races out.
            pass
        return self._payload(
            status="cancelled",
            reason="mediaAccessRevoked",
            lease=lease,
        )

    def _retry_or_terminal(self, lease: AsyncEffectJobLease, *, reason: str) -> dict[str, Any]:
        try:
            with self._unit_of_work(
                correlation_id=f"owner-truth-media-processing-worker-retry-{lease.job_id}",
                command_id=f"ownerTruthMediaProcessingWorkerRetry:{lease.operation_id}",
            ):
                lease_repository = self._store.async_effect_lease_repository()
                intent = lease_repository.load_intent(lease)
                self._assert_typed_intent(intent)
                repository = self._store.owner_truth_media_source_object_repository()
                source_object = repository.get_source_object(
                    vault_id=intent.target.vault_id,
                    source_object_id=intent.target.resource_id,
                    owner_subject_id=intent.target.owner_subject_id,
                )
                if lease.attempt >= intent.max_attempts:
                    return self._terminalize_in_unit_of_work(
                        lease=lease,
                        intent=intent,
                        source_object=source_object,
                        reason="mediaProcessingRetriesExhausted",
                    )
                processor_id, processor_version = self._processor_router.identity_for(source_object)
                updated = repository.record_processing_outcome(
                    vault_id=intent.target.vault_id,
                    source_object_id=intent.target.resource_id,
                    owner_subject_id=intent.target.owner_subject_id,
                    processing_generation=int(source_object["processingGeneration"]),
                    attempt=lease.attempt,
                    processor_id=processor_id,
                    processor_version=processor_version,
                    outcome="retryableFailed",
                    result_hash=_result_hash(intent.stable_key, reason, str(lease.attempt)),
                    failure_code=reason,
                )
                preview = lease_repository.release_retryable(
                    lease,
                    retry_seconds=self._retry_seconds,
                )
                return self._payload(
                    status="retryWait",
                    reason=reason,
                    lease=lease,
                    intent=intent,
                    source_object=updated,
                    retry_available_at=preview.available_at,
                )
        except AsyncEffectLeaseCancelled:
            return self._payload(
                status="cancelled",
                reason="mediaProcessingCancelled",
                lease=lease,
            )
        except AsyncEffectLeaseLost:
            return self._payload(
                status="lost",
                reason="mediaProcessingLeaseLost",
                lease=lease,
            )
        except Exception:
            return self._payload(
                status="failed",
                reason="mediaProcessingRetryReleaseFailed",
                lease=lease,
            )

    def _terminalize(self, lease: AsyncEffectJobLease, *, reason: str) -> dict[str, Any]:
        try:
            with self._unit_of_work(
                correlation_id=f"owner-truth-media-processing-worker-terminal-{lease.job_id}",
                command_id=f"ownerTruthMediaProcessingWorkerTerminal:{lease.operation_id}",
            ):
                lease_repository = self._store.async_effect_lease_repository()
                intent = lease_repository.load_intent(lease)
                self._assert_typed_intent(intent)
                source_object = self._store.owner_truth_media_source_object_repository().get_source_object(
                    vault_id=intent.target.vault_id,
                    source_object_id=intent.target.resource_id,
                    owner_subject_id=intent.target.owner_subject_id,
                )
                return self._terminalize_in_unit_of_work(
                    lease=lease,
                    intent=intent,
                    source_object=source_object,
                    reason=reason,
                )
        except AsyncEffectLeaseCancelled:
            return self._payload(
                status="cancelled",
                reason="mediaProcessingCancelled",
                lease=lease,
            )
        except AsyncEffectLeaseLost:
            return self._payload(
                status="lost",
                reason="mediaProcessingLeaseLost",
                lease=lease,
            )
        except Exception:
            return self._payload(
                status="failed",
                reason="mediaProcessingTerminalizationFailed",
                lease=lease,
            )

    def _terminalize_in_unit_of_work(
        self,
        *,
        lease: AsyncEffectJobLease,
        intent: AsyncEffectIntent,
        source_object: Mapping[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        repository = self._store.owner_truth_media_source_object_repository()
        processor_id, processor_version = self._processor_router.identity_for(source_object)
        is_not_applicable = reason in _NOT_APPLICABLE_REASONS
        state = "notApplicable" if is_not_applicable else "failed"
        outcome = "completed" if is_not_applicable else "failed"
        updated = repository.record_processing_outcome(
            vault_id=intent.target.vault_id,
            source_object_id=intent.target.resource_id,
            owner_subject_id=intent.target.owner_subject_id,
            processing_generation=int(source_object["processingGeneration"]),
            attempt=lease.attempt,
            processor_id=processor_id,
            processor_version=processor_version,
            outcome=state,
            result_hash=_result_hash(intent.stable_key, reason, str(lease.attempt)),
            failure_code=None if is_not_applicable else reason,
        )
        receipt = self._store.async_effect_consumer_repository().consume(
            OwnerTruthMediaProcessingConsumerCommand(
                intent=intent,
                consumer_name=OWNER_TRUTH_MEDIA_PROCESSING_CONSUMER,
                business_target_key=intent.business_target_key,
                outcome=outcome,
                reason_code=reason,
                result_ref_hash=_result_hash(intent.stable_key, reason, str(lease.attempt)),
                processing_state=state,
            )
        )
        completion = self._store.async_effect_lease_repository().complete(
            lease,
            outcome="succeeded" if is_not_applicable else "failed",
            error_code=None if is_not_applicable else reason,
        )
        return self._payload(
            status="completed" if is_not_applicable else "failed",
            reason=reason,
            lease=lease,
            intent=intent,
            completion=completion,
            receipt=receipt,
            source_object=updated,
        )

    @staticmethod
    def _assert_typed_intent(intent: AsyncEffectIntent) -> None:
        target = intent.target
        if (
            intent.operation_type != OWNER_TRUTH_MEDIA_PROCESSING_OPERATION_TYPE
            or intent.job_type != OWNER_TRUTH_MEDIA_PROCESSING_JOB_TYPE
            or target.resource_type != "mediaSourceObject"
            or target.purpose != "privateMediaProcessing"
        ):
            raise OwnerTruthMediaProcessingWorkerError(
                "claimed job does not match media processing worker type"
            )

    def _claim_next(self) -> AsyncEffectJobLease | None:
        with self._unit_of_work(
            correlation_id="owner-truth-media-processing-worker-claim",
            command_id="ownerTruthMediaProcessingWorkerClaim",
        ):
            return self._store.async_effect_lease_repository().claim_next(
                worker_id=self._worker_id,
                lease_seconds=self._lease_seconds,
                supported_job_types=[OWNER_TRUTH_MEDIA_PROCESSING_JOB_TYPE],
            )

    def _runtime_block_reason(self) -> str | None:
        readiness = self._readiness()
        runtime = resolve_async_effect_runtime_status(
            async_effect_v1_enabled=self._settings.async_effect_v1_enabled,
            worker_enabled=self._settings.async_effect_worker_enabled,
            schema_ready=readiness,
        )
        if not runtime.allowed:
            return runtime.reason
        if not self._settings.owner_truth_media_capture_enabled:
            return "ownerTruthMediaCaptureDisabled"
        if not self._settings.owner_truth_media_processing_worker_enabled:
            return "ownerTruthMediaProcessingWorkerDisabled"
        if self._object_store.provider_name == "disabled":
            return "ownerTruthMediaProcessingStorageDisabled"
        return None

    def _readiness(self) -> bool:
        probe = getattr(self._store, "readiness_probe", None)
        return callable(probe) and is_async_effect_store_ready(probe())

    def _worker_store_block_reason(self) -> str | None:
        required = [
            "request_unit_of_work",
            "async_effect_lease_repository",
            "async_effect_consumer_repository",
            "owner_truth_media_source_object_repository",
            "create_owner_truth_source",
            "effect_kernel_repository",
        ]
        if not all(callable(getattr(self._store, name, None)) for name in required):
            return "ownerTruthMediaProcessingWorkerStoreUnsupported"
        return None

    def _unit_of_work(self, *, correlation_id: str, command_id: str):
        return self._store.request_unit_of_work(
            correlation_id=correlation_id,
            command_id=command_id,
        )

    def _payload(
        self,
        *,
        status: str,
        reason: str,
        lease: AsyncEffectJobLease | None = None,
        intent: AsyncEffectIntent | None = None,
        completion: Any | None = None,
        receipt: Any | None = None,
        source_object: Mapping[str, Any] | None = None,
        candidate_effect: Any | None = None,
        retry_available_at: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "mode": "run",
            "status": status,
            "reason": reason,
            "workerId": self._worker_id,
        }
        if lease is not None:
            payload.update(
                {
                    "jobId": lease.job_id,
                    "operationId": lease.operation_id,
                    "attempt": lease.attempt,
                }
            )
        if intent is not None:
            payload.update({"jobType": intent.job_type, "targetStableKey": intent.stable_key})
        if completion is not None:
            payload.update(
                {
                    "jobState": completion.job_state,
                    "operationState": completion.operation_state,
                    "outboxState": completion.outbox_state,
                }
            )
        if receipt is not None:
            payload.update(
                {
                    "consumerOutcome": receipt.outcome,
                    "businessOutcome": receipt.business_outcome,
                    "consumerInboxState": receipt.inbox_state,
                }
            )
        if source_object is not None:
            payload.update(
                {
                    "mediaKind": source_object.get("mediaKind"),
                    "processingStatus": source_object.get("processingStatus"),
                    "retryable": bool(source_object.get("retryable", False)),
                }
            )
        if candidate_effect is not None:
            payload["candidateExtractionRequested"] = candidate_effect.outcome
        if retry_available_at is not None:
            payload["retryAvailableAt"] = retry_available_at
        return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="DreamJourney default-disabled Owner Truth media processing worker"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="claim and consume at most one typed job")
    mode.add_argument("--loop", action="store_true", help="continuously claim typed jobs")
    parser.add_argument("--worker-id", default=None, help="opaque worker identifier")
    parser.add_argument("--lease-seconds", type=int, default=_DEFAULT_LEASE_SECONDS)
    parser.add_argument("--retry-seconds", type=int, default=_DEFAULT_RETRY_SECONDS)
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=None,
        help="idle delay between loop iterations; defaults to OWNER_TRUTH_WORKER_POLL_SECONDS",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)
    settings = Settings.from_env()
    store = make_store(settings)
    open_store(store, wait=True)
    drain_controller = WorkerDrainController()
    drain_controller.install()
    try:
        worker = OwnerTruthMediaProcessingWorkerRuntime(
            settings=settings,
            store=store,
            worker_id=args.worker_id,
            lease_seconds=args.lease_seconds,
            retry_seconds=args.retry_seconds,
        )
        poll_seconds = max(
            0.1,
            float(
                args.poll_seconds
                if args.poll_seconds is not None
                else settings.owner_truth_worker_poll_seconds
            ),
        )
        last_result_key: tuple[str, str] | None = None
        try:
            while True:
                payload = worker.run_once()
                result_key = (str(payload.get("status") or ""), str(payload.get("reason") or ""))
                if not args.loop or result_key != last_result_key:
                    print(json.dumps(payload, sort_keys=True))
                    last_result_key = result_key
                if not args.loop:
                    return 0
                if drain_controller.stop_requested:
                    print(
                        json.dumps(
                            {
                                "mode": "run",
                                "status": "drained",
                                "reason": "workerShutdownRequested",
                            },
                            sort_keys=True,
                        )
                    )
                    return 0
                sleep(poll_seconds)
        except KeyboardInterrupt:
            return 0
    finally:
        drain_controller.restore()
        close_store(store)


if __name__ == "__main__":  # pragma: no cover - exercised by CLI smoke
    raise SystemExit(main())
