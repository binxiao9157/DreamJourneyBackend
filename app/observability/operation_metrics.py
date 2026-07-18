from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets
from threading import Lock
from typing import Any, Callable, Iterable, Mapping, Optional

from app.observability.events import (
    OperationMetricEvidenceEvent,
    validate_evidence_event,
)


_OUTCOME_TO_STATE = {
    "succeeded": "succeeded",
    "failed": "failed",
    "timedOut": "unknown",
    "cancelled": "cancelled",
    "deduplicated": "succeeded",
    "unknown": "unknown",
    "feedbackMissing": "unknown",
}


def _utc_timestamp(value: Optional[datetime]) -> datetime:
    instant = value or datetime.now(timezone.utc)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("occurred_at must include a timezone")
    return instant.astimezone(timezone.utc)


class OperationMetricRecorder:
    """Builds value-free attempt records; persistence is handled by the evidence sink."""

    def __init__(
        self,
        *,
        environment: str,
        build: str,
        event_sink: Optional[Callable[..., Mapping[str, Any]]] = None,
        event_summary_source: Optional[Callable[[], Mapping[str, Any]]] = None,
        retention_days: int = 30,
        identifier_hmac_key: Optional[str] = None,
    ) -> None:
        self.environment = str(environment or "runtime").strip() or "runtime"
        self.build = str(build or "backend").strip() or "backend"
        self._event_sink = event_sink
        self._event_summary_source = event_summary_source
        self._retention_days = max(8, retention_days)
        configured_key = str(identifier_hmac_key or "").encode("utf-8")
        if len(configured_key) >= 32:
            self._identifier_hmac_key = configured_key
            self._identifier_protection = "configuredHmac"
        else:
            self._identifier_hmac_key = secrets.token_bytes(32)
            self._identifier_protection = "ephemeralHmac"
        self._sink_persisted_count = 0
        self._sink_deduplicated_count = 0
        self._sink_failure_count = 0
        self._source_failure_count = 0
        self._lock = Lock()

    def _identifier_hash(self, scope: str, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(f"{scope} source is required")
        return hmac.new(
            self._identifier_hmac_key,
            f"operation-metric-v1|{scope}|{normalized}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _machine_code_hash(self, prefix: str, scope: str, value: str) -> str:
        return f"{prefix}-{self._identifier_hash(scope, value)[:48]}"

    def build_event(
        self,
        *,
        request_key: str,
        operation_key: str,
        attempt: int,
        route: str,
        operation: str,
        outcome: str,
        feedback_state: str,
        occurred_at: Optional[datetime] = None,
        latency_ms: Optional[int] = None,
        http_status: Optional[int] = None,
        correlation_key: Optional[str] = None,
    ) -> OperationMetricEvidenceEvent:
        instant = _utc_timestamp(occurred_at)
        request_hash = self._identifier_hash("request", request_key)
        operation_id = self._machine_code_hash("opm", "operation", operation_key)
        attempt_hash = self._identifier_hash(
            "attempt",
            f"operation-metric-attempt-v1|{operation_key}|{request_key}|{attempt}"
        )
        event_id = self._machine_code_hash("evt-opm", "event", attempt_hash)
        correlation_id = (
            self._machine_code_hash("corr", "correlation", correlation_key)
            if correlation_key is not None and str(correlation_key).strip()
            else None
        )
        normalized_outcome = str(outcome or "").strip()
        state = _OUTCOME_TO_STATE.get(normalized_outcome)
        if state is None:
            raise ValueError("operation metric outcome is unsupported")
        reason = {
            "succeeded": "attemptSucceeded",
            "failed": "attemptFailed",
            "timedOut": "attemptTimedOut",
            "cancelled": "attemptCancelled",
            "deduplicated": "attemptDeduplicated",
            "unknown": "attemptUnknown",
            "feedbackMissing": "attemptFeedbackMissing",
        }[normalized_outcome]
        return OperationMetricEvidenceEvent(
            eventId=event_id,
            operationId=operation_id,
            correlationId=correlation_id,
            principalHash=None,
            resourceType="httpRoute",
            resourceIdHash=None,
            state=state,
            reason=reason,
            attempt=attempt,
            occurredAt=instant,
            env=self.environment,
            build=self.build,
            requestIdHash=request_hash,
            attemptIdHash=attempt_hash,
            operation=operation,
            route=route,
            outcome=normalized_outcome,
            feedbackState=feedback_state,
            latencyMs=latency_ms,
            httpStatus=http_status,
        )

    def record_attempt(self, **kwargs: Any) -> dict[str, Any]:
        event = self.build_event(**kwargs)
        result = {
            "event": event,
            "sinkOutcome": "notConfigured",
        }
        if self._event_sink is None:
            return result
        try:
            receipt = self._event_sink(
                event.model_dump(mode="json"),
                retention_class="operationalTemporary",
                expires_at_iso=(
                    event.occurredAt + timedelta(days=self._retention_days)
                ).isoformat(),
                legal_hold=False,
            )
            sink_outcome = str(receipt.get("outcome") or "appended")
            with self._lock:
                if sink_outcome == "deduplicated":
                    self._sink_deduplicated_count += 1
                else:
                    self._sink_persisted_count += 1
            result["sinkOutcome"] = sink_outcome
        except Exception:
            with self._lock:
                self._sink_failure_count += 1
            result["sinkOutcome"] = "failed"
        return result

    def summary(self) -> dict[str, Any]:
        with self._lock:
            persisted_count = self._sink_persisted_count
            deduplicated_count = self._sink_deduplicated_count
            sink_failure_count = self._sink_failure_count
            source_failure_count = self._source_failure_count
        if self._event_summary_source is None:
            return {
                "schemaVersion": 1,
                "eventCount": 0,
                "evidenceSource": "notConfigured",
                "sinkPersistedCount": persisted_count,
                "sinkDeduplicatedCount": deduplicated_count,
                "sinkFailureCount": sink_failure_count,
                "sourceFailureCount": source_failure_count,
                "identifierProtection": self._identifier_protection,
            }
        try:
            summary = dict(self._event_summary_source())
            summary.update(
                {
                    "evidenceSource": "persistent",
                    "sinkPersistedCount": persisted_count,
                    "sinkDeduplicatedCount": deduplicated_count,
                    "sinkFailureCount": sink_failure_count,
                    "sourceFailureCount": source_failure_count,
                    "identifierProtection": self._identifier_protection,
                }
            )
            return summary
        except Exception:
            with self._lock:
                self._source_failure_count += 1
                source_failure_count = self._source_failure_count
            return {
                "schemaVersion": 1,
                "eventCount": 0,
                "evidenceSource": "summaryUnavailable",
                "sinkPersistedCount": persisted_count,
                "sinkDeduplicatedCount": deduplicated_count,
                "sinkFailureCount": sink_failure_count,
                "sourceFailureCount": source_failure_count,
                "identifierProtection": self._identifier_protection,
            }


def summarize_operation_metrics(
    payloads: Iterable[Mapping[str, Any]],
    *,
    expected_routes: Iterable[str] = (),
) -> dict[str, Any]:
    """Return a recomputable report without exposing event payloads or input values."""

    events: list[OperationMetricEvidenceEvent] = []
    for payload in payloads:
        event = validate_evidence_event(dict(payload))
        if isinstance(event, OperationMetricEvidenceEvent):
            events.append(event)
    events.sort(key=lambda item: (item.occurredAt, item.eventId))

    request_ids = {event.requestIdHash for event in events}
    operation_attempts: dict[str, list[OperationMetricEvidenceEvent]] = defaultdict(list)
    route_counts: Counter[str] = Counter()
    outcome_counts: Counter[str] = Counter()
    feedback_counts: Counter[str] = Counter()
    environments: Counter[str] = Counter()
    builds: Counter[str] = Counter()
    redaction_versions: Counter[str] = Counter()
    for event in events:
        operation_attempts[event.operationId].append(event)
        route_counts[event.route] += 1
        outcome_counts[event.outcome] += 1
        feedback_counts[event.feedbackState] += 1
        environments[event.env] += 1
        builds[event.build] += 1
        redaction_versions[str(event.redactionVersion)] += 1

    final_outcomes: Counter[str] = Counter()
    retry_operation_count = 0
    missing_feedback_operation_count = 0
    for attempts in operation_attempts.values():
        final_outcomes[attempts[-1].outcome] += 1
        if len(attempts) > 1:
            retry_operation_count += 1
        if any(item.feedbackState == "missing" for item in attempts):
            missing_feedback_operation_count += 1

    expected_route_set = set(expected_routes)
    covered_routes = set(route_counts)
    missing_routes = sorted(expected_route_set - covered_routes)
    observed_unregistered_routes = sorted(covered_routes - expected_route_set) if expected_route_set else []
    first = events[0].occurredAt.isoformat() if events else None
    last = events[-1].occurredAt.isoformat() if events else None
    final_unknown_count = sum(
        final_outcomes[outcome]
        for outcome in ("unknown", "feedbackMissing")
    )

    return {
        "schemaVersion": 1,
        "eventCount": len(events),
        "requestCount": len(request_ids),
        "operationCount": len(operation_attempts),
        "attemptCount": len(events),
        "retryOperationCount": retry_operation_count,
        "successfulOperationCount": final_outcomes["succeeded"],
        "failedOperationCount": final_outcomes["failed"],
        "cancelledOperationCount": final_outcomes["cancelled"],
        "timedOutOperationCount": final_outcomes["timedOut"],
        "deduplicatedOperationCount": final_outcomes["deduplicated"],
        "unknownOperationCount": final_unknown_count,
        "missingFeedbackOperationCount": missing_feedback_operation_count,
        "outcomeCounts": dict(sorted(outcome_counts.items())),
        "finalOperationOutcomeCounts": dict(sorted(final_outcomes.items())),
        "feedbackCounts": dict(sorted(feedback_counts.items())),
        "routeCounts": dict(sorted(route_counts.items())),
        "routeCoverage": {
            "expectedRouteCount": len(expected_route_set),
            "coveredRouteCount": len(covered_routes & expected_route_set)
            if expected_route_set
            else len(covered_routes),
            "missingRouteCount": len(missing_routes),
            "missingRoutes": missing_routes,
            "unregisteredObservedRouteCount": len(observed_unregistered_routes),
            "unregisteredObservedRoutes": observed_unregistered_routes,
        },
        "metadata": {
            "environmentCounts": dict(sorted(environments.items())),
            "buildCounts": dict(sorted(builds.items())),
            "redactionVersionCounts": dict(sorted(redaction_versions.items())),
            "windowStartedAt": first,
            "windowEndedAt": last,
        },
        "readiness": {
            "sampleWindowPresent": bool(events),
            "routeCoverageComplete": bool(expected_route_set)
            and not missing_routes
            and not observed_unregistered_routes,
            "sloClaimAllowed": False,
            "reason": "shadowMetricsNoProductionBaseline",
        },
    }


def summarize_operation_metrics_for_observations(
    summary: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the machine-safe aggregate without route names or event payloads."""

    coverage = dict(summary.get("routeCoverage") or {})
    metadata = dict(summary.get("metadata") or {})
    readiness = dict(summary.get("readiness") or {})
    return {
        "schemaVersion": int(summary.get("schemaVersion") or 1),
        "eventCount": int(summary.get("eventCount") or 0),
        "requestCount": int(summary.get("requestCount") or 0),
        "operationCount": int(summary.get("operationCount") or 0),
        "attemptCount": int(summary.get("attemptCount") or 0),
        "retryOperationCount": int(summary.get("retryOperationCount") or 0),
        "successfulOperationCount": int(summary.get("successfulOperationCount") or 0),
        "failedOperationCount": int(summary.get("failedOperationCount") or 0),
        "cancelledOperationCount": int(summary.get("cancelledOperationCount") or 0),
        "timedOutOperationCount": int(summary.get("timedOutOperationCount") or 0),
        "deduplicatedOperationCount": int(summary.get("deduplicatedOperationCount") or 0),
        "unknownOperationCount": int(summary.get("unknownOperationCount") or 0),
        "missingFeedbackOperationCount": int(summary.get("missingFeedbackOperationCount") or 0),
        "outcomeCounts": dict(summary.get("outcomeCounts") or {}),
        "finalOperationOutcomeCounts": dict(summary.get("finalOperationOutcomeCounts") or {}),
        "feedbackCounts": dict(summary.get("feedbackCounts") or {}),
        "routeCoverage": {
            "expectedRouteCount": int(coverage.get("expectedRouteCount") or 0),
            "coveredRouteCount": int(coverage.get("coveredRouteCount") or 0),
            "missingRouteCount": int(coverage.get("missingRouteCount") or 0),
            "unregisteredObservedRouteCount": int(
                coverage.get("unregisteredObservedRouteCount") or 0
            ),
        },
        "metadata": {
            "environmentCounts": dict(metadata.get("environmentCounts") or {}),
            "buildCounts": dict(metadata.get("buildCounts") or {}),
            "redactionVersionCounts": dict(
                metadata.get("redactionVersionCounts") or {}
            ),
            "windowStartedAt": metadata.get("windowStartedAt"),
            "windowEndedAt": metadata.get("windowEndedAt"),
        },
        "readiness": {
            "sampleWindowPresent": bool(readiness.get("sampleWindowPresent")),
            "routeCoverageComplete": bool(readiness.get("routeCoverageComplete")),
            "sloClaimAllowed": bool(readiness.get("sloClaimAllowed")),
            "reason": str(readiness.get("reason") or "unknown"),
        },
        "evidenceSource": str(summary.get("evidenceSource") or "unknown"),
        "sinkPersistedCount": int(summary.get("sinkPersistedCount") or 0),
        "sinkDeduplicatedCount": int(summary.get("sinkDeduplicatedCount") or 0),
        "sinkFailureCount": int(summary.get("sinkFailureCount") or 0),
        "sourceFailureCount": int(summary.get("sourceFailureCount") or 0),
        "identifierProtection": str(
            summary.get("identifierProtection") or "unknown"
        ),
    }
