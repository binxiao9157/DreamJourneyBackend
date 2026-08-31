"""Default-disabled worker for private, text-only narrative generation jobs."""

from __future__ import annotations

import argparse
from time import perf_counter, sleep
from typing import Any

from app.core.config import Settings
from app.services.narrative_generation import (
    NarrativeGenerationProcessor,
)
from app.services.narrative_deepseek import make_narrative_provider
from app.observability.operation_metrics import OperationMetricRecorder
from app.services.store_factory import close_store, make_store, open_store


class TransactionalNarrativeRepository:
    """Open a short database UoW per repository call.

    The provider phase therefore never holds a PostgreSQL transaction or pool
    connection. Multi-artifact writes still use one append_artifacts call.
    """

    def __init__(self, store: Any) -> None:
        self._store = store

    def __getattr__(self, name: str):
        def call(*args: Any, **kwargs: Any):
            with self._store.request_unit_of_work(
                correlation_id=f"narrative-worker:{name}", command_id=None
            ):
                repository = self._store.narrative_repository()
                return getattr(repository, name)(*args, **kwargs)

        return call


class NarrativeGenerationWorkerRuntime:
    metric_component_id = (
        "app.async_effects.narrative_generation_worker.NarrativeGenerationWorkerRuntime"
    )

    def __init__(
        self,
        store: Any,
        settings: Settings,
        operation_metric_recorder: OperationMetricRecorder | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.repository = TransactionalNarrativeRepository(store)
        self.operation_metric_recorder = (
            operation_metric_recorder or self._make_metric_recorder()
        )
        self.processor = NarrativeGenerationProcessor(
            self.repository, make_narrative_provider(settings)
        )

    def _make_metric_recorder(self) -> OperationMetricRecorder:
        sink = getattr(self.store, "append_evidence_event", None)
        return OperationMetricRecorder(
            environment=self.settings.environment,
            build="backend-narrative-generation-worker",
            event_sink=sink if callable(sink) else None,
            retention_days=self.settings.evidence_rollout_retention_days,
            identifier_hmac_key=self.settings.operations_evidence_hmac_key,
        )

    def run_once(self) -> int:
        jobs = self.repository.claim_jobs(
            limit=max(1, self.settings.narrative_generation_max_concurrency)
        )
        for job in jobs:
            started_at = perf_counter()
            result = self.processor.run_job(project_id=job.project_id, job_id=job.job_id)
            if (
                result.state.value == "failed"
                and result.retryable
                and result.attempt_count < result.max_attempts
            ):
                self.repository.defer_job(
                    job_id=result.job_id,
                    delay_seconds=min(60, 2 ** result.attempt_count),
                )
            else:
                if result.state.value == "failed":
                    self.repository.dead_letter_job(job=result)
                self.repository.acknowledge_job(job_id=result.job_id)
            self._record_attempt(job=result, started_at=started_at)
        return len(jobs)

    def _record_attempt(self, *, job: Any, started_at: float) -> None:
        try:
            outcome = {
                "readyForReview": "succeeded",
                "cancelled": "cancelled",
                "failed": "failed",
            }.get(job.state.value, "unknown")
            self.operation_metric_recorder.record_attempt(
                request_key=job.job_id,
                operation_key=job.command_id,
                attempt=max(1, job.attempt_count + 1),
                component_kind="worker",
                component_id=self.metric_component_id,
                operation="narrativeGeneration",
                outcome=outcome,
                feedback_state="notApplicable",
                latency_ms=max(0, int((perf_counter() - started_at) * 1000)),
            )
        except Exception:
            # Metrics must never alter a private writing outcome.
            return

    def run_loop(self) -> None:
        while True:
            processed = self.run_once()
            if processed == 0:
                sleep(self.settings.owner_truth_worker_poll_seconds)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Narrative generation worker")
    parser.add_argument("--loop", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = Settings.from_env()
    if not settings.narrative_generation_worker_enabled:
        raise SystemExit("NARRATIVE_GENERATION_WORKER_ENABLED is false")
    store = make_store(settings)
    open_store(store)
    try:
        worker = NarrativeGenerationWorkerRuntime(store, settings)
        if args.loop:
            worker.run_loop()
            return 0
        worker.run_once()
        return 0
    finally:
        close_store(store)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "NarrativeGenerationWorkerRuntime",
    "TransactionalNarrativeRepository",
    "main",
]
