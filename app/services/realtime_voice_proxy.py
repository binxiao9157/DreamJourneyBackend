from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse, urlunparse

from app.core.config import Settings


class RealtimeVoiceProxyError(ValueError):
    def __init__(self, code: str, *, retryable: bool = False):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class RealtimeVoiceUpstreamUnavailable(RuntimeError):
    pass


class RealtimeVoiceAuthorizationRevoked(RuntimeError):
    pass


class RealtimeVoiceTrafficLimitExceeded(RuntimeError):
    pass


class _RealtimeVoiceTrafficBudget:
    def __init__(self, *, max_frame_bytes: int, max_session_bytes: int):
        self.max_frame_bytes = max(1, int(max_frame_bytes))
        self.max_session_bytes = max(self.max_frame_bytes, int(max_session_bytes))
        self.consumed_bytes = 0

    def consume(self, message: Any) -> None:
        size = len(message) if isinstance(message, bytes) else len(str(message).encode("utf-8"))
        if size > self.max_frame_bytes:
            raise RealtimeVoiceTrafficLimitExceeded("realtime voice frame limit exceeded")
        self.consumed_bytes += size
        if self.consumed_bytes > self.max_session_bytes:
            raise RealtimeVoiceTrafficLimitExceeded("realtime voice session limit exceeded")


