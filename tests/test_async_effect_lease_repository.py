from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import unittest

from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget
from app.async_effects.lease_repository import (
    AsyncEffectLeaseCancelled,
    AsyncEffectLeaseLost,
    InMemoryAsyncEffectLeaseRepository,
)


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 19, 0, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


def payload_hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class AsyncEffectLeaseRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _Clock()
        self.repository = InMemoryAsyncEffectLeaseRepository(now=self.clock.now)
        self.intent = AsyncEffectIntent(
            operation_type="asyncEffect.synthetic.noop",
            target=AsyncEffectTarget(
                owner_subject_id="owner-worker-test",
                vault_id="vault-worker-test",
                resource_type="syntheticEffect",
                resource_id="worker-test-1",
                resource_version=1,
                purpose="workerFoundation",
                authority_epoch=0,
            ),
            payload_hash=payload_hash("metadata-only"),
        )
        self.repository.seed(self.intent)

    def claim(self, worker_id: str = "worker-a"):
        return self.repository.claim_next(
            worker_id=worker_id,
            lease_seconds=30,
            supported_job_types=[self.intent.job_type],
        )

    def test_only_one_worker_claims_an_available_job(self):
        first = self.claim("worker-a")
        second = self.claim("worker-b")

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(first.attempt, 1)
        self.assertEqual(self.repository.attempt_state(first.job_id, 1), "started")

    def test_expired_lease_is_reclaimed_and_old_worker_loses_authority(self):
        first = self.claim("worker-a")
        self.assertIsNotNone(first)
        self.clock.advance(31)

        second = self.claim("worker-b")

        self.assertIsNotNone(second)
        self.assertEqual(second.attempt, 2)
        self.assertEqual(self.repository.attempt_state(first.job_id, 1), "unknown")
        with self.assertRaises(AsyncEffectLeaseLost):
            self.repository.heartbeat(first, lease_seconds=30)

    def test_expired_lease_preview_is_read_only_and_omits_job_identifiers(self):
        lease = self.claim("worker-a")
        self.assertIsNotNone(lease)
        self.clock.advance(31)

        previews = self.repository.preview_expired_leases(limit=5)

        self.assertEqual(len(previews), 1)
        self.assertEqual(previews[0].job_type, self.intent.job_type)
        self.assertEqual(previews[0].attempt, 1)
        self.assertEqual(self.repository.attempt_state(lease.job_id, 1), "started")
        self.assertNotIn(lease.job_id, str(previews[0]))
        self.assertNotIn(lease.operation_id, str(previews[0]))
        self.assertNotIn("worker-a", str(previews[0]))

    def test_heartbeat_renews_a_current_lease_then_retry_release_records_attempt(self):
        lease = self.claim()
        self.assertIsNotNone(lease)
        self.clock.advance(10)

        renewed = self.repository.heartbeat(lease, lease_seconds=30)
        preview = self.repository.release_retryable(renewed, retry_seconds=15)

        self.assertEqual(preview.state, "retryWait")
        self.assertEqual(preview.attempt, 1)
        self.assertEqual(self.repository.attempt_state(lease.job_id, 1), "retryableFailed")
        self.clock.advance(14)
        self.assertIsNone(self.claim("worker-b"))
        self.clock.advance(1)
        self.assertIsNotNone(self.claim("worker-b"))

    def test_contract_retry_context_is_scoped_to_current_lease_and_allowlisted_failure(self):
        first = self.claim("worker-a")
        self.assertIsNotNone(first)
        self.repository.release_retryable(
            first,
            retry_seconds=0,
            error_code="candidateExtraction.live.organizationDecode.invalidJson",
        )
        second = self.claim("worker-b")
        self.assertIsNotNone(second)

        context = self.repository.load_contract_retry_context(second)

        self.assertEqual(context.attempt, 1)
        self.assertEqual(context.stage, "organizationDecode")
        self.assertEqual(context.reason, "invalidJson")
        with self.assertRaises(AsyncEffectLeaseLost):
            self.repository.load_contract_retry_context(first)

        unrelated = InMemoryAsyncEffectLeaseRepository(now=self.clock.now)
        unrelated.seed(self.intent)
        unrelated_first = unrelated.claim_next(
            worker_id="worker-c",
            lease_seconds=30,
            supported_job_types=[self.intent.job_type],
        )
        self.assertIsNotNone(unrelated_first)
        unrelated.release_retryable(
            unrelated_first,
            retry_seconds=0,
            error_code="providerUnavailable",
        )
        unrelated_second = unrelated.claim_next(
            worker_id="worker-d",
            lease_seconds=30,
            supported_job_types=[self.intent.job_type],
        )

        self.assertIsNotNone(unrelated_second)
        self.assertIsNone(unrelated.load_contract_retry_context(unrelated_second))

    def test_contract_retry_context_rejects_patterned_but_unapproved_reason(self):
        first = self.claim("worker-a")
        self.assertIsNotNone(first)
        self.repository.release_retryable(
            first,
            retry_seconds=0,
            error_code=(
                "candidateExtraction.live.organizationDecode.arbitraryButPatterned"
            ),
        )
        second = self.claim("worker-b")
        self.assertIsNotNone(second)

        self.assertIsNone(self.repository.load_contract_retry_context(second))

    def test_contract_retry_context_survives_contiguous_transient_attempt(self):
        first = self.claim("worker-a")
        self.assertIsNotNone(first)
        self.repository.release_retryable(
            first,
            retry_seconds=0,
            error_code="candidateExtraction.live.organizationDecode.invalidJson",
        )
        second = self.claim("worker-b")
        self.assertIsNotNone(second)
        self.repository.release_retryable(
            second,
            retry_seconds=0,
            error_code="candidateExtraction.live.organizationRequest.transport",
        )
        third = self.claim("worker-c")
        self.assertIsNotNone(third)

        context = self.repository.load_contract_retry_context(third)

        self.assertEqual(context.attempt, 1)
        self.assertEqual(context.stage, "organizationDecode")
        self.assertEqual(context.reason, "invalidJson")

    def test_contract_retry_context_rejects_unapproved_later_attempt_failures(self):
        later_error_codes = (
            "candidateExtraction.live.supportValidate.factOmitted",
            "candidateExtraction.live.organizationRequest.authorizationRejected",
            "candidateExtraction.live.organizationRequest.arbitrary",
        )

        for later_error_code in later_error_codes:
            with self.subTest(later_error_code=later_error_code):
                repository = InMemoryAsyncEffectLeaseRepository(now=self.clock.now)
                repository.seed(self.intent)
                first = repository.claim_next(
                    worker_id="worker-a",
                    lease_seconds=30,
                    supported_job_types=[self.intent.job_type],
                )
                self.assertIsNotNone(first)
                repository.release_retryable(
                    first,
                    retry_seconds=0,
                    error_code="candidateExtraction.live.organizationDecode.invalidJson",
                )
                second = repository.claim_next(
                    worker_id="worker-b",
                    lease_seconds=30,
                    supported_job_types=[self.intent.job_type],
                )
                self.assertIsNotNone(second)
                repository.release_retryable(
                    second,
                    retry_seconds=0,
                    error_code=later_error_code,
                )
                third = repository.claim_next(
                    worker_id="worker-c",
                    lease_seconds=30,
                    supported_job_types=[self.intent.job_type],
                )
                self.assertIsNotNone(third)

                self.assertIsNone(repository.load_contract_retry_context(third))

    def test_contract_retry_context_rejects_discontinuous_attempt_history(self):
        first = self.claim("worker-a")
        self.assertIsNotNone(first)
        self.repository.release_retryable(
            first,
            retry_seconds=0,
            error_code="candidateExtraction.live.organizationDecode.invalidJson",
        )
        second = self.claim("worker-b")
        self.assertIsNotNone(second)
        self.repository.release_retryable(
            second,
            retry_seconds=0,
            error_code="candidateExtraction.live.organizationRequest.transport",
        )
        third = self.claim("worker-c")
        self.assertIsNotNone(third)
        del self.repository._attempts[(third.job_id, 2)]

        self.assertIsNone(self.repository.load_contract_retry_context(third))

    def test_contract_retry_context_rejects_expired_current_lease(self):
        first = self.claim("worker-a")
        self.assertIsNotNone(first)
        self.repository.release_retryable(
            first,
            retry_seconds=0,
            error_code="candidateExtraction.live.organizationDecode.invalidJson",
        )
        second = self.claim("worker-b")
        self.assertIsNotNone(second)
        self.clock.advance(31)

        with self.assertRaises(AsyncEffectLeaseLost):
            self.repository.load_contract_retry_context(second)

    def test_cancellation_stops_a_leased_worker_without_executing_business_logic(self):
        lease = self.claim()
        self.assertIsNotNone(lease)

        result = self.repository.request_cancel(lease.job_id)

        self.assertEqual(result.outcome, "cancellationRequested")
        with self.assertRaises(AsyncEffectLeaseCancelled):
            self.repository.heartbeat(lease, lease_seconds=30)

    def test_pending_cancellation_prevents_any_claim(self):
        result = self.repository.request_cancel(self.intent.job_id)

        self.assertEqual(result.outcome, "cancelledBeforeLease")
        self.assertIsNone(self.claim())

    def test_current_lease_can_reconstruct_its_immutable_intent_and_complete(self):
        lease = self.claim()
        self.assertIsNotNone(lease)

        loaded = self.repository.load_intent(lease)
        completion = self.repository.complete(lease, outcome="succeeded")

        self.assertEqual(loaded, self.intent)
        self.assertEqual(completion.job_state, "succeeded")
        self.assertEqual(completion.operation_state, "completed")
        self.assertEqual(completion.outbox_state, "dispatched")
        self.assertEqual(self.repository.attempt_state(lease.job_id, 1), "succeeded")
        self.assertIsNone(self.claim("worker-b"))

    def test_blocked_completion_requires_an_opaque_live_reason(self):
        lease = self.claim()
        self.assertIsNotNone(lease)

        completion = self.repository.complete(
            lease,
            outcome="blocked",
            error_code="authorityEpochChanged",
        )

        self.assertEqual(completion.job_state, "blocked")
        self.assertEqual(completion.operation_state, "blocked")
        self.assertEqual(self.repository.attempt_state(lease.job_id, 1), "terminalFailed")

    def test_failed_completion_records_a_terminal_retry_exhaustion_outcome(self):
        lease = self.claim()
        self.assertIsNotNone(lease)

        completion = self.repository.complete(
            lease,
            outcome="failed",
            error_code="mediaProcessingRetriesExhausted",
        )

        self.assertEqual(completion.job_state, "failed")
        self.assertEqual(completion.operation_state, "failed")
        self.assertEqual(self.repository.attempt_state(lease.job_id, 1), "terminalFailed")

    def test_eligible_preview_is_typed_bounded_and_read_only(self):
        other = AsyncEffectIntent(
            operation_type="asyncEffect.synthetic.other",
            target=AsyncEffectTarget(
                owner_subject_id="owner-worker-test",
                vault_id="vault-worker-test",
                resource_type="syntheticEffect",
                resource_id="worker-test-2",
                resource_version=1,
                purpose="workerFoundation",
                authority_epoch=0,
            ),
            payload_hash=payload_hash("other-metadata-only"),
        )
        self.repository.seed(other)

        previews = self.repository.preview_eligible(
            limit=1,
            job_types=[self.intent.job_type],
        )

        self.assertEqual(len(previews), 1)
        self.assertEqual(previews[0].job_type, self.intent.job_type)
        self.assertEqual(previews[0].state, "pending")
        self.assertEqual(self.repository.attempt_state(self.intent.job_id, 1), None)
        self.assertEqual(
            self.repository.preview_eligible(limit=5, job_types=[other.job_type])[0].job_id,
            other.job_id,
        )


if __name__ == "__main__":
    unittest.main()
