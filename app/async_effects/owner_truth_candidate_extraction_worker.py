"""Default-disabled deterministic worker for private Owner Truth Candidates.

The worker consumes only the value-free ``ownerTruth.source.created`` effect
for a live text-bearing Source.  It reads Source text inside the current
database Unit of Work, emits one conservative pending Candidate for explicit
Owner review, and never invokes a model or a Provider.  Source text never
leaves the transaction through worker output, logs, or a public API.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import socket
from time import perf_counter, sleep
from typing import Any, Optional, Protocol

from app.async_effects.consumer_repository import OwnerTruthSourceBlockedConsumerCommand
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
from app.core.config import Settings
from app.domain.owner_truth.candidate_extraction import (
    CandidateEvidenceSpan,
    CandidateProposal,
    CandidateReviewMode,
    ExtractionResultStatus,
    SyntheticCandidateExtractionCommand,
)
from app.domain.owner_truth.contracts import (
    EpistemicStatus,
    MemoryKind,
    PerspectiveType,
    SensitivityLevel,
)
from app.domain.owner_truth.ontology import OWNER_TRUTH_SCHEMA_VERSION
from app.observability.operation_metrics import OperationMetricRecorder
from app.services.owner_truth_candidate_extraction import (
    OwnerTruthCandidateExtractionInput,
    OwnerTruthCandidateExtractionResult,
    OwnerTruthCandidateExtractionService,
)
from app.services.store_factory import close_store, make_store, open_store


_CONSUMER_NAME = "ownerTruth.source.extraction"
_SOURCE_CANDIDATE_EXTRACTION_JOB_TYPE = "ownerTruth.source.created"
_DEFAULT_LEASE_SECONDS = 60
_DEFAULT_RETRY_SECONDS = 30
_WORKER_METRIC_COMPONENT_ID = "ownerTruthCandidateExtractionWorker"
_TERMINAL_FAILURE_EXTRACTOR_ID = "candidateWorkerTerminalizer"
_TERMINAL_FAILURE_MODEL_ID = "notApplicable"
_TERMINAL_FAILURE_PROMPT_VERSION = "owner-truth-candidate-worker-terminal-v1"
_TERMINAL_FAILURE_REASON = "candidateExtractionRetriesExhausted"


class OwnerTruthCandidateExtractionWorkerError(RuntimeError):
    """The typed Candidate worker cannot safely terminalize its current job."""


def _result_hash(*parts: str) -> str:
    return sha256(":".join(parts).encode("utf-8")).hexdigest()


class OwnerTruthCandidateExtractor(Protocol):
    """A private, provider-neutral Source-to-Candidate adapter."""

    def extract(
        self,
        *,
        intent: AsyncEffectIntent,
        source: OwnerTruthCandidateExtractionInput,
    ) -> SyntheticCandidateExtractionCommand:
        ...


class DeterministicOwnerTruthCandidateExtractor:
    """Create one reviewable Candidate from an explicit owner-authored Source.

    This worker is reached only from the closed-pilot ``/sources`` command,
    which accepts text authored and explicitly submitted by the authenticated
    Vault Owner.  The proposal therefore preserves that first-person recalled
    perspective, but remains pending until the Owner accepts it.  It does not
    turn a model inference into a fact, call a provider, or bypass review.
    """

    _EXTRACTOR_ID = "deterministicSourceEcho"
    _MODEL_ID = "deterministic-owner-source-v2"
    _PROMPT_VERSION = "owner-truth-candidate-extraction-owner-source-v2"

    def extract(
        self,
        *,
        intent: AsyncEffectIntent,
        source: OwnerTruthCandidateExtractionInput,
    ) -> SyntheticCandidateExtractionCommand:
        normalized_text = source.source_text.strip()
        if not normalized_text:
            return SyntheticCandidateExtractionCommand(
                intent=intent,
                extractor_id=self._EXTRACTOR_ID,
                model_id=self._MODEL_ID,
                prompt_version=self._PROMPT_VERSION,
                policy_version=OWNER_TRUTH_SCHEMA_VERSION,
                source_content_hash=source.source_content_hash,
                status=ExtractionResultStatus.QUARANTINED,
                proposals=(),
                failure_code="sourceTextInvalid",
            )

        proposal = CandidateProposal(
            memory_kind=MemoryKind.EXPERIENCE,
            perspective_type=PerspectiveType.FIRST_PERSON,
            epistemic_status=EpistemicStatus.RECALLED,
            sensitivity=SensitivityLevel.STANDARD,
            content={"summary": normalized_text},
            evidence_span=CandidateEvidenceSpan(start=0, end=len(source.source_text)),
            confidence=0.0,
            review_mode=CandidateReviewMode.SINGLE,
        )
        return SyntheticCandidateExtractionCommand(
            intent=intent,
            extractor_id=self._EXTRACTOR_ID,
            model_id=self._MODEL_ID,
            prompt_version=self._PROMPT_VERSION,
            policy_version=OWNER_TRUTH_SCHEMA_VERSION,
            source_content_hash=source.source_content_hash,
            status=ExtractionResultStatus.SUCCEEDED,
            proposals=(proposal,),
        )


class OwnerTruthCandidateExtractionWorkerRuntime:
    """One-shot, fail-closed consumer for default-off Source extraction work."""

    def __init__(
        self,
        *,
        settings: Settings,
        store: Any,
        worker_id: Optional[str] = None,
        lease_seconds: int = _DEFAULT_LEASE_SECONDS,
        retry_seconds: int = _DEFAULT_RETRY_SECONDS,
        heartbeat_interval_seconds: float | None = None,
        extractor: OwnerTruthCandidateExtractor | None = None,
        operation_metric_recorder: OperationMetricRecorder | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._worker_id = str(
            worker_id or f"owner-truth-candidate-extraction-worker-{socket.gethostname()}"
        )
        self._lease_seconds = max(1, int(lease_seconds))
        self._retry_seconds = max(1, int(retry_seconds))
        self._heartbeat_interval_seconds = _heartbeat_interval_seconds(
            lease_seconds=self._lease_seconds,
            configured=heartbeat_interval_seconds,
        )
        self._extractor = extractor or DeterministicOwnerTruthCandidateExtractor()
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
            return self._payload(status="idle", reason="noEligibleCandidateExtractionJob")

        try:
            with self._unit_of_work(
                correlation_id=f"owner-truth-candidate-extraction-worker-{lease.job_id}",
                command_id=f"ownerTruthCandidateExtractionWorker:{lease.operation_id}",
            ):
                result = self._consume_current_lease(lease)
        except AsyncEffectLeaseCancelled:
            result = self._payload(
                status="cancelled",
                reason="candidateExtractionCancelled",
                lease=lease,
            )
        except AsyncEffectLeaseLost:
            result = self._payload(
                status="lost",
                reason="candidateExtractionLeaseLost",
                lease=lease,
            )
        except Exception:
            result = self._release_retryable_or_terminalize(lease)
        self._record_attempt(lease=lease, result=result, started_at=started_at)
        return result

    def _make_metric_recorder(self) -> OperationMetricRecorder:
        sink = getattr(self._store, "append_evidence_event", None)
        return OperationMetricRecorder(
            environment=self._settings.environment,
            build="backend-owner-truth-candidate-worker",
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
        # Shadow observability must never alter a private extraction outcome.
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
                operation="ownerTruthCandidateExtraction",
                outcome=outcome,
                feedback_state="notApplicable",
                latency_ms=max(0, int((perf_counter() - started_at) * 1000)),
                correlation_key=f"ownerTruthCandidateExtraction:{lease.operation_id}",
            )
        except Exception:
            return

    def _consume_current_lease(self, lease: AsyncEffectJobLease) -> dict[str, Any]:
        lease_repository = self._store.async_effect_lease_repository()
        intent = lease_repository.load_intent(lease)
        self._assert_typed_intent(intent)
        admission = (
            self._store.owner_truth_source_target_admission_repository()
            .admit_owner_truth_source(intent)
        )
        consumer_repository = self._store.async_effect_consumer_repository()
        if not admission.allowed:
            receipt = consumer_repository.consume(
                OwnerTruthSourceBlockedConsumerCommand(
                    intent=intent,
                    consumer_name="ownerTruth.source.blocked",
                    business_target_key=intent.business_target_key,
                    outcome="blocked",
                    reason_code=admission.reason_code,
                    result_ref_hash=_result_hash(intent.stable_key, admission.reason_code),
                    admission=admission,
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

        source = (
            self._store.owner_truth_candidate_extraction_input_repository()
            .read_for_candidate_extraction(intent)
        )
        command = self._extract_with_lease_heartbeat(
            lease=lease,
            intent=intent,
            source=source,
        )
        result = OwnerTruthCandidateExtractionService(self._store).record_in_unit_of_work(command)
        if result.outcome == "blocked":
            completion = lease_repository.complete(
                lease,
                outcome="blocked",
                error_code=result.reason_code,
            )
            return self._payload(
                status="blocked",
                reason=result.reason_code,
                lease=lease,
                intent=intent,
                completion=completion,
                receipt=result.consumer,
            )
        if result.status is None or result.extraction_id is None:
            raise OwnerTruthCandidateExtractionWorkerError(
                "candidate extraction completed without a terminal result"
            )

        completion = lease_repository.complete(lease, outcome="succeeded")
        reason = {
            ExtractionResultStatus.SUCCEEDED: "candidateExtractionProposalsPersisted",
            ExtractionResultStatus.QUARANTINED: "candidateExtractionQuarantined",
            ExtractionResultStatus.FAILED: "candidateExtractionFailed",
        }[result.status]
        return self._payload(
            status="completed",
            reason=reason,
            lease=lease,
            intent=intent,
            completion=completion,
            receipt=result.consumer,
            extraction_result=result,
        )

    @staticmethod
    def _assert_typed_intent(intent: AsyncEffectIntent) -> None:
        target = intent.target
        if (
            intent.job_type != _SOURCE_CANDIDATE_EXTRACTION_JOB_TYPE
            or intent.operation_type != "ownerTruth.source.created"
            or target.resource_type != "source"
            or target.purpose != "candidateExtraction"
        ):
            raise OwnerTruthCandidateExtractionWorkerError(
                "claimed job does not match candidate extraction worker type"
            )

    def _claim_next(self) -> AsyncEffectJobLease | None:
        with self._unit_of_work(
            correlation_id="owner-truth-candidate-extraction-worker-claim",
            command_id="ownerTruthCandidateExtractionWorkerClaim",
        ):
            return self._store.async_effect_lease_repository().claim_next(
                worker_id=self._worker_id,
                lease_seconds=self._lease_seconds,
                supported_job_types=[_SOURCE_CANDIDATE_EXTRACTION_JOB_TYPE],
            )

    def _extract_with_lease_heartbeat(
        self,
        *,
        lease: AsyncEffectJobLease,
        intent: AsyncEffectIntent,
        source: OwnerTruthCandidateExtractionInput,
    ) -> SyntheticCandidateExtractionCommand:
        """Renew a claimed lease while an extractor performs external work.

        Admission, source reads, persistence, consumer receipts and lease
        completion remain in the primary Unit of Work. Only the potentially
        slow extractor adapter is wrapped. Renewal uses an independent Unit
        of Work; if it fails, the extraction result is discarded rather than
        committing a success after ownership becomes uncertain.
        """

        heartbeat = self._start_lease_heartbeat(lease)
        try:
            command = self._extractor.extract(intent=intent, source=source)
        except Exception:
            self._stop_and_verify_lease_heartbeat(heartbeat)
            raise
        self._stop_and_verify_lease_heartbeat(heartbeat)
        return command

    def _start_lease_heartbeat(self, lease: AsyncEffectJobLease) -> WorkerLeaseHeartbeat:
        heartbeat = WorkerLeaseHeartbeat(
            heartbeat=lambda: self._renew_lease(lease),
            interval_seconds=self._heartbeat_interval_seconds,
        )
        heartbeat.start()
        return heartbeat

    def _stop_and_verify_lease_heartbeat(self, heartbeat: WorkerLeaseHeartbeat) -> None:
        heartbeat.stop()
        try:
            heartbeat.raise_if_failed()
        except AsyncEffectLeaseCancelled:
            raise
        except AsyncEffectLeaseLost:
            raise
        except Exception as exc:
            # A renewal error means the worker cannot prove ownership of the
            # current lease. Do not persist an extractor result or receipt.
            raise AsyncEffectLeaseLost("candidate extraction lease heartbeat failed") from exc

    def _renew_lease(self, lease: AsyncEffectJobLease) -> None:
        with self._unit_of_work(
            correlation_id=f"owner-truth-candidate-extraction-worker-heartbeat-{lease.job_id}",
            command_id=f"ownerTruthCandidateExtractionWorkerHeartbeat:{lease.operation_id}",
        ):
            self._store.async_effect_lease_repository().heartbeat(
                lease,
                lease_seconds=self._lease_seconds,
            )

    def _release_retryable_or_terminalize(self, lease: AsyncEffectJobLease) -> dict[str, Any]:
        try:
            with self._unit_of_work(
                correlation_id=f"owner-truth-candidate-extraction-worker-retry-{lease.job_id}",
                command_id=f"ownerTruthCandidateExtractionWorkerRetry:{lease.operation_id}",
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
                        reason="candidateExtractionRetryableFailure",
                        lease=lease,
                        intent=intent,
                        retry_available_at=preview.available_at,
                    )

                admission = (
                    self._store.owner_truth_source_target_admission_repository()
                    .admit_owner_truth_source(intent)
                )
                consumer_repository = self._store.async_effect_consumer_repository()
                if not admission.allowed:
                    receipt = consumer_repository.consume(
                        OwnerTruthSourceBlockedConsumerCommand(
                            intent=intent,
                            consumer_name="ownerTruth.source.blocked",
                            business_target_key=intent.business_target_key,
                            outcome="blocked",
                            reason_code=admission.reason_code,
                            result_ref_hash=_result_hash(
                                intent.stable_key,
                                admission.reason_code,
                                str(lease.attempt),
                            ),
                            admission=admission,
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

                source = (
                    self._store.owner_truth_candidate_extraction_input_repository()
                    .read_for_candidate_extraction(intent)
                )
                command = self._terminal_failure_command(
                    intent=intent,
                    source=source,
                )
                extraction_result = OwnerTruthCandidateExtractionService(
                    self._store
                ).record_in_unit_of_work(command)
                if extraction_result.outcome == "blocked":
                    completion = lease_repository.complete(
                        lease,
                        outcome="blocked",
                        error_code=extraction_result.reason_code,
                    )
                    return self._payload(
                        status="blocked",
                        reason=extraction_result.reason_code,
                        lease=lease,
                        intent=intent,
                        completion=completion,
                        receipt=extraction_result.consumer,
                        extraction_result=extraction_result,
                    )

                completion = lease_repository.complete(
                    lease,
                    outcome="failed",
                    error_code=_TERMINAL_FAILURE_REASON,
                )
                admission_record = admit_dead_letter(
                    intent=intent,
                    job_state=AsyncEffectJobState.FAILED,
                    attempt=lease.attempt,
                    max_attempts=int(intent.max_attempts),
                    cause=DeadLetterCause.MAX_ATTEMPTS_EXCEEDED,
                    failure_hash=_result_hash(
                        intent.stable_key,
                        _TERMINAL_FAILURE_REASON,
                        str(lease.attempt),
                    ),
                    last_receipt_hash=_result_hash(
                        extraction_result.consumer.business_receipt_id,
                        extraction_result.consumer.business_target_key,
                        extraction_result.consumer.business_outcome,
                    ),
                )
                dead_letter = self._store.async_effect_dead_letter_repository().record(
                    admission_record
                )
                return self._payload(
                    status="failed",
                    reason=_TERMINAL_FAILURE_REASON,
                    lease=lease,
                    intent=intent,
                    completion=completion,
                    receipt=extraction_result.consumer,
                    extraction_result=extraction_result,
                    dead_letter=dead_letter,
                )
        except AsyncEffectLeaseCancelled:
            return self._payload(
                status="cancelled",
                reason="candidateExtractionCancelled",
                lease=lease,
            )
        except AsyncEffectLeaseLost:
            return self._payload(
                status="lost",
                reason="candidateExtractionLeaseLost",
                lease=lease,
            )
        except Exception:
            return self._payload(
                status="failed",
                reason="candidateExtractionRetryReleaseFailed",
                lease=lease,
            )

    @staticmethod
    def _terminal_failure_command(
        *,
        intent: AsyncEffectIntent,
        source: OwnerTruthCandidateExtractionInput,
    ) -> SyntheticCandidateExtractionCommand:
        """Persist a worker failure without representing it as model output."""

        return SyntheticCandidateExtractionCommand(
            intent=intent,
            extractor_id=_TERMINAL_FAILURE_EXTRACTOR_ID,
            model_id=_TERMINAL_FAILURE_MODEL_ID,
            prompt_version=_TERMINAL_FAILURE_PROMPT_VERSION,
            policy_version=OWNER_TRUTH_SCHEMA_VERSION,
            source_content_hash=source.source_content_hash,
            status=ExtractionResultStatus.FAILED,
            proposals=(),
            failure_code=_TERMINAL_FAILURE_REASON,
            retryable=False,
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
        if not self._settings.owner_truth_candidate_extraction_worker_enabled:
            return "ownerTruthCandidateExtractionWorkerDisabled"
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
            "async_effect_dead_letter_repository",
            "owner_truth_source_target_admission_repository",
            "owner_truth_candidate_extraction_input_repository",
            "owner_truth_candidate_extraction_repository",
        ]
        if not all(callable(getattr(self._store, name, None)) for name in required):
            return "ownerTruthCandidateExtractionWorkerStoreUnsupported"
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
        extraction_result: OwnerTruthCandidateExtractionResult | None = None,
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
        if extraction_result is not None:
            payload.update(
                {
                    "candidateCount": len(extraction_result.candidate_ids),
                    "extractionId": extraction_result.extraction_id,
                    "extractionStatus": extraction_result.status.value
                    if extraction_result.status is not None
                    else None,
                }
            )
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="DreamJourney default-disabled Owner Truth candidate extraction worker"
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


def _heartbeat_interval_seconds(*, lease_seconds: int, configured: float | None) -> float:
    """Renew well before expiry while avoiding a hot loop for short QA leases."""

    if configured is not None:
        normalized = float(configured)
        if normalized <= 0:
            raise ValueError("heartbeat interval must be positive")
        return normalized
    return max(0.1, min(30.0, float(lease_seconds) / 3.0))


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)
    settings = Settings.from_env()
    store = make_store(settings)
    open_store(store, wait=True)
    drain_controller = WorkerDrainController()
    drain_controller.install()
    try:
        worker = OwnerTruthCandidateExtractionWorkerRuntime(
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
