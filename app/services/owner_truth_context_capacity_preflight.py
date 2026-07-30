"""Value-free local capacity preflight for the Owner Truth Context Packet.

This module intentionally exercises only an in-process packet builder with
synthetic data. It catches bounded-packet and isolation regressions before a
deployment exercise, but it is not evidence of production capacity, Postgres
pool behaviour, provider latency, or a release approval.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import json
import math
from statistics import mean
import threading
import time
from typing import Any, Callable, Mapping, Sequence


OWNER_TRUTH_CONTEXT_CAPACITY_PREFLIGHT_SCHEMA_VERSION = (
    "owner-truth-context-capacity-preflight-v1"
)
DFX_SUSTAINED_QPS_TARGET = 10
DFX_SUSTAINED_DURATION_SECONDS_TARGET = 30 * 60
DFX_BURST_CONCURRENCY_TARGET = 100
DEFAULT_CONTEXT_PACKET_MAX_BYTES = 64 * 1024
MAX_LOCAL_BURST_CONCURRENCY = 200

PacketBuilder = Callable[[int], Mapping[str, Any]]
PacketValidator = Callable[[Mapping[str, Any], int], Sequence[str]]


@dataclass(frozen=True)
class ContextCapacityPreflightConfig:
    sustained_qps: int = DFX_SUSTAINED_QPS_TARGET
    sustained_duration_seconds: float = 2.0
    burst_concurrency: int = DFX_BURST_CONCURRENCY_TARGET
    max_packet_bytes: int = DEFAULT_CONTEXT_PACKET_MAX_BYTES

    def __post_init__(self) -> None:
        if self.sustained_qps < 1:
            raise ValueError("sustained_qps must be at least one")
        if self.sustained_duration_seconds <= 0:
            raise ValueError("sustained_duration_seconds must be positive")
        if not 1 <= self.burst_concurrency <= MAX_LOCAL_BURST_CONCURRENCY:
            raise ValueError("burst_concurrency is outside the local safety limit")
        if self.max_packet_bytes < 1024:
            raise ValueError("max_packet_bytes must be at least 1024")

    @property
    def sustained_request_count(self) -> int:
        return max(1, math.ceil(self.sustained_qps * self.sustained_duration_seconds))

    @property
    def full_dfx_shape_requested(self) -> bool:
        return (
            self.sustained_qps >= DFX_SUSTAINED_QPS_TARGET
            and self.sustained_duration_seconds >= DFX_SUSTAINED_DURATION_SECONDS_TARGET
            and self.burst_concurrency >= DFX_BURST_CONCURRENCY_TARGET
        )


@dataclass(frozen=True)
class _Measurement:
    latency_ms: int
    packet_bytes: int
    errors: tuple[str, ...]


def _normalized_error_codes(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                str(value or "").strip()
                for value in values
                if str(value or "").strip()
            }
        )
    )


def _packet_bytes(packet: Mapping[str, Any]) -> int:
    return len(
        json.dumps(
            packet,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _percentile(values: Sequence[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def _measure_packet(
    *,
    request_index: int,
    packet_builder: PacketBuilder,
    packet_validator: PacketValidator,
    max_packet_bytes: int,
) -> _Measurement:
    started = time.perf_counter()
    try:
        packet = packet_builder(request_index)
        if not isinstance(packet, Mapping):
            raise TypeError("packet builder did not return a mapping")
        packet_bytes = _packet_bytes(packet)
        errors = list(packet_validator(packet, request_index))
        if packet_bytes > max_packet_bytes:
            errors.append("packetTooLarge")
    except Exception:
        packet_bytes = 0
        errors = ["packetBuildFailed"]
    latency_ms = int((time.perf_counter() - started) * 1000)
    return _Measurement(
        latency_ms=latency_ms,
        packet_bytes=packet_bytes,
        errors=_normalized_error_codes(errors),
    )


def _summary(measurements: Sequence[_Measurement]) -> dict[str, Any]:
    latencies = [measurement.latency_ms for measurement in measurements]
    packet_sizes = [measurement.packet_bytes for measurement in measurements]
    error_counts: dict[str, int] = {}
    for measurement in measurements:
        for error in measurement.errors:
            error_counts[error] = error_counts.get(error, 0) + 1
    return {
        "requestCount": len(measurements),
        "failedRequestCount": sum(1 for measurement in measurements if measurement.errors),
        "errorCounts": dict(sorted(error_counts.items())),
        "latencyMs": {
            "min": min(latencies) if latencies else 0,
            "mean": int(mean(latencies)) if latencies else 0,
            "p95": _percentile(latencies, 0.95),
            "max": max(latencies) if latencies else 0,
        },
        "packetBytes": {
            "min": min(packet_sizes) if packet_sizes else 0,
            "p95": _percentile(packet_sizes, 0.95),
            "max": max(packet_sizes) if packet_sizes else 0,
        },
    }


def run_owner_truth_context_capacity_preflight(
    *,
    config: ContextCapacityPreflightConfig,
    packet_builder: PacketBuilder,
    packet_validator: PacketValidator,
) -> dict[str, Any]:
    """Run a value-free local preflight for sustained and burst packet reads.

    The supplied builder and validator must not persist input values in their
    output. This function only returns aggregate timings, byte counts and
    machine error codes, making it safe to include in QA evidence.
    """

    sustained: list[_Measurement] = []
    sustained_started = time.perf_counter()
    for request_index in range(config.sustained_request_count):
        scheduled_at = sustained_started + (request_index / config.sustained_qps)
        wait_seconds = scheduled_at - time.perf_counter()
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        sustained.append(
            _measure_packet(
                request_index=request_index,
                packet_builder=packet_builder,
                packet_validator=packet_validator,
                max_packet_bytes=config.max_packet_bytes,
            )
        )
    sustained_elapsed_seconds = max(0.001, time.perf_counter() - sustained_started)

    burst_started_signal = threading.Event()

    def run_burst_request(request_index: int) -> _Measurement:
        if not burst_started_signal.wait(timeout=10):
            return _Measurement(latency_ms=0, packet_bytes=0, errors=("burstStartTimedOut",))
        return _measure_packet(
            request_index=config.sustained_request_count + request_index,
            packet_builder=packet_builder,
            packet_validator=packet_validator,
            max_packet_bytes=config.max_packet_bytes,
        )

    burst: list[_Measurement] = []
    with ThreadPoolExecutor(max_workers=config.burst_concurrency) as executor:
        futures = [
            executor.submit(run_burst_request, request_index)
            for request_index in range(config.burst_concurrency)
        ]
        burst_started = time.perf_counter()
        burst_started_signal.set()
        for future in as_completed(futures):
            try:
                burst.append(future.result())
            except Exception:
                burst.append(_Measurement(latency_ms=0, packet_bytes=0, errors=("burstWorkerFailed",)))
    burst_elapsed_seconds = max(0.001, time.perf_counter() - burst_started)

    sustained_summary = _summary(sustained)
    burst_summary = _summary(burst)
    passed = not sustained_summary["failedRequestCount"] and not burst_summary["failedRequestCount"]
    return {
        "schemaVersion": OWNER_TRUTH_CONTEXT_CAPACITY_PREFLIGHT_SCHEMA_VERSION,
        "status": "passed" if passed else "failed",
        "evidenceClass": "localSyntheticPreflight",
        "productionCapacityEvidence": False,
        "dfxTarget": {
            "sustainedQps": DFX_SUSTAINED_QPS_TARGET,
            "sustainedDurationSeconds": DFX_SUSTAINED_DURATION_SECONDS_TARGET,
            "burstConcurrency": DFX_BURST_CONCURRENCY_TARGET,
            "fullShapeRequested": config.full_dfx_shape_requested,
            "fullShapeProductionVerified": False,
        },
        "configured": {
            "sustainedQps": config.sustained_qps,
            "sustainedDurationSeconds": config.sustained_duration_seconds,
            "sustainedRequestCount": config.sustained_request_count,
            "burstConcurrency": config.burst_concurrency,
            "maxPacketBytes": config.max_packet_bytes,
        },
        "execution": {
            "sustained": {
                **sustained_summary,
                "elapsedMilliseconds": int(sustained_elapsed_seconds * 1000),
                "achievedQps": round(len(sustained) / sustained_elapsed_seconds, 2),
            },
            "burst": {
                **burst_summary,
                "elapsedMilliseconds": int(burst_elapsed_seconds * 1000),
            },
        },
        "requirementsForProductionEvidence": [
            "isolated_deployed_postgres_environment",
            "approved_duration_and_concurrency_budget",
            "retrieval_projection_lag_measurement",
            "cross_vault_and_citation_assertions",
            "operations_review",
        ],
    }
