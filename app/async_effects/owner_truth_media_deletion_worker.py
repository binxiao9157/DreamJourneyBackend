"""Default-off physical deletion worker for private Owner Truth media.

The API revokes SourceObject access before it accepts a deletion effect. This
worker is the only component that removes the private object-store bytes. It
keeps the deletion generation and authority epoch locked through the provider
call, records value-free completion evidence, and never restores access when a
delete, retry, or stale worker races with another command.
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
from app.core.config import Settings
from app.observability.operation_metrics import OperationMetricRecorder
from app.services.owner_truth_media_deletion import (
    OWNER_TRUTH_MEDIA_DELETION_CONSUMER,
    OWNER_TRUTH_MEDIA_DELETION_EVENT_TYPE,
    OWNER_TRUTH_MEDIA_DELETION_JOB_TYPE,
    OWNER_TRUTH_MEDIA_DELETION_OPERATION_TYPE,
    OwnerTruthMediaDeletionConsumerCommand,
)
from app.services.owner_truth_media_source_object import (
    OwnerTruthMediaAccessRevoked,
    OwnerTruthMediaAuthorityEpochConflict,
    OwnerTruthMediaCaptureUnavailable,
    OwnerTruthMediaUploadConflict,
    OwnerTruthMediaUploadInvalid,
    PrivateMediaObjectStore,
    build_private_media_object_store,
)
from app.services.store_factory import close_store, make_store, open_store


_DEFAULT_LEASE_SECONDS = 120
_WORKER_METRIC_COMPONENT_ID = "ownerTruthMediaDeletionWorker"


class OwnerTruthMediaDeletionWorkerError(RuntimeError):
    """The typed private-media deletion worker could not safely finish a lease."""


def _result_hash(*parts: str) -> str:
    return sha256(":".join(parts).encode("utf-8")).hexdigest()


class OwnerTruthMediaDeletionWorkerRuntime:
    """Claim and physically delete at most one revoked private media object."""

    def __init__(
        self,
        *,
        settings: Settings,
        store: Any,
        worker_id: Optional[str] = None,
        lease_seconds: int = _DEFAULT_LEASE_SECONDS,
        object_store: PrivateMediaObjectStore | None = None,
        operation_metric_recorder: OperationMetricRecorder | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._worker_id = str(
            worker_id or f"owner-truth-media-deletion-worker-{socket.gethostname()}"
        )
        self._lease_seconds = max(1, int(lease_seconds))
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
            return self._payload(status="idle", reason="noEligibleMediaDeletionJob")

        try:
            with self._unit_of_work(
                correlation_id=f"owner-truth-media-deletion-worker-{lease.job_id}",
                command_id=f"ownerTruthMediaDeletionWorker:{lease.operation_id}",
            ):
                result = self._consume_current_lease(lease)
        except AsyncEffectLeaseCancelled:
            result = self._payload(
                status="cancelled",
                reason="mediaDeletionCancelled",
                lease=lease,
            )
        except AsyncEffectLeaseLost:
            result = self._payload(
                status="lost",
                reason="mediaDeletionLeaseLost",
                lease=lease,
            )
        except (
            OwnerTruthMediaAccessRevoked,
            OwnerTruthMediaAuthorityEpochConflict,
            OwnerTruthMediaUploadConflict,
        ):
            # A newer retry generation or authority change won the race. It is
            # never safe for this lease to issue a provider delete after that.
            result = self._block_stale_lease(lease)
        except OwnerTruthMediaCaptureUnavailable:
            result = self._record_incomplete(
                lease,
                deletion_state="partial",
                retryable=True,
                reason="privateMediaDeletionUnavailable",
            )
        except OwnerTruthMediaUploadInvalid:
            result = self._record_incomplete(
                lease,
                deletion_state="unsupported",
                retryable=False,
                reason="privateMediaDeletionUnsupported",
            )
        except FileNotFoundError:
            # Object stores can report an already-deleted key differently. The
            # source is already revoked, so this is an idempotent completion.
            result = self._record_completed(
                lease,
                reason="privateMediaDeletionObjectAbsent",
            )
        except Exception:
            # Provider exceptions can carry filenames, keys, request ids, or
            # response content. Persist only a stable failure code.
            result = self._record_incomplete(
                lease,
                deletion_state="partial",
                retryable=True,
                reason="privateMediaDeletionUnexpectedFailure",
            )
        self._record_attempt(lease=lease, result=result, started_at=started_at)
        return result

    def _consume_current_lease(self, lease: AsyncEffectJobLease) -> dict[str, Any]:
        lease_repository = self._store.async_effect_lease_repository()
        intent = lease_repository.load_intent(lease)
        self._assert_typed_intent(intent)
        source_object = self._store.owner_truth_media_source_object_repository().assert_deletion_execution_allowed(
            vault_id=intent.target.vault_id,
            source_object_id=intent.target.resource_id,
            owner_subject_id=intent.target.owner_subject_id,
            expected_authority_epoch=intent.target.authority_epoch,
            expected_deletion_generation=intent.target.resource_version,
        )
        storage_key = str(source_object.get("storageKey") or "").strip()
        if not storage_key:
            return self._finish_in_unit_of_work(
                lease=lease,
                intent=intent,
                source_object=source_object,
                deletion_state="completed",
                retryable=False,
                reason="privateMediaDeletionNoObject",
                lease_outcome="succeeded",
            )
        if str(source_object.get("storageProvider") or "") != self._object_store.provider_name:
            return self._finish_in_unit_of_work(
                lease=lease,
                intent=intent,
                source_object=source_object,
                deletion_state="unsupported",
                retryable=False,
                reason="privateMediaDeletionStorageProviderMismatch",
                lease_outcome="failed",
            )

        # The repository row remains locked in this request UoW while the
        # provider call occurs. A process crash can repeat an idempotent object
        # delete later, but a newer generation cannot be physically removed.
        self._object_store.delete(storage_key=storage_key)
        return self._finish_in_unit_of_work(
            lease=lease,
            intent=intent,
            source_object=source_object,
            deletion_state="completed",
            retryable=False,
            reason="privateMediaDeletionCompleted",
            lease_outcome="succeeded",
        )

    def _record_completed(self, lease: AsyncEffectJobLease, *, reason: str) -> dict[str, Any]:
        return self._record_outcome(
            lease,
            deletion_state="completed",
            retryable=False,
            reason=reason,
            lease_outcome="succeeded",
        )

    def _record_incomplete(
        self,
        lease: AsyncEffectJobLease,
        *,
        deletion_state: str,
        retryable: bool,
        reason: str,
    ) -> dict[str, Any]:
        return self._record_outcome(
            lease,
            deletion_state=deletion_state,
            retryable=retryable,
            reason=reason,
            lease_outcome="failed",
        )

    def _record_outcome(
        self,
        lease: AsyncEffectJobLease,
        *,
        deletion_state: str,
        retryable: bool,
        reason: str,
        lease_outcome: str,
    ) -> dict[str, Any]:
        try:
            with self._unit_of_work(
                correlation_id=f"owner-truth-media-deletion-worker-outcome-{lease.job_id}",
                command_id=f"ownerTruthMediaDeletionWorkerOutcome:{lease.operation_id}",
            ):
                lease_repository = self._store.async_effect_lease_repository()
                intent = lease_repository.load_intent(lease)
                self._assert_typed_intent(intent)
                source_object = self._store.owner_truth_media_source_object_repository().assert_deletion_execution_allowed(
                    vault_id=intent.target.vault_id,
                    source_object_id=intent.target.resource_id,
                    owner_subject_id=intent.target.owner_subject_id,
                    expected_authority_epoch=intent.target.authority_epoch,
                    expected_deletion_generation=intent.target.resource_version,
                )
                return self._finish_in_unit_of_work(
                    lease=lease,
                    intent=intent,
                    source_object=source_object,
                    deletion_state=deletion_state,
                    retryable=retryable,
                    reason=reason,
                    lease_outcome=lease_outcome,
                )
        except AsyncEffectLeaseCancelled:
            return self._payload(status="cancelled", reason="mediaDeletionCancelled", lease=lease)
        except AsyncEffectLeaseLost:
            return self._payload(status="lost", reason="mediaDeletionLeaseLost", lease=lease)
        except (
            OwnerTruthMediaAccessRevoked,
            OwnerTruthMediaAuthorityEpochConflict,
            OwnerTruthMediaUploadConflict,
        ):
            return self._block_stale_lease(lease)
        except Exception:
            return self._payload(
                status="failed",
                reason="mediaDeletionOutcomePersistenceFailed",
                lease=lease,
            )

    def _finish_in_unit_of_work(
        self,
        *,
        lease: AsyncEffectJobLease,
        intent: AsyncEffectIntent,
        source_object: Mapping[str, Any],
        deletion_state: str,
        retryable: bool,
        reason: str,
        lease_outcome: str,
    ) -> dict[str, Any]:
        updated = self._store.owner_truth_media_source_object_repository().record_deletion_outcome(
            vault_id=intent.target.vault_id,
            source_object_id=intent.target.resource_id,
            owner_subject_id=intent.target.owner_subject_id,
            deletion_generation=int(intent.target.resource_version),
            outcome=deletion_state,
            retryable=retryable,
            failure_code=None if deletion_state == "completed" else reason,
        )
        receipt = self._store.async_effect_consumer_repository().consume(
            OwnerTruthMediaDeletionConsumerCommand(
                intent=intent,
                consumer_name=OWNER_TRUTH_MEDIA_DELETION_CONSUMER,
                business_target_key=intent.business_target_key,
                outcome="completed" if deletion_state == "completed" else "failed",
                reason_code=reason,
                result_ref_hash=_result_hash(
                    intent.stable_key,
                    reason,
                    str(intent.target.resource_version),
                    str(lease.attempt),
                ),
                deletion_state=deletion_state,
            )
        )
        completion = self._store.async_effect_lease_repository().complete(
            lease,
            outcome=lease_outcome,
            error_code=None if lease_outcome == "succeeded" else reason,
        )
        return self._payload(
            status="completed" if deletion_state == "completed" else "failed",
            reason=reason,
            lease=lease,
            intent=intent,
            completion=completion,
            receipt=receipt,
            source_object=updated,
        )

    def _block_stale_lease(self, lease: AsyncEffectJobLease) -> dict[str, Any]:
        try:
            with self._unit_of_work(
                correlation_id=f"owner-truth-media-deletion-worker-stale-{lease.job_id}",
                command_id=f"ownerTruthMediaDeletionWorkerStale:{lease.operation_id}",
            ):
                completion = self._store.async_effect_lease_repository().complete(
                    lease,
                    outcome="blocked",
                    error_code="mediaDeletionStale",
                )
                return self._payload(
                    status="blocked",
                    reason="mediaDeletionStale",
                    lease=lease,
                    completion=completion,
                )
        except AsyncEffectLeaseCancelled:
            return self._payload(status="cancelled", reason="mediaDeletionCancelled", lease=lease)
        except AsyncEffectLeaseLost:
            return self._payload(status="lost", reason="mediaDeletionLeaseLost", lease=lease)
        except Exception:
            return self._payload(
                status="failed",
                reason="mediaDeletionStaleCompletionFailed",
                lease=lease,
            )

    def _make_metric_recorder(self) -> OperationMetricRecorder:
        sink = getattr(self._store, "append_evidence_event", None)
        return OperationMetricRecorder(
            environment=self._settings.environment,
            build="backend-owner-truth-media-deletion-worker",
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
        try:
            outcome = {
                "completed": "succeeded",
                "blocked": "cancelled",
                "cancelled": "cancelled",
                "lost": "unknown",
                "failed": "failed",
            }.get(str(result.get("status") or "").strip(), "unknown")
            self._operation_metric_recorder.record_attempt(
                request_key=lease.job_id,
                operation_key=lease.operation_id,
                attempt=lease.attempt,
                component_kind="worker",
                component_id=_WORKER_METRIC_COMPONENT_ID,
                operation="ownerTruthMediaDeletion",
                outcome=outcome,
                feedback_state="notApplicable",
                latency_ms=max(0, int((perf_counter() - started_at) * 1000)),
                correlation_key=f"ownerTruthMediaDeletion:{lease.operation_id}",
            )
        except Exception:
            return

    @staticmethod
    def _assert_typed_intent(intent: AsyncEffectIntent) -> None:
        target = intent.target
        if (
            intent.operation_type != OWNER_TRUTH_MEDIA_DELETION_OPERATION_TYPE
            or intent.event_type != OWNER_TRUTH_MEDIA_DELETION_EVENT_TYPE
            or intent.job_type != OWNER_TRUTH_MEDIA_DELETION_JOB_TYPE
            or target.resource_type != "mediaSourceObject"
            or target.purpose != "privateMediaDeletion"
        ):
            raise OwnerTruthMediaDeletionWorkerError(
                "claimed job does not match media deletion worker type"
            )

    def _claim_next(self) -> AsyncEffectJobLease | None:
        with self._unit_of_work(
            correlation_id="owner-truth-media-deletion-worker-claim",
            command_id="ownerTruthMediaDeletionWorkerClaim",
        ):
            return self._store.async_effect_lease_repository().claim_next(
                worker_id=self._worker_id,
                lease_seconds=self._lease_seconds,
                supported_job_types=[OWNER_TRUTH_MEDIA_DELETION_JOB_TYPE],
            )

    def _runtime_block_reason(self) -> str | None:
        runtime = resolve_async_effect_runtime_status(
            async_effect_v1_enabled=self._settings.async_effect_v1_enabled,
            worker_enabled=self._settings.async_effect_worker_enabled,
            schema_ready=self._readiness(),
        )
        if not runtime.allowed:
            return runtime.reason
        if not self._settings.owner_truth_media_capture_enabled:
            return "ownerTruthMediaCaptureDisabled"
        if not self._settings.owner_truth_media_deletion_worker_enabled:
            return "ownerTruthMediaDeletionWorkerDisabled"
        if self._object_store.provider_name == "disabled":
            return "ownerTruthMediaDeletionStorageDisabled"
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
        ]
        if not all(callable(getattr(self._store, name, None)) for name in required):
            return "ownerTruthMediaDeletionWorkerStoreUnsupported"
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
                    "deletionStatus": source_object.get("deletionStatus"),
                    "deletionRetryable": bool(source_object.get("deletionRetryable", False)),
                }
            )
        return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="DreamJourney default-disabled Owner Truth media deletion worker"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="claim and consume at most one typed job")
    mode.add_argument("--loop", action="store_true", help="continuously claim typed jobs")
    parser.add_argument("--worker-id", default=None, help="opaque worker identifier")
    parser.add_argument("--lease-seconds", type=int, default=_DEFAULT_LEASE_SECONDS)
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
    try:
        worker = OwnerTruthMediaDeletionWorkerRuntime(
            settings=settings,
            store=store,
            worker_id=args.worker_id,
            lease_seconds=args.lease_seconds,
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
                sleep(poll_seconds)
        except KeyboardInterrupt:
            return 0
    finally:
        close_store(store)


if __name__ == "__main__":  # pragma: no cover - exercised by CLI smoke
    raise SystemExit(main())
