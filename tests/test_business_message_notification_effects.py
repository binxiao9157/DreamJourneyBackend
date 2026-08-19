import unittest
from hashlib import sha256
from uuid import UUID

from app.async_effects.consumer_repository import (
    AsyncEffectSyntheticConsumerCommand,
    InMemoryAsyncEffectConsumerRepository,
)
from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget
from app.async_effects.message_notification_effects import (
    BusinessCompletionMessageSource,
    BusinessMessageNotificationContractError,
    DeviceSubscriptionNotificationBinding,
    DeviceSubscriptionStatus,
    InAppMessageKind,
    InAppMessageState,
    NotificationChannel,
    NotificationDeliveryReceipt,
    NotificationDeliveryState,
    NotificationIntentReconciliation,
    NotificationRouteAuthorizationReason,
    build_business_completion_message_notification_plan,
    register_device_subscription,
    revoke_device_subscription,
    rotate_device_subscription,
)


def _intent(*, resource_id: str = "letter-001") -> AsyncEffectIntent:
    return AsyncEffectIntent(
        operation_type="asyncEffect.synthetic.messageNotification.fixture",
        target=AsyncEffectTarget(
            owner_subject_id="owner-001",
            vault_id="vault-001",
            resource_type="timeLetter",
            resource_id=resource_id,
            resource_version=4,
            purpose="timeLetterDelivery",
            authority_epoch=3,
        ),
        payload_hash=sha256(b"value-free-message-notification-fixture").hexdigest(),
    )


def _source(
    *,
    outcome: str = "completed",
    inbox_subject_id=None,
    inbox_vault_id=None,
    resource_id: str = "letter-001",
) -> BusinessCompletionMessageSource:
    intent = _intent(resource_id=resource_id)
    receipt = InMemoryAsyncEffectConsumerRepository().consume(
        AsyncEffectSyntheticConsumerCommand(
            intent=intent,
            consumer_name="fixture.messageNotification",
            business_target_key=intent.business_target_key,
            outcome=outcome,
            reason_code="fixtureCompletion",
            result_ref_hash=sha256(b"fixture-result").hexdigest(),
        )
    )
    source = BusinessCompletionMessageSource(
        intent=intent,
        completion=receipt,
        message_kind=InAppMessageKind.TIME_LETTER,
    )
    if inbox_subject_id is None and inbox_vault_id is None:
        return source
    return BusinessCompletionMessageSource(
        intent=intent,
        completion=receipt,
        message_kind=InAppMessageKind.TIME_LETTER,
        inbox_subject_id=inbox_subject_id,
        inbox_vault_id=inbox_vault_id,
    )


def _subscription(
    *,
    token_seed: bytes = b"token-one",
    inbox_subject_id: str = "owner-001",
    inbox_vault_id: str = "vault-001",
    account_epoch: int = 3,
):
    return register_device_subscription(
        inbox_subject_id=inbox_subject_id,
        inbox_vault_id=inbox_vault_id,
        installation_id="ios-installation-001",
        platform="ios",
        token_hash=sha256(token_seed).hexdigest(),
        account_epoch=account_epoch,
    ).subscription


