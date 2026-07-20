from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from app.async_effects.contracts import AsyncEffectRuntimeStatus
from app.async_effects.lease_repository import AsyncEffectJobPreview
from app.async_effects.readiness_evidence import (
    AsyncEffectReadinessObservationState,
    build_async_effect_worker_readiness_evidence,
)


class AsyncEffectReadinessEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 20, 2, 40, tzinfo=timezone.utc)
        self.runtime_ready = AsyncEffectRuntimeStatus(
            enabled=True,
            worker_enabled=True,
            allowed=True,
            reason="asyncEffectRuntimeReady",
        )

    def test_ready_evidence_is_value_free_and_summarizes_backlog(self):
        evidence = build_async_effect_worker_readiness_evidence(
            runtime_status=self.runtime_ready,
            worker_id="worker-contract-test",
            previews=(
                AsyncEffectJobPreview(
                    job_id="job-sensitive-1",
                    operation_id="operation-sensitive-1",
                    job_type="asyncEffect.synthetic.noop",
                    state="pending",
                    attempt=0,
                    available_at="2026-07-20T02:38:00+00:00",
                ),
                AsyncEffectJobPreview(
                    job_id="job-sensitive-2",
                    operation_id="operation-sensitive-2",
                    job_type="timeLetter.delivery",
                    state="retryWait",
                    attempt=2,
                    available_at="2026-07-20T02:39:30+00:00",
                ),
            ),
            runnable_handler_count=1,
            observed_at=self.now,
            expires_at=self.now + timedelta(minutes=5),
        )

        summary = evidence.value_free_summary(now=self.now)

        self.assertEqual(summary["observationState"], "ready")
        self.assertEqual(summary["backlogEligibleCount"], 2)
        self.assertEqual(
            summary["backlogJobTypeCounts"],
            {"asyncEffect.synthetic.noop": 1, "timeLetter.delivery": 1},
        )
        self.assertEqual(summary["oldestEligibleAgeSeconds"], 120)
        self.assertEqual(summary["runnableHandlerCount"], 1)
        serialized = str(summary)
        for forbidden in ("job-sensitive", "operation-sensitive", "payload", "owner", "vault"):
            self.assertNotIn(forbidden, serialized)

    def test_skipped_unknown_and_expired_evidence_never_promote_readiness(self):
        skipped = build_async_effect_worker_readiness_evidence(
            runtime_status=self.runtime_ready,
            worker_id="worker-contract-test",
            previews=(),
            runnable_handler_count=1,
            observed_at=self.now,
            expires_at=self.now + timedelta(minutes=5),
            store_supported=False,
        )
        unknown = build_async_effect_worker_readiness_evidence(
            runtime_status=self.runtime_ready,
            worker_id="worker-contract-test",
            previews=(),
            runnable_handler_count=1,
            observed_at=self.now,
            expires_at=self.now + timedelta(minutes=5),
            collection_error_code="asyncEffectBacklogObservationFailed",
        )
        expired = build_async_effect_worker_readiness_evidence(
            runtime_status=self.runtime_ready,
            worker_id="worker-contract-test",
            previews=(),
            runnable_handler_count=1,
            observed_at=self.now,
            expires_at=self.now + timedelta(seconds=1),
        )

        self.assertEqual(skipped.value_free_summary(now=self.now)["observationState"], "skipped")
        self.assertEqual(unknown.value_free_summary(now=self.now)["observationState"], "unknown")
        self.assertEqual(
            expired.value_free_summary(now=self.now + timedelta(seconds=2))["observationState"],
            "expired",
        )
        self.assertFalse(skipped.is_ready(now=self.now))
        self.assertFalse(unknown.is_ready(now=self.now))
        self.assertFalse(expired.is_ready(now=self.now + timedelta(seconds=2)))

    def test_no_runnable_handler_is_blocked_not_ready(self):
        evidence = build_async_effect_worker_readiness_evidence(
            runtime_status=self.runtime_ready,
            worker_id="worker-contract-test",
            previews=(),
            runnable_handler_count=0,
            observed_at=self.now,
            expires_at=self.now + timedelta(minutes=5),
        )

        summary = evidence.value_free_summary(now=self.now)

        self.assertEqual(
            evidence.observation_state,
            AsyncEffectReadinessObservationState.BLOCKED,
        )
        self.assertEqual(summary["reason"], "asyncEffectNoRunnableHandlers")
        self.assertFalse(summary["ready"])


if __name__ == "__main__":
    unittest.main()
