from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import unittest

from app.async_effects.business_message_projection_effects import (
    BUSINESS_MESSAGE_PROJECTION_CONSUMER_NAME,
    BUSINESS_MESSAGE_PROJECTION_EVENT_TYPE,
    BUSINESS_MESSAGE_PROJECTION_JOB_TYPE,
    BUSINESS_MESSAGE_PROJECTION_OPERATION_TYPE,
    BUSINESS_MESSAGE_PROJECTION_PURPOSE,
    BUSINESS_MESSAGE_PROJECTION_RESOURCE_TYPE,
    BusinessMessageProjectionEffectError,
    BusinessMessageProjectionRequest,
    is_business_message_projection_intent,
)
from app.async_effects.business_message_projection_repository import InboxAccountSnapshot
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


def _source(
    *,
    inbox_subject_id: str = "owner-message-effect-001",
    inbox_vault_id: str = "vault-message-effect-001",
) -> BusinessCompletionMessageSource:
    intent = AsyncEffectIntent(
        operation_type="asyncEffect.synthetic.businessMessageProjectionEffect.fixture",
        target=AsyncEffectTarget(
            owner_subject_id="owner-message-effect-001",
            vault_id="vault-message-effect-001",
            resource_type="timeLetter",
            resource_id="letter-message-effect-001",
            resource_version=2,
            purpose="timeLetterDelivery",
            authority_epoch=5,
        ),
        payload_hash=_digest("business-message-effect-source"),
    )
    completion = InMemoryAsyncEffectConsumerRepository().consume(
        AsyncEffectSyntheticConsumerCommand(
            intent=intent,
            consumer_name="fixture.businessMessageProjectionEffect",
            business_target_key=intent.business_target_key,
            outcome="completed",
            reason_code="fixtureCompleted",
            result_ref_hash=_digest("business-message-effect-result"),
        )
    )
    return BusinessCompletionMessageSource(
        intent=intent,
        completion=completion,
        message_kind=InAppMessageKind.TIME_LETTER,
        inbox_subject_id=inbox_subject_id,
        inbox_vault_id=inbox_vault_id,
    )


def _request(*, account_epoch: int = 5, max_attempts: int = 3) -> BusinessMessageProjectionRequest:
    source = _source()
    return BusinessMessageProjectionRequest(
        source=source,
        inbox_account=InboxAccountSnapshot(
            inbox_subject_id=str(source.inbox_subject_id),
            inbox_vault_id=str(source.inbox_vault_id),
            account_epoch=account_epoch,
        ),
        max_attempts=max_attempts,
    )


class BusinessMessageProjectionEffectTests(unittest.TestCase):
    def test_request_builds_one_typed_default_off_effect_without_message_content(self) -> None:
        request = _request(max_attempts=4)
        intent = request.effect_intent

        self.assertTrue(is_business_message_projection_intent(intent))
        self.assertEqual(intent.operation_type, BUSINESS_MESSAGE_PROJECTION_OPERATION_TYPE)
        self.assertEqual(intent.event_type, BUSINESS_MESSAGE_PROJECTION_EVENT_TYPE)
        self.assertEqual(intent.job_type, BUSINESS_MESSAGE_PROJECTION_JOB_TYPE)
        self.assertEqual(intent.target.resource_type, BUSINESS_MESSAGE_PROJECTION_RESOURCE_TYPE)
        self.assertEqual(intent.target.purpose, BUSINESS_MESSAGE_PROJECTION_PURPOSE)
        self.assertEqual(intent.target.resource_id, request.message_id)
        self.assertEqual(intent.max_attempts, 4)

        summary = request.value_free_summary()
        self.assertEqual(summary["effect"]["jobType"], BUSINESS_MESSAGE_PROJECTION_JOB_TYPE)
        self.assertEqual(summary["schemaVersion"], "business-message-projection-effect-v1")
        rendered = str(summary)
        self.assertNotIn("private message body", rendered)
        self.assertNotIn("notification token", rendered)

    def test_inbox_coordinates_and_max_attempts_fail_closed(self) -> None:
        source = _source()
        with self.assertRaises(BusinessMessageProjectionEffectError):
            BusinessMessageProjectionRequest(
                source=source,
                inbox_account=InboxAccountSnapshot(
                    inbox_subject_id=str(source.inbox_subject_id),
                    inbox_vault_id="other-message-effect-vault",
                    account_epoch=5,
                ),
            )
        with self.assertRaises(BusinessMessageProjectionEffectError):
            _request(max_attempts=0)
        with self.assertRaises(BusinessMessageProjectionEffectError):
            _request(max_attempts=11)

    def test_terminal_consumer_commands_have_fixed_outcomes_and_reasons(self) -> None:
        request = _request()

        completed = request.completion_command(
            projection_outcome="recorded",
            result_ref_hash=_digest("recorded"),
        )
        failed = request.failed_completion_command(result_ref_hash=_digest("failed"))
        blocked = BusinessMessageProjectionRequest.blocked_completion_command(
            intent=request.effect_intent,
            result_ref_hash=_digest("blocked"),
        )
        inbox_blocked = BusinessMessageProjectionRequest.blocked_completion_command(
            intent=request.effect_intent,
            result_ref_hash=_digest("inbox-blocked"),
            reason_code="businessMessageProjectionInboxUnavailable",
        )

        self.assertEqual(completed.consumer_name, BUSINESS_MESSAGE_PROJECTION_CONSUMER_NAME)
        self.assertEqual(completed.outcome, "completed")
        self.assertEqual(completed.reason_code, "businessMessageProjectionRecorded")
        self.assertEqual(failed.outcome, "failed")
        self.assertEqual(failed.reason_code, "businessMessageProjectionRetriesExhausted")
        self.assertEqual(blocked.outcome, "blocked")
        self.assertEqual(blocked.reason_code, "businessMessageProjectionInputUnavailable")
        self.assertEqual(inbox_blocked.outcome, "blocked")
        self.assertEqual(inbox_blocked.reason_code, "businessMessageProjectionInboxUnavailable")

        with self.assertRaises(BusinessMessageProjectionEffectError):
            request.completion_command(
                projection_outcome="unexpected",
                result_ref_hash=_digest("unexpected"),
            )
        with self.assertRaises(BusinessMessageProjectionEffectError):
            BusinessMessageProjectionRequest.blocked_completion_command(
                intent=request.effect_intent,
                result_ref_hash=_digest("invalid-blocked"),
                reason_code="unexpectedBlockedReason",
            )

    def test_request_hash_change_cannot_reuse_same_effect_identity(self) -> None:
        request = _request(account_epoch=5)
        changed = _request(account_epoch=6)
        self.assertEqual(request.effect_intent.job_id, changed.effect_intent.job_id)
        self.assertNotEqual(request.request_hash, changed.request_hash)
        self.assertNotEqual(request.effect_intent, changed.effect_intent)

        invalid = replace(request.effect_intent, job_type="anotherJob")
        self.assertFalse(is_business_message_projection_intent(invalid))


if __name__ == "__main__":
    unittest.main()