class RealtimeVoiceSessionBroker:
    REQUIRED_PROPERTIES = ("scope", "ttl", "audience", "revocation")
    TICKET_HEADER = "X-DreamJourney-Voice-Session"

    def __init__(self, settings: Settings, store: Any):
        self.settings = settings
        self.store = store

    def capability_descriptor(self) -> Dict[str, Any]:
        reason = self._configuration_block_reason()
        if reason is not None:
            return self._blocked_descriptor(reason)
        return {
            "status": "ready",
            "capability": "realtimeVoice",
            "provider": "volcengineViaDreamJourneyProxy",
            "credentialMode": "oneTimeBackendProxyTicket",
            "accessPath": "backendRealtimeProxy",
            "mobileDirectAllowed": False,
            "brokerStatus": "verified",
            "providerReady": True,
            "releaseVisible": True,
            "retryable": True,
            "decisionReceipt": {
                "decision": "allowBackendRealtimeProxy",
                "reasonCode": "backendProxyContractVerified",
                "requiredProperties": list(self.REQUIRED_PROPERTIES),
                "verifiedProperties": list(self.REQUIRED_PROPERTIES),
                "missingProperties": [],
                "evidenceVersion": "dreamjourney-realtime-voice-proxy-v1",
            },
            "fallback": {"enabled": True, "mode": "text"},
            "contractVersion": 4,
        }

    def issue_runtime_config(self, *, user_id: str, auth_session_id: str) -> Dict[str, Any]:
        descriptor = self.capability_descriptor()
        if descriptor["status"] != "ready":
            return descriptor
        if not str(user_id or "").strip() or not str(auth_session_id or "").strip():
            raise RealtimeVoiceProxyError("authenticatedVoiceSessionRequired")

        now = datetime.now(timezone.utc)
        ticket_ttl = max(15, min(int(self.settings.realtime_voice_ticket_ttl_seconds), 300))
        expires_at = now + timedelta(seconds=ticket_ttl)
        raw_ticket = "djv_" + secrets.token_urlsafe(32)
        ticket_hash = self.ticket_hash(raw_ticket)
        ticket_id = "rvs_" + secrets.token_hex(16)
        record = {
            "ticketId": ticket_id,
            "ticketHash": ticket_hash,
            "userId": str(user_id),
            "authSessionId": str(auth_session_id),
            "status": "issued",
            "scope": "realtimeVoice:connect",
            "audience": "dreamjourney-realtime-voice-proxy",
            "expiresAt": expires_at.isoformat(),
            "createdAt": now.isoformat(),
            "updatedAt": now.isoformat(),
            "contractVersion": 1,
        }
        try:
            self.store.issue_realtime_voice_session_ticket(
                record,
                max_concurrent_sessions=max(
                    1,
                    int(self.settings.realtime_voice_max_concurrent_sessions_per_user),
                ),
                now_iso=now.isoformat(),
            )
        except ValueError as exc:
            if "concurrent session limit" in str(exc):
                raise RealtimeVoiceProxyError(
                    "realtimeVoiceConcurrentSessionLimit",
                    retryable=True,
                ) from exc
            raise

        address, uri = self._public_proxy_endpoint()
        response = dict(descriptor)
        response.update(
            {
                "expiresAt": expires_at.isoformat(),
                "expiresInSeconds": ticket_ttl,
                "proxy": {
                    "address": address,
                    "uri": uri,
                    "sessionToken": raw_ticket,
                    "sessionHeader": self.TICKET_HEADER,
                    "sdkClientID": "dreamjourney-realtime-proxy",
                    "sdkClientKey": "proxy-session",
                    "sdkResourceID": "dreamjourney.realtime.proxy",
                    "uid": "dju_" + hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:24],
                },
            }
        )
        return response

    def consume(self, raw_ticket: str) -> Optional[Dict[str, Any]]:
        if self.capability_descriptor()["status"] != "ready":
            return None
        normalized = str(raw_ticket or "").strip()
        if not normalized.startswith("djv_") or len(normalized) < 32:
            return None
        now = datetime.now(timezone.utc)
        max_seconds = max(
            60,
            min(int(self.settings.realtime_voice_max_session_seconds), 4 * 60 * 60),
        )
        return self.store.consume_realtime_voice_session_ticket(
            self.ticket_hash(normalized),
            now_iso=now.isoformat(),
            session_expires_at_iso=(now + timedelta(seconds=max_seconds)).isoformat(),
        )

    def release(self, lease: Dict[str, Any], *, reason: str) -> None:
        ticket_id = str(lease.get("ticketId") or "")
        if not ticket_id:
            return
        self.store.release_realtime_voice_session_ticket(
            ticket_id,
            released_at_iso=datetime.now(timezone.utc).isoformat(),
            reason=reason,
        )

    def is_lease_authorized(self, lease: Dict[str, Any]) -> bool:
        if self.capability_descriptor()["status"] != "ready":
            return False
        expires_at = self._parse_datetime(str(lease.get("expiresAt") or ""))
        if expires_at is None or expires_at <= datetime.now(timezone.utc):
            return False
        return bool(
            self.store.is_realtime_voice_auth_session_active(
                str(lease.get("authSessionId") or ""),
                str(lease.get("userId") or ""),
            )
        )

    def upstream_url(self) -> str:
        address = str(self.settings.volcengine_realtime_address or "").strip().rstrip("/")
        uri = "/" + str(self.settings.volcengine_realtime_uri or "").strip().lstrip("/")
        parsed = urlparse(address)
        if parsed.scheme != "wss" or not parsed.netloc or parsed.username or parsed.password:
            raise RealtimeVoiceProxyError("realtimeVoiceUpstreamURLInvalid")
        if parsed.query or parsed.fragment:
            raise RealtimeVoiceProxyError("realtimeVoiceUpstreamURLInvalid")
        return address + uri

    def upstream_headers(self) -> Dict[str, str]:
        return {
            "X-Api-App-ID": str(self.settings.volcengine_app_id or ""),
            "X-Api-Access-Key": str(self.settings.volcengine_app_token or ""),
            "X-Api-Resource-Id": str(self.settings.volcengine_realtime_resource_id or ""),
            "X-Api-App-Key": str(self.settings.volcengine_app_key or ""),
            "X-Api-Connect-Id": secrets.token_hex(16),
        }

    async def relay(
        self,
        client: Any,
        lease: Dict[str, Any],
        *,
        connector: Optional[Callable[..., Any]] = None,
    ) -> None:
        if connector is None:
            try:
                from websockets.asyncio.client import connect as connector
            except ImportError as exc:  # pragma: no cover
                raise RealtimeVoiceUpstreamUnavailable("websocket client unavailable") from exc
        try:
            traffic_budget = _RealtimeVoiceTrafficBudget(
                max_frame_bytes=self.settings.realtime_voice_max_frame_bytes,
                max_session_bytes=self.settings.realtime_voice_max_session_bytes,
            )
            upstream_context = connector(
                self.upstream_url(),
                additional_headers=self.upstream_headers(),
                open_timeout=max(
                    1.0,
                    float(self.settings.realtime_voice_upstream_connect_timeout_seconds),
                ),
                ping_interval=20,
                ping_timeout=20,
                close_timeout=5,
                max_size=max(1, int(self.settings.realtime_voice_max_frame_bytes)),
                compression=None,
            )
            async with upstream_context as upstream:
                tasks = {
                    asyncio.create_task(
                        self._client_to_upstream(client, upstream, traffic_budget)
                    ),
                    asyncio.create_task(
                        self._upstream_to_client(upstream, client, traffic_budget)
                    ),
                    asyncio.create_task(self._watch_authorization(lease)),
                }
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                for task in done:
                    error = task.exception()
                    if error is not None:
                        raise error
        except RealtimeVoiceAuthorizationRevoked:
            raise
        except RealtimeVoiceTrafficLimitExceeded:
            raise
        except Exception as exc:
            raise RealtimeVoiceUpstreamUnavailable("realtime voice upstream unavailable") from exc

    async def _client_to_upstream(
        self,
        client: Any,
        upstream: Any,
        traffic_budget: _RealtimeVoiceTrafficBudget,
    ) -> None:
        while True:
            message = await client.receive()
            message_type = str(message.get("type") or "")
            if message_type == "websocket.disconnect":
                return
            if message.get("bytes") is not None:
                traffic_budget.consume(message["bytes"])
                await upstream.send(message["bytes"])
            elif message.get("text") is not None:
                traffic_budget.consume(message["text"])
                await upstream.send(message["text"])

    @staticmethod
    async def _upstream_to_client(
        upstream: Any,
        client: Any,
        traffic_budget: _RealtimeVoiceTrafficBudget,
    ) -> None:
        async for message in upstream:
            traffic_budget.consume(message)
            if isinstance(message, bytes):
                await client.send_bytes(message)
            else:
                await client.send_text(str(message))

    async def _watch_authorization(self, lease: Dict[str, Any]) -> None:
        interval = max(2.0, float(self.settings.realtime_voice_auth_recheck_seconds))
        while True:
            await asyncio.sleep(interval)
            authorized = await asyncio.to_thread(self.is_lease_authorized, lease)
            if not authorized:
                raise RealtimeVoiceAuthorizationRevoked("realtime voice authorization revoked")

    def _configuration_block_reason(self) -> Optional[str]:
        if not self.settings.realtime_voice_proxy_enabled:
            return "backendRealtimeProxyDisabled"
        if not all(
            str(value or "").strip()
            for value in (
                self.settings.volcengine_app_id,
                self.settings.volcengine_app_key,
                self.settings.volcengine_app_token,
                self.settings.volcengine_realtime_resource_id,
            )
        ):
            return "providerConfigurationIncomplete"
        try:
            self._public_proxy_endpoint()
            self.upstream_url()
        except RealtimeVoiceProxyError:
            return "realtimeVoiceProxyURLInvalid"
        return None

    def _blocked_descriptor(self, reason: str) -> Dict[str, Any]:
        return {
            "status": "blocked",
            "capability": "realtimeVoice",
            "provider": "volcengine",
            "credentialMode": "blockedStaticCredential",
            "accessPath": "backendProxyOrText",
            "mobileDirectAllowed": False,
            "brokerStatus": "providerContractNotVerified",
            "providerReady": False,
            "releaseVisible": False,
            "retryable": False,
            "decisionReceipt": {
                "decision": "keepDirectMobileClosed",
                "reasonCode": (
                    "scopedSessionCredentialContractNotVerified"
                    if reason == "backendRealtimeProxyDisabled"
                    else reason
                ),
                "requiredProperties": list(self.REQUIRED_PROPERTIES),
                "verifiedProperties": [],
                "missingProperties": list(self.REQUIRED_PROPERTIES),
                "evidenceVersion": "dreamjourney-realtime-voice-proxy-v1",
            },
            "fallback": {"enabled": True, "mode": "backendProxyOrText"},
            "contractVersion": 4,
        }

    def _public_proxy_endpoint(self) -> tuple[str, str]:
        parsed = urlparse(str(self.settings.public_base_url or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RealtimeVoiceProxyError("realtimeVoicePublicURLInvalid")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise RealtimeVoiceProxyError("realtimeVoicePublicURLInvalid")
        websocket_scheme = "wss" if parsed.scheme == "https" else "ws"
        address = urlunparse((websocket_scheme, parsed.netloc, "", "", "", ""))
        base_path = parsed.path.rstrip("/")
        return address, f"{base_path}/voice/realtime-stream"

    @staticmethod
    def ticket_hash(raw_ticket: str) -> str:
        return hashlib.sha256(raw_ticket.encode("utf-8")).hexdigest()

    @staticmethod
    def _parse_datetime(value: str) -> Optional[datetime]:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
