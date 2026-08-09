from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import time
import unittest

from app.services.data_rights_adapter import make_account_delete_request
from app.services.data_rights_external_deletion_executor import (
    DataRightsExternalDeletionExecutor,
)
from app.services.data_rights_external_effect_reconciler import (
    DataRightsExternalEffectAdapterObservation,
)
from app.services.in_memory_store import InMemoryStore


@dataclass
class FakeProvider:
    state: str = "completed"
    calls: int = 0
    delay: float = 0

    def delete(self, *, effect_identity_hash: str, attempt: int):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        return DataRightsExternalEffectAdapterObservation(
            state=self.state,
            provider_receipt_present=self.state == "completed",
            reason_code=(
                "fakeProviderDeleted"
                if self.state == "completed"
                else "fakeProviderUnknown"
            ),
            evidence_hash=(
                sha256(f"{effect_identity_hash}:{attempt}".encode()).hexdigest()
                if self.state == "completed"
                else None
            ),
        )


class ExternalDeletionExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryStore()
        request = make_account_delete_request(
            command_id="external-delete-executor-command",
            subject_id="external-delete-executor-owner",
            phone="13900009991",
            lifecycle_marker="softDeleteAccepted",
            now="2026-08-09T12:00:00+00:00",
        )
        self.request = self.store.create_rights_request(request)
        self.request_id = request.request_id
        self.now = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)

    def test_access_must_be_revoked_before_any_provider_executes(self) -> None:
        provider = FakeProvider()
        result = DataRightsExternalDeletionExecutor(
            self.store,
            providers={"objectStorage": provider},
        ).execute(
            request_id=self.request_id,
            access_revocation_status="pending",
            now=self.now,
        )
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["accessState"], "notConfirmed")
        self.assertEqual(provider.calls, 0)

    def test_completed_receipts_are_idempotent_and_missing_domains_stay_unsupported(self) -> None:
        provider = FakeProvider()
        executor = DataRightsExternalDeletionExecutor(
            self.store,
            providers={"objectStorage": provider},
        )
        first = executor.execute(
            request_id=self.request_id,
            access_revocation_status="revoked",
            now=self.now,
        )
        replay = executor.execute(
            request_id=self.request_id,
            access_revocation_status="revoked",
            now=self.now,
        )
        self.assertEqual(first["status"], "partial")
        self.assertEqual(replay["status"], "partial")
        self.assertEqual(provider.calls, 1)
        by_domain = {item["domain"]: item for item in replay["domains"]}
        self.assertEqual(by_domain["objectStorage"]["outcome"], "alreadyCompleted")
        self.assertEqual(by_domain["providerVoice"]["state"], "unsupported")
        serialized = str(replay)
        self.assertNotIn("external-delete-executor-owner", serialized)

    def test_timeouts_converge_to_unknown_and_manual_review_without_raw_error(self) -> None:
        provider = FakeProvider(delay=0.05)
        executor = DataRightsExternalDeletionExecutor(
            self.store,
            providers={"objectStorage": provider},
            timeout_seconds=0.01,
            max_attempts=2,
        )
        first = executor.execute(
            request_id=self.request_id,
            access_revocation_status="revoked",
            now=self.now,
        )
        second = executor.execute(
            request_id=self.request_id,
            access_revocation_status="revoked",
            now=self.now,
        )
        self.assertEqual(first["status"], "unknown")
        self.assertEqual(second["status"], "unknown")
        self.assertTrue(second["requiresManualReview"])
        object_storage = next(
            item for item in second["domains"] if item["domain"] == "objectStorage"
        )
        self.assertEqual(object_storage["outcome"], "appended")
        self.assertEqual(object_storage["reasonCode"], "externalEffectManualReviewRequired")
        self.assertNotIn("timed out", str(second))


if __name__ == "__main__":
    unittest.main()
