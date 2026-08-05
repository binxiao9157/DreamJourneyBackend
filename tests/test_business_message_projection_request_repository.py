from __future__ import annotations

from hashlib import sha256
import unittest

from app.async_effects.business_message_projection_effects import (
    BusinessMessageProjectionRequest,
)
from app.async_effects.business_message_projection_repository import InboxAccountSnapshot
from app.async_effects.business_message_projection_request_repository import (
    BusinessMessageProjectionRequestConflict,
    BusinessMessageProjectionRequestPersistenceError,
    InMemoryBusinessMessageProjectionRequestRepository,
)
from app.async_effects.consumer_repository import (
    AsyncEffectSyntheticConsumerCommand,
    InMemoryAsyncEffectConsumerRepository,
)
from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget
from app.async_effects.message_notification_effects import (
    BusinessCompletionMessageSource,
    InAppMessageKind,
)


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _request(*, account_epoch: int = 4) -> BusinessMessageProjectionRequest:
    source_intent = AsyncEffectIntent(
        operation_type="asyncEffect.synthetic.businessMessageProjectionRequest.fixture",
        target=AsyncEffectTarget(
            owner_subject_id="owner-message-request-001",
            vault_id="vault-message-request-001",
            resource_type="timeLetter",
            resource_id="letter-message-request-001",
            resource_version=3,
            purpose="timeLetterDelivery",
            authority_epoch=4,
        ),
        payload_hash=_digest("business-message-request-source"),
    )
    completion = InMemoryAsyncEffectConsumerRepository().consume(
        AsyncEffectSyntheticConsumerCommand(
            intent=source_intent,
            consumer_name="fixture.businessMessageProjectionRequest",
            business_target_key=source_intent.business_target_key,
            outcome="completed",
            reason_code="fixtureCompleted",
            result_ref_hash=_digest("business-message-request-result"),
        )
    )
    source = BusinessCompletionMessageSource(
        intent=source_intent,
        completion=completion,
        message_kind=InAppMessageKind.TIME_LETTER,
    )
    return BusinessMessageProjectionRequest(
        source=source,
        inbox_account=InboxAccountSnapshot(
            inbox_subject_id=str(source.inbox_subject_id),
            inbox_vault_id=str(source.inbox_vault_id),
            account_epoch=account_epoch,
        ),
    )


class BusinessMessageProjectionRequestRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryBusinessMessageProjectionRequestRepository()

    def test_records_and_reloads_one_immutable_typed_request(self) -> None:
        request = _request()

        first = self.repository.record(request)
        duplicate = self.repository.record(request)
        restored = self.repository.load_for_intent(request.effect_intent)

        self.assertEqual(first.outcome, "recorded")
        self.assertEqual(duplicate.outcome, "deduplicated")
        self.assertEqual(restored, request)
        self.assertEqual(self.repository.request_count(), 1)
        rendered = str(first.value_free_summary())
        self.assertNotIn("owner-message-request-001", rendered)
        self.assertNotIn("vault-message-request-001", rendered)

    def test_same_job_with_a_changed_account_epoch_is_a_hard_conflict(self) -> None:
        first = _request(account_epoch=4)
        changed = _request(account_epoch=5)
        self.repository.record(first)

        with self.assertRaises(BusinessMessageProjectionRequestConflict):
            self.repository.record(changed)

    def test_wrong_effect_type_and_mutated_payload_fail_closed(self) -> None:
        request = _request()
        wrong_intent = AsyncEffectIntent(
            operation_type="asyncEffect.synthetic.notMessageProjection.fixture",
            target=request.effect_intent.target,
            payload_hash=_digest("wrong-effect"),
        )
        with self.assertRaises(BusinessMessageProjectionRequestPersistenceError):
            self.repository.load_for_intent(wrong_intent)

        self.repository.record(request)
        changed = AsyncEffectIntent(
            operation_type=request.effect_intent.operation_type,
            target=request.effect_intent.target,
            payload_hash=_digest("changed-payload"),
            event_type=request.effect_intent.event_type,
            job_type=request.effect_intent.job_type,
            max_attempts=request.effect_intent.max_attempts,
        )
        with self.assertRaises(BusinessMessageProjectionRequestConflict):
            self.repository.load_for_intent(changed)


if __name__ == "__main__":
    unittest.main()
