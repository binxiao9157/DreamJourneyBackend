import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
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


class _ProjectionReader:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def read(self, *, context):
        del context
        return dict(self.snapshot)


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


class _UpstreamFrames:
    def __init__(self, frames):
        self.frames = iter(frames)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.frames)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _ClientSink:
    def __init__(self):
        self.binary_messages = []
        self.text_messages = []

    async def send_bytes(self, message):
        self.binary_messages.append(message)

    async def send_text(self, message):
        self.text_messages.append(message)


class _RepeatingClientFrames:
    def __init__(self, frame, count):
        self.frame = frame
        self.remaining = count

    async def receive(self):
        if self.remaining <= 0:
            return {"type": "websocket.disconnect"}
        self.remaining -= 1
        return {"type": "websocket.receive", "bytes": self.frame}


class _CountingUpstreamSink:
    def __init__(self):
        self.message_count = 0
        self.byte_count = 0

    async def send(self, message):
        self.message_count += 1
        self.byte_count += len(message if isinstance(message, bytes) else message.encode("utf-8"))


class _RepeatingUpstreamFrames:
    def __init__(self, frame, count):
        self.frame = frame
        self.remaining = count

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.remaining <= 0:
            raise StopAsyncIteration
        self.remaining -= 1
        return self.frame


class _CountingClientSink:
    def __init__(self):
        self.binary_count = 0
        self.binary_bytes = 0

    async def send_bytes(self, message):
        self.binary_count += 1
        self.binary_bytes += len(message)

    async def send_text(self, message):
        raise AssertionError(f"unexpected text frame: {message!r}")


