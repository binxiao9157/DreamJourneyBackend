"""Fail-closed coverage inventory for shadow operation metrics.

The operation metric recorder currently observes every registered HTTP route
through middleware.  Background workers are intentionally enumerated
separately: until a worker records equivalent attempt evidence, it must remain
explicitly ``notInstrumented`` and cannot support an SLO claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


OPERATION_METRIC_COVERAGE_SCHEMA_VERSION = "operation-metric-coverage-v1"


class OperationMetricCoverageError(ValueError):
    """A coverage manifest is incomplete or attempts to overstate coverage."""


class OperationMetricCoverageStatus(str, Enum):
    INSTRUMENTED = "instrumented"
    NOT_INSTRUMENTED = "notInstrumented"
    NOT_APPLICABLE = "notApplicable"


@dataclass(frozen=True)
class OperationMetricCoverageEntry:
    """One code-owned component classification with no runtime business data."""

    component_id: str
    component_kind: str
    status: OperationMetricCoverageStatus
    reason_code: str

    def __post_init__(self) -> None:
        if not str(self.component_id or "").strip():
            raise OperationMetricCoverageError("coverage component id is required")
        if self.component_kind not in {"httpRoute", "worker"}:
            raise OperationMetricCoverageError("coverage component kind is unsupported")
        if not isinstance(self.status, OperationMetricCoverageStatus):
            raise OperationMetricCoverageError("coverage status is unsupported")
        if not str(self.reason_code or "").strip():
            raise OperationMetricCoverageError("coverage reason code is required")


# This catalog is deliberately short and source-owned. The matching unit test
# scans async_effects for *WorkerRuntime classes, so adding a worker requires an
# explicit coverage classification instead of silently becoming unobserved.
CRITICAL_WORKER_COVERAGE = (
    OperationMetricCoverageEntry(
        component_id="app.async_effects.worker.AsyncEffectWorkerRuntime",
        component_kind="worker",
        status=OperationMetricCoverageStatus.NOT_INSTRUMENTED,
        reason_code="workerRecorderNotAttached",
    ),
    OperationMetricCoverageEntry(
        component_id=(
            "app.async_effects.owner_truth_memory_projection_worker."
            "OwnerTruthMemoryProjectionWorkerRuntime"
        ),
        component_kind="worker",
        status=OperationMetricCoverageStatus.NOT_INSTRUMENTED,
        reason_code="workerRecorderNotAttached",
    ),
)


@dataclass(frozen=True)
class OperationMetricCoverageManifest:
    """Internal inventory plus a redacted machine-observation summary."""

    entries: tuple[OperationMetricCoverageEntry, ...]

    def __post_init__(self) -> None:
        if not self.entries:
            raise OperationMetricCoverageError("coverage manifest must not be empty")
        component_keys = {
            (entry.component_kind, entry.component_id)
            for entry in self.entries
        }
        if len(component_keys) != len(self.entries):
            raise OperationMetricCoverageError("coverage manifest contains duplicate components")
        if not any(entry.component_kind == "httpRoute" for entry in self.entries):
            raise OperationMetricCoverageError("coverage manifest requires registered HTTP routes")
        known_workers = {
            entry.component_id
            for entry in self.entries
            if entry.component_kind == "worker"
        }
        expected_workers = {entry.component_id for entry in CRITICAL_WORKER_COVERAGE}
        if known_workers != expected_workers:
            raise OperationMetricCoverageError("coverage manifest worker catalog is incomplete")

    @property
    def coverage_complete(self) -> bool:
        return all(
            entry.status is OperationMetricCoverageStatus.INSTRUMENTED
            for entry in self.entries
        )

    def observation_summary(self) -> dict[str, object]:
        """Return counts only; route labels and worker identifiers stay internal."""

        def counts(component_kind: str) -> dict[str, int]:
            relevant = [entry for entry in self.entries if entry.component_kind == component_kind]
            return {
                "expectedCount": len(relevant),
                "instrumentedCount": sum(
                    entry.status is OperationMetricCoverageStatus.INSTRUMENTED
                    for entry in relevant
                ),
                "notInstrumentedCount": sum(
                    entry.status is OperationMetricCoverageStatus.NOT_INSTRUMENTED
                    for entry in relevant
                ),
                "notApplicableCount": sum(
                    entry.status is OperationMetricCoverageStatus.NOT_APPLICABLE
                    for entry in relevant
                ),
            }

        return {
            "schemaVersion": OPERATION_METRIC_COVERAGE_SCHEMA_VERSION,
            "httpRouteCoverage": counts("httpRoute"),
            "criticalWorkerCoverage": counts("worker"),
            "coverageComplete": self.coverage_complete,
            # Metrics remain shadow-only until retention, thresholds, and
            # operational review have independent evidence.
            "sloClaimAllowed": False,
            "valueFree": True,
        }


def build_operation_metric_coverage_manifest(
    expected_routes: Iterable[str],
) -> OperationMetricCoverageManifest:
    """Classify the route registry and every critical worker fail-closed."""

    normalized_routes = tuple(
        sorted({str(route or "").strip() for route in expected_routes if str(route or "").strip()})
    )
    if not normalized_routes:
        raise OperationMetricCoverageError("registered route inventory is required")
    route_entries = tuple(
        OperationMetricCoverageEntry(
            component_id=route,
            component_kind="httpRoute",
            status=OperationMetricCoverageStatus.INSTRUMENTED,
            reason_code="routeAuthenticationMiddleware",
        )
        for route in normalized_routes
    )
    return OperationMetricCoverageManifest(entries=route_entries + CRITICAL_WORKER_COVERAGE)
