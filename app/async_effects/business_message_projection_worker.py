"""Default-disabled worker for private business-message projections.

This worker is intentionally below the product mailbox boundary. It turns a
durably completed business receipt plus an explicit inbox account snapshot into
the existing metadata-only shadow record. It never writes ``mailbox_letters``,
contacts APNs, or exposes a user-visible message body.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import socket
from time import perf_counter, sleep
from typing import Any, Optional

from app.async_effects.business_message_projection_effects import (
    BUSINESS_MESSAGE_PROJECTION_JOB_TYPE,
    BusinessMessageProjectionRequest,
    is_business_message_projection_intent,
)
from app.async_effects.business_message_projection_request_repository import (
    BusinessMessageProjectionRequestConflict,
    BusinessMessageProjectionRequestPersistenceError,
)
from app.async_effects.contracts import (
    AsyncEffectIntent,
    AsyncEffectJobState,
    is_async_effect_store_ready,
    resolve_async_effect_runtime_status,
)
from app.async_effects.dead_letter_effects import DeadLetterCause, admit_dead_letter
from app.async_effects.lease_repository import (
    AsyncEffectJobLease,
    AsyncEffectLeaseCancelled,
    AsyncEffectLeaseLost,
)
from app.async_effects.worker_lifecycle import WorkerDrainController, WorkerLeaseHeartbeat
from app.async_effects.legacy_identity_inbox_bridge import (
    LegacyInboxAccountResolutionError,
)
from app.core.config import Settings
from app.observability.operation_metrics import OperationMetricRecorder
from app.services.store_factory import close_store, make_store, open_store


_DEFAULT_LEASE_SECONDS = 60
_DEFAULT_RETRY_SECONDS = 30
_WORKER_METRIC_COMPONENT_ID = "businessMessageProjectionWorker"
_TERMINAL_FAILURE_REASON = "businessMessageProjectionRetriesExhausted"
_MISSING_INPUT_REASON = "businessMessageProjectionInputUnavailable"
_INBOX_UNAVAILABLE_REASON = "businessMessageProjectionInboxUnavailable"
_INBOX_SNAPSHOT_MISMATCH_REASON = "businessMessageProjectionInboxSnapshotMismatch"
_CROSS_ACCOUNT_UNSUPPORTED_REASON = "businessMessageProjectionCrossAccountUnsupported"


class BusinessMessageProjectionWorkerError(RuntimeError):
    """The worker could not safely consume a typed message projection job."""


class _BusinessMessageProjectionBlocked(BusinessMessageProjectionWorkerError):
    """The source was durable, but its inbox is no longer safe to project."""

    def __init__(self, reason: str) -> None:
        self.reason = str(reason)
        super().__init__(self.reason)


def _result_hash(*parts: object) -> str:
    return sha256(":".join(str(part) for part in parts).encode("utf-8")).hexdigest()


class BusinessMessageProjectionWorkerRuntime:
    """Claim one typed private message projection with bounded retries."""

    def __init__(
        self,
        *,
        settings: Settings,
        store: Any,
        worker_id: Optional[str] = None,
        lease_seconds: int = _DEFAULT_LEASE_SECONDS,
        retry_seconds: int = _DEFAULT_RETRY_SECONDS,
        heartbeat_interval_seconds: float | None = None,
        operation_metric_recorder: OperationMetricRecorder | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._worker_id = str(
            worker_id or f"business-message-projection-worker-{socket.gethostname()}"
        )
        self._lease_seconds = max(1, int(lease_seconds))
        self._retry_seconds = max(1, int(retry_seconds))
        self._heartbeat_interval_seconds = _heartbeat_interval_seconds(
            lease_seconds=self._lease_seconds,
            configured=heartbeat_interval_seconds,
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
            return self._payload(status="idle", reason="noEligibleBusinessMessageProjectionJob")
        try:
            result = self._consume_current_lease(lease)
        except AsyncEffectLeaseCancelled:
            result = self._payload(
                status="cancelled",
                reason="businessMessageProjectionCancelled",
                lease=lease,
            )
        except AsyncEffectLeaseLost:
            result = self._payload(
                status="lost",
                reason="businessMessageProjectionLeaseLost",
                lease=lease,
            )
        except Exception:
            result = self._release_retryable_or_terminalize(lease)
        self._record_attempt(lease=lease, result=result, started_at=started_at)
        return result

    def _consume_current_lease(self, lease: AsyncEffectJobLease) -> dict[str, Any]:
        """Run durable writes in short transactions around the heartbeat window.

        The input read and terminal completion both take a short lease-scoped
        Unit of Work.  The projection write itself is isolated between them so
        its independent heartbeat never contends with a long-held ``jobs``
        row lock.  If the process stops after the append-only shadow write but
        before terminal completion, replay safely deduplicates the same
        projection rather than creating another user-visible delivery.
        """

        with self._unit_of_work(
            correlation_id=f"business-message-projection-worker-input-{lease.job_id}",
            command_id=f"businessMessageProjectionWorkerInput:{lease.operation_id}",
        ):
            lease_repository = self._store.async_effect_lease_repository()
            intent = lease_repository.load_intent(lease)
            self._assert_typed_intent(intent)
            try:
                request = (
                    self._store.async_effect_business_message_projection_request_repository()
                    .load_for_intent(intent)
                )
            except (BusinessMessageProjectionRequestConflict, BusinessMessageProjectionRequestPersistenceError):
                return self._complete_blocked_input(
                    lease=lease,
                    intent=intent,
                    lease_repository=lease_repository,
                    reason=_MISSING_INPUT_REASON,
                )
            if request is None:
                return self._complete_blocked_input(
                    lease=lease,
                    intent=intent,
                    lease_repository=lease_repository,
                    reason=_MISSING_INPUT_REASON,
                )

        try:
            projection = self._record_projection_with_lease_heartbeat(lease=lease, request=request)
        except _BusinessMessageProjectionBlocked as exc:
            with self._unit_of_work(
                correlation_id=f"business-message-projection-worker-blocked-{lease.job_id}",
                command_id=f"businessMessageProjectionWorkerBlocked:{lease.operation_id}",
            ):
                lease_repository = self._store.async_effect_lease_repository()
                current_intent = lease_repository.load_intent(lease)
                self._assert_typed_intent(current_intent)
                return self._complete_blocked_input(
                    lease=lease,
                    intent=current_intent,
                    lease_repository=lease_repository,
                    reason=exc.reason,
                )
        with self._unit_of_work(
            correlation_id=f"business-message-projection-worker-complete-{lease.job_id}",
            command_id=f"businessMessageProjectionWorkerComplete:{lease.operation_id}",
        ):
            lease_repository = self._store.async_effect_lease_repository()
            current_intent = lease_repository.load_intent(lease)
            self._assert_typed_intent(current_intent)
            try:
                current_request = (
                    self._store.async_effect_business_message_projection_request_repository()
                    .load_for_intent(current_intent)
                )
            except (BusinessMessageProjectionRequestConflict, BusinessMessageProjectionRequestPersistenceError):
                return self._complete_blocked_input(
                    lease=lease,
                    intent=current_intent,
                    lease_repository=lease_repository,
                    reason=_MISSING_INPUT_REASON,
                )
            if current_request is None:
                return self._complete_blocked_input(
                    lease=lease,
                    intent=current_intent,
                    lease_repository=lease_repository,
                    reason=_MISSING_INPUT_REASON,
                )
            if current_request.request_hash != request.request_hash:
                raise BusinessMessageProjectionWorkerError(
                    "message projection request changed before terminal completion"
                )
            receipt = self._store.async_effect_consumer_repository().consume(
                current_request.completion_command(
                    projection_outcome=projection.outcome,
                    result_ref_hash=_result_hash(
                        current_request.request_hash,
                        projection.outcome,
                        projection.record.projection_hash,
                    ),
                )
            )
            completion = lease_repository.complete(lease, outcome="succeeded")
            return self._payload(
                status="completed",
                reason=(
                    "businessMessageProjectionRecorded"
                    if projection.outcome == "recorded"
                    else "businessMessageProjectionDeduplicated"
                ),
                lease=lease,
                intent=current_intent,
                completion=completion,
                receipt=receipt,
                message_projection=projection,
            )

    def _complete_blocked_input(
        self,
        *,
        lease: AsyncEffectJobLease,
        intent: AsyncEffectIntent,
        lease_repository: Any,
        reason: str,
    ) -> dict[str, Any]:
        receipt = self._store.async_effect_consumer_repository().consume(
            BusinessMessageProjectionRequest.blocked_completion_command(
                intent=intent,
                result_ref_hash=_result_hash(intent.stable_key, reason),
                reason_code=reason,
            )
        )
        completion = lease_repository.complete(
            lease,
            outcome="blocked",
            error_code=reason,
        )
        return self._payload(
            status="blocked",
            reason=reason,
            lease=lease,
            intent=intent,
            completion=completion,
            receipt=receipt,
        )

    def _record_projection_with_lease_heartbeat(
        self,
        *,
        lease: AsyncEffectJobLease,
        request: BusinessMessageProjectionRequest,
    ) -> Any:
        """Fence the durable projection write if the current lease is lost."""

        # Renew before the external/slow window. The subsequent heartbeat owns
        # independent UoWs; there is deliberately no long-lived outer
        # transaction holding a lock on ``async_effects.jobs``.
        self._renew_lease(lease)
        heartbeat = WorkerLeaseHeartbeat(
            heartbeat=lambda: self._renew_lease(lease),
            interval_seconds=self._heartbeat_interval_seconds,
        )
        heartbeat.start()
        try:
            with self._unit_of_work(
                correlation_id=f"business-message-projection-worker-write-{lease.job_id}",
                command_id=f"businessMessageProjectionWorkerWrite:{lease.operation_id}",
            ):
                admission_reason = self._current_inbox_block_reason(request)
                if admission_reason is not None:
                    raise _BusinessMessageProjectionBlocked(admission_reason)
                summary = self._store.async_effect_business_message_projection_repository().record(
                    request.source,
                    request.inbox_account,
                )
        except Exception:
            self._stop_and_verify_lease_heartbeat(heartbeat)
            raise
        self._stop_and_verify_lease_heartbeat(heartbeat)
        return summary

    def _release_retryable_or_terminalize(self, lease: AsyncEffectJobLease) -> dict[str, Any]:
        try:
            with self._unit_of_work(
                correlation_id=f"business-message-projection-worker-retry-{lease.job_id}",
                command_id=f"businessMessageProjectionWorkerRetry:{lease.operation_id}",
            ):
                lease_repository = self._store.async_effect_lease_repository()
                intent = lease_repository.load_intent(lease)
                self._assert_typed_intent(intent)
                if lease.attempt < int(intent.max_attempts):
                    preview = lease_repository.release_retryable(
                        lease,
                        retry_seconds=self._retry_seconds,
                    )
                    return self._payload(
                        status="retryWait",
                        reason="businessMessageProjectionRetryableFailure",
                        lease=lease,
                        intent=intent,
                        retry_available_at=preview.available_at,
                    )
                try:
                    request = self._store.async_effect_business_message_projection_request_repository().load_for_intent(
                        intent
                    )
                except (BusinessMessageProjectionRequestConflict, BusinessMessageProjectionRequestPersistenceError):
                    return self._complete_blocked_input(
                        lease=lease,
                        intent=intent,
                        lease_repository=lease_repository,
                        reason=_MISSING_INPUT_REASON,
                    )
                if request is None:
                    receipt = self._store.async_effect_consumer_repository().consume(
                        BusinessMessageProjectionRequest.blocked_completion_command(
                            intent=intent,
                            result_ref_hash=_result_hash(intent.stable_key, _MISSING_INPUT_REASON),
                            reason_code=_MISSING_INPUT_REASON,
                        )
                    )
                    completion = lease_repository.complete(
                        lease,
                        outcome="blocked",
                        error_code=_MISSING_INPUT_REASON,
                    )
                    return self._payload(
                        status="blocked",
                        reason=_MISSING_INPUT_REASON,
                        lease=lease,
                        intent=intent,
                        completion=completion,
                        receipt=receipt,
                    )
                result_hash = _result_hash(
                    request.request_hash,
                    _TERMINAL_FAILURE_REASON,
                    str(lease.attempt),
                )
                receipt = self._store.async_effect_consumer_repository().consume(
                    request.failed_completion_command(result_ref_hash=result_hash)
                )
                completion = lease_repository.complete(
                    lease,
                    outcome="failed",
                    error_code=_TERMINAL_FAILURE_REASON,
                )
                admission = admit_dead_letter(
                    intent=intent,
                    job_state=AsyncEffectJobState.FAILED,
                    attempt=lease.attempt,
                    max_attempts=int(intent.max_attempts),
                    cause=DeadLetterCause.MAX_ATTEMPTS_EXCEEDED,
                    failure_hash=result_hash,
                    last_receipt_hash=_result_hash(
                        receipt.business_receipt_id,
                        receipt.business_target_key,
                        receipt.business_outcome,
                    ),
                )
                dead_letter = self._store.async_effect_dead_letter_repository().record(admission)
                return self._payload(
                    status="failed",
                    reason=_TERMINAL_FAILURE_REASON,
                    lease=lease,
                    intent=intent,
                    completion=completion,
                    receipt=receipt,
                    dead_letter=dead_letter,
                )
        except AsyncEffectLeaseCancelled:
            return self._payload(
                status="cancelled",
                reason="businessMessageProjectionCancelled",
                lease=lease,
            )
        except AsyncEffectLeaseLost:
            return self._payload(
                status="lost",
                reason="businessMessageProjectionLeaseLost",
                lease=lease,
            )
        except Exception:
            return self._payload(
                status="failed",
                reason="businessMessageProjectionRetryReleaseFailed",
                lease=lease,
            )

    def _claim_next(self) -> AsyncEffectJobLease | None:
        with self._unit_of_work(
            correlation_id="business-message-projection-worker-claim",
            command_id="businessMessageProjectionWorkerClaim",
        ):
            return self._store.async_effect_lease_repository().claim_next(
                worker_id=self._worker_id,
                lease_seconds=self._lease_seconds,
                supported_job_types=[BUSINESS_MESSAGE_PROJECTION_JOB_TYPE],
            )

    @staticmethod
    def _assert_typed_intent(intent: AsyncEffectIntent) -> None:
        if not is_business_message_projection_intent(intent):
            raise BusinessMessageProjectionWorkerError(
                "claimed job does not match message projection worker type"
            )

    def _renew_lease(self, lease: AsyncEffectJobLease) -> None:
        with self._unit_of_work(
            correlation_id=f"business-message-projection-worker-heartbeat-{lease.job_id}",
            command_id=f"businessMessageProjectionWorkerHeartbeat:{lease.operation_id}",
        ):
            self._store.async_effect_lease_repository().heartbeat(
                lease,
                lease_seconds=self._lease_seconds,
            )

    def _current_inbox_block_reason(
        self,
        request: BusinessMessageProjectionRequest,
    ) -> str | None:
        """Require one current owner inbox before writing an internal shadow.

        The V4 worker intentionally does not turn a historical recipient
        snapshot into a cross-account delivery authorization. Cross-account
        time-letter delivery remains behind its dedicated grant admission
        service until it has a runtime-safe worker contract of its own.
        """

        source_target = request.source.intent.target
        inbox = request.inbox_account
        if (
            inbox.inbox_subject_id != source_target.owner_subject_id
            or inbox.inbox_vault_id != source_target.vault_id
        ):
            return _CROSS_ACCOUNT_UNSUPPORTED_REASON
        resolver_factory = getattr(self._store, "async_effect_legacy_inbox_account_resolver", None)
        if not callable(resolver_factory):
            return _INBOX_UNAVAILABLE_REASON
        try:
            resolved = resolver_factory().resolve_active(inbox.inbox_subject_id)
        except (LegacyInboxAccountResolutionError, ValueError, RuntimeError, AttributeError):
            return _INBOX_UNAVAILABLE_REASON
        if getattr(resolved, "snapshot", None) != inbox:
            return _INBOX_SNAPSHOT_MISMATCH_REASON
        return None

    @staticmethod
    def _stop_and_verify_lease_heartbeat(heartbeat: WorkerLeaseHeartbeat) -> None:
        heartbeat.stop()
        try:
            heartbeat.raise_if_failed()
        except AsyncEffectLeaseCancelled:
            raise
        except AsyncEffectLeaseLost:
            raise
        except Exception as exc:
            raise AsyncEffectLeaseLost("message projection lease heartbeat failed") from exc

    def _runtime_block_reason(self) -> str | None:
        runtime = resolve_async_effect_runtime_status(
            async_effect_v1_enabled=self._settings.async_effect_v1_enabled,
            worker_enabled=self._settings.async_effect_worker_enabled,
            schema_ready=self._readiness(),
        )
        if not runtime.allowed:
            return runtime.reason
        if not self._settings.business_message_projection_worker_enabled:
            return "businessMessageProjectionWorkerDisabled"
        return None

    def _readiness(self) -> bool:
        probe = getattr(self._store, "readiness_probe", None)
        return callable(probe) and is_async_effect_store_ready(probe())

    def _worker_store_block_reason(self) -> str | None:
        required = (
            "request_unit_of_work",
            "async_effect_lease_repository",
            "async_effect_consumer_repository",
            "async_effect_dead_letter_repository",
            "async_effect_legacy_inbox_account_resolver",
            "async_effect_business_message_projection_request_repository",
            "async_effect_business_message_projection_repository",
        )
        if not all(callable(getattr(self._store, name, None)) for name in required):
            return "businessMessageProjectionWorkerStoreUnsupported"
        return None

    def _make_metric_recorder(self) -> OperationMetricRecorder:
        sink = getattr(self._store, "append_evidence_event", None)
        return OperationMetricRecorder(
            environment=self._settings.environment,
            build="backend-business-message-projection-worker",
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
                operation="businessMessageProjection",
                outcome=outcome,
                feedback_state="notApplicable",
                latency_ms=max(0, int((perf_counter() - started_at) * 1000)),
                correlation_key=f"businessMessageProjection:{lease.operation_id}",
            )
        except Exception:
            return

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
        message_projection: Any | None = None,
        retry_available_at: str | None = None,
        dead_letter: Any | None = None,
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
        if message_projection is not None:
            payload["messageProjectionOutcome"] = message_projection.outcome
        if retry_available_at is not None:
            payload["retryAvailableAt"] = retry_available_at
        if dead_letter is not None:
            admission = dead_letter.admission
            payload.update(
                {
                    "deadLetterCause": admission.cause.value,
                    "deadLetterId": admission.dead_letter_id,
                    "deadLetterNextAction": admission.next_action,
                    "deadLetterOutcome": dead_letter.outcome,
                    "deadLetterState": admission.state.value,
                }
            )
        return payload


def _heartbeat_interval_seconds(*, lease_seconds: int, configured: float | None) -> float:
    if configured is not None:
        normalized = float(configured)
        if normalized <= 0:
            raise ValueError("heartbeat interval must be positive")
        return normalized
    return max(0.1, min(30.0, float(lease_seconds) / 3.0))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="DreamJourney default-disabled business message projection worker"
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
        worker = BusinessMessageProjectionWorkerRuntime(
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
            )
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


if __name__ == "__main__":  # pragma: no cover - exercised through CLI tests
    raise SystemExit(main())
