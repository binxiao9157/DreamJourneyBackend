import unittest
from hashlib import sha256

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


def _intent() -> AsyncEffectIntent:
    return AsyncEffectIntent(
        operation_type="asyncEffect.synthetic.messageNotification.fixture",
        target=AsyncEffectTarget(
            owner_subject_id="owner-001",
            vault_id="vault-001",
            resource_type="timeLetter",
            resource_id="letter-001",
            resource_version=4,
            purpose="timeLetterDelivery",
            authority_epoch=3,
        ),
        payload_hash=sha256(b"value-free-message-notification-fixture").hexdigest(),
    )


def _source(*, outcome: str = "completed") -> BusinessCompletionMessageSource:
    intent = _intent()
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
    return BusinessCompletionMessageSource(
        intent=intent,
        completion=receipt,
        message_kind=InAppMessageKind.TIME_LETTER,
    )


def _subscription(*, token_seed: bytes = b"token-one"):
    return register_device_subscription(
        owner_subject_id="owner-001",
        vault_id="vault-001",
        installation_id="ios-installation-001",
        platform="ios",
        token_hash=sha256(token_seed).hexdigest(),
        authority_epoch=3,
    ).subscription


class BusinessMessageNotificationPlanTests(unittest.TestCase):
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
        self.assertNotIn("body", projection)
        self.assertNotIn("title", projection)
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
                owner_subject_id="owner-001",
                vault_id="vault-001",
                installation_id="ios-installation-001",
                platform="ios",
                token_hash="raw-device-token-is-not-a-hash",
                authority_epoch=3,
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

    def test_notification_route_rejects_owner_and_generation_mismatches(self):
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
        wrong_owner_route = dict(route)
        wrong_owner_route["ownerDigest"] = sha256(b"other-owner").hexdigest()
        wrong_notification_generation = dict(route)
        wrong_notification_generation["generation"] = 6
        wrong_subscription_generation = dict(route)
        wrong_subscription_generation["deviceSubscriptionGeneration"] = 1

        self.assertEqual(
            binding.authorize_route(wrong_owner_route).reason,
            NotificationRouteAuthorizationReason.OWNER_MISMATCH,
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
