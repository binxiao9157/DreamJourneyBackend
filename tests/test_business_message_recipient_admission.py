from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import unittest

from app.async_effects.business_message_recipient_admission import (
    TimeLetterRecipientMessageAdmissionDisposition,
    TimeLetterRecipientMessageAdmissionInput,
    TimeLetterRecipientMessageAdmissionService,
)
from app.async_effects.consumer_repository import InMemoryAsyncEffectConsumerRepository
from app.async_effects.legacy_identity_inbox_bridge import (
    InMemoryLegacyInboxAccountResolver,
    LegacyAliasClaimState,
    LegacyInboxAccountBinding,
)
from app.async_effects.message_notification_effects import (
    BusinessCompletionMessageSource,
    InAppMessageKind,
)
from app.services.delegated_access import (
    AccessGrantCommand,
    AccessGrantPurpose,
    DelegatedAccessService,
    GrantOperation,
    RelationshipLifecycleCommand,
    RelationshipOperation,
    ResourceScopeType,
)
from app.services.in_memory_store import InMemoryStore
from app.services.time_letter_delivery_effects import (
    TimeLetterDeliveryCompletion,
    TimeLetterDeliveryDisposition,
    TimeLetterDeliveryTarget,
    TimeLetterSealedSnapshot,
)


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class BusinessMessageRecipientAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc)
        self.owner_subject_id = "subject_owner"
        self.owner_vault_id = "vault_owner"
        self.family_member_id = "family_member_01"
        self.recipient_subject_id = "subject_recipient"
        self.recipient_vault_id = "vault_recipient"
        self.store = InMemoryStore()
        self.access = DelegatedAccessService(self.store, now_provider=lambda: self.now)
        self.relationship = self.access.ensure_relationship(
            owner_subject_id=self.owner_subject_id,
            family_member_id=self.family_member_id,
            member_subject_id=self.recipient_subject_id,
            status="accepted",
        )
        self.resolver_calls = 0

    def _target(self, **changes: object) -> TimeLetterDeliveryTarget:
        values: dict[str, object] = {
            "snapshot": TimeLetterSealedSnapshot(
                owner_subject_id=self.owner_subject_id,
                vault_id=self.owner_vault_id,
                letter_id="letter_001",
                sealed_version=3,
                authority_epoch=5,
                sealed_payload_hash=_hash("sealed-letter"),
                open_at="2026-07-30T07:00:00Z",
            ),
            "recipient_id": self.family_member_id,
            "recipient_subject_id": self.recipient_subject_id,
            "role": "recipient",
        }
        values.update(changes)
        return TimeLetterDeliveryTarget(**values)  # type: ignore[arg-type]

    def _completion(self, target: TimeLetterDeliveryTarget | None = None, *, disposition=TimeLetterDeliveryDisposition.DELIVERED):
        target = target or self._target()
        reason = "mailboxPersisted" if disposition is TimeLetterDeliveryDisposition.DELIVERED else "grantRevoked"
        return TimeLetterDeliveryCompletion(
            target=target,
            disposition=disposition,
            reason_code=reason,
        )

    def _owner_source(
        self,
        completion: TimeLetterDeliveryCompletion | None = None,
        *,
        message_kind: InAppMessageKind = InAppMessageKind.TIME_LETTER,
        inbox_subject_id: str | None = None,
        inbox_vault_id: str | None = None,
    ) -> BusinessCompletionMessageSource:
        completion = completion or self._completion()
        command = completion.consumer_command
        receipt = InMemoryAsyncEffectConsumerRepository().consume(command)
        return BusinessCompletionMessageSource(
            intent=command.intent,
            completion=receipt,
            message_kind=message_kind,
            inbox_subject_id=inbox_subject_id,
            inbox_vault_id=inbox_vault_id,
        )

    def _recipient_resolver(self, **changes: object):
        values: dict[str, object] = {
            "legacy_user_id": "user_recipient",
            "legacy_alias_hash": _hash("recipient-alias"),
            "subject_id": self.recipient_subject_id,
            "vault_id": self.recipient_vault_id,
            "claim_state": LegacyAliasClaimState.VERIFIED,
            "identity_proof_subject_id": self.recipient_subject_id,
            "subject_state": "active",
            "vault_owner_subject_id": self.recipient_subject_id,
            "vault_state": "active",
            "account_access_state": "active",
            "account_deletion_state": "active",
            "account_auth_epoch": 9,
            "bridge_row_version": 1,
        }
        values.update(changes)
        return InMemoryLegacyInboxAccountResolver(
            [LegacyInboxAccountBinding(**values)]  # type: ignore[arg-type]
        )

    def _service(self, **resolver_changes: object) -> TimeLetterRecipientMessageAdmissionService:
        def factory():
            self.resolver_calls += 1
            return self._recipient_resolver(**resolver_changes)

        return TimeLetterRecipientMessageAdmissionService(
            self.store,
            inbox_account_resolver_factory=factory,
        )

    def _input(
        self,
        target: TimeLetterDeliveryTarget | None = None,
        completion: TimeLetterDeliveryCompletion | None = None,
        source: BusinessCompletionMessageSource | None = None,
        *,
        now_iso: str = "2026-07-30T08:00:00Z",
    ) -> TimeLetterRecipientMessageAdmissionInput:
        target = target or self._target()
        completion = completion or self._completion(target)
        source = source or self._owner_source(completion)
        return TimeLetterRecipientMessageAdmissionInput(
            source=source,
            delivery_completion=completion,
            target=target,
            now_iso=now_iso,
        )

    def _grant(self, *, letter_id: str = "letter_001", purpose=AccessGrantPurpose.TIME_LETTER_READ):
        resource_type = (
            ResourceScopeType.TIME_LETTER
            if purpose is AccessGrantPurpose.TIME_LETTER_READ
            else ResourceScopeType.CARE_SNAPSHOT
        )
        return self.access.grant_access(
            AccessGrantCommand(
                grantorSubjectId=self.owner_subject_id,
                relationshipId=self.relationship["id"],
                granteeSubjectId=self.recipient_subject_id,
                purpose=purpose,
                resourceType=resource_type,
                resourceId=letter_id if resource_type is ResourceScopeType.TIME_LETTER else None,
                operations=[GrantOperation.READ],
                expiresAt=self.now + timedelta(days=1),
            )
        )

    def test_default_disabled_returns_before_bridge_or_authorization_side_effects(self) -> None:
        result = TimeLetterRecipientMessageAdmissionService(self.store).evaluate_shadow(self._input())

        self.assertEqual(result.disposition, TimeLetterRecipientMessageAdmissionDisposition.SHADOW_DISABLED)
        self.assertEqual(result.reason_code, "admissionDisabled")
        self.assertFalse(result.would_admit)
        self.assertEqual(self.store.list_access_receipts(owner_subject_id=self.owner_subject_id), [])

    def test_enabled_shadow_requires_exact_grant_but_writes_no_receipt_or_message(self) -> None:
        grant = self._grant()
        result = self._service().evaluate_shadow(self._input(), enabled=True)

        self.assertEqual(result.disposition, TimeLetterRecipientMessageAdmissionDisposition.WOULD_ADMIT)
        self.assertTrue(result.would_admit)
        self.assertIsNotNone(result.admission)
        self.assertEqual(result.admission.access_decision.grant_id, grant["id"])
        self.assertIsNone(result.admission.access_decision.receipt_id)
        self.assertEqual(self.resolver_calls, 1)
        self.assertEqual(self.store.list_access_receipts(owner_subject_id=self.owner_subject_id), [])
        serialized = str(result.value_free_summary())
        for forbidden in (
            self.owner_subject_id,
            self.recipient_subject_id,
            self.owner_vault_id,
            self.recipient_vault_id,
            "letter_001",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_enabled_shadow_accepts_a_numeric_leading_time_letter_target_hash(self) -> None:
        target = self._target(
            snapshot=TimeLetterSealedSnapshot(
                owner_subject_id=self.owner_subject_id,
                vault_id=self.owner_vault_id,
                letter_id="letter_numeric_0",
                sealed_version=3,
                authority_epoch=5,
                sealed_payload_hash=_hash("sealed-letter"),
                open_at="2026-07-30T07:00:00Z",
            )
        )
        self.assertTrue(target.stable_target_key[0].isdigit())
        self._grant(letter_id="letter_numeric_0")

        result = self._service().evaluate_shadow(self._input(target=target), enabled=True)

        self.assertTrue(result.would_admit)
        summary = result.value_free_summary()
        admission = summary["admission"]
        self.assertIsInstance(admission, dict)
        self.assertEqual(
            admission["source"]["resourceIdHash"],
            _hash(target.stable_target_key),
        )

    def test_accepted_relationship_without_exact_grant_is_denied(self) -> None:
        result = self._service().evaluate_shadow(self._input(), enabled=True)

        self.assertEqual(result.disposition, TimeLetterRecipientMessageAdmissionDisposition.DENIED)
        self.assertEqual(result.reason_code, "delegatedAccessDenied:activeGrantRequired")
        self.assertEqual(self.store.list_access_receipts(owner_subject_id=self.owner_subject_id), [])

    def test_wrong_resource_or_purpose_and_revoked_relationship_are_denied(self) -> None:
        for grant_args in (
            {"letter_id": "letter_other"},
            {"purpose": AccessGrantPurpose.CARE_SNAPSHOT},
        ):
            with self.subTest(grant_args=grant_args):
                self._grant(**grant_args)
                result = self._service().evaluate_shadow(self._input(), enabled=True)
                self.assertEqual(result.reason_code, "delegatedAccessDenied:activeGrantRequired")
                self.store = InMemoryStore()
                self.access = DelegatedAccessService(self.store, now_provider=lambda: self.now)
                self.relationship = self.access.ensure_relationship(
                    owner_subject_id=self.owner_subject_id,
                    family_member_id=self.family_member_id,
                    member_subject_id=self.recipient_subject_id,
                    status="accepted",
                )

        self._grant()
        self.access.change_relationship(
            RelationshipLifecycleCommand(
                ownerSubjectId=self.owner_subject_id,
                relationshipId=self.relationship["id"],
                operation=RelationshipOperation.REVOKE,
                expectedEpoch=self.relationship["relationshipEpoch"],
            )
        )
        result = self._service().evaluate_shadow(self._input(), enabled=True)
        self.assertEqual(result.reason_code, "delegatedAccessDenied:relationshipInactive")

    def test_non_due_or_nonstandard_completion_never_resolves_bridge(self) -> None:
        not_due_target = self._target(
            snapshot=TimeLetterSealedSnapshot(
                owner_subject_id=self.owner_subject_id,
                vault_id=self.owner_vault_id,
                letter_id="letter_001",
                sealed_version=3,
                authority_epoch=5,
                sealed_payload_hash=_hash("sealed-letter"),
                open_at="2026-07-31T08:00:00Z",
            )
        )
        delivered_target = self._target()
        completed_source = self._owner_source(self._completion(delivered_target))
        skipped_completion = self._completion(
            delivered_target,
            disposition=TimeLetterDeliveryDisposition.SKIPPED_REVOKED,
        )
        cases = (
            self._input(target=not_due_target, now_iso="2026-07-30T08:00:00Z"),
            self._input(
                target=delivered_target,
                completion=skipped_completion,
                source=completed_source,
            ),
        )
        for input in cases:
            with self.subTest(now=input.now_iso):
                result = self._service().evaluate_shadow(input, enabled=True)
                self.assertEqual(result.disposition, TimeLetterRecipientMessageAdmissionDisposition.INVALID)
        self.assertEqual(self.resolver_calls, 0)
        self.assertEqual(self.store.list_access_receipts(owner_subject_id=self.owner_subject_id), [])

    def test_completion_source_bridge_and_owner_scope_mismatch_fail_closed(self) -> None:
        completion = self._completion()
        source = self._owner_source(completion)
        wrong_consumer = BusinessCompletionMessageSource(
            intent=source.intent,
            completion=replace(source.completion, consumer_name="wrongConsumer"),
            message_kind=InAppMessageKind.TIME_LETTER,
        )
        recipient_scoped_source = self._owner_source(
            completion,
            inbox_subject_id=self.recipient_subject_id,
            inbox_vault_id=self.recipient_vault_id,
        )
        for source, expected in (
            (wrong_consumer, "completionSourceMismatch"),
            (recipient_scoped_source, "sourceMustBeOwnerScoped"),
        ):
            with self.subTest(expected=expected):
                result = self._service().evaluate_shadow(
                    self._input(completion=completion, source=source),
                    enabled=True,
                )
                self.assertEqual(result.reason_code, expected)
        self._grant()
        bridge_result = self._service(
            claim_state=LegacyAliasClaimState.CLAIM_PENDING,
            identity_proof_subject_id=None,
        ).evaluate_shadow(self._input(), enabled=True)
        self.assertEqual(
            bridge_result.disposition,
            TimeLetterRecipientMessageAdmissionDisposition.DENIED,
        )
        self.assertEqual(bridge_result.reason_code, "recipientInboxUnavailable")


if __name__ == "__main__":
    unittest.main()
