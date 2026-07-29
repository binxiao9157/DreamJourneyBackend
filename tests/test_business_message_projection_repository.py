from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import unittest

from app.async_effects.business_message_projection_repository import (
    BusinessMessageProjectionConflict,
    BusinessMessageProjectionPersistenceError,
    BusinessMessageProjectionRecord,
    InboxAccountSnapshot,
    InMemoryBusinessMessageProjectionRepository,
)
from app.async_effects.consumer_repository import (
    AsyncEffectSyntheticConsumerCommand,
    InMemoryAsyncEffectConsumerRepository,
)
from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget
from app.async_effects.message_notification_effects import (
    BusinessCompletionMessageSource,
    InAppMessageKind,
    InAppMessageState,
)


def _intent() -> AsyncEffectIntent:
    return AsyncEffectIntent(
        operation_type="asyncEffect.synthetic.businessMessageProjection.fixture",
        target=AsyncEffectTarget(
            owner_subject_id="owner-message-projection-001",
            vault_id="owner-vault-projection-001",
            resource_type="timeLetter",
            resource_id="letter-message-projection-001",
            resource_version=4,
            purpose="timeLetterDelivery",
            authority_epoch=3,
        ),
        payload_hash=sha256(b"business-message-projection-fixture").hexdigest(),
    )


def _source(
    *,
    inbox_subject_id: str = "owner-message-projection-001",
    inbox_vault_id: str = "owner-vault-projection-001",
) -> BusinessCompletionMessageSource:
    intent = _intent()
    receipt = InMemoryAsyncEffectConsumerRepository().consume(
        AsyncEffectSyntheticConsumerCommand(
            intent=intent,
            consumer_name="fixture.businessMessageProjection",
            business_target_key=intent.business_target_key,
            outcome="completed",
            reason_code="fixtureCompletion",
            result_ref_hash=sha256(b"business-message-projection-result").hexdigest(),
        )
    )
    return BusinessCompletionMessageSource(
        intent=intent,
        completion=receipt,
        message_kind=InAppMessageKind.TIME_LETTER,
        inbox_subject_id=inbox_subject_id,
        inbox_vault_id=inbox_vault_id,
    )


def _snapshot(
    *,
    inbox_subject_id: str = "owner-message-projection-001",
    inbox_vault_id: str = "owner-vault-projection-001",
    account_epoch: int = 3,
    access_state: str = "active",
) -> InboxAccountSnapshot:
    return InboxAccountSnapshot(
        inbox_subject_id=inbox_subject_id,
        inbox_vault_id=inbox_vault_id,
        account_epoch=account_epoch,
        access_state=access_state,
    )


class BusinessMessageProjectionRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryBusinessMessageProjectionRepository()

    def test_cross_account_projection_is_deduplicated_without_a_public_body(self) -> None:
        source = _source(
            inbox_subject_id="family-message-projection-002",
            inbox_vault_id="family-vault-projection-002",
        )
        snapshot = _snapshot(
            inbox_subject_id="family-message-projection-002",
            inbox_vault_id="family-vault-projection-002",
            account_epoch=11,
        )

        first = self.repository.record(source, snapshot)
        duplicate = self.repository.record(source, snapshot)
        summary = first.value_free_summary()

        self.assertEqual(first.outcome, "recorded")
        self.assertEqual(duplicate.outcome, "deduplicated")
        self.assertEqual(first.record, duplicate.record)
        self.assertEqual(self.repository.record_count(), 1)
        self.assertEqual(first.record.message.state, InAppMessageState.UNREAD)
        self.assertEqual(first.record.inbox_account_epoch, 11)
        self.assertEqual(
            self.repository.load(first.record.message.message_id),
            first.record,
        )
        serialized = str(summary).lower()
        for forbidden in (
            "owner-message-projection-001",
            "family-message-projection-002",
            "private message body",
            "title",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_each_explicit_inbox_receives_its_own_immutable_projection(self) -> None:
        owner_source = _source()
        family_source = _source(
            inbox_subject_id="family-message-projection-002",
            inbox_vault_id="family-vault-projection-002",
        )

        owner = self.repository.record(owner_source, _snapshot())
        family = self.repository.record(
            family_source,
            _snapshot(
                inbox_subject_id="family-message-projection-002",
                inbox_vault_id="family-vault-projection-002",
                account_epoch=11,
            ),
        )

        self.assertNotEqual(owner.record.message.message_id, family.record.message.message_id)
        self.assertEqual(self.repository.record_count(), 2)

    def test_snapshot_mismatch_inactive_account_and_mutation_fail_closed(self) -> None:
        source = _source()
        with self.assertRaises(BusinessMessageProjectionPersistenceError):
            self.repository.record(
                source,
                _snapshot(inbox_vault_id="wrong-vault-projection-001"),
            )
        with self.assertRaises(BusinessMessageProjectionPersistenceError):
            _snapshot(access_state="suspended")

        first = self.repository.record(source, _snapshot(account_epoch=3))
        with self.assertRaises(BusinessMessageProjectionConflict):
            self.repository.record(source, _snapshot(account_epoch=4))

        self.assertEqual(self.repository.record_count(), 1)
        self.assertEqual(first.record.message.state, InAppMessageState.UNREAD)

    def test_non_unread_projection_cannot_be_constructed_for_initial_shadow_write(self) -> None:
        source = _source()
        message = replace(source.projection(), state=InAppMessageState.READ)
        self.assertEqual(message.state, InAppMessageState.READ)
        with self.assertRaises(BusinessMessageProjectionPersistenceError):
            BusinessMessageProjectionRecord(
                message=message,
                operation_id=source.intent.operation_id,
                inbox_account_epoch=3,
            )


if __name__ == "__main__":
    unittest.main()
