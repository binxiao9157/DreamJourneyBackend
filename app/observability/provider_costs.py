"""Value-free provider usage and cost evidence.

This module deliberately records only operational metadata. Provider requests,
responses, prompts, media, direct identities, and credentials never enter the
event payload or summary. Commercial budget thresholds are a product decision;
until they are approved, every summary remains not-ready even when a verified
rate card supplies a numeric cost.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets
from threading import Lock
from typing import Any, Callable, Iterable, Mapping, Optional

from app.observability.events import ProviderCostEvidenceEvent, validate_evidence_event


_KNOWN_COST_SOURCES = frozenset({"providerMetered", "approvedRateCard"})
_VALID_STATES = frozenset(
    {"started", "succeeded", "failed", "denied", "observed", "cancelled", "unknown"}
)


def _utc_timestamp(value: Optional[datetime]) -> datetime:
    instant = value or datetime.now(timezone.utc)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("occurred_at must include a timezone")
    return instant.astimezone(timezone.utc)


class ProviderCostEvidenceRecorder:
    """Build and persist bounded provider usage evidence without changing calls."""

    policy_version = "providerCostEvidence-v1"

    def __init__(
        self,
        *,
        environment: str,
        build: str,
        event_sink: Optional[Callable[..., Mapping[str, Any]]] = None,
        event_source: Optional[Callable[[], Iterable[Mapping[str, Any]]]] = None,
        retention_days: int = 30,
        identifier_hmac_key: Optional[str] = None,
    ) -> None:
        self.environment = str(environment or "runtime").strip() or "runtime"
        self.build = str(build or "backend").strip() or "backend"
        self._event_sink = event_sink
        self._event_source = event_source
        self._retention_days = max(8, int(retention_days))
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
            f"provider-cost-v1|{scope}|{normalized}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _machine_code_hash(self, prefix: str, scope: str, value: str) -> str:
        return f"{prefix}-{self._identifier_hash(scope, value)[:48]}"

    def build_event(
        self,
        *,
        request_key: str,
        operation_key: str,
        provider: str,
        capability: str,
        unit_type: str,
        units: int,
        state: str,
        reason: str,
        attempt: int = 1,
        principal_key: Optional[str] = None,
        provider_request_key: Optional[str] = None,
        correlation_key: Optional[str] = None,
        cost_source: str = "unknown",
        cost_micros: Optional[int] = None,
        rate_card_version: Optional[str] = None,
        latency_ms: Optional[int] = None,
        occurred_at: Optional[datetime] = None,
    ) -> ProviderCostEvidenceEvent:
        normalized_state = str(state or "").strip()
        if normalized_state not in _VALID_STATES:
            raise ValueError("provider cost state is unsupported")
        normalized_source = str(cost_source or "unknown").strip()
        if normalized_source not in {"unknown", *tuple(_KNOWN_COST_SOURCES)}:
            raise ValueError("provider cost source is unsupported")
        if normalized_source == "approvedRateCard" and not str(rate_card_version or "").strip():
            raise ValueError("approvedRateCard requires rate_card_version")
        if normalized_source != "approvedRateCard" and rate_card_version is not None:
            raise ValueError("rate_card_version is only valid for approvedRateCard")
        if normalized_source != "unknown" and cost_micros is None:
            raise ValueError("known provider cost source requires cost_micros")

        instant = _utc_timestamp(occurred_at)
        normalized_attempt = max(1, int(attempt))
        request_hash = self._identifier_hash("request", request_key)
        operation_id = self._machine_code_hash("pc", "operation", operation_key)
        event_seed = "|".join(
            (
                operation_key,
                request_key,
                str(normalized_attempt),
                str(provider).strip(),
                str(capability).strip(),
                normalized_state,
                str(unit_type).strip(),
                str(max(0, int(units))),
            )
        )
        event_id = self._machine_code_hash("evt-pc", "event", event_seed)
        principal_hash = (
            self._identifier_hash("principal", principal_key)
            if principal_key is not None and str(principal_key).strip()
            else None
        )
        correlation_id = (
            self._machine_code_hash("corr", "correlation", correlation_key)
            if correlation_key is not None and str(correlation_key).strip()
            else None
        )
        provider_request_hash = (
            self._identifier_hash("providerRequest", provider_request_key)
            if provider_request_key is not None and str(provider_request_key).strip()
            else None
        )
        resource_hash = self._identifier_hash(
            "providerCapability",
            f"{str(provider).strip()}|{str(capability).strip()}",
        )
        return ProviderCostEvidenceEvent(
            eventId=event_id,
            operationId=operation_id,
            correlationId=correlation_id,
            principalHash=principal_hash,
            resourceType="providerCapability",
            resourceIdHash=resource_hash,
            state=normalized_state,
            reason=str(reason or "providerUsageUnknown").strip() or "providerUsageUnknown",
            attempt=normalized_attempt,
            occurredAt=instant,
            env=self.environment,
            build=self.build,
            provider=str(provider).strip(),
            capability=str(capability).strip(),
            providerRequestHash=provider_request_hash,
            unitType=str(unit_type).strip(),
            units=max(0, int(units)),
            costMicros=cost_micros,
            costSource=normalized_source,
            rateCardVersion=(
                str(rate_card_version).strip()
                if normalized_source == "approvedRateCard"
                else None
            ),
            latencyMs=latency_ms,
        )

    def record_attempt(self, **kwargs: Any) -> dict[str, Any]:
        event = self.build_event(**kwargs)
        result: dict[str, Any] = {"event": event, "sinkOutcome": "notConfigured"}
        if self._event_sink is None:
            return result
        try:
            receipt = self._event_sink(
                event.model_dump(mode="json"),
                retention_class="providerCost",
                expires_at_iso=(
                    event.occurredAt + timedelta(days=self._retention_days)
                ).isoformat(),
                legal_hold=False,
            )
            outcome = str(receipt.get("outcome") or "appended")
            with self._lock:
                if outcome == "deduplicated":
                    self._sink_deduplicated_count += 1
                else:
                    self._sink_persisted_count += 1
            result["sinkOutcome"] = outcome
        except Exception:
            with self._lock:
                self._sink_failure_count += 1
            result["sinkOutcome"] = "failed"
        return result

    def summary(self) -> dict[str, Any]:
        with self._lock:
            counters = {
                "sinkPersistedCount": self._sink_persisted_count,
                "sinkDeduplicatedCount": self._sink_deduplicated_count,
                "sinkFailureCount": self._sink_failure_count,
                "sourceFailureCount": self._source_failure_count,
            }
        if self._event_source is None:
            return _not_configured_summary(
                identifier_protection=self._identifier_protection,
                **counters,
            )
        try:
            summary = summarize_provider_cost_evidence(self._event_source())
            summary.update(
                {
                    "evidenceSource": "persistent",
                    "identifierProtection": self._identifier_protection,
                    **counters,
                }
            )
            return summary
        except Exception:
            with self._lock:
                self._source_failure_count += 1
                source_failure_count = self._source_failure_count
            return _not_configured_summary(
                evidence_source="summaryUnavailable",
                identifier_protection=self._identifier_protection,
                sinkPersistedCount=counters["sinkPersistedCount"],
                sinkDeduplicatedCount=counters["sinkDeduplicatedCount"],
                sinkFailureCount=counters["sinkFailureCount"],
                sourceFailureCount=source_failure_count,
            )


def _not_configured_summary(
    *,
    evidence_source: str = "notConfigured",
    identifier_protection: str,
    sinkPersistedCount: int,
    sinkDeduplicatedCount: int,
    sinkFailureCount: int,
    sourceFailureCount: int,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "policyVersion": ProviderCostEvidenceRecorder.policy_version,
        "eventCount": 0,
        "knownCostEventCount": 0,
        "unknownCostEventCount": 0,
        "knownCostMicros": 0,
        "observedProviderCapabilityCount": 0,
        "providerCapabilityCounts": {},
        "stateCounts": {},
        "costSourceCounts": {},
        "totalUnits": 0,
        "retentionClass": "providerCost",
        "windowStartedAt": None,
        "windowEndedAt": None,
        "readiness": _readiness(event_count=0, unknown_cost_event_count=0),
        "evidenceSource": evidence_source,
        "identifierProtection": identifier_protection,
        "sinkPersistedCount": sinkPersistedCount,
        "sinkDeduplicatedCount": sinkDeduplicatedCount,
        "sinkFailureCount": sinkFailureCount,
        "sourceFailureCount": sourceFailureCount,
    }


def _readiness(*, event_count: int, unknown_cost_event_count: int) -> dict[str, Any]:
    if event_count <= 0:
        reason = "providerCostEvidenceMissing"
    elif unknown_cost_event_count > 0:
        reason = "providerCostUnknown"
    else:
        # DR-027 deliberately defers commercial thresholds. Numeric costs alone
        # do not authorise provider expansion or a budget pass claim.
        reason = "commercialBudgetDeferred"
    return {
        "status": "notReady",
        "reason": reason,
        "commercialBudgetDecision": "deferred",
        "costEvidenceComplete": bool(event_count) and unknown_cost_event_count == 0,
        "costLimitEnforcementAllowed": False,
        "providerExpansionAllowed": False,
    }


def _event_payload(candidate: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = candidate.get("payload")
    return payload if isinstance(payload, Mapping) else candidate


def summarize_provider_cost_evidence(
    candidates: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Summarize only allowlisted provider cost fields, never event payloads."""

    events: list[ProviderCostEvidenceEvent] = []
    for candidate in candidates:
        event = validate_evidence_event(dict(_event_payload(candidate)))
        if isinstance(event, ProviderCostEvidenceEvent):
            events.append(event)
    events.sort(key=lambda item: (item.occurredAt, item.eventId))

    state_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    capability_counts: Counter[str] = Counter()
    total_units = 0
    known_cost_micros = 0
    known_cost_event_count = 0
    unknown_cost_event_count = 0
    for event in events:
        state_counts[event.state] += 1
        source_counts[event.costSource] += 1
        capability_counts[f"{event.provider}:{event.capability}"] += 1
        total_units += event.units
        if event.costSource in _KNOWN_COST_SOURCES and event.costMicros is not None:
            known_cost_event_count += 1
            known_cost_micros += event.costMicros
        else:
            unknown_cost_event_count += 1

    first = events[0].occurredAt.isoformat() if events else None
    last = events[-1].occurredAt.isoformat() if events else None
    return {
        "schemaVersion": 1,
        "policyVersion": ProviderCostEvidenceRecorder.policy_version,
        "eventCount": len(events),
        "knownCostEventCount": known_cost_event_count,
        "unknownCostEventCount": unknown_cost_event_count,
        "knownCostMicros": known_cost_micros,
        "observedProviderCapabilityCount": len(capability_counts),
        "providerCapabilityCounts": dict(sorted(capability_counts.items())),
        "stateCounts": dict(sorted(state_counts.items())),
        "costSourceCounts": dict(sorted(source_counts.items())),
        "totalUnits": total_units,
        "retentionClass": "providerCost",
        "windowStartedAt": first,
        "windowEndedAt": last,
        "readiness": _readiness(
            event_count=len(events),
            unknown_cost_event_count=unknown_cost_event_count,
        ),
    }


