from __future__ import annotations

import json
import unittest

from app.services.owner_truth_context_capacity_preflight import (
    DFX_BURST_CONCURRENCY_TARGET,
    DFX_SUSTAINED_DURATION_SECONDS_TARGET,
    DFX_SUSTAINED_QPS_TARGET,
    ContextCapacityPreflightConfig,
    run_owner_truth_context_capacity_preflight,
)


class OwnerTruthContextCapacityPreflightTests(unittest.TestCase):
    @staticmethod
    def _packet_for(owner_id: str) -> dict[str, object]:
        return {
            "userId": owner_id,
            "persona": {"personaScope": "personal", "digitalHumanId": owner_id},
            "generationContext": {
                "text": "synthetic context",
                "maxChars": 12000,
                "sourceRefs": [{"source": "archive", "refId": f"archive-{owner_id}"}],
            },
        }

    def test_reports_value_free_success_for_sustained_and_burst_reads(self) -> None:
        owners = ("owner-a", "owner-b")

        def build_packet(index: int) -> dict[str, object]:
            return self._packet_for(owners[index % len(owners)])

        def validate(packet: dict[str, object], index: int) -> list[str]:
            expected_owner = owners[index % len(owners)]
            body = json.dumps(packet, ensure_ascii=False, sort_keys=True)
            errors: list[str] = []
            if packet.get("userId") != expected_owner:
                errors.append("ownerMismatch")
            if owners[(index + 1) % len(owners)] in body:
                errors.append("crossOwnerMarkerObserved")
            return errors

        report = run_owner_truth_context_capacity_preflight(
            config=ContextCapacityPreflightConfig(
                sustained_qps=40,
                sustained_duration_seconds=0.05,
                burst_concurrency=8,
                max_packet_bytes=4096,
            ),
            packet_builder=build_packet,
            packet_validator=validate,
        )

        self.assertEqual(report["status"], "passed")
        self.assertFalse(report["productionCapacityEvidence"])
        self.assertEqual(report["execution"]["burst"]["requestCount"], 8)
        self.assertEqual(report["execution"]["sustained"]["failedRequestCount"], 0)
        self.assertNotIn("owner-a", json.dumps(report, ensure_ascii=False))
        self.assertNotIn("owner-b", json.dumps(report, ensure_ascii=False))

    def test_fails_closed_for_packet_size_or_validation_regression(self) -> None:
        report = run_owner_truth_context_capacity_preflight(
            config=ContextCapacityPreflightConfig(
                sustained_qps=40,
                sustained_duration_seconds=0.05,
                burst_concurrency=4,
                max_packet_bytes=1024,
            ),
            packet_builder=lambda _: {"userId": "owner", "payload": "x" * 2048},
            packet_validator=lambda _packet, _index: ["crossOwnerMarkerObserved"],
        )

        self.assertEqual(report["status"], "failed")
        errors = report["execution"]["burst"]["errorCounts"]
        self.assertGreater(errors["packetTooLarge"], 0)
        self.assertGreater(errors["crossOwnerMarkerObserved"], 0)

    def test_full_target_configuration_is_not_a_production_capacity_claim(self) -> None:
        config = ContextCapacityPreflightConfig(
            sustained_qps=DFX_SUSTAINED_QPS_TARGET,
            sustained_duration_seconds=DFX_SUSTAINED_DURATION_SECONDS_TARGET,
            burst_concurrency=DFX_BURST_CONCURRENCY_TARGET,
        )

        self.assertTrue(config.full_dfx_shape_requested)
        self.assertEqual(config.sustained_request_count, 18_000)

    def test_rejects_unsafe_or_empty_configuration(self) -> None:
        with self.assertRaises(ValueError):
            ContextCapacityPreflightConfig(sustained_qps=0)
        with self.assertRaises(ValueError):
            ContextCapacityPreflightConfig(sustained_duration_seconds=0)
        with self.assertRaises(ValueError):
            ContextCapacityPreflightConfig(burst_concurrency=201)
        with self.assertRaises(ValueError):
            ContextCapacityPreflightConfig(max_packet_bytes=1023)
