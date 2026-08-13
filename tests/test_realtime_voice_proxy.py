import asyncio
import json
import unittest

from app.core.config import Settings
from app.services.auth_sessions import AuthSessionService
from app.services.in_memory_store import InMemoryStore
from app.services.realtime_voice_proxy import (
    RealtimeVoiceProxyError,
    RealtimeVoiceSessionBroker,
    RealtimeVoiceTrafficLimitExceeded,
    _RealtimeVoiceTrafficBudget,
)


class _ClientFrames:
    def __init__(self, frames):
        self.frames = list(frames)

    async def receive(self):
        return self.frames.pop(0)


class _UpstreamSink:
    def __init__(self):
        self.messages = []

    async def send(self, message):
        self.messages.append(message)


class RealtimeVoiceSessionBrokerTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(
            store_backend="memory",
            public_base_url="https://api.example.test/dreamjourney-api",
            volcengine_app_id="provider-app-id-secret",
            volcengine_app_key="provider-app-key-secret",
            volcengine_app_token="provider-access-token-secret",
            volcengine_realtime_resource_id="volc.speech.dialog",
            realtime_voice_proxy_enabled=True,
            realtime_voice_ticket_ttl_seconds=60,
            realtime_voice_max_session_seconds=600,
            realtime_voice_max_concurrent_sessions_per_user=1,
            realtime_voice_max_frame_bytes=32,
            realtime_voice_max_session_bytes=64,
        )
        self.store = InMemoryStore()
        self.user = self.store.upsert_user(
            phone="13800138888",
            nickname="Realtime Voice User",
        )
        self.auth_service = AuthSessionService(
            self.store,
            access_ttl_seconds=900,
            refresh_ttl_seconds=3600,
        )
        self.auth = self.auth_service.issue(self.user["id"])
        self.broker = RealtimeVoiceSessionBroker(self.settings, self.store)

    def issue(self):
        return self.broker.issue_runtime_config(
            user_id=self.user["id"],
            auth_session_id=self.auth["sessionId"],
        )

    def test_ready_contract_exposes_only_backend_proxy_ticket(self):
        config = self.issue()

        self.assertEqual(config["status"], "ready")
        self.assertEqual(config["credentialMode"], "oneTimeBackendProxyTicket")
        self.assertEqual(config["accessPath"], "backendRealtimeProxy")
        self.assertFalse(config["mobileDirectAllowed"])
        self.assertEqual(config["proxy"]["address"], "wss://api.example.test")
        self.assertEqual(
            config["proxy"]["uri"],
            "/dreamjourney-api/voice/realtime-stream",
        )
        self.assertTrue(config["proxy"]["sessionToken"].startswith("djv_"))
        serialized = json.dumps(config, sort_keys=True)
        for secret in (
            self.settings.volcengine_app_id,
            self.settings.volcengine_app_key,
            self.settings.volcengine_app_token,
            self.settings.volcengine_realtime_address,
        ):
            self.assertNotIn(str(secret), serialized)
        persisted = next(iter(self.store._realtime_voice_session_tickets.values()))
        self.assertNotIn(config["proxy"]["sessionToken"], str(persisted))
        self.assertEqual(len(persisted["ticketHash"]), 64)

    def test_ticket_is_single_use_and_released_explicitly(self):
        config = self.issue()
        ticket = config["proxy"]["sessionToken"]

        lease = self.broker.consume(ticket)
        self.assertIsNotNone(lease)
        self.assertIsNone(self.broker.consume(ticket))
        self.assertTrue(self.broker.is_lease_authorized(lease))
        self.broker.release(lease, reason="clientDisconnected")
        persisted = self.store._realtime_voice_session_tickets[lease["ticketId"]]
        self.assertEqual(persisted["status"], "released")

    def test_new_ticket_is_denied_while_user_has_active_session(self):
        first = self.issue()
        self.assertIsNotNone(self.broker.consume(first["proxy"]["sessionToken"]))

        with self.assertRaises(RealtimeVoiceProxyError) as raised:
            self.issue()

        self.assertEqual(raised.exception.code, "realtimeVoiceConcurrentSessionLimit")
        self.assertTrue(raised.exception.retryable)

    def test_logout_revokes_an_active_proxy_lease(self):
        config = self.issue()
        lease = self.broker.consume(config["proxy"]["sessionToken"])
        self.assertTrue(self.broker.is_lease_authorized(lease))

        self.auth_service.revoke_access_token(self.auth["accessToken"])

        self.assertFalse(self.broker.is_lease_authorized(lease))

    def test_refresh_preserves_lease_until_current_session_logs_out(self):
        config = self.issue()
        lease = self.broker.consume(config["proxy"]["sessionToken"])

        refreshed = self.auth_service.refresh(self.auth["refreshToken"])
        self.assertTrue(self.broker.is_lease_authorized(lease))

        self.auth_service.revoke_access_token(refreshed["accessToken"])
        self.assertFalse(self.broker.is_lease_authorized(lease))

    def test_client_frames_are_forwarded_opaquely_with_a_shared_budget(self):
        client = _ClientFrames(
            [
                {"type": "websocket.receive", "bytes": b"audio"},
                {"type": "websocket.receive", "text": "event"},
                {"type": "websocket.disconnect"},
            ]
        )
        upstream = _UpstreamSink()
        budget = _RealtimeVoiceTrafficBudget(max_frame_bytes=32, max_session_bytes=64)

        asyncio.run(self.broker._client_to_upstream(client, upstream, budget))

        self.assertEqual(upstream.messages, [b"audio", "event"])
        self.assertEqual(budget.consumed_bytes, 10)

    def test_oversized_frame_is_rejected_before_forwarding(self):
        client = _ClientFrames(
            [{"type": "websocket.receive", "bytes": b"x" * 33}]
        )
        upstream = _UpstreamSink()
        budget = _RealtimeVoiceTrafficBudget(max_frame_bytes=32, max_session_bytes=64)

        with self.assertRaises(RealtimeVoiceTrafficLimitExceeded):
            asyncio.run(self.broker._client_to_upstream(client, upstream, budget))

        self.assertEqual(upstream.messages, [])


class RealtimeVoiceBlockedContractTests(unittest.TestCase):
    def test_proxy_remains_fail_closed_until_explicitly_enabled(self):
        descriptor = RealtimeVoiceSessionBroker(
            Settings(
                public_base_url="https://api.example.test",
                volcengine_app_id="app",
                volcengine_app_key="key",
                volcengine_app_token="token",
                realtime_voice_proxy_enabled=False,
            ),
            store=None,
        ).capability_descriptor()

        self.assertEqual(descriptor["status"], "blocked")
        self.assertFalse(descriptor["providerReady"])
        self.assertFalse(descriptor["mobileDirectAllowed"])
        self.assertNotIn("proxy", descriptor)


if __name__ == "__main__":
    unittest.main()
