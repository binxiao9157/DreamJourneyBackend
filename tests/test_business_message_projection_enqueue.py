from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import unittest

from app.async_effects.business_message_projection_effects import BusinessMessageProjectionRequest
from app.async_effects.business_message_projection_enqueue import (
    BusinessMessageProjectionEnqueueCoordinator,
    BusinessMessageProjectionEnqueueError,
)
from app.async_effects.business_message_projection_repository import InboxAccountSnapshot
from app.async_effects.business_message_projection_request_repository import (
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
from app.async_effects.repository import InMemoryEffectKernelRepository


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _request() -> BusinessMessageProjectionRequest:
    source_intent = AsyncEffectIntent(
        operation_type="asyncEffect.synthetic.businessMessageProjectionEnqueue.fixture",
        target=AsyncEffectTarget(
            owner_subject_id="owner-message-enqueue-001",
            vault_id="vault-message-enqueue-001",
            resource_type="timeLetter",
            resource_id="letter-message-enqueue-001",
            resource_version=2,
            purpose="timeLetterDelivery",
            authority_epoch=3,
        ),
        payload_hash=_digest("business-message-enqueue-source"),
    )
    completion = InMemoryAsyncEffectConsumerRepository().consume(
        AsyncEffectSyntheticConsumerCommand(
            intent=source_intent,
            consumer_name="fixture.businessMessageProjectionEnqueue",
            business_target_key=source_intent.business_target_key,
            outcome="completed",
            reason_code="fixtureCompleted",
            result_ref_hash=_digest("business-message-enqueue-result"),
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
            account_epoch=3,
        ),
    )


class _Store:
    def __init__(self) -> None:
        self.kernel = InMemoryEffectKernelRepository()
        self.inputs = InMemoryBusinessMessageProjectionRequestRepository()

    @contextmanager
    def request_unit_of_work(self):
        yield self

    def effect_kernel_repository(self):
        return self.kernel

    def async_effect_business_message_projection_request_repository(self):
        return self.inputs


class BusinessMessageProjectionEnqueueTests(unittest.TestCase):
    def test_accepts_effect_and_immutable_input_together_with_idempotent_replay(self) -> None:
        store = _Store()
        coordinator = BusinessMessageProjectionEnqueueCoordinator(store)
        request = _request()

        with store.request_unit_of_work():
            first = coordinator.accept(request)
        with store.request_unit_of_work():
            replay = coordinator.accept(request)

        self.assertEqual(first.effect.outcome, "accepted")
        self.assertEqual(first.input.outcome, "recorded")
        self.assertEqual(replay.effect.outcome, "deduplicated")
        self.assertEqual(replay.input.outcome, "deduplicated")
        self.assertEqual(store.kernel.record_count(), 1)
        self.assertEqual(store.inputs.request_count(), 1)
        rendered = repr(first.value_free_summary())
        self.assertNotIn("owner-message-enqueue-001", rendered)
        self.assertNotIn("vault-message-enqueue-001", rendered)

    def test_requires_both_internal_repositories(self) -> None:
        with self.assertRaises(BusinessMessageProjectionEnqueueError):
            BusinessMessageProjectionEnqueueCoordinator(object()).accept(_request())


if __name__ == "__main__":
    unittest.main()
