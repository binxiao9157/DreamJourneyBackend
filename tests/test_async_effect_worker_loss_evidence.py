from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from app.async_effects.contracts import AsyncEffectRuntimeStatus
from app.async_effects.lease_repository import AsyncEffectExpiredLeasePreview
from app.async_effects.worker_loss_evidence import (
    AsyncEffectWorkerLossObservationState,
    build_async_effect_worker_loss_evidence,
)


class AsyncEffectWorkerLossEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 20, 4, 30, tzinfo=timezone.utc)
        self.runtime = AsyncEffectRuntimeStatus(
            enabled=False,
            worker_enabled=False,
            allowed=False,
            reason="asyncEffectV1Disabled",
        )

    def test_expired_leases_become_value_free_manual_review_evidence(self):
        evidence = build_async_effect_worker_loss_evidence(
            runtime_status=self.runtime,
            observer_worker_id="worker-observer-sensitive",
            previews=(
                AsyncEffectExpiredLeasePreview(
                    job_type="timeLetter.delivery",
                    attempt=3,
                    lease_until="2026-07-20T04:26:00+00:00",
                    lease_owner="worker-owner-sensitive-a",
                ),
                AsyncEffectExpiredLeasePreview(
                    job_type="timeLetter.delivery",
                    attempt=2,
                    lease_until="2026-07-20T04:29:30+00:00",
                    lease_owner="worker-owner-sensitive-b",
                ),
            ),
            observed_at=self.now,
            expires_at=self.now + timedelta(minutes=5),
        )

        summary = evidence.value_free_summary(now=self.now)

        self.assertEqual(evidence.observation_state, AsyncEffectWorkerLossObservationState.OBSERVED)
        self.assertEqual(summary["expiredLeaseCount"], 2)
        self.assertEqual(summary["expiredJobTypeCounts"], {"timeLetter.delivery": 2})
        self.assertEqual(summary["oldestExpiredLeaseAgeSeconds"], 240)
        self.assertEqual(summary["leaseOwnerHashCount"], 2)
        self.assertTrue(summary["requiresManualReview"])
        self.assertFalse(summary["workerEnabled"])
        serialized = str(summary)
        for forbidden in (
            "worker-owner-sensitive",
            "worker-observer-sensitive",
            "job-sensitive",
            "operation-sensitive",
            "payload",
            "vault",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_clear_skipped_unknown_and_expired_states_do_not_claim_readiness(self):
        clear = build_async_effect_worker_loss_evidence(
            runtime_status=self.runtime,
            observer_worker_id="worker-observer",
            previews=(),
            observed_at=self.now,
            expires_at=self.now + timedelta(minutes=5),
        )
        skipped = build_async_effect_worker_loss_evidence(
            runtime_status=self.runtime,
            observer_worker_id="worker-observer",
            previews=(),
            observed_at=self.now,
            expires_at=self.now + timedelta(minutes=5),
            store_supported=False,
        )
        unknown = build_async_effect_worker_loss_evidence(
            runtime_status=self.runtime,
            observer_worker_id="worker-observer",
            previews=(),
            observed_at=self.now,
            expires_at=self.now + timedelta(minutes=5),
            collection_error_code="asyncEffectWorkerLossObservationFailed",
        )

        self.assertEqual(clear.value_free_summary(now=self.now)["observationState"], "clear")
        self.assertFalse(clear.value_free_summary(now=self.now)["requiresManualReview"])
        self.assertEqual(skipped.value_free_summary(now=self.now)["observationState"], "skipped")
        self.assertTrue(skipped.value_free_summary(now=self.now)["requiresManualReview"])
        self.assertEqual(unknown.value_free_summary(now=self.now)["observationState"], "unknown")
        self.assertTrue(unknown.value_free_summary(now=self.now)["requiresManualReview"])
        self.assertEqual(
            clear.value_free_summary(now=self.now + timedelta(minutes=6))["observationState"],
            "expired",
        )
        self.assertTrue(clear.value_free_summary(now=self.now + timedelta(minutes=6))["requiresManualReview"])

    def test_nonexpired_preview_fails_closed(self):
        with self.assertRaises(ValueError):
            build_async_effect_worker_loss_evidence(
                runtime_status=self.runtime,
                observer_worker_id="worker-observer",
                previews=(
                    AsyncEffectExpiredLeasePreview(
                        job_type="timeLetter.delivery",
                        attempt=1,
                        lease_until="2026-07-20T04:31:00+00:00",
                        lease_owner="worker-owner",
                    ),
                ),
                observed_at=self.now,
                expires_at=self.now + timedelta(minutes=5),
            )


if __name__ == "__main__":
    unittest.main()
