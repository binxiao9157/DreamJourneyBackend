#!/usr/bin/env python3
"""Verify value-free provider cost evidence without a live provider call."""

import json

from app.observability.provider_costs import ProviderCostEvidenceRecorder
from app.services.in_memory_store import InMemoryStore


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    store = InMemoryStore()
    recorder = ProviderCostEvidenceRecorder(
        environment="smoke",
        build="provider-cost-smoke",
        identifier_hmac_key="provider-cost-smoke-key-" + ("x" * 32),
        event_sink=store.append_evidence_event,
        event_source=lambda: store.list_evidence_events(event_type="providerCost"),
    )
    private_marker = "PROVIDER_COST_PRIVATE_MARKER"
    recorder.record_attempt(
        request_key=private_marker,
        operation_key="provider-cost-smoke-operation",
        provider="deepseek",
        capability="kbExtract",
        unit_type="request",
        units=1,
        state="succeeded",
        reason="providerUsageObserved",
        principal_key="provider-cost-smoke-principal",
    )
    recorder.record_attempt(
        request_key="provider-cost-rate-card-request",
        operation_key="provider-cost-rate-card-operation",
        provider="volcengineVoiceClone",
        capability="voiceCloneSynthesis",
        unit_type="character",
        units=24,
        state="succeeded",
        reason="providerUsageObserved",
        cost_source="approvedRateCard",
        cost_micros=240,
        rate_card_version="smoke-rate-card-v1",
    )

    summary = recorder.summary()
    serialized = json.dumps(summary, ensure_ascii=False, sort_keys=True)
    readiness = summary.get("readiness") or {}
    require(summary.get("eventCount") == 2, "provider cost events must persist")
    require(summary.get("unknownCostEventCount") == 1, "unknown cost evidence count")
    require(summary.get("knownCostEventCount") == 1, "known cost evidence count")
    require(summary.get("knownCostMicros") == 240, "known cost total")
    require(summary.get("retentionClass") == "providerCost", "retention class")
    require(readiness.get("status") == "notReady", "budget readiness must remain blocked")
    require(readiness.get("reason") == "providerCostUnknown", "unknown cost reason")
    require(readiness.get("costLimitEnforcementAllowed") is False, "no budget enforcement claim")
    require(readiness.get("providerExpansionAllowed") is False, "no provider expansion claim")
    require(private_marker not in serialized, "raw provider marker leaked")

    print(
        json.dumps(
            {
                "status": "passed",
                "schemaVersion": 1,
                "eventCount": summary["eventCount"],
                "knownCostEventCount": summary["knownCostEventCount"],
                "unknownCostEventCount": summary["unknownCostEventCount"],
                "readiness": readiness["status"],
                "readinessReason": readiness["reason"],
                "commercialBudgetDecision": readiness["commercialBudgetDecision"],
                "rawPrivateMarkerLeaked": False,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