class _MutableClock:
    def __init__(self, current):
        self.current = current

    def __call__(self):
        return self.current


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

    def test_ticket_binds_live_target_and_formal_memory_checkpoint(self):
        self.store._owner_truth_vaults[self.user["id"]] = {
            "ownerSubjectId": self.user["id"],
            "authorityEpoch": 7,
            "status": "active",
        }
        self.store.owner_truth_memory_projection_repository = lambda: _ProjectionReader(
            {
                "state": "ready",
                "rightsState": "active",
                "authorityEpoch": 7,
                "checkpoint": "checkpoint-7",
                "memoryRevision": 12,
            }
        )
        provider_role = "server role with formal facts"
        provider_hash = "sha256:" + hashlib.sha256(provider_role.encode()).hexdigest()
        config = self.broker.issue_runtime_config(
            user_id=self.user["id"],
            auth_session_id=self.auth["sessionId"],
            purpose="echoLive",
            persona_scope="personal",
            target_persona_id=self.user["id"],
            product_session_id="echo_live_product_001",
            projection_checkpoint="checkpoint-7",
            context_hash="sha256:context",
            authority_epoch=7,
            memory_revision=12,
            session_context={
                "systemRole": "role",
                "providerRoleText": provider_role,
                "providerContextHash": provider_hash,
                "formalMemorySnapshot": {
                    "projectionCheckpoint": "checkpoint-7",
                    "contextHash": "sha256:context",
                    "memoryRevision": 12,
                    "providerRoleText": provider_role,
                    "providerContextHash": provider_hash,
                },
            },
        )

        self.assertEqual(config["contractVersion"], 7)
        self.assertEqual(config["echoSession"]["productSessionId"], "echo_live_product_001")
        self.assertEqual(config["echoSession"]["projectionCheckpoint"], "checkpoint-7")
        lease = self.broker.consume(config["proxy"]["sessionToken"])
        self.assertEqual(lease["purpose"], "echoLive")
        self.assertEqual(lease["targetPersonaId"], self.user["id"])
        self.assertEqual(lease["projectionCheckpoint"], "checkpoint-7")
        self.assertEqual(lease["authorityEpoch"], 7)
        self.assertEqual(lease["memoryRevision"], 12)

    def test_formal_memory_revision_change_revokes_an_active_bound_live_lease(self):
        self.store._owner_truth_vaults[self.user["id"]] = {
            "ownerSubjectId": self.user["id"],
            "authorityEpoch": 7,
            "status": "active",
        }
        reader = _ProjectionReader(
            {
                "state": "ready",
                "rightsState": "active",
                "authorityEpoch": 7,
                "checkpoint": "checkpoint-current",
                "memoryRevision": 12,
            }
        )
        self.store.owner_truth_memory_projection_repository = lambda: reader
        provider_role = "server role with current formal facts"
        provider_hash = "sha256:" + hashlib.sha256(provider_role.encode()).hexdigest()
        config = self.broker.issue_runtime_config(
            user_id=self.user["id"],
            auth_session_id=self.auth["sessionId"],
            purpose="echoLive",
            target_persona_id=self.user["id"],
            product_session_id="echo_live_memory_revision_001",
            projection_checkpoint="checkpoint-current",
            context_hash="sha256:current-context",
            authority_epoch=7,
            memory_revision=12,
            session_context={
                "providerRoleText": provider_role,
                "providerContextHash": provider_hash,
                "formalMemorySnapshot": {
                    "projectionCheckpoint": "checkpoint-current",
                    "contextHash": "sha256:current-context",
                    "memoryRevision": 12,
                    "providerRoleText": provider_role,
                    "providerContextHash": provider_hash,
                }
            },
        )
        lease = self.broker.consume(config["proxy"]["sessionToken"])
        self.assertIsNotNone(lease)
        self.assertTrue(self.broker.is_lease_authorized(lease))

        reader.snapshot["memoryRevision"] = 13
        reader.snapshot["checkpoint"] = "checkpoint-new"

        self.assertFalse(self.broker.is_lease_authorized(lease))

    def test_live_ticket_rejects_a_snapshot_that_does_not_match_its_binding(self):
        with self.assertRaises(RealtimeVoiceProxyError) as raised:
            self.broker.issue_runtime_config(
                user_id=self.user["id"],
                auth_session_id=self.auth["sessionId"],
                projection_checkpoint="checkpoint-7",
                context_hash="sha256:expected",
                memory_revision=12,
                session_context={
                    "formalMemorySnapshot": {
                        "projectionCheckpoint": "checkpoint-other",
                        "contextHash": "sha256:other",
                        "memoryRevision": 12,
                    }
                },
            )
        self.assertEqual(raised.exception.code, "realtimeVoiceFormalMemoryBindingInvalid")

    def test_client_session_id_reuses_product_session_across_provider_tickets(self):
        first = self.broker.issue_runtime_config(
            user_id=self.user["id"],
            auth_session_id=self.auth["sessionId"],
            product_session_id="echo_live_stable_001",
            client_session_id="echo_live_stable_001",
        )
        first_lease = self.broker.consume(first["proxy"]["sessionToken"])
        self.broker.release(first_lease, reason="providerReconnect")

        second = self.broker.issue_runtime_config(
            user_id=self.user["id"],
            auth_session_id=self.auth["sessionId"],
            product_session_id="echo_live_stable_001",
            client_session_id="echo_live_stable_001",
        )

        self.assertEqual(
            second["echoSession"]["productSessionId"],
            first["echoSession"]["productSessionId"],
        )
        persisted = next(
            item
            for item in self.store._realtime_voice_session_tickets.values()
            if item["ticketId"] != first_lease["ticketId"]
        )
        self.assertEqual(persisted["clientSessionId"], "echo_live_stable_001")

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

    def test_long_memory_live_profile_is_server_selected_and_pinned_to_lease(self):
        object.__setattr__(self.settings, "owner_truth_live_long_memory_pipeline_enabled", True)
        object.__setattr__(self.settings, "realtime_voice_long_live_profile_enabled", True)
        object.__setattr__(self.settings, "realtime_voice_long_live_max_session_seconds", 7200)
        object.__setattr__(self.settings, "realtime_voice_long_live_max_session_bytes", 1024 * 1024 * 1024)

        config = self.broker.issue_runtime_config(
            user_id=self.user["id"],
            auth_session_id=self.auth["sessionId"],
            purpose="echoLive",
            product_session_id="echo_live_long_profile_001",
        )
        persisted = next(iter(self.store._realtime_voice_session_tickets.values()))

        self.assertEqual(persisted["resourceProfile"], "echoLiveLongMemoryV1")
        self.assertEqual(persisted["maxSessionSeconds"], 7200)
        self.assertEqual(persisted["maxSessionBytes"], 1024 * 1024 * 1024)
        lease = self.broker.consume(config["proxy"]["sessionToken"])
        self.assertEqual(lease["resourceProfile"], "echoLiveLongMemoryV1")
        self.assertEqual(lease["maxSessionSeconds"], 7200)
        self.assertEqual(lease["maxSessionBytes"], 1024 * 1024 * 1024)

    def test_default_profile_is_unchanged_when_long_live_profile_is_disabled(self):
        object.__setattr__(self.settings, "owner_truth_live_long_memory_pipeline_enabled", True)
        object.__setattr__(self.settings, "realtime_voice_long_live_profile_enabled", False)

        config = self.broker.issue_runtime_config(
            user_id=self.user["id"],
            auth_session_id=self.auth["sessionId"],
            purpose="echoLive",
            product_session_id="echo_live_default_profile_001",
        )
        persisted = next(iter(self.store._realtime_voice_session_tickets.values()))

        self.assertEqual(persisted["resourceProfile"], "default")
        self.assertEqual(persisted["maxSessionSeconds"], 600)
        self.assertEqual(persisted["maxSessionBytes"], 64)
        lease = self.broker.consume(config["proxy"]["sessionToken"])
        self.assertEqual(lease["resourceProfile"], "default")
        self.assertEqual(lease["maxSessionSeconds"], 600)
        self.assertEqual(lease["maxSessionBytes"], 64)

    def test_long_live_lease_remains_authorized_until_pinned_7200_second_deadline(self):
        started_at = datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc)
        clock = _MutableClock(started_at)
        broker = RealtimeVoiceSessionBroker(self.settings, self.store, now=clock)
        object.__setattr__(self.settings, "owner_truth_live_long_memory_pipeline_enabled", True)
        object.__setattr__(self.settings, "realtime_voice_long_live_profile_enabled", True)
        object.__setattr__(self.settings, "realtime_voice_long_live_max_session_seconds", 7200)
        object.__setattr__(self.settings, "realtime_voice_long_live_max_session_bytes", 1024 * 1024 * 1024)

        config = broker.issue_runtime_config(
            user_id=self.user["id"],
            auth_session_id=self.auth["sessionId"],
            purpose="echoLive",
            product_session_id="echo_live_long_clock_001",
        )
        lease = broker.consume(config["proxy"]["sessionToken"])

        self.assertEqual(lease["productSessionId"], "echo_live_long_clock_001")
        self.assertEqual(
            lease["expiresAt"],
            (started_at + timedelta(seconds=7200)).isoformat(),
        )
        for elapsed in (3599, 3601, 3900):
            clock.current = started_at + timedelta(seconds=elapsed)
            self.assertTrue(broker.is_lease_authorized(lease), elapsed)
        clock.current = started_at + timedelta(seconds=7201)
        self.assertFalse(broker.is_lease_authorized(lease))

    def test_client_cannot_select_long_live_resource_profile(self):
        object.__setattr__(self.settings, "owner_truth_live_long_memory_pipeline_enabled", True)
        object.__setattr__(self.settings, "realtime_voice_long_live_profile_enabled", False)

        config = self.broker.issue_runtime_config(
            user_id=self.user["id"],
            auth_session_id=self.auth["sessionId"],
            purpose="echoLive",
            product_session_id="echo_live_spoofed_profile_001",
            session_context={
                "resourceProfile": "echoLiveLongMemoryV1",
                "maxSessionSeconds": 14_400,
                "maxSessionBytes": 2 * 1024 * 1024 * 1024,
            },
        )
        persisted = next(iter(self.store._realtime_voice_session_tickets.values()))
        lease = self.broker.consume(config["proxy"]["sessionToken"])

        self.assertEqual(persisted["resourceProfile"], "default")
        self.assertEqual(persisted["maxSessionSeconds"], 600)
        self.assertEqual(persisted["maxSessionBytes"], 64)
        self.assertEqual(lease["resourceProfile"], "default")
        self.assertEqual(lease["maxSessionSeconds"], 600)
        self.assertEqual(lease["maxSessionBytes"], 64)

    def test_long_live_traffic_budget_accepts_one_gibibyte_then_rejects_next_byte(self):
        budget = _RealtimeVoiceTrafficBudget(
            max_frame_bytes=2 * 1024 * 1024,
            max_session_bytes=1024 * 1024 * 1024,
        )
        encoded_audio_frame = b"a" * (1024 * 1024)

        for _ in range(1024):
            budget.consume(encoded_audio_frame)
        self.assertEqual(budget.consumed_bytes, 1024 * 1024 * 1024)
        with self.assertRaises(RealtimeVoiceTrafficLimitExceeded):
            budget.consume(b"a")

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

    def test_authority_epoch_change_revokes_bound_live_lease(self):
        self.store._owner_truth_vaults[self.user["id"]] = {
            "ownerSubjectId": self.user["id"],
            "authorityEpoch": 7,
            "status": "active",
        }
        config = self.broker.issue_runtime_config(
            user_id=self.user["id"],
            auth_session_id=self.auth["sessionId"],
            target_persona_id=self.user["id"],
            product_session_id="echo_live_authority_001",
            authority_epoch=7,
        )
        lease = self.broker.consume(config["proxy"]["sessionToken"])
        self.assertTrue(self.broker.is_lease_authorized(lease))

        self.store._owner_truth_vaults[self.user["id"]]["authorityEpoch"] = 8

        self.assertFalse(self.broker.is_lease_authorized(lease))

    def test_authority_epoch_change_before_connect_rejects_ticket(self):
        self.store._owner_truth_vaults[self.user["id"]] = {
            "ownerSubjectId": self.user["id"],
            "authorityEpoch": 7,
            "status": "active",
        }
        config = self.broker.issue_runtime_config(
            user_id=self.user["id"],
            auth_session_id=self.auth["sessionId"],
            target_persona_id=self.user["id"],
            product_session_id="echo_live_authority_002",
            authority_epoch=7,
        )
        self.store._owner_truth_vaults[self.user["id"]]["authorityEpoch"] = 8

        self.assertIsNone(self.broker.consume(config["proxy"]["sessionToken"]))

    def test_refresh_preserves_lease_until_current_session_logs_out(self):
        config = self.issue()
        lease = self.broker.consume(config["proxy"]["sessionToken"])

        refreshed = self.auth_service.refresh(self.auth["refreshToken"])
        self.assertTrue(self.broker.is_lease_authorized(lease))

        self.auth_service.revoke_access_token(refreshed["accessToken"])
        self.assertFalse(self.broker.is_lease_authorized(lease))

    def test_typed_subject_does_not_require_a_legacy_user_projection(self):
        subject_id = "sub_typed_realtime_voice_owner"
        self.store._subjects[subject_id] = {
            "subjectId": subject_id,
            "status": "active",
        }
        auth = self.auth_service.issue(subject_id)

        config = self.broker.issue_runtime_config(
            user_id=subject_id,
            auth_session_id=auth["sessionId"],
        )
        lease = self.broker.consume(config["proxy"]["sessionToken"])

        self.assertIsNotNone(lease)
        self.assertTrue(self.broker.is_lease_authorized(lease))
        self.assertNotIn(subject_id, self.store._users)

    def test_suspended_typed_subject_is_denied_and_revokes_active_lease(self):
        subject_id = "sub_suspended_realtime_voice_owner"
        self.store._subjects[subject_id] = {
            "subjectId": subject_id,
            "status": "active",
        }
        auth = self.auth_service.issue(subject_id)
        config = self.broker.issue_runtime_config(
            user_id=subject_id,
            auth_session_id=auth["sessionId"],
        )
        lease = self.broker.consume(config["proxy"]["sessionToken"])
        self.store._subjects[subject_id]["status"] = "suspended"

        self.assertFalse(self.broker.is_lease_authorized(lease))
        with self.assertRaises(RealtimeVoiceProxyError) as raised:
            self.broker.issue_runtime_config(
                user_id=subject_id,
                auth_session_id=auth["sessionId"],
            )
        self.assertEqual(raised.exception.code, "realtimeVoiceSubjectUnavailable")

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

    def test_bidirectional_encoded_frames_share_the_same_session_budget(self):
        # A representative PCM frame is transported as encoded bytes; the
        # proxy must account for the serialized wire payload in both directions.
        encoded_audio = bytes(range(24))
        client = _ClientFrames(
            [
                {"type": "websocket.receive", "bytes": encoded_audio},
                {"type": "websocket.disconnect"},
            ]
        )
        upstream_sink = _UpstreamSink()
        client_sink = _ClientSink()
        upstream_frames = _UpstreamFrames([encoded_audio, '{"type":"done"}'])
        budget = _RealtimeVoiceTrafficBudget(max_frame_bytes=32, max_session_bytes=96)

        async def relay():
            await self.broker._client_to_upstream(client, upstream_sink, budget)
            await self.broker._upstream_to_client(
                upstream_frames,
                client_sink,
                budget,
            )

        asyncio.run(relay())

        self.assertEqual(upstream_sink.messages, [encoded_audio])
        self.assertEqual(client_sink.binary_messages, [encoded_audio])
        self.assertEqual(client_sink.text_messages, ['{"type":"done"}'])
        self.assertEqual(
            budget.consumed_bytes,
            len(encoded_audio) * 2 + len('{"type":"done"}'.encode("utf-8")),
        )

    def test_long_live_3900_second_pcm_relay_stays_within_one_gibibyte(self):
        seconds = 3_900
        frames_per_second = 50
        frame_count = seconds * frames_per_second
        pcm_bytes_per_frame = 16_000 * 2 // frames_per_second
        provider_envelope_bytes = 16
        encoded_frame = b"p" * (pcm_bytes_per_frame + provider_envelope_bytes)
        client = _RepeatingClientFrames(encoded_frame, frame_count)
        upstream_sink = _CountingUpstreamSink()
        upstream_frames = _RepeatingUpstreamFrames(encoded_frame, frame_count)
        client_sink = _CountingClientSink()
        one_gibibyte = 1024 * 1024 * 1024
        budget = _RealtimeVoiceTrafficBudget(
            max_frame_bytes=2 * 1024 * 1024,
            max_session_bytes=one_gibibyte,
        )

        async def relay():
            await self.broker._client_to_upstream(client, upstream_sink, budget)
            await self.broker._upstream_to_client(upstream_frames, client_sink, budget)

        asyncio.run(relay())

        expected_payload_bytes = len(encoded_frame) * frame_count * 2
        # RFC 6455: 640+16 byte binary frames use a 2-byte extended length;
        # client frames add a 4-byte mask, server frames do not.
        websocket_wire_bytes = (
            (len(encoded_frame) + 8) * frame_count
            + (len(encoded_frame) + 4) * frame_count
        )
        self.assertEqual(pcm_bytes_per_frame, 640)
        self.assertEqual(upstream_sink.message_count, frame_count)
        self.assertEqual(client_sink.binary_count, frame_count)
        self.assertEqual(budget.consumed_bytes, expected_payload_bytes)
        self.assertEqual(upstream_sink.byte_count + client_sink.binary_bytes, expected_payload_bytes)
        self.assertEqual(expected_payload_bytes, 255_840_000)
        self.assertEqual(websocket_wire_bytes, 258_180_000)
        self.assertLess(websocket_wire_bytes, one_gibibyte)

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
