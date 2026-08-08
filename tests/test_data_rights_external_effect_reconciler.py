import hashlib
import unittest

from app.services.data_rights_contract import DataRightsRequestAuthority
from app.services.data_rights_external_effect_reconciler import (
    DataRightsExternalEffectAdapterObservation,
    DataRightsExternalEffectReconciler,
)
from app.services.data_rights_external_effect_receipts import (
    DataRightsExternalEffectReceipt,
)
from app.services.in_memory_store import InMemoryStore


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class FakeExternalEffectAdapter:
    def __init__(self, outcomes):
        self.outcomes = {domain: list(values) for domain, values in outcomes.items()}
        self.calls = []

    def observe(self, *, domain: str, effect_identity_hash: str, attempt: int):
        self.calls.append((domain, effect_identity_hash, attempt))
        values = self.outcomes.get(domain, [])
        if not values:
            return DataRightsExternalEffectAdapterObservation(
                state="unsupported",
                provider_receipt_present=False,
                reason_code="externalEffectAdapterUnsupported",
            )
        value = values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class DataRightsExternalEffectReconcilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryStore()
        request = DataRightsRequestAuthority().create_request(
            command_id="external-effect-reconcile-command",
            subject_id="external-effect-reconcile-owner",
            identity_proof={"kind": "reauthenticated"},
            payload={"action": "account.delete", "scope": ["all"]},
            now="2026-08-08T10:00:00+00:00",
        ).request
        self.store.create_rights_request(request)
        self.request_id = request.request_id

    @staticmethod
    def completed(domain: str) -> DataRightsExternalEffectAdapterObservation:
        return DataRightsExternalEffectAdapterObservation(
            state="completed",
            provider_receipt_present=True,
            reason_code="externalEffectProviderCompleted",
            evidence_hash=digest(f"completed:{domain}"),
        )

    def test_access_revocation_is_required_before_any_provider_observation(self) -> None:
        adapter = FakeExternalEffectAdapter({})
        result = DataRightsExternalEffectReconciler(self.store).reconcile(
            request_id=self.request_id,
            access_revocation_status="unknown",
            adapter=adapter,
            now="2026-08-08T10:01:00+00:00",
        )

        self.assertEqual(result["status"], "blockedAccessNotRevoked")
        self.assertEqual(adapter.calls, [])
        self.assertEqual(
            self.store.list_rights_external_effect_receipts(self.request_id),
            [],
        )

    def test_all_domains_are_value_minimized_and_replay_is_idempotent(self) -> None:
        adapter = FakeExternalEffectAdapter(
            {
                "objectStorage": [self.completed("objectStorage")],
                "providerVoice": [
                    DataRightsExternalEffectAdapterObservation(
                        state="pending",
                        provider_receipt_present=False,
                        reason_code="externalEffectProviderPending",
                    )
                ],
                "providerDigitalHuman": [TimeoutError("raw provider timeout detail")],
                "notificationDelivery": [
                    DataRightsExternalEffectAdapterObservation(
                        state="unsupported",
                        provider_receipt_present=False,
                        reason_code="externalEffectAdapterUnsupported",
                    )
                ],
                "backupRetention": [RuntimeError("raw backup provider detail")],
            }
        )
        reconciler = DataRightsExternalEffectReconciler(self.store, max_attempts=3)
        first = reconciler.reconcile(
            request_id=self.request_id,
            access_revocation_status="revoked",
            adapter=adapter,
            now="2026-08-08T10:01:00+00:00",
        )

        statuses = {item["domain"]: item["state"] for item in first["domains"]}
        self.assertEqual(statuses["objectStorage"], "completed")
        self.assertEqual(statuses["providerVoice"], "pending")
        self.assertEqual(statuses["providerDigitalHuman"], "unknown")
        self.assertEqual(statuses["notificationDelivery"], "unsupported")
        self.assertEqual(statuses["backupRetention"], "failed")
        serialized = str(first)
        self.assertNotIn("raw provider timeout detail", serialized)
        self.assertNotIn("raw backup provider detail", serialized)
        self.assertNotIn("effect_identity_hash", serialized)

        replay_adapter = FakeExternalEffectAdapter(
            {
                "providerVoice": [
                    DataRightsExternalEffectAdapterObservation(
                        state="pending",
                        provider_receipt_present=False,
                        reason_code="externalEffectProviderPending",
                    )
                ],
                "providerDigitalHuman": [TimeoutError("different raw value")],
                "notificationDelivery": [
                    DataRightsExternalEffectAdapterObservation(
                        state="unsupported",
                        provider_receipt_present=False,
                        reason_code="externalEffectAdapterUnsupported",
                    )
                ],
                "backupRetention": [RuntimeError("different raw value")],
            }
        )
        second = reconciler.reconcile(
            request_id=self.request_id,
            access_revocation_status="revoked",
            adapter=replay_adapter,
            now="2026-08-08T10:02:00+00:00",
        )

        self.assertNotIn("objectStorage", [call[0] for call in replay_adapter.calls])
        object_result = next(item for item in second["domains"] if item["domain"] == "objectStorage")
        self.assertEqual(object_result["outcome"], "alreadyCompleted")

    def test_retry_budget_dead_letters_and_manual_completion_can_reconcile(self) -> None:
        reconciler = DataRightsExternalEffectReconciler(
            self.store,
            domains=("providerVoice",),
            max_attempts=2,
        )
        adapter = FakeExternalEffectAdapter(
            {"providerVoice": [TimeoutError("one"), TimeoutError("two")]}
        )
        first = reconciler.reconcile(
            request_id=self.request_id,
            access_revocation_status="revoked",
            adapter=adapter,
            now="2026-08-08T10:01:00+00:00",
        )
        second = reconciler.reconcile(
            request_id=self.request_id,
            access_revocation_status="revoked",
            adapter=adapter,
            now="2026-08-08T10:02:00+00:00",
        )

        self.assertFalse(first["domains"][0]["requiresManualReview"])
        self.assertTrue(second["domains"][0]["requiresManualReview"])
        self.assertEqual(second["domains"][0]["state"], "failed")
        self.assertEqual(second["domains"][0]["reasonCode"], "externalEffectManualReviewRequired")

        manual = reconciler.record_manual_resolution(
            request_id=self.request_id,
            domain="providerVoice",
            state="completed",
            provider_receipt_present=True,
            reason_code="externalEffectManualCompletionVerified",
            evidence_hash=digest("manual-completion"),
            observed_at="2026-08-08T10:03:00+00:00",
        )
        replay = reconciler.record_manual_resolution(
            request_id=self.request_id,
            domain="providerVoice",
            state="completed",
            provider_receipt_present=True,
            reason_code="externalEffectManualCompletionVerified",
            evidence_hash=digest("manual-completion"),
            observed_at="2026-08-08T10:04:00+00:00",
        )

        self.assertEqual(manual["state"], "completed")
        self.assertEqual(manual["outcome"], "appended")
        self.assertEqual(replay["outcome"], "deduplicated")
        final = reconciler.reconcile(
            request_id=self.request_id,
            access_revocation_status="revoked",
            adapter=FakeExternalEffectAdapter({}),
            now="2026-08-08T10:05:00+00:00",
        )
        self.assertEqual(final["domains"][0]["outcome"], "alreadyCompleted")

    def test_account_delete_boundary_does_not_consume_provider_retry_budget(self) -> None:
        effect_hash = digest(f"{self.request_id}:accountDelete:providerVoice")
        self.store.record_rights_external_effect_receipt(
            DataRightsExternalEffectReceipt(
                request_id=self.request_id,
                owner_subject_hash=self.store.summarize_rights_request(self.request_id)[
                    "request"
                ]["subjectHash"],
                domain="providerVoice",
                effect_identity_hash=effect_hash,
                state="unsupported",
                provider_receipt_present=False,
                reason_code="providerVoiceExitAdapterNotConfigured",
                observed_at="2026-08-08T10:00:30+00:00",
            )
        )
        adapter = FakeExternalEffectAdapter(
            {"providerVoice": [TimeoutError("one"), TimeoutError("two")]}
        )
        reconciler = DataRightsExternalEffectReconciler(
            self.store,
            domains=("providerVoice",),
            max_attempts=2,
        )

        first = reconciler.reconcile(
            request_id=self.request_id,
            access_revocation_status="revoked",
            adapter=adapter,
            now="2026-08-08T10:01:00+00:00",
        )
        second = reconciler.reconcile(
            request_id=self.request_id,
            access_revocation_status="revoked",
            adapter=adapter,
            now="2026-08-08T10:02:00+00:00",
        )

        self.assertEqual([call[2] for call in adapter.calls], [1, 2])
        self.assertFalse(first["domains"][0]["requiresManualReview"])
        self.assertEqual(first["domains"][0]["attempt"], 1)
        self.assertTrue(second["domains"][0]["requiresManualReview"])
        self.assertEqual(second["domains"][0]["attempt"], 2)


if __name__ == "__main__":
    unittest.main()
