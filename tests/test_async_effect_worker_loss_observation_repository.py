from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from app.async_effects.contracts import AsyncEffectRuntimeStatus
from app.async_effects.lease_repository import AsyncEffectExpiredLeasePreview
from app.async_effects.worker_loss_evidence import build_async_effect_worker_loss_evidence
from app.async_effects.worker_loss_observation_repository import (
    AsyncEffectWorkerLossObservationConflict,
    InMemoryAsyncEffectWorkerLossObservationRepository,
)


def _evidence(*, observed_at: datetime | None = None):
    now = observed_at or datetime.now(timezone.utc)
    return build_async_effect_worker_loss_evidence(
        runtime_status=AsyncEffectRuntimeStatus(
            enabled=False,
            worker_enabled=False,
            allowed=False,
            reason="asyncEffectV1Disabled",
        ),
        observer_worker_id="worker-observer",
        previews=(
            AsyncEffectExpiredLeasePreview(
                job_type="timeLetter.delivery",
                attempt=1,
                lease_until=(now - timedelta(minutes=2)).isoformat(),
                lease_owner="worker-lost",
            ),
        ),
        observed_at=now,
        expires_at=now + timedelta(minutes=5),
    )


class AsyncEffectWorkerLossObservationRepositoryTests(unittest.TestCase):
    def test_record_is_append_only_and_idempotent_for_same_observation(self):
        repository = InMemoryAsyncEffectWorkerLossObservationRepository()
        evidence = _evidence(observed_at=datetime(2026, 7, 20, 4, 30, tzinfo=timezone.utc))

        first = repository.record(evidence)
        replay = repository.record(evidence)

        self.assertEqual(first.outcome, "recorded")
        self.assertEqual(replay.outcome, "deduplicated")
        self.assertEqual(repository.load(evidence.observation_id), evidence)
        self.assertEqual(repository.record_count(), 1)
        self.assertTrue(first.value_free_summary()["requiresManualReview"])

    def test_observation_id_cannot_be_reused_with_different_immutable_evidence(self):
        repository = InMemoryAsyncEffectWorkerLossObservationRepository()
        evidence = _evidence(observed_at=datetime(2026, 7, 20, 4, 30, tzinfo=timezone.utc))
        repository.record(evidence)
        changed = _evidence(observed_at=datetime(2026, 7, 20, 4, 31, tzinfo=timezone.utc))
        object.__setattr__(changed, "observation_id", evidence.observation_id)

        with self.assertRaises(AsyncEffectWorkerLossObservationConflict):
            repository.record(changed)


if __name__ == "__main__":
    unittest.main()
