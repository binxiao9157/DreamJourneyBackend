import unittest

from app.services.apns_delivery import (
    APNSConfiguration,
    APNSDeliveryError,
    APNSDeliveryService,
    APNSProviderReceipt,
    EphemeralAPNSTokenVault,
    FakeAPNSProvider,
)
from app.core.config import Settings
from app.services.runtime_config import RuntimeConfigService


class APNSDeliveryTests(unittest.TestCase):
    def service(self, receipts=None, *, max_attempts=3):
        provider = FakeAPNSProvider(receipts)
        service = APNSDeliveryService(
            configuration=APNSConfiguration(
                provider="fake",
                token_vault_provider="ephemeral",
                topic="com.yxj.dreamjourney.app",
                environment="sandbox",
                max_attempts=max_attempts,
            ),
            token_vault=EphemeralAPNSTokenVault(),
            provider=provider,
        )
        return service, provider

    def test_register_enqueue_dispatch_and_receipt_are_value_minimized(self):
        service, provider = self.service()
        token = "a1" * 32
        registration = service.register(
            owner_user_id="owner-apns",
            installation_id="ios-installation-1",
            device_token=token,
            topic="com.yxj.dreamjourney.app",
            environment="sandbox",
        )
        self.assertNotIn(token, str(registration.public_contract()))
        job = service.enqueue(
            message_id="message-apns-1",
            registration=registration,
            payload={"aps": {"content-available": 1}, "route": "mailbox"},
        )
        replay = service.enqueue(
            message_id="message-apns-1",
            registration=registration,
            payload={"aps": {"content-available": 1}, "route": "mailbox"},
        )
        self.assertEqual(job.job_id, replay.job_id)
        delivered = service.dispatch(job.job_id)
        second = service.dispatch(job.job_id)
        self.assertEqual(delivered.state, "accepted")
        self.assertEqual(second, delivered)
        self.assertEqual(len(provider.calls), 1)
        self.assertTrue(delivered.public_contract()["providerReceiptPresent"])
        self.assertNotIn(token, str(delivered.public_contract()))

    def test_topic_and_environment_are_strictly_isolated(self):
        service, _provider = self.service()
        for topic, environment, code in (
            ("com.other.app", "sandbox", "apnsTopicMismatch"),
            ("com.yxj.dreamjourney.app", "production", "apnsEnvironmentMismatch"),
        ):
            with self.subTest(code=code):
                with self.assertRaises(APNSDeliveryError) as raised:
                    service.register(
                        owner_user_id="owner-apns",
                        installation_id="ios-installation-1",
                        device_token="a1" * 32,
                        topic=topic,
                        environment=environment,
                    )
                self.assertEqual(raised.exception.code, code)

    def test_retry_and_terminal_unknown_require_explicit_receipts(self):
        service, provider = self.service(
            [
                APNSProviderReceipt(
                    state="failed",
                    reason_code="fakeTransientFailure",
                    retryable=True,
                ),
                APNSProviderReceipt(
                    state="accepted",
                    reason_code="fakeProviderAccepted",
                    provider_receipt_id="provider-receipt-2",
                ),
            ]
        )
        registration = service.register(
            owner_user_id="owner-apns",
            installation_id="ios-installation-1",
            device_token="b2" * 32,
            topic="com.yxj.dreamjourney.app",
            environment="sandbox",
        )
        job = service.enqueue(
            message_id="message-apns-retry",
            registration=registration,
            payload={"aps": {"content-available": 1}},
        )
        failed = service.dispatch(job.job_id)
        self.assertTrue(failed.retryable)
        queued = service.retry(job.job_id)
        self.assertEqual(queued.state, "queued")
        accepted = service.dispatch(job.job_id)
        self.assertEqual(accepted.state, "accepted")
        self.assertEqual(accepted.attempt, 2)
        self.assertEqual(len(provider.calls), 2)

    def test_disabled_configuration_is_fail_closed(self):
        configuration = APNSConfiguration()
        self.assertFalse(configuration.public_descriptor()["enabled"])
        service = APNSDeliveryService(
            configuration=configuration,
            token_vault=EphemeralAPNSTokenVault(),
            provider=FakeAPNSProvider(),
        )
        with self.assertRaises(APNSDeliveryError) as raised:
            service.register(
                owner_user_id="owner-apns",
                installation_id="ios-installation-1",
                device_token="a1" * 32,
                topic="com.yxj.dreamjourney.app",
                environment="sandbox",
            )
        self.assertEqual(raised.exception.code, "apnsDeliveryDisabled")

    def test_runtime_config_is_secret_free_and_fail_closed(self):
        disabled = RuntimeConfigService(Settings()).public_config()
        self.assertFalse(disabled["capabilities"]["apnsDelivery"])
        self.assertEqual(disabled["notifications"]["apns"]["reason"], "apnsDisabled")

        qa = RuntimeConfigService(
            Settings(
                apns_delivery_provider="fake",
                apns_token_vault_provider="ephemeral",
                apns_topic="com.yxj.dreamjourney.app",
                apns_environment="sandbox",
            )
        ).public_config()
        descriptor = qa["notifications"]["apns"]
        self.assertTrue(qa["capabilities"]["apnsDelivery"])
        self.assertFalse(descriptor["realProviderReady"])
        self.assertNotIn("deviceToken", str(descriptor))

        invalid = RuntimeConfigService(
            Settings(
                apns_delivery_provider="fake",
                apns_token_vault_provider="disabled",
                apns_topic="com.yxj.dreamjourney.app",
            )
        ).public_config()["notifications"]["apns"]
        self.assertFalse(invalid["enabled"])
        self.assertEqual(invalid["reason"], "apnsTokenVaultRequired")


if __name__ == "__main__":
    unittest.main()
