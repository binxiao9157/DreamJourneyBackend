"""Default-off G0 binding observer for future generated Voice audio.

The legacy ``/voice/synthesis`` route remains a compatibility path.  This
module establishes the V4 vocabulary needed before a future GeneratedAudio
store, provider receipt ledger, and cache promotion lane can exist.  It only
handles opaque identifiers, hashes, format metadata, and timestamps.  It
never receives text or audio bytes, calls a provider, writes a cache, or
persists a GeneratedAudio object.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import json
import re
from threading import RLock
from typing import Any
from uuid import UUID

from app.services.voice_dh_authority import (
    VoiceDHAuthorityContext,
    VoiceDHProvider,
    VoiceDHPurpose,
    VoiceProfileVersionAuthorityRecord,
)


VOICE_GENERATED_AUDIO_BINDING_SHADOW_SCHEMA_VERSION = "voice-generated-audio-binding-shadow-v1"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_HASH_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_ALLOWED_PURPOSES = frozenset(
    {
        VoiceDHPurpose.PREVIEW,
        VoiceDHPurpose.PRIVATE_SYNTHESIS,
        VoiceDHPurpose.MEMOIR,
        VoiceDHPurpose.DH_AUDIO_DRIVE,
    }
)


class VoiceGeneratedAudioBindingError(ValueError):
    """Raised when a value-minimized future GeneratedAudio binding is invalid."""


class VoiceGeneratedAudioBindingDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    INVALID_CONTEXT = "invalid_context"
    BLOCKED = "blocked"


class VoiceGeneratedAudioOutputMode(str, Enum):
    DEFAULT = "default"
    TENCENT_AUDIO_DRIVE = "tencentAudioDrive"


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise VoiceGeneratedAudioBindingError(f"{field} must be an opaque identifier")
    return normalized


def _hash(value: object, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _HASH_PATTERN.fullmatch(normalized):
        raise VoiceGeneratedAudioBindingError(f"{field} must be a lowercase SHA-256 digest")
    return normalized


def _utc(value: object, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise VoiceGeneratedAudioBindingError(f"{field} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _positive_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise VoiceGeneratedAudioBindingError(f"{field} must be a positive integer")
    return value


def _canonical_json(value: dict[str, object]) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise VoiceGeneratedAudioBindingError("binding material must be serializable") from error


@dataclass(frozen=True)
class VoiceGeneratedAudioBindingCommand:
    """Opaque, stable request metadata for a future generated audio object.

    ``source_binding_hash`` identifies the answer/memoir source version and
    ``text_hash`` identifies the exact rendered text.  Raw text, audio, URLs,
    provider IDs, credentials, and receipt values are intentionally absent.
    """

    command_id: str
    profile_version_id: str
    profile_id: str
    profile_version: int
    purpose: VoiceDHPurpose
    provider: VoiceDHProvider
    policy_version: str
    source_binding_hash: str
    text_hash: str
    request_hash: str
    output_mode: VoiceGeneratedAudioOutputMode
    audio_format: str
    sample_rate: int
    channel_count: int
    issued_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", _identifier(self.command_id, field="command_id"))
        try:
            object.__setattr__(self, "profile_version_id", str(UUID(str(self.profile_version_id))))
        except (TypeError, ValueError) as error:
            raise VoiceGeneratedAudioBindingError("profile_version_id must be a UUID") from error
        profile_id = _identifier(self.profile_id, field="profile_id")
        if profile_id.startswith("S_"):
            raise VoiceGeneratedAudioBindingError("profile_id must not be a provider speaker ID")
        object.__setattr__(self, "profile_id", profile_id)
        object.__setattr__(self, "profile_version", _positive_int(self.profile_version, field="profile_version"))
        if not isinstance(self.purpose, VoiceDHPurpose) or self.purpose not in _ALLOWED_PURPOSES:
            raise VoiceGeneratedAudioBindingError("purpose is not allowed for private generated audio")
        if self.provider is not VoiceDHProvider.VOLCENGINE_VOICE_CLONE:
            raise VoiceGeneratedAudioBindingError("generated voice audio requires the voice clone provider")
        object.__setattr__(self, "policy_version", _identifier(self.policy_version, field="policy_version"))
        for field in ("source_binding_hash", "text_hash", "request_hash"):
            object.__setattr__(self, field, _hash(getattr(self, field), field=field))
        try:
            output_mode = VoiceGeneratedAudioOutputMode(self.output_mode)
        except ValueError as error:
            raise VoiceGeneratedAudioBindingError("unsupported output_mode") from error
        object.__setattr__(self, "output_mode", output_mode)
        audio_format = str(self.audio_format or "").strip().lower()
        if audio_format not in {"mp3", "wav", "pcm16kmono"}:
            raise VoiceGeneratedAudioBindingError("unsupported audio_format")
        object.__setattr__(self, "audio_format", audio_format)
        object.__setattr__(self, "sample_rate", _positive_int(self.sample_rate, field="sample_rate"))
        object.__setattr__(self, "channel_count", _positive_int(self.channel_count, field="channel_count"))
        issued_at = _utc(self.issued_at, field="issued_at")
        expires_at = _utc(self.expires_at, field="expires_at")
        if expires_at <= issued_at:
            raise VoiceGeneratedAudioBindingError("expires_at must be after issued_at")
        object.__setattr__(self, "issued_at", issued_at)
        object.__setattr__(self, "expires_at", expires_at)
        self._validate_output_contract()

    def _validate_output_contract(self) -> None:
        if self.purpose is VoiceDHPurpose.DH_AUDIO_DRIVE:
            if self.output_mode is not VoiceGeneratedAudioOutputMode.TENCENT_AUDIO_DRIVE:
                raise VoiceGeneratedAudioBindingError("dh audio drive requires tencentAudioDrive output")
            if (self.audio_format, self.sample_rate, self.channel_count) != ("pcm16kmono", 16000, 1):
                raise VoiceGeneratedAudioBindingError(
                    "tencentAudioDrive requires pcm16kMono/16000Hz/mono metadata"
                )
            return
        if self.output_mode is VoiceGeneratedAudioOutputMode.TENCENT_AUDIO_DRIVE:
            raise VoiceGeneratedAudioBindingError(
                "tencentAudioDrive output requires dh_audio_drive purpose"
            )
        if self.purpose is VoiceDHPurpose.PREVIEW and self.audio_format not in {"mp3", "wav"}:
            raise VoiceGeneratedAudioBindingError("preview requires a preview-compatible audio format")

    @property
    def binding_fingerprint(self) -> str:
        material = {
            "audioFormat": self.audio_format,
            "channelCount": self.channel_count,
            "commandId": self.command_id,
            "outputMode": self.output_mode.value,
            "policyVersion": self.policy_version,
            "profileId": self.profile_id,
            "profileVersion": self.profile_version,
            "profileVersionId": self.profile_version_id,
            "provider": self.provider.value,
            "purpose": self.purpose.value,
            "requestHash": self.request_hash,
            "sampleRate": self.sample_rate,
            "sourceBindingHash": self.source_binding_hash,
            "textHash": self.text_hash,
        }
        return sha256(_canonical_json(material).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class VoiceGeneratedAudioBindingResult:
    disposition: VoiceGeneratedAudioBindingDisposition
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.disposition, VoiceGeneratedAudioBindingDisposition):
            raise TypeError("binding disposition is required")
        reason_codes = tuple(sorted({_identifier(value, field="reason_code") for value in self.reason_codes}))
        if not reason_codes:
            raise VoiceGeneratedAudioBindingError("at least one reason code is required")
        object.__setattr__(self, "reason_codes", reason_codes)

    def value_free_summary(self) -> dict[str, object]:
        return {
            "audioBytesStored": False,
            "cachePromotionAllowed": False,
            "generatedAudioPersisted": False,
            "providerEffectAllowed": False,
            "providerEffectPerformed": False,
            "reasonCodes": list(self.reason_codes),
            "releaseVisible": False,
            "schemaVersion": VOICE_GENERATED_AUDIO_BINDING_SHADOW_SCHEMA_VERSION,
            "status": self.disposition.value,
        }


class VoiceGeneratedAudioBindingShadow:
    """In-memory G0 observer; it cannot issue or persist generated audio."""

    def __init__(self, *, max_ttl_seconds: int = 3600) -> None:
        self._max_ttl_seconds = _positive_int(max_ttl_seconds, field="max_ttl_seconds")
        self._lock = RLock()
        self._observed_request_hashes: dict[tuple[str, str], str] = {}

    def observe(
        self,
        *,
        context: VoiceDHAuthorityContext | object,
        profile_authority: VoiceProfileVersionAuthorityRecord | object,
        command: VoiceGeneratedAudioBindingCommand | object,
        enabled: object = False,
        now: datetime | object | None = None,
    ) -> VoiceGeneratedAudioBindingResult:
        if enabled is not True:
            return VoiceGeneratedAudioBindingResult(
                disposition=VoiceGeneratedAudioBindingDisposition.SHADOW_DISABLED,
                reason_codes=("generatedAudioBindingShadowDisabled",),
            )
        if (
            not isinstance(context, VoiceDHAuthorityContext)
            or not isinstance(profile_authority, VoiceProfileVersionAuthorityRecord)
            or not isinstance(command, VoiceGeneratedAudioBindingCommand)
        ):
            return VoiceGeneratedAudioBindingResult(
                disposition=VoiceGeneratedAudioBindingDisposition.INVALID_CONTEXT,
                reason_codes=("invalidGeneratedAudioBindingContext",),
            )

        observed_at = datetime.now(timezone.utc) if now is None else _utc(now, field="now")
        reasons: set[str] = {
            "g0NoGeneratedAudioPersistence",
            "g2ObjectAndReceiptStoreRequired",
            "g3ProviderReceiptEvidenceRequired",
            "profileAuthorityBlocked",
            "releasePolicyDefaultOff",
        }
        record_command = profile_authority.command
        if profile_authority.context != context:
            reasons.add("profileAuthorityContextMismatch")
        if context.actor_subject_id != context.owner_subject_id:
            reasons.add("contextActorOwnerMismatch")
        if record_command.subject_id != context.owner_subject_id:
            reasons.add("profileAuthoritySubjectMismatch")
        if profile_authority.status != "blocked":
            reasons.add("unexpectedProfileAuthorityStatus")
        if record_command.purpose is not VoiceDHPurpose.PRIVATE_SYNTHESIS:
            reasons.add("profilePurposeNotPrivateSynthesis")
        if record_command.provider is not command.provider:
            reasons.add("profileProviderMismatch")
        if (
            command.profile_version_id != profile_authority.id
            or command.profile_id != record_command.profile_id
            or command.profile_version != record_command.profile_version
            or command.policy_version != record_command.policy_version
        ):
            reasons.add("profileVersionBindingMismatch")
        if command.issued_at > observed_at:
            reasons.add("issuedAtInFuture")
        if command.expires_at <= observed_at:
            reasons.add("bindingExpired")
        if (command.expires_at - command.issued_at).total_seconds() > self._max_ttl_seconds:
            reasons.add("ttlExceedsShadowMaximum")

        request_key = (context.vault_id, command.command_id)
        with self._lock:
            existing_request_hash = self._observed_request_hashes.get(request_key)
            if existing_request_hash is None:
                self._observed_request_hashes[request_key] = command.request_hash
            elif existing_request_hash == command.request_hash:
                reasons.add("stableCommandReplayObserved")
            else:
                reasons.add("stableCommandHashConflict")

        return VoiceGeneratedAudioBindingResult(
            disposition=VoiceGeneratedAudioBindingDisposition.BLOCKED,
            reason_codes=tuple(reasons),
        )


__all__ = [
    "VOICE_GENERATED_AUDIO_BINDING_SHADOW_SCHEMA_VERSION",
    "VoiceGeneratedAudioBindingCommand",
    "VoiceGeneratedAudioBindingDisposition",
    "VoiceGeneratedAudioBindingError",
    "VoiceGeneratedAudioBindingResult",
    "VoiceGeneratedAudioBindingShadow",
    "VoiceGeneratedAudioOutputMode",
]
