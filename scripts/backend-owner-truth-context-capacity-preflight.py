#!/usr/bin/env python3
"""Run the synthetic Context Packet capacity preflight without external effects."""

from __future__ import annotations

import json
import os
from typing import Any, Mapping

from app.core.config import Settings
from app.services.context_packet import ContextPacketBuilder
from app.services.in_memory_store import InMemoryStore
from app.services.owner_truth_context_capacity_preflight import (
    DEFAULT_CONTEXT_PACKET_MAX_BYTES,
    ContextCapacityPreflightConfig,
    run_owner_truth_context_capacity_preflight,
)


def _int_env(name: str, default: int) -> int:
    value = str(os.environ.get(name, default)).strip()
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _float_env(name: str, default: float) -> float:
    value = str(os.environ.get(name, default)).strip()
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric") from exc


def _seed_owner_context(store: InMemoryStore, owner_id: str) -> None:
    for index in range(12):
        store.add_archive_item(
            owner_id,
            {
                "id": f"capacity-archive-{owner_id}-{index}",
                "kind": "textNote",
                "title": f"synthetic archive {index}",
                "note": f"synthetic memory {index}",
                "personaScope": "personal",
                "digitalHumanId": owner_id,
                "analysisStatus": "analyzed",
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )
    store.save_kb_snapshot(
        owner_id,
        {
            "people": [],
            "places": [],
            "events": [],
            "facts": [
                {
                    "id": f"capacity-fact-{owner_id}-{index}",
                    "statement": f"synthetic fact {index}",
                    "confidence": "confirmed",
                    "ownerUserId": owner_id,
                    "personaScope": "personal",
                    "digitalHumanId": owner_id,
                    "evidenceStatus": "confirmed",
                    "privacyMetadata": {"scope": "generationAllowed"},
                }
                for index in range(8)
            ],
        },
    )
    store.save_care_snapshot(
        owner_id,
        {
            "riskLevel": "watch",
            "summary": "synthetic care summary",
            "trendSummary": "synthetic care trend",
            "suggestions": ["synthetic care suggestion"],
        },
    )


def main() -> None:
    config = ContextCapacityPreflightConfig(
        sustained_qps=_int_env("OWNER_TRUTH_CONTEXT_CAPACITY_QPS", 10),
        sustained_duration_seconds=_float_env(
            "OWNER_TRUTH_CONTEXT_CAPACITY_DURATION_SECONDS", 2.0
        ),
        burst_concurrency=_int_env("OWNER_TRUTH_CONTEXT_CAPACITY_BURST_CONCURRENCY", 100),
        max_packet_bytes=_int_env(
            "OWNER_TRUTH_CONTEXT_CAPACITY_MAX_PACKET_BYTES", DEFAULT_CONTEXT_PACKET_MAX_BYTES
        ),
    )
    owners = ("capacity-owner-a", "capacity-owner-b")
    store = InMemoryStore()
    for owner_id in owners:
        _seed_owner_context(store, owner_id)
    builder = ContextPacketBuilder(store, Settings(store_backend="memory"))

    def build_packet(index: int) -> Mapping[str, Any]:
        owner_id = owners[index % len(owners)]
        return builder.build(
            {
                "userId": owner_id,
                "intent": "echo_chat",
                "query": "synthetic query",
                "personaScope": "personal",
                "digitalHumanId": owner_id,
            }
        )

    def validate_packet(packet: Mapping[str, Any], index: int) -> list[str]:
        expected_owner = owners[index % len(owners)]
        other_owner = owners[(index + 1) % len(owners)]
        errors: list[str] = []
        if packet.get("userId") != expected_owner:
            errors.append("ownerMismatch")
        persona = packet.get("persona") if isinstance(packet.get("persona"), Mapping) else {}
        if persona.get("digitalHumanId") != expected_owner:
            errors.append("personaIdentityMismatch")
        generation = (
            packet.get("generationContext")
            if isinstance(packet.get("generationContext"), Mapping)
            else {}
        )
        text = str(generation.get("text") or "")
        max_chars = int(generation.get("maxChars") or 0)
        if max_chars <= 0 or len(text) > max_chars:
            errors.append("generationContextBoundViolation")
        serialized_packet = json.dumps(packet, ensure_ascii=False, sort_keys=True)
        if other_owner in serialized_packet:
            errors.append("crossOwnerMarkerObserved")
        return errors

    report = run_owner_truth_context_capacity_preflight(
        config=config,
        packet_builder=build_packet,
        packet_validator=validate_packet,
    )
    report_json = json.dumps(report, ensure_ascii=False, sort_keys=True)
    if any(owner_id in report_json for owner_id in owners):
        raise AssertionError("capacity report must not include synthetic owner identifiers")
    print(report_json)
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
