"""Value-minimized M0 DFX baseline report contract.

The baseline is deliberately a regression evidence envelope, not a production
SLO system.  It accepts only aggregate synthetic measurements and machine
codes, so neither Source content nor identifiers can enter an exported report.
Metrics that the current probes cannot measure (SQL count, queue age, process
resources) are represented explicitly as ``notMeasured``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Mapping, Sequence


M0_DFX_BASELINE_SCHEMA_VERSION = "m0-dfx-baseline-v1"
M0_DFX_PROBE_IDS = frozenset(
    {
        "contextPacket",
        "stage2MediaCandidateProjection",
        "crossVaultRevocation",
    }
)
_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{7,64}$")
_CODE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")


class M0DfxBaselineError(ValueError):
    """Raised when a baseline attempts to claim unsupported evidence."""


def _machine_code(value: Any, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _CODE_PATTERN.fullmatch(normalized):
        raise M0DfxBaselineError(f"{field} must be a machine code")
    return normalized


def _timestamp(value: Any, *, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except ValueError as exc:
            raise M0DfxBaselineError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise M0DfxBaselineError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class M0DfxProbeResult:
    """One aggregate, content-free synthetic probe result."""

    probe_id: str
    status: str
    elapsed_ms: int
    sample_count: int
    failure_count: int
    error_codes: tuple[str, ...] = ()
    metrics: Mapping[str, int] | None = None

    def __post_init__(self) -> None:
        if self.probe_id not in M0_DFX_PROBE_IDS:
            raise M0DfxBaselineError("probe id is unsupported")
        if self.status not in {"passed", "failed"}:
            raise M0DfxBaselineError("probe status is unsupported")
        for field, value in (
            ("elapsedMs", self.elapsed_ms),
            ("sampleCount", self.sample_count),
            ("failureCount", self.failure_count),
        ):
            if isinstance(value, bool) or int(value) < 0:
                raise M0DfxBaselineError(f"{field} must be non-negative")
        if int(self.failure_count) > int(self.sample_count):
            raise M0DfxBaselineError("failureCount cannot exceed sampleCount")
        if self.status == "passed" and int(self.failure_count) != 0:
            raise M0DfxBaselineError("passed probes cannot contain failures")
        if self.status == "failed" and int(self.failure_count) == 0:
            raise M0DfxBaselineError("failed probes must contain a failure")
        normalized_codes = tuple(
            _machine_code(value, field="errorCode") for value in self.error_codes
        )
        if len(set(normalized_codes)) != len(normalized_codes):
            raise M0DfxBaselineError("error codes must be unique")
        object.__setattr__(self, "error_codes", normalized_codes)
        normalized_metrics: dict[str, int] = {}
        for key, value in dict(self.metrics or {}).items():
            normalized_key = _machine_code(key, field="metric key")
            if isinstance(value, bool) or int(value) < 0:
                raise M0DfxBaselineError("metric values must be non-negative integers")
            normalized_metrics[normalized_key] = int(value)
        object.__setattr__(self, "metrics", normalized_metrics)

    def report_payload(self) -> dict[str, Any]:
        return {
            "probe": self.probe_id,
            "status": self.status,
            "elapsedMs": int(self.elapsed_ms),
            "failureDenominator": {
                "sampleCount": int(self.sample_count),
                "failureCount": int(self.failure_count),
                "successCount": int(self.sample_count) - int(self.failure_count),
            },
            "errorCodes": list(self.error_codes),
            "metrics": dict(sorted(dict(self.metrics or {}).items())),
        }


def build_m0_dfx_baseline_report(
    *,
    build: str,
    environment: str,
    dataset: Mapping[str, int],
    concurrency: Mapping[str, int],
    probes: Sequence[M0DfxProbeResult],
    started_at: Any,
    completed_at: Any,
) -> dict[str, Any]:
    """Build a strict report for the fixed M0 synthetic regression baseline."""

    normalized_build = str(build or "").strip().lower()
    if not _COMMIT_PATTERN.fullmatch(normalized_build):
        raise M0DfxBaselineError("build must be a source commit hash")
    normalized_environment = _machine_code(environment, field="environment")
    normalized_dataset = _nonnegative_integer_map(dataset, field="dataset")
    normalized_concurrency = _nonnegative_integer_map(concurrency, field="concurrency")
    if not normalized_dataset or not normalized_concurrency:
        raise M0DfxBaselineError("dataset and concurrency must not be empty")
    result_by_id = {item.probe_id: item for item in probes}
    if set(result_by_id) != M0_DFX_PROBE_IDS or len(result_by_id) != len(probes):
        raise M0DfxBaselineError("baseline requires exactly one result per M0 DFX probe")
    started = _timestamp(started_at, field="startedAt")
    completed = _timestamp(completed_at, field="completedAt")
    if completed < started:
        raise M0DfxBaselineError("completedAt must not precede startedAt")
    result_payloads = [result_by_id[probe_id].report_payload() for probe_id in sorted(result_by_id)]
    passed = all(item.status == "passed" for item in probes)
    total_samples = sum(item.sample_count for item in probes)
    total_failures = sum(item.failure_count for item in probes)
    failed_probe_count = sum(item.status == "failed" for item in probes)
    return {
        "schemaVersion": M0_DFX_BASELINE_SCHEMA_VERSION,
        "status": "passed" if passed else "failed",
        "evidenceClass": "syntheticRegressionBaseline",
        "metadata": {
            "build": normalized_build,
            "environment": normalized_environment,
            "dataset": {
                "fixtureId": "ownerTruthM0SyntheticV1",
                "topology": "independentDisposablePostgresProbes",
                "counts": dict(sorted(normalized_dataset.items())),
            },
            "concurrency": dict(sorted(normalized_concurrency.items())),
            "window": {
                "startedAt": started.isoformat(),
                "completedAt": completed.isoformat(),
                "elapsedMs": int((completed - started).total_seconds() * 1000),
            },
        },
        "probes": result_payloads,
        "aggregate": {
            "probeCount": len(probes),
            "failedProbeCount": failed_probe_count,
            "failureDenominator": {
                "sampleCount": total_samples,
                "failureCount": total_failures,
                "successCount": total_samples - total_failures,
            },
        },
        "metricAvailability": [
            {
                "metric": "latencyMs",
                "status": "measured",
                "reasonCode": "syntheticProbeWallClock",
            },
            {
                "metric": "failureDenominator",
                "status": "measured",
                "reasonCode": "syntheticProbeOutcomes",
            },
            {
                "metric": "contextBytes",
                "status": "measured",
                "reasonCode": "contextCapacityPreflight",
            },
            {
                "metric": "queueAgeMs",
                "status": "notMeasured",
                "reasonCode": "disposableFixturesDoNotPreserveQueueClock",
            },
            {
                "metric": "sqlStatementCount",
                "status": "notMeasured",
                "reasonCode": "sqlInstrumentationNotAttachedToSyntheticProbes",
            },
            {
                "metric": "processResource",
                "status": "notMeasured",
                "reasonCode": "resourceSamplerNotAttachedToSyntheticProbes",
            },
            {
                "metric": "projectionLagMs",
                "status": "notMeasured",
                "reasonCode": "workerReceiptDoesNotExposeProjectionClock",
            },
        ],
        "thresholds": {
            "mode": "contractRegressionOnly",
            "contractChecksPassed": passed,
            "latencySloEnforced": False,
            "reasonCode": "syntheticBaselineDoesNotEstablishProductionSlo",
        },
        "readiness": {
            "productionPerformanceClaimAllowed": False,
            "externalProviderEvidenceIncluded": False,
            "deviceEvidenceIncluded": False,
            "reasonCode": "nonDeviceSyntheticRegressionOnly",
        },
    }


def _nonnegative_integer_map(value: Mapping[str, int], *, field: str) -> dict[str, int]:
    if not isinstance(value, Mapping):
        raise M0DfxBaselineError(f"{field} must be an object")
    normalized: dict[str, int] = {}
    for key, item in value.items():
        normalized_key = _machine_code(key, field=f"{field} key")
        if isinstance(item, bool) or int(item) < 0:
            raise M0DfxBaselineError(f"{field} values must be non-negative integers")
        normalized[normalized_key] = int(item)
    return normalized


__all__ = [
    "M0_DFX_BASELINE_SCHEMA_VERSION",
    "M0_DFX_PROBE_IDS",
    "M0DfxBaselineError",
    "M0DfxProbeResult",
    "build_m0_dfx_baseline_report",
]
