from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from app.services.owner_truth_source_projection_rebuild_request import (
    InMemoryOwnerTruthSourceProjectionRebuildRequestRepository,
    OwnerTruthSourceProjectionRebuildLeaseLost,
    OwnerTruthSourceProjectionRebuildRequest,
    PostgresOwnerTruthSourceProjectionRebuildRequestRepository,
)


class _MappingCursor:
    def __init__(self, row):
        self.row = row

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, statement, params):
        self.statement = statement
        self.params = params

    def fetchone(self):
        return self.row


class _MappingConnection:
    def __init__(self, row):
        self.row = row
        self.row_factory = None

    def cursor(self, *, row_factory=None):
        self.row_factory = row_factory
        return _MappingCursor(self.row)


class OwnerTruthSourceProjectionRebuildRequestRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 10, 9, 0, tzinfo=timezone.utc)
        self.repository = InMemoryOwnerTruthSourceProjectionRebuildRequestRepository(
            now=lambda: self.now
        )
        self.repository.seed(
            OwnerTruthSourceProjectionRebuildRequest(
                request_id=1,
                vault_id="vault-source-recovery",
                owner_subject_id="owner-source-recovery",
                source_id="00000000-0000-0000-0000-000000000121",
                source_version=1,
                authority_epoch=4,
                memory_revision=7,
                rights_revision=2,
                attempt=0,
                max_attempts=3,
            )
        )

    def test_claim_retry_and_completion_are_lease_fenced(self) -> None:
        first = self.repository.claim_next(worker_id="projection-worker-a", lease_seconds=5)
        self.assertIsNotNone(first)
        self.assertEqual(first.attempt, 1)

        retry = self.repository.release_retryable(
            first,
            retry_seconds=2,
            error_code="sourceProjectionRebuildFailed",
        )
        self.assertEqual(retry.state, "pending")
        self.assertIsNone(self.repository.claim_next(worker_id="projection-worker-b", lease_seconds=5))

        self.now += timedelta(seconds=2)
        second = self.repository.claim_next(worker_id="projection-worker-b", lease_seconds=5)
        self.assertEqual(second.attempt, 2)
        completed = self.repository.complete(second)
        self.assertEqual(completed.state, "completed")
        self.assertIsNone(self.repository.claim_next(worker_id="projection-worker-c", lease_seconds=5))

    def test_expired_processing_lease_is_reclaimed_after_crash(self) -> None:
        first = self.repository.claim_next(worker_id="projection-worker-a", lease_seconds=1)
        self.now += timedelta(seconds=2)

        recovered = self.repository.claim_next(worker_id="projection-worker-b", lease_seconds=5)

        self.assertEqual(recovered.request_id, first.request_id)
        self.assertEqual(recovered.attempt, 2)
        self.assertEqual(recovered.lease_owner, "projection-worker-b")

    def test_repeated_crashes_block_after_expired_final_lease(self) -> None:
        leases = []
        for expected_attempt in (1, 2, 3):
            lease = self.repository.claim_next(
                worker_id=f"projection-worker-{expected_attempt}",
                lease_seconds=1,
            )
            self.assertIsNotNone(lease)
            self.assertEqual(lease.attempt, expected_attempt)
            leases.append(lease)
            self.now += timedelta(seconds=2)

        self.assertIsNone(
            self.repository.claim_next(
                worker_id="projection-worker-must-not-get-attempt-4",
                lease_seconds=1,
            )
        )
        row = self.repository._rows[1]
        self.assertEqual(row["attempt"], 3)
        self.assertEqual(row["state"], "blocked")
        self.assertEqual(
            row["last_error_code"],
            "sourceProjectionRebuildLeaseExpiredAttemptsExhausted",
        )
        self.assertIsNone(row["lease_owner"])
        self.assertIsNone(row["lease_until"])
        self.assertIsNone(row["heartbeat_at"])

        with self.assertRaises(OwnerTruthSourceProjectionRebuildLeaseLost):
            self.repository.complete(leases[-1])

    def test_last_legal_attempt_remains_active_until_lease_expires(self) -> None:
        for expected_attempt in (1, 2):
            lease = self.repository.claim_next(
                worker_id=f"projection-worker-{expected_attempt}",
                lease_seconds=1,
            )
            self.assertEqual(lease.attempt, expected_attempt)
            self.now += timedelta(seconds=2)

        final_lease = self.repository.claim_next(
            worker_id="projection-worker-final",
            lease_seconds=5,
        )
        self.assertEqual(final_lease.attempt, 3)
        self.assertIsNone(
            self.repository.claim_next(
                worker_id="projection-worker-concurrent",
                lease_seconds=1,
            )
        )

        completed = self.repository.complete(final_lease)
        self.assertEqual(completed.state, "completed")
        self.assertEqual(completed.attempt, 3)

    def test_retry_exhaustion_records_explicit_blocked_state(self) -> None:
        for expected_attempt in (1, 2, 3):
            lease = self.repository.claim_next(worker_id="projection-worker", lease_seconds=5)
            self.assertEqual(lease.attempt, expected_attempt)
            outcome = self.repository.release_retryable(
                lease,
                retry_seconds=1,
                error_code="sourceProjectionRebuildFailed",
            )
            if expected_attempt < 3:
                self.assertEqual(outcome.state, "pending")
                self.now += timedelta(seconds=1)
            else:
                self.assertEqual(outcome.state, "blocked")

    def test_postgres_claim_requests_mapping_rows(self) -> None:
        connection = _MappingConnection(
            {
                "request_id": 9,
                "vault_id": "vault-source-recovery",
                "owner_subject_id": "owner-source-recovery",
                "source_id": "00000000-0000-0000-0000-000000000121",
                "source_version": 1,
                "authority_epoch": 4,
                "memory_revision": 7,
                "rights_revision": 2,
                "attempt": 1,
                "max_attempts": 3,
                "state": "processing",
                "available_at": self.now,
                "lease_owner": "projection-worker",
                "lease_until": self.now + timedelta(seconds=5),
                "heartbeat_at": self.now,
                "last_error_code": None,
            }
        )
        repository = PostgresOwnerTruthSourceProjectionRebuildRequestRepository(
            connection
        )

        claimed = repository.claim_next(
            worker_id="projection-worker",
            lease_seconds=5,
        )

        self.assertIsNotNone(connection.row_factory)
        self.assertEqual(claimed.request_id, 9)
        self.assertEqual(claimed.lease_owner, "projection-worker")


if __name__ == "__main__":
    unittest.main()
