from datetime import datetime, timezone
import json
import unittest

from app.observability.m0_dfx_baseline import (
    M0_DFX_BASELINE_SCHEMA_VERSION,
    M0DfxBaselineError,
    M0DfxProbeResult,
    build_m0_dfx_baseline_report,
)


class M0DfxBaselineTests(unittest.TestCase):
    @staticmethod
    def _probe(probe_id: str, *, status: str = "passed") -> M0DfxProbeResult:
        return M0DfxProbeResult(
            probe_id=probe_id,
            status=status,
            elapsed_ms=12,
            sample_count=4,
            failure_count=0 if status == "passed" else 1,
            error_codes=() if status == "passed" else ("syntheticProbeFailed",),
            metrics={"verifiedCheckCount": 4},
        )

    def _report(self, probes):
        return build_m0_dfx_baseline_report(
            build="0123456789abcdef",
            environment="serverContainer",
            dataset={"ownerCount": 2, "vaultCount": 2},
            concurrency={"contextBurstConcurrency": 4, "postgresProbeConcurrency": 1},
            probes=probes,
            started_at=datetime(2026, 8, 5, 10, 0, tzinfo=timezone.utc),
            completed_at=datetime(2026, 8, 5, 10, 0, 1, tzinfo=timezone.utc),
        )

    def test_passing_report_is_value_minimized_and_does_not_claim_an_slo(self) -> None:
        report = self._report(
            [
                self._probe("contextPacket"),
                self._probe("stage2MediaCandidateProjection"),
                self._probe("crossVaultRevocation"),
            ]
        )

        rendered = json.dumps(report, ensure_ascii=False, sort_keys=True)
        availability = {item["metric"]: item for item in report["metricAvailability"]}

        self.assertEqual(report["schemaVersion"], M0_DFX_BASELINE_SCHEMA_VERSION)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(
            report["metadata"]["dataset"]["fixtureId"], "ownerTruthM0SyntheticV1"
        )
        self.assertFalse(report["thresholds"]["latencySloEnforced"])
        self.assertFalse(report["readiness"]["productionPerformanceClaimAllowed"])
        self.assertEqual(availability["queueAgeMs"]["status"], "notMeasured")
        self.assertEqual(availability["sqlStatementCount"]["status"], "notMeasured")
        self.assertEqual(availability["processResource"]["status"], "notMeasured")
        self.assertNotIn("private-source-content", rendered)
        self.assertNotIn("providerLogId", rendered)

    def test_missing_or_duplicate_probe_is_rejected(self) -> None:
        with self.assertRaisesRegex(M0DfxBaselineError, "exactly one"):
            self._report(
                [
                    self._probe("contextPacket"),
                    self._probe("stage2MediaCandidateProjection"),
                ]
            )
        with self.assertRaisesRegex(M0DfxBaselineError, "exactly one"):
            self._report(
                [
                    self._probe("contextPacket"),
                    self._probe("contextPacket"),
                    self._probe("stage2MediaCandidateProjection"),
                    self._probe("crossVaultRevocation"),
                ]
            )

    def test_failed_probe_is_visible_to_the_regression_gate(self) -> None:
        report = self._report(
            [
                self._probe("contextPacket"),
                self._probe("stage2MediaCandidateProjection", status="failed"),
                self._probe("crossVaultRevocation"),
            ]
        )

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["aggregate"]["failedProbeCount"], 1)
        self.assertFalse(report["thresholds"]["contractChecksPassed"])

    def test_machine_codes_and_commit_are_fail_closed(self) -> None:
        with self.assertRaisesRegex(M0DfxBaselineError, "machine code"):
            M0DfxProbeResult(
                probe_id="contextPacket",
                status="failed",
                elapsed_ms=1,
                sample_count=1,
                failure_count=1,
                error_codes=("private error text",),
            )
        with self.assertRaisesRegex(M0DfxBaselineError, "commit hash"):
            build_m0_dfx_baseline_report(
                build="unversioned",
                environment="serverContainer",
                dataset={"ownerCount": 2},
                concurrency={"workers": 1},
                probes=[
                    self._probe("contextPacket"),
                    self._probe("stage2MediaCandidateProjection"),
                    self._probe("crossVaultRevocation"),
                ],
                started_at="2026-08-05T10:00:00+00:00",
                completed_at="2026-08-05T10:00:01+00:00",
            )

    def test_failed_probe_must_contribute_to_failure_denominator(self) -> None:
        with self.assertRaisesRegex(M0DfxBaselineError, "must contain a failure"):
            M0DfxProbeResult(
                probe_id="contextPacket",
                status="failed",
                elapsed_ms=1,
                sample_count=1,
                failure_count=0,
                error_codes=("syntheticProbeFailed",),
            )


if __name__ == "__main__":
    unittest.main()
