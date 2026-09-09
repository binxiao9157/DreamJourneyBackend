"""Value-free latency accounting for Owner Truth DFX exercises.

This module deliberately knows nothing about user payloads.  Callers provide
bounded operations and receive aggregate timing/error evidence only, so a
load report cannot accidentally become another store for private memory text.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from math import ceil
from threading import Lock
from time import monotonic, perf_counter, sleep
from typing import Any, Callable, Mapping


class OwnerTruthDfxLoadError(ValueError):
    """A load profile or operation result is unsafe or invalid."""


def nearest_rank_percentile(values: list[float] | tuple[float, ...], percentile: float) -> float:
    """Return a deterministic nearest-rank percentile in milliseconds."""

    if not values:
        raise OwnerTruthDfxLoadError("latency sample is empty")
    if not 0 < float(percentile) <= 100:
        raise OwnerTruthDfxLoadError("percentile must be in (0, 100]")
    ordered = sorted(float(value) for value in values)
    rank = max(1, ceil((float(percentile) / 100.0) * len(ordered)))
    return ordered[rank - 1]


@dataclass(frozen=True)
class ScheduledLoadConfig:
    target_qps: float
    duration_seconds: float
    max_workers: int
    latency_budget_ms: float

    def __post_init__(self) -> None:
        if not 0 < float(self.target_qps) <= 1_000:
            raise OwnerTruthDfxLoadError("target_qps is outside the bounded range")
        if not 0 < float(self.duration_seconds) <= 3_600:
            raise OwnerTruthDfxLoadError("duration_seconds is outside the bounded range")
        if not 1 <= int(self.max_workers) <= 256:
            raise OwnerTruthDfxLoadError("max_workers is outside the bounded range")
        if not 0 < float(self.latency_budget_ms) <= 120_000:
            raise OwnerTruthDfxLoadError("latency_budget_ms is outside the bounded range")

    @property
    def scheduled_samples(self) -> int:
        return max(1, ceil(float(self.target_qps) * float(self.duration_seconds)))


@dataclass(frozen=True)
class OperationObservation:
    latency_ms: float
    success: bool
    timeout: bool = False
    error_code: str | None = None


class _ObservationSink:
    def __init__(self) -> None:
        self._values: list[OperationObservation] = []
        self._lock = Lock()

    def append(self, value: OperationObservation) -> None:
        with self._lock:
            self._values.append(value)

    def values(self) -> tuple[OperationObservation, ...]:
        with self._lock:
            return tuple(self._values)


def _safe_error_code(error: BaseException) -> str:
    """Return only an exception class, never its potentially private message."""

    return type(error).__name__[:120] or "OperationError"


def _invoke(operation: Callable[[int], Any], sample_index: int) -> OperationObservation:
    started = perf_counter()
    try:
        operation(sample_index)
    except TimeoutError as error:
        return OperationObservation(
            latency_ms=(perf_counter() - started) * 1_000.0,
            success=False,
            timeout=True,
            error_code=_safe_error_code(error),
        )
    except Exception as error:  # noqa: BLE001 - load evidence must retain failures
        return OperationObservation(
            latency_ms=(perf_counter() - started) * 1_000.0,
            success=False,
            error_code=_safe_error_code(error),
        )
    return OperationObservation(
        latency_ms=(perf_counter() - started) * 1_000.0,
        success=True,
    )


def run_scheduled_load(
    *,
    name: str,
    config: ScheduledLoadConfig,
    operation: Callable[[int], Any],
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a bounded open-loop schedule and return aggregate evidence.

    The scheduler uses the requested arrival rate rather than sleeping after
    each response.  Slow operations therefore create observable queue delay
    and concurrency pressure instead of silently reducing the offered load.
    """

    normalized_name = str(name or "").strip()
    if not normalized_name:
        raise OwnerTruthDfxLoadError("metric name is required")

    sink = _ObservationSink()
    futures: list[Future[OperationObservation]] = []
    scheduled = config.scheduled_samples
    interval = 1.0 / float(config.target_qps)
    started = monotonic()
    with ThreadPoolExecutor(max_workers=int(config.max_workers)) as pool:
        for sample_index in range(scheduled):
            due = started + (sample_index * interval)
            remaining = due - monotonic()
            if remaining > 0:
                sleep(remaining)
            futures.append(pool.submit(_invoke, operation, sample_index))
        for future in as_completed(futures):
            sink.append(future.result())
    elapsed_seconds = max(0.0, monotonic() - started)

    observations = sink.values()
    successful_latencies = [item.latency_ms for item in observations if item.success]
    failure_codes: dict[str, int] = {}
    for item in observations:
        if item.success:
            continue
        code = item.error_code or "OperationError"
        failure_codes[code] = failure_codes.get(code, 0) + 1
    success_count = len(successful_latencies)
    failure_count = len(observations) - success_count
    timeout_count = sum(1 for item in observations if item.timeout)
    percentiles = (
        {
            "p50": round(nearest_rank_percentile(successful_latencies, 50), 3),
            "p95": round(nearest_rank_percentile(successful_latencies, 95), 3),
            "p99": round(nearest_rank_percentile(successful_latencies, 99), 3),
            "max": round(max(successful_latencies), 3),
        }
        if successful_latencies
        else None
    )
    p95 = percentiles["p95"] if percentiles is not None else None
    passed = (
        failure_count == 0
        and p95 is not None
        and float(p95) <= float(config.latency_budget_ms)
    )
    return {
        "schemaVersion": "owner-truth-dfx-operation-v1",
        "name": normalized_name,
        "status": "passed" if passed else "failed",
        "targetQps": float(config.target_qps),
        "offeredQps": round(len(observations) / elapsed_seconds, 3)
        if elapsed_seconds > 0
        else None,
        "durationSeconds": round(elapsed_seconds, 3),
        "scheduledSamples": scheduled,
        "completedSamples": len(observations),
        "successCount": success_count,
        "failureCount": failure_count,
        "timeoutCount": timeout_count,
        "failureCodes": dict(sorted(failure_codes.items())),
        "latencyMs": percentiles,
        "latencyBudgetMs": float(config.latency_budget_ms),
        "metadata": dict(metadata or {}),
    }


__all__ = [
    "OperationObservation",
    "OwnerTruthDfxLoadError",
    "ScheduledLoadConfig",
    "nearest_rank_percentile",
    "run_scheduled_load",
]
