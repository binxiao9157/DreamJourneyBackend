from __future__ import annotations

import unittest

from app.services.owner_truth_dfx_load import (
    OwnerTruthDfxLoadError,
    ScheduledLoadConfig,
    nearest_rank_percentile,
    run_scheduled_load,
)


class OwnerTruthDfxLoadTests(unittest.TestCase):
    def test_nearest_rank_percentile_is_deterministic(self) -> None:
        values = [5, 1, 3, 4, 2]
        self.assertEqual(nearest_rank_percentile(values, 50), 3)
        self.assertEqual(nearest_rank_percentile(values, 95), 5)
        self.assertEqual(nearest_rank_percentile(values, 99), 5)

    def test_load_report_contains_only_aggregate_success_evidence(self) -> None:
        seen: list[int] = []
        report = run_scheduled_load(
            name="receipt",
            config=ScheduledLoadConfig(
                target_qps=100,
                duration_seconds=0.02,
                max_workers=2,
                latency_budget_ms=100,
            ),
            operation=lambda index: seen.append(index),
            metadata={"evidenceClass": "unit"},
        )

        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["scheduledSamples"], 2)
        self.assertEqual(report["successCount"], 2)
        self.assertEqual(report["failureCount"], 0)
        self.assertEqual(sorted(seen), [0, 1])
        self.assertEqual(report["metadata"]["evidenceClass"], "unit")

    def test_failures_are_counted_by_class_without_message_leakage(self) -> None:
        private_marker = "private-memory-value"

        def fail(_index: int) -> None:
            raise RuntimeError(private_marker)

        report = run_scheduled_load(
            name="failure",
            config=ScheduledLoadConfig(
                target_qps=10,
                duration_seconds=0.1,
                max_workers=1,
                latency_budget_ms=100,
            ),
            operation=fail,
        )

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["failureCodes"], {"RuntimeError": 1})
        self.assertNotIn(private_marker, str(report))

    def test_invalid_profiles_fail_closed(self) -> None:
        with self.assertRaises(OwnerTruthDfxLoadError):
            ScheduledLoadConfig(
                target_qps=0,
                duration_seconds=1,
                max_workers=1,
                latency_budget_ms=100,
            )
        with self.assertRaises(OwnerTruthDfxLoadError):
            nearest_rank_percentile([], 95)


if __name__ == "__main__":
    unittest.main()
