"""Default-disabled worker for Owner Truth compatibility projection rebuilds.

The worker consumes only the typed effect emitted after an Owner-approved
MemoryVersion becomes active.  It rechecks the current Vault/MemoryVersion
authority inside its execution Unit of Work before rebuilding a derived
projection.  It never changes public Context/Echo behavior and never sends
memory content through the effect kernel.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import socket
from time import perf_counter, sleep
from typing import Any, Mapping, Optional

from app.async_effects.consumer_repository import (
    OwnerTruthMemoryProjectionRebuildConsumerCommand,
)
from app.async_effects.contracts import (
    AsyncEffectIntent,
    is_async_effect_store_ready,
    resolve_async_effect_runtime_status,
)
from app.async_effects.lease_repository import (
    AsyncEffectJobLease,
    AsyncEffectLeaseCancelled,
    AsyncEffectLeaseError,
    AsyncEffectLeaseLost,
)
from app.async_effects.worker_lifecycle import WorkerDrainController
from app.core.config import Settings
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.observability.operation_metrics import OperationMetricRecorder
from app.services.owner_truth_memory_projection_effects import (
    MEMORY_PROJECTION_REBUILD_JOB_TYPE,
    MEMORY_PROJECTION_RIGHTS_REBUILD_OPERATION_TYPE,
)
from app.services.store_factory import close_store, make_store, open_store


_CONSUMER_NAME = "ownerTruth.memoryProjection.rebuild"
_DEFAULT_LEASE_SECONDS = 60
_DEFAULT_RETRY_SECONDS = 30
_WORKER_METRIC_COMPONENT_ID = "ownerTruthMemoryProjectionWorker"


class OwnerTruthMemoryProjectionWorkerError(RuntimeError):
    """The typed projection worker cannot safely produce terminal evidence."""


def _result_hash(*parts: str) -> str:
    return sha256(":".join(parts).encode("utf-8")).hexdigest()


class OwnerTruthMemoryProjectionWorkerRuntime:
    """One-shot, fail-closed consumer for active MemoryVersion rebuild intents."""

    def __init__(
        self,
        *,
        settings: Settings,
        store: Any,
        worker_id: Optional[str] = None,
        lease_seconds: int = _DEFAULT_LEASE_SECONDS,
        retry_seconds: int = _DEFAULT_RETRY_SECONDS,
        operation_metric_recorder: OperationMetricRecorder | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._worker_id = str(
            worker_id or f"owner-truth-memory-projection-worker-{socket.gethostname()}"
        )
        self._lease_seconds = max(1, int(lease_seconds))
        self._retry_seconds = max(1, int(retry_seconds))
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
            return self._payload(status="idle", reason="noEligibleMemoryProjectionRebuildJob")

        try:
            with self._unit_of_work(
                correlation_id=f"owner-truth-memory-projection-worker-{lease.job_id}",
                command_id=f"ownerTruthMemoryProjectionWorker:{lease.operation_id}",
            ):
                result = self._consume_current_lease(lease)
        except AsyncEffectLeaseCancelled:
            result = self._payload(
                status="cancelled",
                reason="memoryProjectionRebuildCancelled",
                lease=lease,
            )
        except AsyncEffectLeaseLost:
            result = self._payload(
                status="lost",
                reason="memoryProjectionLeaseLost",
                lease=lease,
            )
        except Exception:
            result = self._release_retryable(lease)
        self._record_attempt(lease=lease, result=result, started_at=started_at)
        return result

    def _make_metric_recorder(self) -> OperationMetricRecorder:
        sink = getattr(self._store, "append_evidence_event", None)
        return OperationMetricRecorder(
            environment=self._settings.environment,
            build="backend-owner-truth-projection-worker",
            event_sink=sink if callable(sink) else None,
            retention_days=self._settings.evidence_rollout_retention_days,
            identifier_hmac_key=self._settings.operations_evidence_hmac_key,
        )

    def _record_attempt(
        self,
        *,
        lease: AsyncEffectJobLease,
        result: dict[str, Any],
        started_at: float,
    ) -> None:
        # Shadow observability must never alter projection or lease outcomes.
        try:
            status = str(result.get("status") or "").strip()
            outcome = {
                "completed": "succeeded",
                "blocked": "cancelled",
                "cancelled": "cancelled",
                "lost": "unknown",
                "retryWait": "failed",
                "failed": "failed",
            }.get(status, "unknown")
            self._operation_metric_recorder.record_attempt(
                request_key=lease.job_id,
                operation_key=lease.operation_id,
                attempt=lease.attempt,
                component_kind="worker",
                component_id=_WORKER_METRIC_COMPONENT_ID,
                operation="ownerTruthMemoryProjection",
                outcome=outcome,
                feedback_state="notApplicable",
                latency_ms=max(0, int((perf_counter() - started_at) * 1000)),
                correlation_key=f"ownerTruthMemoryProjection:{lease.operation_id}",
            )
        except Exception:
            return

    def _consume_current_lease(self, lease: AsyncEffectJobLease) -> dict[str, Any]:
        lease_repository = self._store.async_effect_lease_repository()
        intent = lease_repository.load_intent(lease)
        if intent.job_type != MEMORY_PROJECTION_REBUILD_JOB_TYPE:
            raise OwnerTruthMemoryProjectionWorkerError("claimed job does not match projection worker type")
        admission_repository = self._store.owner_truth_memory_projection_target_admission_repository()
        if intent.operation_type == MEMORY_PROJECTION_RIGHTS_REBUILD_OPERATION_TYPE:
            admission = admission_repository.admit_owner_truth_projection_rights_rebuild(intent)
        else:
            admission = admission_repository.admit_owner_truth_memory_projection(intent)
        consumer_repository = self._store.async_effect_consumer_repository()
        if not admission.allowed:
            receipt = consumer_repository.consume(
                OwnerTruthMemoryProjectionRebuildConsumerCommand(
                    intent=intent,
                    consumer_name=_CONSUMER_NAME,
                    business_target_key=intent.business_target_key,
                    outcome="blocked",
                    reason_code=admission.reason_code,
                    result_ref_hash=_result_hash(intent.stable_key, admission.reason_code),
                    admission=admission,
                    projection_outcome=None,
                )
            )
            completion = lease_repository.complete(
                lease,
                outcome="blocked",
                error_code=admission.reason_code,
            )
            return self._payload(
                status="blocked",
                reason=admission.reason_code,
                lease=lease,
                intent=intent,
                completion=completion,
                receipt=receipt,
            )

        context = OwnerTruthCommandContext(
            vault_id=intent.target.vault_id,
            owner_subject_id=intent.target.owner_subject_id,
            actor_subject_id=intent.target.owner_subject_id,
        )
        projection = self._store.owner_truth_memory_projection_repository().rebuild(context=context)
        projection_outcome = str(getattr(projection, "outcome", "")).strip()
        snapshot = getattr(projection, "snapshot", None)
        if projection_outcome not in {"rebuilt", "unchanged"} or not isinstance(snapshot, Mapping):
            raise OwnerTruthMemoryProjectionWorkerError("projection rebuild returned an invalid outcome")
        checkpoint = str(snapshot.get("checkpoint") or "").strip()
        if len(checkpoint) != 64:
            raise OwnerTruthMemoryProjectionWorkerError("projection rebuild returned no checkpoint")
        search_projection_outcome: str | None = None
        search_projection_document_count: int | None = None
        if self._settings.owner_truth_memory_search_projection_worker_enabled:
            (
                search_projection_outcome,
                search_projection_document_count,
            ) = self._rebuild_search_projection(
                context=context,
                source_checkpoint=checkpoint,
                authority_epoch=int(intent.target.authority_epoch),
            )
        reason = (
            "memoryProjectionRebuilt"
            if projection_outcome == "rebuilt"
            else "memoryProjectionUnchanged"
        )
        receipt = consumer_repository.consume(
            OwnerTruthMemoryProjectionRebuildConsumerCommand(
                intent=intent,
                consumer_name=_CONSUMER_NAME,
                business_target_key=intent.business_target_key,
                outcome="completed",
                reason_code=reason,
                result_ref_hash=checkpoint,
                admission=admission,
                projection_outcome=projection_outcome,
            )
        )
        completion = lease_repository.complete(lease, outcome="succeeded")
        return self._payload(
            status="completed",
            reason=reason,
            lease=lease,
            intent=intent,
            completion=completion,
            receipt=receipt,
            projection_outcome=projection_outcome,
            projection_checkpoint=checkpoint,
            projection_entry_count=snapshot.get("entryCount"),
            search_projection_outcome=search_projection_outcome,
            search_projection_document_count=search_projection_document_count,
        )

    def _rebuild_search_projection(
        self,
        *,
        context: OwnerTruthCommandContext,
        source_checkpoint: str,
        authority_epoch: int,
    ) -> tuple[str, int]:
        """Rebuild the optional private index only from this fresh source snapshot.

        The index is a compatibility projection, not a new authority or a
        separate async effect. Any incomplete or mismatched result makes the
        current typed job retryable so a read cannot observe stale documents.
        """

        result = (
            self._store.owner_truth_memory_search_document_projection_repository()
            .rebuild(context=context)
        )
        outcome = str(getattr(result, "outcome", "")).strip()
        projection = getattr(result, "projection", None)
        if outcome not in {"rebuilt", "unchanged"} or projection is None:
            raise OwnerTruthMemoryProjectionWorkerError(
                "search projection rebuild returned an invalid outcome"
            )
        if (
            str(getattr(projection, "checkpoint", "")).strip() != source_checkpoint
            or str(getattr(projection, "vault_id", "")).strip() != context.vault_id
            or str(getattr(projection, "owner_subject_id", "")).strip()
            != context.owner_subject_id
            or getattr(projection, "authority_epoch", None) != authority_epoch
        ):
            raise OwnerTruthMemoryProjectionWorkerError(
                "search projection rebuild returned a cross-scope or stale checkpoint"
            )
        documents = getattr(projection, "documents", None)
        if not isinstance(documents, tuple):
            raise OwnerTruthMemoryProjectionWorkerError(
                "search projection rebuild returned an invalid document set"
            )
        return outcome, len(documents)

    def _claim_next(self) -> AsyncEffectJobLease | None:
        with self._unit_of_work(
            correlation_id="owner-truth-memory-projection-worker-claim",
            command_id="ownerTruthMemoryProjectionWorkerClaim",
        ):
            return self._store.async_effect_lease_repository().claim_next(
                worker_id=self._worker_id,
                lease_seconds=self._lease_seconds,
                supported_job_types=[MEMORY_PROJECTION_REBUILD_JOB_TYPE],
            )

    def _release_retryable(self, lease: AsyncEffectJobLease) -> dict[str, Any]:
        try:
            with self._unit_of_work(
                correlation_id=f"owner-truth-memory-projection-worker-retry-{lease.job_id}",
                command_id=f"ownerTruthMemoryProjectionWorkerRetry:{lease.operation_id}",
            ):
                preview = self._store.async_effect_lease_repository().release_retryable(
                    lease,
                    retry_seconds=self._retry_seconds,
                )
            return self._payload(
                status="retryWait",
                reason="memoryProjectionRebuildRetryableFailure",
                lease=lease,
                retry_available_at=preview.available_at,
            )
        except AsyncEffectLeaseCancelled:
            return self._payload(
                status="cancelled",
                reason="memoryProjectionRebuildCancelled",
                lease=lease,
            )
        except AsyncEffectLeaseLost:
            return self._payload(
                status="lost",
                reason="memoryProjectionLeaseLost",
                lease=lease,
            )
        except Exception:
            return self._payload(
                status="failed",
                reason="memoryProjectionRetryReleaseFailed",
                lease=lease,
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
        if not self._settings.owner_truth_memory_projection_worker_enabled:
            return "ownerTruthMemoryProjectionWorkerDisabled"
        return None

    def _readiness(self) -> bool:
        probe = getattr(self._store, "readiness_probe", None)
        if not callable(probe):
            return False
        return is_async_effect_store_ready(probe())

    def _worker_store_block_reason(self) -> str | None:
        required = [
            "request_unit_of_work",
            "async_effect_lease_repository",
            "async_effect_consumer_repository",
            "owner_truth_memory_projection_target_admission_repository",
            "owner_truth_memory_projection_repository",
        ]
        if not all(callable(getattr(self._store, name, None)) for name in required):
            return "ownerTruthProjectionWorkerStoreUnsupported"
        if self._settings.owner_truth_memory_search_projection_worker_enabled and not callable(
            getattr(self._store, "owner_truth_memory_search_document_projection_repository", None)
        ):
            return "ownerTruthMemorySearchProjectionWorkerStoreUnsupported"
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
        projection_outcome: str | None = None,
        projection_checkpoint: str | None = None,
        projection_entry_count: object | None = None,
        search_projection_outcome: str | None = None,
        search_projection_document_count: int | None = None,
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
            payload["jobType"] = intent.job_type
            payload["targetStableKey"] = intent.stable_key
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
        if projection_outcome is not None:
            payload["projectionOutcome"] = projection_outcome
        if projection_checkpoint is not None:
            payload["projectionCheckpoint"] = projection_checkpoint
        if isinstance(projection_entry_count, int) and projection_entry_count >= 0:
            payload["projectionEntryCount"] = projection_entry_count
        if search_projection_outcome is not None:
            payload["searchProjectionOutcome"] = search_projection_outcome
        if (
            isinstance(search_projection_document_count, int)
            and search_projection_document_count >= 0
        ):
            payload["searchProjectionDocumentCount"] = search_projection_document_count
        if retry_available_at is not None:
            payload["retryAvailableAt"] = retry_available_at
        return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="DreamJourney default-disabled Owner Truth projection worker"
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
        worker = OwnerTruthMemoryProjectionWorkerRuntime(
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
