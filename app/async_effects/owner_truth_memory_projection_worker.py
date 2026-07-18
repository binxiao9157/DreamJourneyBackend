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
from typing import Any, Mapping, Optional

from app.async_effects.consumer_repository import (
    OwnerTruthMemoryProjectionRebuildConsumerCommand,
)
from app.async_effects.contracts import AsyncEffectIntent, resolve_async_effect_runtime_status
from app.async_effects.lease_repository import (
    AsyncEffectJobLease,
    AsyncEffectLeaseCancelled,
    AsyncEffectLeaseError,
    AsyncEffectLeaseLost,
)
from app.core.config import Settings
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_memory_projection_effects import (
    MEMORY_PROJECTION_REBUILD_JOB_TYPE,
)
from app.services.store_factory import close_store, make_store, open_store


_CONSUMER_NAME = "ownerTruth.memoryProjection.rebuild"
_DEFAULT_LEASE_SECONDS = 60
_DEFAULT_RETRY_SECONDS = 30


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
    ) -> None:
        self._settings = settings
        self._store = store
        self._worker_id = str(
            worker_id or f"owner-truth-memory-projection-worker-{socket.gethostname()}"
        )
        self._lease_seconds = max(1, int(lease_seconds))
        self._retry_seconds = max(1, int(retry_seconds))

    def run_once(self) -> dict[str, Any]:
        reason = self._runtime_block_reason()
        if reason is not None:
            return self._payload(status="blocked", reason=reason)
        if not self._supports_worker_store():
            return self._payload(status="blocked", reason="ownerTruthProjectionWorkerStoreUnsupported")

        lease = self._claim_next()
        if lease is None:
            return self._payload(status="idle", reason="noEligibleMemoryProjectionRebuildJob")

        try:
            with self._unit_of_work(
                correlation_id=f"owner-truth-memory-projection-worker-{lease.job_id}",
                command_id=f"ownerTruthMemoryProjectionWorker:{lease.operation_id}",
            ):
                return self._consume_current_lease(lease)
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
            return self._release_retryable(lease)

    def _consume_current_lease(self, lease: AsyncEffectJobLease) -> dict[str, Any]:
        lease_repository = self._store.async_effect_lease_repository()
        intent = lease_repository.load_intent(lease)
        if intent.job_type != MEMORY_PROJECTION_REBUILD_JOB_TYPE:
            raise OwnerTruthMemoryProjectionWorkerError("claimed job does not match projection worker type")
        admission = (
            self._store.owner_truth_memory_projection_target_admission_repository()
            .admit_owner_truth_memory_projection(intent)
        )
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
        )

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
        payload = probe()
        return isinstance(payload, Mapping) and payload.get("status") == "ready"

    def _supports_worker_store(self) -> bool:
        required = (
            "request_unit_of_work",
            "async_effect_lease_repository",
            "async_effect_consumer_repository",
            "owner_truth_memory_projection_target_admission_repository",
            "owner_truth_memory_projection_repository",
        )
        return all(callable(getattr(self._store, name, None)) for name in required)

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
        if retry_available_at is not None:
            payload["retryAvailableAt"] = retry_available_at
        return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="DreamJourney default-disabled Owner Truth projection worker"
    )
    parser.add_argument("--once", action="store_true", help="claim and consume at most one typed job")
    parser.add_argument("--worker-id", default=None, help="opaque worker identifier")
    parser.add_argument("--lease-seconds", type=int, default=_DEFAULT_LEASE_SECONDS)
    parser.add_argument("--retry-seconds", type=int, default=_DEFAULT_RETRY_SECONDS)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)
    settings = Settings.from_env()
    store = make_store(settings)
    open_store(store, wait=True)
    try:
        worker = OwnerTruthMemoryProjectionWorkerRuntime(
            settings=settings,
            store=store,
            worker_id=args.worker_id,
            lease_seconds=args.lease_seconds,
            retry_seconds=args.retry_seconds,
        )
        payload = worker.run_once()
        print(json.dumps(payload, sort_keys=True))
        return 0
    finally:
        close_store(store)


if __name__ == "__main__":  # pragma: no cover - exercised by CLI smoke
    raise SystemExit(main())
