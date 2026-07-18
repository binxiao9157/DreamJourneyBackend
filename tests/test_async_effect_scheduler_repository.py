from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import unittest

from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget
from app.async_effects.scheduler_repository import (
    AsyncEffectSchedulerLeaseLost,
    InMemoryAsyncEffectSchedulerLeaseRepository,
)


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 19, 1, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


def payload_hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class AsyncEffectSchedulerLeaseRepositoryTests(unittest.TestCase):
    scheduler_key = "scheduler.synthetic.tick"

    def setUp(self) -> None:
        self.clock = _Clock()
        self.repository = InMemoryAsyncEffectSchedulerLeaseRepository(now=self.clock.now)
        self.intent = AsyncEffectIntent(
            operation_type="asyncEffect.synthetic.scheduler",
            target=AsyncEffectTarget(
                owner_subject_id="owner-scheduler-test",
                vault_id="vault-scheduler-test",
                resource_type="syntheticEffect",
                resource_id="scheduler-test-1",
                resource_version=1,
                purpose="schedulerFoundation",
                authority_epoch=0,
            ),
            payload_hash=payload_hash("scheduler-metadata-only"),
        )
        self.repository.register(self.intent, scheduler_key=self.scheduler_key)

    def claim(self, scheduler_id: str = "scheduler-a"):
        return self.repository.claim_next(
            scheduler_id=scheduler_id,
            lease_seconds=30,
            supported_scheduler_keys=[self.scheduler_key],
        )

    def test_registration_is_idempotent_and_contains_no_business_payload(self):
        result = self.repository.register(self.intent, scheduler_key=self.scheduler_key)

        self.assertEqual(result.outcome, "deduplicated")
        self.assertEqual(result.state, "available")
        self.assertFalse(hasattr(result, "payload"))

    def test_only_one_scheduler_claims_an_available_lease(self):
        first = self.claim("scheduler-a")
        second = self.claim("scheduler-b")

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(first.attempt, 1)

    def test_expired_scheduler_lease_is_reclaimed_and_stale_scheduler_loses_authority(self):
        first = self.claim("scheduler-a")
        self.assertIsNotNone(first)
        self.clock.advance(31)

        second = self.claim("scheduler-b")

        self.assertIsNotNone(second)
        self.assertEqual(second.attempt, 2)
        with self.assertRaises(AsyncEffectSchedulerLeaseLost):
            self.repository.heartbeat(first, lease_seconds=30)

    def test_heartbeat_renews_current_scheduler_lease_then_release_removes_it_from_shadow(self):
        lease = self.claim()
        self.assertIsNotNone(lease)
        self.clock.advance(10)

        renewed = self.repository.heartbeat(lease, lease_seconds=30)
        released = self.repository.release(renewed)

        self.assertEqual(released.state, "released")
        self.assertEqual(self.repository.preview_eligible(), [])
        self.assertIsNone(self.claim("scheduler-b"))


if __name__ == "__main__":
    unittest.main()
