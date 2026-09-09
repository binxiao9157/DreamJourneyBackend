"""Bounded derived-worker for current Owner Truth SearchDocument embeddings."""

from __future__ import annotations

import argparse
import socket
from time import perf_counter, sleep
from typing import Any, Optional

from app.core.config import Settings
from app.observability.operation_metrics import OperationMetricRecorder
from app.services.owner_truth_memory_search_embedding_backfill import (
    OwnerTruthMemorySearchEmbeddingBackfillError,
    OwnerTruthMemorySearchEmbeddingTask,
)
from app.services.owner_truth_memory_search_embedding_runtime import (
    build_configured_embedding_provider,
    embedding_runtime_readiness,
)
from app.services.store_factory import close_store, make_store, open_store


_DEFAULT_LEASE_SECONDS = 90
_DEFAULT_RETRY_SECONDS = 30
_WORKER_METRIC_COMPONENT_ID = "ownerTruthMemorySearchEmbeddingWorker"


class OwnerTruthMemorySearchEmbeddingWorkerRuntime:
    """Claim, embed, and persist derived vectors without touching formal facts."""

    def __init__(
        self,
        *,
        settings: Settings,
        store: Any,
        provider: Any | None = None,
        worker_id: str | None = None,
        lease_seconds: int = _DEFAULT_LEASE_SECONDS,
        retry_seconds: int = _DEFAULT_RETRY_SECONDS,
        operation_metric_recorder: OperationMetricRecorder | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._provider = provider or build_configured_embedding_provider(settings)
        self._worker_id = str(
            worker_id or f"owner-truth-memory-search-embedding-{socket.gethostname()}"
        )
        self._lease_seconds = max(1, min(900, int(lease_seconds)))
        self._retry_seconds = max(1, min(3_600, int(retry_seconds)))
        self._operation_metric_recorder = (
            operation_metric_recorder or self._make_metric_recorder()
        )

    def run_once(self) -> dict[str, object]:
        started_at = perf_counter()
        reason = self._runtime_block_reason()
        if reason is not None:
            return self._payload(status="blocked", reason=reason)
        assert self._provider is not None
        model = self._provider.model
        try:
            with self._unit_of_work(command_id="ownerTruthMemorySearchEmbeddingEnqueue"):
                repository = self._repository()
                enqueued = repository.enqueue_missing(
                    model=model,
                    limit=self._settings.owner_truth_memory_search_embedding_backfill_scan_limit,
                )
            with self._unit_of_work(command_id="ownerTruthMemorySearchEmbeddingClaim"):
                tasks = self._repository().claim_batch(
                    worker_id=self._worker_id,
                    model=model,
                    limit=self._settings.owner_truth_memory_search_embedding_batch_size,
                    lease_seconds=self._lease_seconds,
                )
        except Exception:
            return self._payload(status="blocked", reason="embeddingRepositoryUnavailable")
        if not tasks:
            return self._payload(
                status="idle",
                reason="noEligibleSearchEmbeddingJob",
                enqueued_count=enqueued,
            )

        try:
            vectors = self._provider.embed_documents(
                texts=tuple(task.search_text for task in tasks)
            )
            if len(vectors) != len(tasks):
                raise OwnerTruthMemorySearchEmbeddingBackfillError(
                    "embedding provider returned a mismatched batch"
                )
        except Exception:
            result = self._retry(tasks=tasks, enqueued_count=enqueued)
            self._record_attempt(tasks=tasks, result=result, started_at=started_at)
            return result

        try:
            with self._unit_of_work(command_id="ownerTruthMemorySearchEmbeddingComplete"):
                completion = self._repository().complete_batch(
                    worker_id=self._worker_id,
                    tasks=tasks,
                    vectors=vectors,
                )
        except Exception:
            result = self._retry(tasks=tasks, enqueued_count=enqueued)
            self._record_attempt(tasks=tasks, result=result, started_at=started_at)
            return result
        result = self._payload(
            status="completed",
            reason="searchEmbeddingBatchCompleted",
            enqueued_count=enqueued,
            claimed_count=len(tasks),
            ready_count=completion.ready_count,
            stale_count=completion.stale_count,
        )
        self._record_attempt(tasks=tasks, result=result, started_at=started_at)
        return result

    def _make_metric_recorder(self) -> OperationMetricRecorder:
        sink = getattr(self._store, "append_evidence_event", None)
        return OperationMetricRecorder(
            environment=self._settings.environment,
            build="backend-owner-truth-search-embedding-worker",
            event_sink=sink if callable(sink) else None,
            retention_days=self._settings.evidence_rollout_retention_days,
            identifier_hmac_key=self._settings.operations_evidence_hmac_key,
        )

    def _record_attempt(
        self,
        *,
        tasks: tuple[OwnerTruthMemorySearchEmbeddingTask, ...],
        result: dict[str, object],
        started_at: float,
    ) -> None:
        """Append a value-free batch attempt without changing embedding results."""

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
            task_keys = tuple(
                sorted(
                    f"{task.memory_version_id}:{task.content_hash}:{task.attempt}"
                    for task in tasks
                )
            )
            batch_key = "|".join(task_keys)
            self._operation_metric_recorder.record_attempt(
                request_key=batch_key,
                operation_key=(
                    f"ownerTruthMemorySearchEmbedding:{self._worker_id}:{batch_key}"
                ),
                attempt=max(int(task.attempt) for task in tasks),
                component_kind="worker",
                component_id=_WORKER_METRIC_COMPONENT_ID,
                operation="ownerTruthMemorySearchEmbedding",
                outcome=outcome,
                feedback_state="notApplicable",
                latency_ms=max(0, int((perf_counter() - started_at) * 1000)),
                correlation_key=f"ownerTruthMemorySearchEmbedding:{batch_key}",
            )
        except Exception:
            # Metrics are shadow-only and may never change a derived write.
            return

    def _retry(
        self,
        *,
        tasks: tuple[OwnerTruthMemorySearchEmbeddingTask, ...],
        enqueued_count: int,
    ) -> dict[str, object]:
        try:
            with self._unit_of_work(command_id="ownerTruthMemorySearchEmbeddingRetry"):
                retry_count, failed_count = self._repository().retry_batch(
                    worker_id=self._worker_id,
                    tasks=tasks,
                    retry_seconds=self._retry_seconds,
                    error_code="embeddingProviderUnavailable",
                    max_attempts=self._settings.owner_truth_memory_search_embedding_max_attempts,
                )
        except Exception:
            return self._payload(
                status="lost",
                reason="embeddingRetryStateUnknown",
                enqueued_count=enqueued_count,
                claimed_count=len(tasks),
            )
        return self._payload(
            status="failed" if failed_count else "retryWait",
            reason=(
                "searchEmbeddingRetriesExhausted"
                if failed_count
                else "searchEmbeddingProviderRetryableFailure"
            ),
            enqueued_count=enqueued_count,
            claimed_count=len(tasks),
            retry_count=retry_count,
            failed_count=failed_count,
        )

    def _runtime_block_reason(self) -> str | None:
        if self._settings.store_backend != "postgres":
            return "ownerTruthEmbeddingPostgresRequired"
        if not self._settings.owner_truth_memory_search_embedding_worker_enabled:
            return "ownerTruthMemorySearchEmbeddingWorkerDisabled"
        if not self._settings.owner_truth_memory_search_projection_worker_enabled:
            return "ownerTruthMemorySearchProjectionWorkerDisabled"
        if self._provider is None:
            return embedding_runtime_readiness(self._settings).reason
        if not callable(
            getattr(self._store, "owner_truth_memory_search_embedding_repository", None)
        ):
            return "ownerTruthEmbeddingWorkerStoreUnsupported"
        return None

    def _repository(self):
        return self._store.owner_truth_memory_search_embedding_repository()

    def _unit_of_work(self, *, command_id: str):
        return self._store.request_unit_of_work(
            correlation_id=f"owner-truth-memory-search-embedding-{command_id}",
            command_id=command_id,
        )

    def _payload(self, *, status: str, reason: str, **counts: object) -> dict[str, object]:
        result: dict[str, object] = {
            "mode": "run",
            "status": status,
            "reason": reason,
            "workerId": self._worker_id,
        }
        for key, value in counts.items():
            if isinstance(value, int) and value >= 0:
                result[key] = value
        return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="DreamJourney current SearchDocument embedding backfill worker"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--loop", action="store_true")
    parser.add_argument("--worker-id", default=None)
    parser.add_argument("--lease-seconds", type=int, default=_DEFAULT_LEASE_SECONDS)
    parser.add_argument("--retry-seconds", type=int, default=_DEFAULT_RETRY_SECONDS)
    parser.add_argument("--poll-seconds", type=float, default=None)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)
    settings = Settings.from_env()
    store = make_store(settings)
    open_store(store, wait=True)
    try:
        worker = OwnerTruthMemorySearchEmbeddingWorkerRuntime(
            settings=settings,
            store=store,
            worker_id=args.worker_id,
            lease_seconds=args.lease_seconds,
            retry_seconds=args.retry_seconds,
        )
        poll_seconds = (
            float(args.poll_seconds)
            if args.poll_seconds is not None
            else max(0.1, float(settings.owner_truth_worker_poll_seconds))
        )
        while True:
            result = worker.run_once()
            print({key: value for key, value in result.items() if key != "workerId"})
            if not args.loop:
                return 0 if result["status"] not in {"blocked", "lost"} else 2
            sleep(poll_seconds)
    finally:
        close_store(store)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
