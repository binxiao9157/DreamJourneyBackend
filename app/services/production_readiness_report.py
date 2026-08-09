"""Value-free production readiness aggregation for the V4 M0 lanes.

The base ``/ready`` endpoint answers whether the API can serve traffic. This
report answers a different question: whether the complete M0 product path may
be promoted. Missing Providers, disabled workers, partial exports and stale
operational evidence therefore fail closed without making the API unhealthy.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Iterable, Mapping, Optional


PRODUCTION_READINESS_REPORT_SCHEMA_VERSION = 1

_CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_WORKER_ORDER = (
    "ownerTruthCandidateExtraction",
    "ownerTruthMemoryProjection",
    "ownerTruthMediaProcessing",
    "ownerTruthMediaDeletion",
)


def build_production_readiness_report(
    *,
    core_readiness: Mapping[str, Any],
    provider_inventory: Mapping[str, Any],
    runtime_capability_control: Mapping[str, Any],
    worker_activations: Iterable[Mapping[str, Any]],
    context_authority_enabled: bool,
    application_export_ready: bool,
    media_export_ready: bool,
    deletion_reconciliation_healthy: Optional[bool],
    scanner_evidence: Mapping[str, Any],
    operation_metrics: Mapping[str, Any],
    provider_metrics: Mapping[str, Any],
    active_kill_switches: Iterable[str],
    observed_at: Optional[datetime] = None,
) -> dict[str, Any]:
    """Build a bounded M0 promotion report from already-redacted evidence."""

    instant = _utc(observed_at or datetime.now(timezone.utc))
    providers = _mapping(provider_inventory.get("capabilities"))
    controls = _mapping(runtime_capability_control.get("capabilities"))
    workers = _worker_lane(worker_activations)
    media_storage = _provider_lane(
        _mapping(providers.get("ownerTruthMediaStorage")),
        control=_mapping(controls.get("ownerTruthMediaStorage")),
        control_required=True,
    )
    media_processing = _provider_lane(
        _mapping(providers.get("ownerTruthMediaProcessing")),
        control=_mapping(controls.get("ownerTruthMediaProcessing")),
        control_required=True,
    )
    identity = _provider_lane(
        _mapping(providers.get("identityChallenge")),
        control={},
        control_required=False,
        reject_evidence_status="syntheticOnly",
    )
    content_safety = _scanner_lane(scanner_evidence)
    lanes = {
        "coreService": _core_lane(core_readiness),
        "identity": identity,
        "contentSafety": content_safety,
        "mediaStorage": media_storage,
        "mediaProcessing": media_processing,
        "workers": workers,
        "context": _context_lane(
            enabled=bool(context_authority_enabled),
            workers=workers,
        ),
        "export": _export_lane(
            application_ready=bool(application_export_ready),
            media_ready=bool(media_export_ready),
        ),
        "deletion": _deletion_lane(
            media_storage=media_storage,
            reconciliation_healthy=deletion_reconciliation_healthy,
        ),
        "operationTelemetry": _telemetry_lane(
            operation_metrics,
            missing_reason="operationTelemetrySampleMissing",
            unavailable_reason="operationTelemetryUnavailable",
        ),
        "providerTelemetry": _telemetry_lane(
            provider_metrics,
            missing_reason="providerTelemetrySampleMissing",
            unavailable_reason="providerTelemetryUnavailable",
        ),
    }
    kill_switches = sorted(
        {
            normalized
            for value in active_kill_switches
            if (normalized := _safe_code(value, fallback=""))
        }
    )
    alerts = []
    for lane_id, lane in lanes.items():
        if lane["state"] == "ready":
            continue
        alerts.append(
            {
                "severity": "blocker" if lane["state"] == "blocked" else "warning",
                "lane": lane_id,
                "code": lane["reason"],
            }
        )
    if kill_switches:
        alerts.append(
            {
                "severity": "blocker",
                "lane": "releasePolicy",
                "code": "productionReadinessKillSwitchActive",
            }
        )

    blocked_count = sum(lane["state"] == "blocked" for lane in lanes.values())
    degraded_count = sum(lane["state"] == "degraded" for lane in lanes.values())
    ready_count = sum(lane["state"] == "ready" for lane in lanes.values())
    if blocked_count or kill_switches:
        status = "blocked"
    elif degraded_count:
        status = "degraded"
    else:
        status = "ready"
    return {
        "schemaVersion": PRODUCTION_READINESS_REPORT_SCHEMA_VERSION,
        "status": status,
        "releaseDecision": "go" if status == "ready" else "noGo",
        "observedAt": instant.isoformat(),
        "lanes": lanes,
        "killSwitches": {
            "active": bool(kill_switches),
            "activeFeatures": kill_switches,
        },
        "alerts": alerts,
        "summary": {
            "laneCount": len(lanes),
            "readyCount": ready_count,
            "degradedCount": degraded_count,
            "blockedCount": blocked_count,
            "blockerAlertCount": sum(item["severity"] == "blocker" for item in alerts),
            "warningAlertCount": sum(item["severity"] == "warning" for item in alerts),
        },
    }


def _core_lane(readiness: Mapping[str, Any]) -> dict[str, Any]:
    components = readiness.get("components")
    values = components if isinstance(components, list) else []
    non_ready = next(
        (
            item
            for item in values
            if isinstance(item, Mapping) and item.get("status") != "ready"
        ),
        None,
    )
    ready = readiness.get("status") == "ready" and non_ready is None
    return _lane(
        state="ready" if ready else "blocked",
        reason=(
            "coreServiceReady"
            if ready
            else _safe_code(
                None if non_ready is None else non_ready.get("reason"),
                fallback="coreServiceNotReady",
            )
        ),
        details={
            "componentCount": len(values),
            "readyComponentCount": sum(
                isinstance(item, Mapping) and item.get("status") == "ready"
                for item in values
            ),
        },
    )


def _provider_lane(
    capability: Mapping[str, Any],
    *,
    control: Mapping[str, Any],
    control_required: bool,
    reject_evidence_status: Optional[str] = None,
) -> dict[str, Any]:
    provider_ready = capability.get("enabled") is True and capability.get("providerReady") is True
    evidence_status = _safe_code(
        capability.get("evidenceStatus"),
        fallback="unknown",
    )
    operational_ready = control.get("operationalReady") is True
    rejected = reject_evidence_status is not None and evidence_status == reject_evidence_status
    ready = provider_ready and not rejected and (
        operational_ready if control_required else True
    )
    if ready:
        reason = "providerCapabilityReady"
    elif rejected:
        reason = _safe_code(capability.get("reason"), fallback=reject_evidence_status)
    elif control_required and provider_ready and not operational_ready:
        reason = _safe_code(
            control.get("reason"),
            fallback="runtimeCapabilityEvidenceMissing",
        )
    else:
        reason = _safe_code(
            capability.get("reason"),
            fallback="providerCapabilityUnavailable",
        )
    return _lane(
        state="ready" if ready else "blocked",
        reason=reason,
        details={
            "configured": capability.get("configurationStatus") == "valid",
            "providerReady": provider_ready,
            "operationalReady": operational_ready if control_required else None,
            "evidenceStatus": evidence_status,
            "backlogCount": _optional_non_negative_int(control.get("backlogCount")),
            "openDeadLetterCount": _optional_non_negative_int(
                control.get("openDeadLetterCount")
            ),
        },
    )


def _scanner_lane(evidence: Mapping[str, Any]) -> dict[str, Any]:
    ready = evidence.get("ready") is True
    return _lane(
        state="ready" if ready else "blocked",
        reason=(
            "clamavRuntimeReady"
            if ready
            else _safe_code(
                evidence.get("reason"),
                fallback="clamavRuntimeUnavailable",
            )
        ),
        details={
            "engineVersion": _safe_version(evidence.get("engineVersion")),
            "signatureVersion": _safe_version(evidence.get("signatureVersion")),
        },
    )


def _worker_lane(activations: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    supplied = {
        _safe_code(item.get("worker"), fallback=""): item
        for item in activations
        if isinstance(item, Mapping)
    }
    items = []
    first_reason = None
    for worker in _WORKER_ORDER:
        decision = _mapping(supplied.get(worker))
        ready = decision.get("ready") is True
        reason = (
            "ownerTruthWorkerActivationReady"
            if ready
            else _safe_code(
                decision.get("reason"),
                fallback="workerActivationEvidenceMissing",
            )
        )
        if not ready and first_reason is None:
            first_reason = reason
        items.append({"worker": worker, "ready": ready, "reason": reason})
    return _lane(
        state="ready" if first_reason is None else "blocked",
        reason=first_reason or "ownerTruthWorkersReady",
        details={
            "readyCount": sum(item["ready"] for item in items),
            "workerCount": len(items),
            "workers": items,
        },
    )


def _context_lane(*, enabled: bool, workers: Mapping[str, Any]) -> dict[str, Any]:
    worker_items = _mapping(workers.get("details")).get("workers")
    values = worker_items if isinstance(worker_items, list) else []
    required_workers_ready = all(
        next(
            (
                item.get("ready") is True
                for item in values
                if isinstance(item, Mapping) and item.get("worker") == worker
            ),
            False,
        )
        for worker in (
            "ownerTruthCandidateExtraction",
            "ownerTruthMemoryProjection",
        )
    )
    if not enabled:
        return _lane(
            state="blocked",
            reason="ownerTruthContextClosedPilotDisabled",
            details={"authorityEnabled": False, "projectionWorkersReady": required_workers_ready},
        )
    if not required_workers_ready:
        return _lane(
            state="blocked",
            reason="ownerTruthContextWorkersNotReady",
            details={"authorityEnabled": True, "projectionWorkersReady": False},
        )
    return _lane(
        state="ready",
        reason="ownerTruthContextRuntimeReady",
        details={"authorityEnabled": True, "projectionWorkersReady": True},
    )


def _export_lane(*, application_ready: bool, media_ready: bool) -> dict[str, Any]:
    if not application_ready:
        state, reason = "blocked", "applicationDataExportUnavailable"
    elif not media_ready:
        state, reason = "degraded", "mediaExportBoundaryOpen"
    else:
        state, reason = "ready", "completeDataExportReady"
    return _lane(
        state=state,
        reason=reason,
        details={
            "applicationDataReady": application_ready,
            "mediaBytesReady": media_ready,
        },
    )


def _deletion_lane(
    *,
    media_storage: Mapping[str, Any],
    reconciliation_healthy: Optional[bool],
) -> dict[str, Any]:
    storage_ready = media_storage.get("state") == "ready"
    ready = storage_ready and reconciliation_healthy is True
    if not storage_ready:
        reason = "objectStorageDeletionUnavailable"
    elif reconciliation_healthy is not True:
        reason = "deletionReconciliationUnavailable"
    else:
        reason = "externalDeletionReconciliationReady"
    return _lane(
        state="ready" if ready else "blocked",
        reason=reason,
        details={
            "objectStorageReady": storage_ready,
            "reconciliationHealthy": reconciliation_healthy,
        },
    )


def _telemetry_lane(
    metrics: Mapping[str, Any],
    *,
    missing_reason: str,
    unavailable_reason: str,
) -> dict[str, Any]:
    evidence_source = _safe_code(metrics.get("evidenceSource"), fallback="unknown")
    sink_failures = _optional_non_negative_int(metrics.get("sinkFailureCount")) or 0
    source_failures = _optional_non_negative_int(metrics.get("sourceFailureCount")) or 0
    event_count = _optional_non_negative_int(metrics.get("eventCount")) or 0
    if evidence_source != "persistent" or sink_failures or source_failures:
        state, reason = "degraded", unavailable_reason
    elif event_count == 0:
        state, reason = "degraded", missing_reason
    else:
        state, reason = "ready", "operationalTelemetryReady"
    return _lane(
        state=state,
        reason=reason,
        details={
            "evidenceSource": evidence_source,
            "eventCount": event_count,
            "sinkFailureCount": sink_failures,
            "sourceFailureCount": source_failures,
        },
    )


def _lane(*, state: str, reason: str, details: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "state": state,
        "reason": _safe_code(reason, fallback="productionReadinessUnknown"),
        "details": dict(details),
    }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _safe_code(value: Any, *, fallback: str) -> str:
    normalized = str(value or "").strip()
    return normalized if _CODE.fullmatch(normalized) else fallback


def _safe_version(value: Any) -> Optional[str]:
    normalized = str(value or "").strip()
    return normalized if _VERSION.fullmatch(normalized) else None


def _optional_non_negative_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("observed_at must include a timezone")
    return value.astimezone(timezone.utc)


__all__ = [
    "PRODUCTION_READINESS_REPORT_SCHEMA_VERSION",
    "build_production_readiness_report",
]