class BusinessMessageNotificationPlanTests(unittest.TestCase):
    def test_canonical_uuid_resource_id_starting_with_digit_is_valid(self):
        resource_id = "31735a38-d953-4d7a-b0e4-e5939476aa01"
        source = _source(resource_id=resource_id)

        plan = build_business_completion_message_notification_plan(
            source,
            notification_channels=(NotificationChannel.LOCAL,),
            generation=2,
        )

        self.assertEqual(plan.message.resource_id, str(UUID(resource_id)))

    def test_canonical_hash_resource_id_is_valid_for_message_and_notification_routes(self):
        resource_id = "0" * 64
        source = _source(resource_id=resource_id)

        plan = build_business_completion_message_notification_plan(
            source,
            notification_channels=(NotificationChannel.LOCAL,),
            generation=2,
        )

        self.assertEqual(plan.message.resource_id, resource_id)
        self.assertEqual(plan.notification_intents[0].route_contract()["resourceId"], resource_id)

    def test_completed_business_receipt_creates_one_redacted_message_and_channel_intents(self):
        source = _source()

        plan = build_business_completion_message_notification_plan(
            source,
            notification_channels=(NotificationChannel.LOCAL, NotificationChannel.APNS),
            generation=7,
        )

        projection = plan.message.server_projection()
        self.assertEqual(plan.message.state, InAppMessageState.UNREAD)
        self.assertEqual(projection["kind"], "timeLetter")
        self.assertTrue(projection["metadataOnly"])
        self.assertTrue(projection["contentRedacted"])
        self.assertEqual(projection["resourceOwnerSubjectId"], "owner-001")
        self.assertEqual(projection["inboxSubjectId"], "owner-001")
        self.assertNotIn("body", projection)
        self.assertNotIn("title", projection)
        self.assertNotIn("ownerSubjectId", projection)
        self.assertEqual(
            [intent.channel for intent in plan.notification_intents],
            [NotificationChannel.LOCAL, NotificationChannel.APNS],
        )
        for intent in plan.notification_intents:
            route = intent.route_contract()
            self.assertEqual(route["generation"], 7)
            self.assertEqual(route["resourceId"], "letter-001")
            self.assertNotIn("ownerSubjectId", route)

    def test_replay_is_deterministic_but_duplicate_channel_is_rejected(self):
        source = _source()
        first = build_business_completion_message_notification_plan(
            source,
            notification_channels=(NotificationChannel.LOCAL,),
            generation=2,
        )
        replay = build_business_completion_message_notification_plan(
            source,
            notification_channels=(NotificationChannel.LOCAL,),
            generation=2,
        )

        self.assertEqual(first.message.message_id, replay.message.message_id)
        self.assertEqual(
            first.notification_intents[0].notification_intent_id,
            replay.notification_intents[0].notification_intent_id,
        )
        with self.assertRaises(BusinessMessageNotificationContractError):
            build_business_completion_message_notification_plan(
                source,
                notification_channels=(NotificationChannel.LOCAL, NotificationChannel.LOCAL),
                generation=2,
            )

    def test_cross_account_message_has_distinct_resource_and_inbox_coordinates(self):
        source = _source(
            inbox_subject_id="family-subject-002",
            inbox_vault_id="family-vault-002",
        )
        plan = build_business_completion_message_notification_plan(
            source,
            notification_channels=(NotificationChannel.APNS,),
            generation=2,
        )
        message = plan.message
        recipient_subscription = _subscription(
            inbox_subject_id="family-subject-002",
            inbox_vault_id="family-vault-002",
            account_epoch=11,
        )
        binding = DeviceSubscriptionNotificationBinding(
            subscription=recipient_subscription,
            notification_intent=plan.notification_intents[0],
        )

        self.assertEqual(message.resource_owner_subject_id, "owner-001")
        self.assertEqual(message.resource_vault_id, "vault-001")
        self.assertEqual(message.inbox_subject_id, "family-subject-002")
        self.assertEqual(message.inbox_vault_id, "family-vault-002")
        self.assertTrue(binding.authorize_route(binding.route_contract()).allowed)
        route = binding.route_contract()
        self.assertNotIn("owner-001", str(route))
        self.assertNotIn("family-subject-002", str(route))
        with self.assertRaises(BusinessMessageNotificationContractError):
            DeviceSubscriptionNotificationBinding(
                subscription=_subscription(),
                notification_intent=plan.notification_intents[0],
            )

    def test_cross_account_message_identity_must_be_complete_and_changes_message_identity(self):
        base = _source()
        with self.assertRaises(BusinessMessageNotificationContractError):
            BusinessCompletionMessageSource(
                intent=base.intent,
                completion=base.completion,
                message_kind=InAppMessageKind.TIME_LETTER,
                inbox_subject_id="family-subject-002",
            )

        recipient = BusinessCompletionMessageSource(
            intent=base.intent,
            completion=base.completion,
            message_kind=InAppMessageKind.TIME_LETTER,
            inbox_subject_id="family-subject-002",
            inbox_vault_id="family-vault-002",
        )
        self.assertNotEqual(base.message_id, recipient.message_id)

    def test_delivery_failure_never_changes_completed_business_or_message_state(self):
        source = _source()
        plan = build_business_completion_message_notification_plan(
            source,
            notification_channels=(NotificationChannel.APNS,),
            generation=1,
        )
        failed = NotificationDeliveryReceipt(
            intent=plan.notification_intents[0],
            provider_attempt_id="apns-attempt-001",
            state=NotificationDeliveryState.FAILED,
            reason_code="providerUnavailable",
            observation_origin="apnsProvider",
        )

        delivery = NotificationIntentReconciliation(
            intent=plan.notification_intents[0],
            receipts=(failed,),
        )

        self.assertEqual(delivery.state, NotificationDeliveryState.FAILED)
        self.assertEqual(source.completion.business_outcome, "completed")
        self.assertEqual(plan.message.state, InAppMessageState.UNREAD)
        self.assertEqual(
            plan.value_free_summary()["businessOutcome"],
            "completed",
        )

    def test_conflicting_late_delivery_observations_fail_closed_to_unknown(self):
        source = _source()
        plan = build_business_completion_message_notification_plan(
            source,
            notification_channels=(NotificationChannel.LOCAL,),
            generation=9,
        )
        intent = plan.notification_intents[0]
        accepted = NotificationDeliveryReceipt(
            intent=intent,
            provider_attempt_id="local-attempt-001",
            state=NotificationDeliveryState.ACCEPTED,
            reason_code="scheduled",
            observation_origin="localScheduler",
        )
        failed = NotificationDeliveryReceipt(
            intent=intent,
            provider_attempt_id="local-attempt-002",
            state=NotificationDeliveryState.FAILED,
            reason_code="schedulerLost",
            observation_origin="localScheduler",
        )

        delivery = NotificationIntentReconciliation(intent=intent, receipts=(accepted, failed))

        self.assertEqual(delivery.state, NotificationDeliveryState.UNKNOWN)
        self.assertTrue(delivery.requires_manual_review)
        self.assertEqual(source.completion.business_outcome, "completed")

    def test_non_completed_or_cross_operation_receipts_cannot_create_messages(self):
        with self.assertRaises(BusinessMessageNotificationContractError):
            _source(outcome="failed")

        source = _source()
        other_intent = AsyncEffectIntent(
            operation_type="asyncEffect.synthetic.messageNotification.other",
            target=source.intent.target,
            payload_hash=sha256(b"other").hexdigest(),
        )
        with self.assertRaises(BusinessMessageNotificationContractError):
            BusinessCompletionMessageSource(
                intent=other_intent,
                completion=source.completion,
                message_kind=InAppMessageKind.TIME_LETTER,
            )

    def test_device_subscription_registers_hash_only_and_rotation_invalidates_old_route(self):
        subscription = _subscription()
        source = _source()
        plan = build_business_completion_message_notification_plan(
            source,
            notification_channels=(NotificationChannel.APNS,),
            generation=7,
        )
        old_binding = DeviceSubscriptionNotificationBinding(
            subscription=subscription,
            notification_intent=plan.notification_intents[0],
        )
        old_route = old_binding.route_contract()

        rotation = rotate_device_subscription(
            subscription,
            token_hash=sha256(b"token-two").hexdigest(),
        )
        rotated = rotation.subscription
        current_binding = DeviceSubscriptionNotificationBinding(
            subscription=rotated,
            notification_intent=plan.notification_intents[0],
        )

        self.assertTrue(rotation.changed)
        self.assertEqual(rotated.subscription_id, subscription.subscription_id)
        self.assertEqual(rotated.generation, subscription.generation + 1)
        self.assertEqual(
            current_binding.authorize_route(old_route).reason,
            NotificationRouteAuthorizationReason.SUBSCRIPTION_GENERATION_MISMATCH,
        )
        self.assertTrue(current_binding.authorize_route(current_binding.route_contract()).allowed)
        self.assertNotIn("token", str(rotation.value_free_summary()).lower())
        self.assertNotIn(subscription.token_hash, str(rotation.value_free_summary()))

        same_hash_replay = rotate_device_subscription(rotated, token_hash=rotated.token_hash)
        self.assertFalse(same_hash_replay.changed)
        self.assertEqual(same_hash_replay.subscription.generation, rotated.generation)

        with self.assertRaises(BusinessMessageNotificationContractError):
            register_device_subscription(
                inbox_subject_id="owner-001",
                inbox_vault_id="vault-001",
                installation_id="ios-installation-001",
                platform="ios",
                token_hash="raw-device-token-is-not-a-hash",
                account_epoch=3,
            )

    def test_device_subscription_revoke_prevents_notification_route_delivery(self):
        source = _source()
        plan = build_business_completion_message_notification_plan(
            source,
            notification_channels=(NotificationChannel.APNS,),
            generation=2,
        )
        revoked = revoke_device_subscription(_subscription()).subscription
        binding = DeviceSubscriptionNotificationBinding(
            subscription=revoked,
            notification_intent=plan.notification_intents[0],
        )

        authorization = binding.authorize_route(binding.route_contract())

        self.assertEqual(revoked.status, DeviceSubscriptionStatus.REVOKED)
        self.assertFalse(binding.is_deliverable)
        self.assertFalse(authorization.allowed)
        self.assertEqual(
            authorization.reason,
            NotificationRouteAuthorizationReason.SUBSCRIPTION_REVOKED,
        )
        self.assertFalse(revoke_device_subscription(revoked).changed)

    def test_notification_route_rejects_resource_inbox_and_generation_mismatches(self):
        source = _source()
        plan = build_business_completion_message_notification_plan(
            source,
            notification_channels=(NotificationChannel.APNS,),
            generation=5,
        )
        binding = DeviceSubscriptionNotificationBinding(
            subscription=_subscription(),
            notification_intent=plan.notification_intents[0],
        )
        route = dict(binding.route_contract())
        wrong_resource_owner_route = dict(route)
        wrong_resource_owner_route["resourceOwnerDigest"] = sha256(b"other-owner").hexdigest()
        wrong_inbox_route = dict(route)
        wrong_inbox_route["inboxSubjectDigest"] = sha256(b"other-inbox").hexdigest()
        wrong_resource_epoch = dict(route)
        wrong_resource_epoch["resourceAuthorityEpoch"] = 4
        wrong_subscription_epoch = dict(route)
        wrong_subscription_epoch["deviceSubscriptionAccountEpoch"] = 4
        wrong_notification_generation = dict(route)
        wrong_notification_generation["generation"] = 6
        wrong_subscription_generation = dict(route)
        wrong_subscription_generation["deviceSubscriptionGeneration"] = 1

        self.assertEqual(
            binding.authorize_route(wrong_resource_owner_route).reason,
            NotificationRouteAuthorizationReason.RESOURCE_OWNER_MISMATCH,
        )
        self.assertEqual(
            binding.authorize_route(wrong_inbox_route).reason,
            NotificationRouteAuthorizationReason.INBOX_SUBJECT_MISMATCH,
        )
        self.assertEqual(
            binding.authorize_route(wrong_resource_epoch).reason,
            NotificationRouteAuthorizationReason.RESOURCE_AUTHORITY_EPOCH_MISMATCH,
        )
        self.assertEqual(
            binding.authorize_route(wrong_subscription_epoch).reason,
            NotificationRouteAuthorizationReason.SUBSCRIPTION_ACCOUNT_EPOCH_MISMATCH,
        )
        self.assertEqual(
            binding.authorize_route(wrong_notification_generation).reason,
            NotificationRouteAuthorizationReason.NOTIFICATION_GENERATION_MISMATCH,
        )
        self.assertEqual(
            binding.authorize_route(wrong_subscription_generation).reason,
            NotificationRouteAuthorizationReason.SUBSCRIPTION_GENERATION_MISMATCH,
        )


if __name__ == "__main__":
    unittest.main()