def summarize_provider_cost_evidence_for_observations(
    summary: Mapping[str, Any],
) -> dict[str, Any]:
    """Expose only aggregate readiness to the machine-only operations route."""

    readiness = dict(summary.get("readiness") or {})
    return {
        "schemaVersion": int(summary.get("schemaVersion") or 1),
        "policyVersion": str(summary.get("policyVersion") or "unknown"),
        "eventCount": int(summary.get("eventCount") or 0),
        "knownCostEventCount": int(summary.get("knownCostEventCount") or 0),
        "unknownCostEventCount": int(summary.get("unknownCostEventCount") or 0),
        "knownCostMicros": int(summary.get("knownCostMicros") or 0),
        "observedProviderCapabilityCount": int(
            summary.get("observedProviderCapabilityCount") or 0
        ),
        "providerCapabilityCounts": dict(summary.get("providerCapabilityCounts") or {}),
        "stateCounts": dict(summary.get("stateCounts") or {}),
        "costSourceCounts": dict(summary.get("costSourceCounts") or {}),
        "totalUnits": int(summary.get("totalUnits") or 0),
        "retentionClass": str(summary.get("retentionClass") or "providerCost"),
        "windowStartedAt": summary.get("windowStartedAt"),
        "windowEndedAt": summary.get("windowEndedAt"),
        "readiness": {
            "status": str(readiness.get("status") or "notReady"),
            "reason": str(readiness.get("reason") or "unknown"),
            "commercialBudgetDecision": str(
                readiness.get("commercialBudgetDecision") or "deferred"
            ),
            "costEvidenceComplete": bool(readiness.get("costEvidenceComplete")),
            "costLimitEnforcementAllowed": bool(
                readiness.get("costLimitEnforcementAllowed")
            ),
            "providerExpansionAllowed": bool(readiness.get("providerExpansionAllowed")),
        },
        "evidenceSource": str(summary.get("evidenceSource") or "unknown"),
        "identifierProtection": str(summary.get("identifierProtection") or "unknown"),
        "sinkPersistedCount": int(summary.get("sinkPersistedCount") or 0),
        "sinkDeduplicatedCount": int(summary.get("sinkDeduplicatedCount") or 0),
        "sinkFailureCount": int(summary.get("sinkFailureCount") or 0),
        "sourceFailureCount": int(summary.get("sourceFailureCount") or 0),
    }
