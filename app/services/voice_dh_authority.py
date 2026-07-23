"""Default-deny V4 Authority records for Voice Clone and Digital Human.

The legacy ``voice_profiles`` and ``digital_human_sessions`` stores remain
compatibility adapters.  This module is intentionally separate: it records a
hash-only, append-only *blocked admission* for a future Voice Profile version.
It never trains a voice, synthesizes audio, opens a Tencent session, stores an
audio sample/text/URL, or issues a provider credential.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import re
from threading import RLock
from typing import Any, Protocol
from uuid import UUID, uuid5


VOICE_DH_AUTHORITY_SCHEMA_VERSION = "voice-dh-authority-v1"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_HASH_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_PROFILE_VERSION_NAMESPACE = UUID("5a0a895a-9f22-4a58-8a8a-4ea9cad77919")
_SAMPLE_INTENT_NAMESPACE = UUID("d49dbcb5-d634-4b60-b10f-0d7dcc98fde6")
_RECEIPT_NAMESPACE = UUID("7f30211b-a079-4ae9-b5fa-1601ca99d20c")
_ALLOWED_SAMPLE_FORMATS = frozenset({"wav", "mp3", "m4a"})


class VoiceDHAuthorityError(ValueError):
    """Raised when a future Voice/DH Authority envelope is malformed."""


class VoiceDHAuthorityAccessDenied(VoiceDHAuthorityError):
    """The default G0 slice only permits an Owner's own future profile."""


class VoiceDHAuthorityConflict(VoiceDHAuthorityError):
    """An immutable profile version or receipt is being reused differently."""


class VoiceDHPurpose(str, Enum):
    TRAINING = "training"
    PREVIEW = "preview"
    PRIVATE_SYNTHESIS = "private_synthesis"
    MEMOIR = "memoir"
    DH_AUDIO_DRIVE = "dh_audio_drive"
    VISITOR_PUBLIC_VOICE = "visitor_public_voice"


class VoiceDHProvider(str, Enum):
    VOLCENGINE_VOICE_CLONE = "volcengineVoiceClone"
    TENCENT_DIGITAL_HUMAN = "tencentDigitalHuman"


class VoiceDHAuthorityDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    BLOCKED_RECORDED = "blocked_recorded"


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise VoiceDHAuthorityError(f"{field} must be an opaque identifier")
    return normalized


def _hash(value: object, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _HASH_PATTERN.fullmatch(normalized):
        raise VoiceDHAuthorityError(f"{field} must be a lowercase SHA-256 digest")
    return normalized


def _positive_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise VoiceDHAuthorityError(f"{field} must be a positive integer")
    return value


def _nonnegative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise VoiceDHAuthorityError(f"{field} must be a non-negative integer")
    return value


def _command_hash(command_id: str) -> str:
    return sha256(command_id.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class VoiceDHAuthorityContext:
    vault_id: str
    owner_subject_id: str
    actor_subject_id: str
    authority_epoch: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "vault_id", _identifier(self.vault_id, field="vault_id"))
        object.__setattr__(
            self,
            "owner_subject_id",
            _identifier(self.owner_subject_id, field="owner_subject_id"),
        )
        object.__setattr__(
            self,
            "actor_subject_id",
            _identifier(self.actor_subject_id, field="actor_subject_id"),
        )
        object.__setattr__(
            self,
            "authority_epoch",
            _nonnegative_int(self.authority_epoch, field="authority_epoch"),
        )


@dataclass(frozen=True)
class VoiceProfileVersionAdmissionCommand:
    """Hash-only future profile admission; it cannot carry a sample or secret."""

    command_id: str
    profile_id: str
    profile_version: int
    subject_id: str
    purpose: VoiceDHPurpose
    provider: VoiceDHProvider
    policy_version: str
    consent_receipt_hash: str
    purpose_grant_hash: str
    payload_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", _identifier(self.command_id, field="command_id"))
        profile_id = _identifier(self.profile_id, field="profile_id")
        if profile_id.startswith("S_"):
            raise VoiceDHAuthorityError("profile_id must not be a provider speaker ID")
        object.__setattr__(self, "profile_id", profile_id)
        object.__setattr__(
            self,
            "profile_version",
            _positive_int(self.profile_version, field="profile_version"),
        )
        object.__setattr__(self, "subject_id", _identifier(self.subject_id, field="subject_id"))
        if not isinstance(self.purpose, VoiceDHPurpose):
            raise VoiceDHAuthorityError("purpose is required")
        if not isinstance(self.provider, VoiceDHProvider):
            raise VoiceDHAuthorityError("provider is required")
        object.__setattr__(self, "policy_version", _identifier(self.policy_version, field="policy_version"))
        object.__setattr__(
            self,
            "consent_receipt_hash",
            _hash(self.consent_receipt_hash, field="consent_receipt_hash"),
        )
        object.__setattr__(
            self,
            "purpose_grant_hash",
            _hash(self.purpose_grant_hash, field="purpose_grant_hash"),
        )
        object.__setattr__(self, "payload_hash", _hash(self.payload_hash, field="payload_hash"))

    @property
    def command_id_hash(self) -> str:
        return _command_hash(self.command_id)


@dataclass(frozen=True)
class VoiceProfileVersionAuthorityRecord:
    id: str
    context: VoiceDHAuthorityContext
    command: VoiceProfileVersionAdmissionCommand
    status: str = "blocked"

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "id", str(UUID(str(self.id))))
        except (TypeError, ValueError) as error:
            raise VoiceDHAuthorityError("profile record id must be a UUID") from error
        if not isinstance(self.context, VoiceDHAuthorityContext):
            raise TypeError("profile record context is required")
        if not isinstance(self.command, VoiceProfileVersionAdmissionCommand):
            raise TypeError("profile record command is required")
        if self.status != "blocked":
            raise VoiceDHAuthorityError("G0 profile records must remain blocked")


@dataclass(frozen=True)
class VoiceDHAuthorityReceipt:
    id: str
    profile_version_id: str
    context: VoiceDHAuthorityContext
    command: VoiceProfileVersionAdmissionCommand
    operation: str = "blockedAdmission"
    reason_code: str = "g0DefaultDenyNoProviderEffect"

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "id", str(UUID(str(self.id))))
            object.__setattr__(
                self,
                "profile_version_id",
                str(UUID(str(self.profile_version_id))),
            )
        except (TypeError, ValueError) as error:
            raise VoiceDHAuthorityError("authority receipt identifiers must be UUIDs") from error
        if not isinstance(self.context, VoiceDHAuthorityContext):
            raise TypeError("authority receipt context is required")
        if not isinstance(self.command, VoiceProfileVersionAdmissionCommand):
            raise TypeError("authority receipt command is required")
        if self.operation != "blockedAdmission":
            raise VoiceDHAuthorityError("G0 authority receipt operation must remain blockedAdmission")
        object.__setattr__(self, "reason_code", _identifier(self.reason_code, field="reason_code"))


@dataclass(frozen=True)
class VoiceDHBlockedSampleIntentCommand:
    """Opaque, synthetic-only intent that remains blocked before training.

    ``preflight_request_hash`` is persisted as the sample intent payload hash.
    It binds this intent to a hash-only preflight observation without retaining
    the source audio, object location, quality evidence, or training payload.
    """

    command_id: str
    profile_version_id: str
    preflight_request_hash: str
    sample_hash: str
    sample_format: str
    duration_millis: int
    policy_version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", _identifier(self.command_id, field="command_id"))
        try:
            object.__setattr__(
                self,
                "profile_version_id",
                str(UUID(str(self.profile_version_id))),
            )
        except (TypeError, ValueError) as error:
            raise VoiceDHAuthorityError("sample intent profile_version_id must be a UUID") from error
        object.__setattr__(
            self,
            "preflight_request_hash",
            _hash(self.preflight_request_hash, field="preflight_request_hash"),
        )
        object.__setattr__(self, "sample_hash", _hash(self.sample_hash, field="sample_hash"))
        sample_format = str(self.sample_format or "").strip().lower()
        if sample_format not in _ALLOWED_SAMPLE_FORMATS:
            raise VoiceDHAuthorityError("sample_format must be a supported opaque media format")
        object.__setattr__(self, "sample_format", sample_format)
        object.__setattr__(
            self,
            "duration_millis",
            _positive_int(self.duration_millis, field="duration_millis"),
        )
        object.__setattr__(self, "policy_version", _identifier(self.policy_version, field="policy_version"))

    @property
    def command_id_hash(self) -> str:
        return _command_hash(self.command_id)

    @property
    def payload_hash(self) -> str:
        return self.preflight_request_hash

    @classmethod
    def from_synthetic_preflight(
        cls,
        *,
        context: VoiceDHAuthorityContext,
        profile_version_id: str,
        profile_command: VoiceProfileVersionAdmissionCommand,
        profile_policy_version: str,
        command_id: str,
        request: object,
        decision: object,
    ) -> "VoiceDHBlockedSampleIntentCommand":
        """Build a blocked intent only from a successful synthetic G0 preflight.

        The import is local so this Authority module stays free of any media,
        network, or Provider dependency.  This method intentionally rejects
        every preflight that could be mistaken for a real training admission.
        """

        from app.services.voice_training_preflight_shadow import (
            VoiceTrainingPreflightDecision,
            VoiceTrainingPreflightDisposition,
            VoiceTrainingPreflightRequest,
        )

        if not isinstance(context, VoiceDHAuthorityContext):
            raise TypeError("voice authority context is required")
        if not isinstance(profile_command, VoiceProfileVersionAdmissionCommand):
            raise TypeError("blocked sample intent requires a profile admission command")
        if not isinstance(request, VoiceTrainingPreflightRequest):
            raise VoiceDHAuthorityAccessDenied("blocked sample intent requires a synthetic preflight request")
        if not isinstance(decision, VoiceTrainingPreflightDecision):
            raise VoiceDHAuthorityAccessDenied("blocked sample intent requires a synthetic preflight decision")
        if decision.status is not VoiceTrainingPreflightDisposition.BLOCKED:
            raise VoiceDHAuthorityAccessDenied("only blocked synthetic preflight decisions are admissible")
        if not decision.syntheticPreconditionsObserved:
            raise VoiceDHAuthorityAccessDenied("synthetic preflight prerequisites were not observed")
        if (
            decision.providerEffectAllowed
            or decision.providerEffectPerformed
            or decision.trainingCommandCreated
            or decision.sampleObjectCreated
            or decision.releaseVisible
        ):
            raise VoiceDHAuthorityAccessDenied("preflight must remain default-deny and effect-free")
        if (
            request.vaultId != context.vault_id
            or request.ownerSubjectId != context.owner_subject_id
            or request.actorSubjectId != context.actor_subject_id
            or request.subjectId != context.owner_subject_id
            or request.authorityEpoch != context.authority_epoch
            or request.purpose.value != VoiceDHPurpose.TRAINING.value
            or request.provider != VoiceDHProvider.VOLCENGINE_VOICE_CLONE.value
            or request.profileReference.status != "blocked"
        ):
            raise VoiceDHAuthorityAccessDenied("synthetic preflight does not bind the current owner authority")
        if (
            profile_command.subject_id != context.owner_subject_id
            or profile_command.purpose is not VoiceDHPurpose.TRAINING
            or profile_command.provider is not VoiceDHProvider.VOLCENGINE_VOICE_CLONE
            or profile_command.policy_version != _identifier(
                profile_policy_version,
                field="profile_policy_version",
            )
            or request.profileReference.profileId != profile_command.profile_id
            or request.profileReference.profileVersion != profile_command.profile_version
        ):
            raise VoiceDHAuthorityAccessDenied("synthetic preflight does not bind the training profile command")
        expected_profile_version_id = str(
            uuid5(
                _PROFILE_VERSION_NAMESPACE,
                f"{context.vault_id}:{profile_command.profile_id}:"
                f"{profile_command.profile_version}:{profile_command.command_id_hash}",
            )
        )
        if str(profile_version_id) != expected_profile_version_id:
            raise VoiceDHAuthorityAccessDenied("sample intent must bind the deterministic admitted profile version")
        return cls(
            command_id=command_id,
            profile_version_id=profile_version_id,
            preflight_request_hash=request.requestHash,
            sample_hash=request.sampleDescriptor.sampleHash,
            sample_format=request.sampleDescriptor.mediaFormat,
            duration_millis=request.sampleDescriptor.durationMilliseconds,
            policy_version=profile_policy_version,
        )


@dataclass(frozen=True)
class VoiceDHBlockedSampleIntentRecord:
    id: str
    context: VoiceDHAuthorityContext
    command: VoiceDHBlockedSampleIntentCommand
    status: str = "blocked"

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "id", str(UUID(str(self.id))))
        except (TypeError, ValueError) as error:
            raise VoiceDHAuthorityError("sample intent record id must be a UUID") from error
        if not isinstance(self.context, VoiceDHAuthorityContext):
            raise TypeError("sample intent context is required")
        if not isinstance(self.command, VoiceDHBlockedSampleIntentCommand):
            raise TypeError("sample intent command is required")
        if self.status != "blocked":
            raise VoiceDHAuthorityError("G0 sample intents must remain blocked")


@dataclass(frozen=True)
class VoiceDHBlockedSampleIntentReceipt:
    id: str
    sample_intent_id: str
    context: VoiceDHAuthorityContext
    command: VoiceDHBlockedSampleIntentCommand
    operation: str = "blockedAdmission"
    reason_code: str = "g0DefaultDenyNoProviderEffect"

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "id", str(UUID(str(self.id))))
            object.__setattr__(
                self,
                "sample_intent_id",
                str(UUID(str(self.sample_intent_id))),
            )
        except (TypeError, ValueError) as error:
            raise VoiceDHAuthorityError("sample intent receipt identifiers must be UUIDs") from error
        if not isinstance(self.context, VoiceDHAuthorityContext):
            raise TypeError("sample intent receipt context is required")
        if not isinstance(self.command, VoiceDHBlockedSampleIntentCommand):
            raise TypeError("sample intent receipt command is required")
        if self.operation != "blockedAdmission":
            raise VoiceDHAuthorityError("G0 sample intent receipt operation must remain blockedAdmission")
        object.__setattr__(self, "reason_code", _identifier(self.reason_code, field="reason_code"))


@dataclass(frozen=True)
class VoiceDHAuthorityAdmissionResult:
    disposition: VoiceDHAuthorityDisposition
    profile_version_id: str | None = None
    receipt_id: str | None = None
    outcome: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.disposition, VoiceDHAuthorityDisposition):
            raise TypeError("authority disposition is required")
        if self.disposition is VoiceDHAuthorityDisposition.SHADOW_DISABLED:
            if any(value is not None for value in (self.profile_version_id, self.receipt_id, self.outcome)):
                raise VoiceDHAuthorityError("disabled result must not expose persisted identifiers")
        elif self.outcome not in {"created", "deduplicated"}:
            raise VoiceDHAuthorityError("recorded result needs a stable outcome")

    def value_free_summary(self) -> dict[str, object]:
        return {
            "authorityRecordWritten": self.disposition is VoiceDHAuthorityDisposition.BLOCKED_RECORDED,
            "disposition": self.disposition.value,
            "providerEffectAllowed": False,
            "providerEffectPerformed": False,
            "releaseVisible": False,
            "schemaVersion": VOICE_DH_AUTHORITY_SCHEMA_VERSION,
            "status": "blocked" if self.disposition is VoiceDHAuthorityDisposition.BLOCKED_RECORDED else "unavailable",
        }


@dataclass(frozen=True)
class VoiceDHBlockedSampleIntentResult:
    disposition: VoiceDHAuthorityDisposition
    sample_intent_id: str | None = None
    receipt_id: str | None = None
    outcome: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.disposition, VoiceDHAuthorityDisposition):
            raise TypeError("authority disposition is required")
        if self.disposition is VoiceDHAuthorityDisposition.SHADOW_DISABLED:
            if any(value is not None for value in (self.sample_intent_id, self.receipt_id, self.outcome)):
                raise VoiceDHAuthorityError("disabled result must not expose persisted identifiers")
        elif self.outcome not in {"created", "deduplicated"}:
            raise VoiceDHAuthorityError("recorded result needs a stable outcome")

    def value_free_summary(self) -> dict[str, object]:
        return {
            "disposition": self.disposition.value,
            "providerEffectAllowed": False,
            "providerEffectPerformed": False,
            "releaseVisible": False,
            "sampleIntentWritten": self.disposition is VoiceDHAuthorityDisposition.BLOCKED_RECORDED,
            "sampleObjectCreated": False,
            "schemaVersion": VOICE_DH_AUTHORITY_SCHEMA_VERSION,
            "status": "blocked" if self.disposition is VoiceDHAuthorityDisposition.BLOCKED_RECORDED else "unavailable",
            "trainingCommandCreated": False,
        }


class VoiceDHAuthorityRepository(Protocol):
    def record_blocked_profile_version(
        self,
        *,
        record: VoiceProfileVersionAuthorityRecord,
        receipt: VoiceDHAuthorityReceipt,
    ) -> str:
        """Persist an immutable blocked record and return created/deduplicated."""

    def record_blocked_sample_intent(
        self,
        *,
        record: VoiceDHBlockedSampleIntentRecord,
        receipt: VoiceDHBlockedSampleIntentReceipt,
    ) -> str:
        """Persist an immutable blocked sample intent and paired receipt."""


class InMemoryVoiceDHAuthorityRepository:
    """Deterministic semantic double for the G0 append-only Authority boundary."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._profiles: dict[tuple[str, str, int], VoiceProfileVersionAuthorityRecord] = {}
        self._profiles_by_id: dict[str, VoiceProfileVersionAuthorityRecord] = {}
        self._sample_intents: dict[tuple[str, str], VoiceDHBlockedSampleIntentRecord] = {}
        self._receipts: dict[tuple[str, str, str], object] = {}

    def record_blocked_profile_version(
        self,
        *,
        record: VoiceProfileVersionAuthorityRecord,
        receipt: VoiceDHAuthorityReceipt,
    ) -> str:
        if receipt.profile_version_id != record.id:
            raise VoiceDHAuthorityConflict("receipt must bind the exact profile version")
        profile_key = (
            record.context.vault_id,
            record.command.profile_id,
            record.command.profile_version,
        )
        receipt_key = (record.context.vault_id, "voiceProfileVersion", record.command.command_id_hash)
        with self._lock:
            existing = self._profiles.get(profile_key)
            existing_receipt = self._receipts.get(receipt_key)
            if existing is None and existing_receipt is None:
                self._profiles[profile_key] = record
                self._profiles_by_id[record.id] = record
                self._receipts[receipt_key] = receipt
                return "created"
            if existing != record or existing_receipt != receipt:
                raise VoiceDHAuthorityConflict("profile version admission is immutable")
            return "deduplicated"

    def record_blocked_sample_intent(
        self,
        *,
        record: VoiceDHBlockedSampleIntentRecord,
        receipt: VoiceDHBlockedSampleIntentReceipt,
    ) -> str:
        if receipt.sample_intent_id != record.id:
            raise VoiceDHAuthorityConflict("receipt must bind the exact sample intent")
        sample_key = (record.context.vault_id, record.command.command_id_hash)
        receipt_key = (record.context.vault_id, "sampleIntent", record.command.command_id_hash)
        with self._lock:
            parent = self._profiles_by_id.get(record.command.profile_version_id)
            if parent is None:
                raise VoiceDHAuthorityConflict("sample intent requires an existing profile authority")
            if (
                parent.context != record.context
                or parent.status != "blocked"
                or parent.command.purpose is not VoiceDHPurpose.TRAINING
                or parent.command.provider is not VoiceDHProvider.VOLCENGINE_VOICE_CLONE
                or parent.command.policy_version != record.command.policy_version
            ):
                raise VoiceDHAuthorityConflict("sample intent must bind a blocked training profile authority")
            existing = self._sample_intents.get(sample_key)
            existing_receipt = self._receipts.get(receipt_key)
            if existing is None and existing_receipt is None:
                self._sample_intents[sample_key] = record
                self._receipts[receipt_key] = receipt
                return "created"
            if existing != record or existing_receipt != receipt:
                raise VoiceDHAuthorityConflict("sample intent admission is immutable")
            return "deduplicated"

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "profileVersionCount": len(self._profiles),
                "sampleIntentCount": len(self._sample_intents),
                "receiptCount": len(self._receipts),
            }


class PostgresVoiceDHAuthorityRepository:
    """Append-only profile admission writer for an active Postgres transaction."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def record_blocked_profile_version(
        self,
        *,
        record: VoiceProfileVersionAuthorityRecord,
        receipt: VoiceDHAuthorityReceipt,
    ) -> str:
        if receipt.profile_version_id != record.id:
            raise VoiceDHAuthorityConflict("receipt must bind the exact profile version")
        with self._cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO voice_dh.voice_profile_versions (
                    id, vault_id, owner_subject_id, actor_subject_id, subject_id,
                    authority_epoch, profile_id, version_number, purpose, provider,
                    policy_version, consent_receipt_hash, purpose_grant_hash,
                    command_id_hash, payload_hash, status
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s
                )
                ON CONFLICT (vault_id, profile_id, version_number) DO NOTHING
                RETURNING id
                """,
                self._profile_params(record),
            )
            inserted_profile = cursor.fetchone()
            if inserted_profile is None:
                self._assert_matching_profile(cursor, record)

            cursor.execute(
                """
                INSERT INTO voice_dh.authority_receipts (
                    id, vault_id, owner_subject_id, actor_subject_id, authority_epoch,
                    resource_kind, resource_id, purpose, policy_version, operation,
                    reason_code, command_id_hash, payload_hash
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    'voiceProfileVersion', %s, %s, %s, %s,
                    %s, %s, %s
                )
                ON CONFLICT (vault_id, resource_kind, command_id_hash) DO NOTHING
                RETURNING id
                """,
                self._receipt_params(receipt),
            )
            inserted_receipt = cursor.fetchone()
            if inserted_receipt is None:
                self._assert_matching_receipt(cursor, receipt)

        if (inserted_profile is None) != (inserted_receipt is None):
            raise VoiceDHAuthorityConflict("profile admission and receipt must be atomically paired")
        return "created" if inserted_profile is not None else "deduplicated"

    def record_blocked_sample_intent(
        self,
        *,
        record: VoiceDHBlockedSampleIntentRecord,
        receipt: VoiceDHBlockedSampleIntentReceipt,
    ) -> str:
        if receipt.sample_intent_id != record.id:
            raise VoiceDHAuthorityConflict("receipt must bind the exact sample intent")
        with self._cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO voice_dh.sample_intents (
                    id, profile_version_id, vault_id, owner_subject_id, actor_subject_id,
                    authority_epoch, purpose, provider, sample_hash, sample_format,
                    duration_millis, policy_version, command_id_hash, payload_hash, status
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s
                )
                ON CONFLICT (vault_id, command_id_hash) DO NOTHING
                RETURNING id
                """,
                self._sample_intent_params(record),
            )
            inserted_sample = cursor.fetchone()
            if inserted_sample is None:
                self._assert_matching_sample_intent(cursor, record)

            cursor.execute(
                """
                INSERT INTO voice_dh.authority_receipts (
                    id, vault_id, owner_subject_id, actor_subject_id, authority_epoch,
                    resource_kind, resource_id, purpose, policy_version, operation,
                    reason_code, command_id_hash, payload_hash
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    'sampleIntent', %s, %s, %s, %s,
                    %s, %s, %s
                )
                ON CONFLICT (vault_id, resource_kind, command_id_hash) DO NOTHING
                RETURNING id
                """,
                self._sample_intent_receipt_params(receipt),
            )
            inserted_receipt = cursor.fetchone()
            if inserted_receipt is None:
                self._assert_matching_sample_intent_receipt(cursor, receipt)

        if (inserted_sample is None) != (inserted_receipt is None):
            raise VoiceDHAuthorityConflict("sample intent and receipt must be atomically paired")
        return "created" if inserted_sample is not None else "deduplicated"

    @staticmethod
    def _profile_params(record: VoiceProfileVersionAuthorityRecord) -> tuple[object, ...]:
        context = record.context
        command = record.command
        return (
            record.id,
            context.vault_id,
            context.owner_subject_id,
            context.actor_subject_id,
            command.subject_id,
            context.authority_epoch,
            command.profile_id,
            command.profile_version,
            command.purpose.value,
            command.provider.value,
            command.policy_version,
            command.consent_receipt_hash,
            command.purpose_grant_hash,
            command.command_id_hash,
            command.payload_hash,
            record.status,
        )

    @staticmethod
    def _receipt_params(receipt: VoiceDHAuthorityReceipt) -> tuple[object, ...]:
        context = receipt.context
        command = receipt.command
        return (
            receipt.id,
            context.vault_id,
            context.owner_subject_id,
            context.actor_subject_id,
            context.authority_epoch,
            receipt.profile_version_id,
            command.purpose.value,
            command.policy_version,
            receipt.operation,
            receipt.reason_code,
            command.command_id_hash,
            command.payload_hash,
        )

    @staticmethod
    def _sample_intent_params(record: VoiceDHBlockedSampleIntentRecord) -> tuple[object, ...]:
        context = record.context
        command = record.command
        return (
            record.id,
            command.profile_version_id,
            context.vault_id,
            context.owner_subject_id,
            context.actor_subject_id,
            context.authority_epoch,
            VoiceDHPurpose.TRAINING.value,
            VoiceDHProvider.VOLCENGINE_VOICE_CLONE.value,
            command.sample_hash,
            command.sample_format,
            command.duration_millis,
            command.policy_version,
            command.command_id_hash,
            command.payload_hash,
            record.status,
        )

    @staticmethod
    def _sample_intent_receipt_params(
        receipt: VoiceDHBlockedSampleIntentReceipt,
    ) -> tuple[object, ...]:
        context = receipt.context
        command = receipt.command
        return (
            receipt.id,
            context.vault_id,
            context.owner_subject_id,
            context.actor_subject_id,
            context.authority_epoch,
            receipt.sample_intent_id,
            VoiceDHPurpose.TRAINING.value,
            command.policy_version,
            receipt.operation,
            receipt.reason_code,
            command.command_id_hash,
            command.payload_hash,
        )

    @staticmethod
    def _assert_matching_profile(cursor: Any, record: VoiceProfileVersionAuthorityRecord) -> None:
        cursor.execute(
            """
            SELECT id, owner_subject_id, actor_subject_id, subject_id, authority_epoch,
                purpose, provider, policy_version, consent_receipt_hash,
                purpose_grant_hash, command_id_hash, payload_hash, status
            FROM voice_dh.voice_profile_versions
            WHERE vault_id = %s AND profile_id = %s AND version_number = %s
            FOR SHARE
            """,
            (record.context.vault_id, record.command.profile_id, record.command.profile_version),
        )
        row = cursor.fetchone()
        expected = {
            "id": record.id,
            "owner_subject_id": record.context.owner_subject_id,
            "actor_subject_id": record.context.actor_subject_id,
            "subject_id": record.command.subject_id,
            "authority_epoch": record.context.authority_epoch,
            "purpose": record.command.purpose.value,
            "provider": record.command.provider.value,
            "policy_version": record.command.policy_version,
            "consent_receipt_hash": record.command.consent_receipt_hash,
            "purpose_grant_hash": record.command.purpose_grant_hash,
            "command_id_hash": record.command.command_id_hash,
            "payload_hash": record.command.payload_hash,
            "status": record.status,
        }
        actual = PostgresVoiceDHAuthorityRepository._normalized_row(row, expected)
        if actual != expected:
            raise VoiceDHAuthorityConflict("profile version replay conflicts with immutable authority")

    @staticmethod
    def _assert_matching_receipt(cursor: Any, receipt: VoiceDHAuthorityReceipt) -> None:
        cursor.execute(
            """
            SELECT id, owner_subject_id, actor_subject_id, authority_epoch, resource_id,
                purpose, policy_version, operation, reason_code, payload_hash
            FROM voice_dh.authority_receipts
            WHERE vault_id = %s AND resource_kind = 'voiceProfileVersion'
                AND command_id_hash = %s
            FOR SHARE
            """,
            (receipt.context.vault_id, receipt.command.command_id_hash),
        )
        row = cursor.fetchone()
        expected = {
            "id": receipt.id,
            "owner_subject_id": receipt.context.owner_subject_id,
            "actor_subject_id": receipt.context.actor_subject_id,
            "authority_epoch": receipt.context.authority_epoch,
            "resource_id": receipt.profile_version_id,
            "purpose": receipt.command.purpose.value,
            "policy_version": receipt.command.policy_version,
            "operation": receipt.operation,
            "reason_code": receipt.reason_code,
            "payload_hash": receipt.command.payload_hash,
        }
        actual = PostgresVoiceDHAuthorityRepository._normalized_row(row, expected)
        if actual != expected:
            raise VoiceDHAuthorityConflict("authority receipt replay conflicts with immutable authority")

    @staticmethod
    def _assert_matching_sample_intent(
        cursor: Any,
        record: VoiceDHBlockedSampleIntentRecord,
    ) -> None:
        cursor.execute(
            """
            SELECT id, profile_version_id, owner_subject_id, actor_subject_id,
                authority_epoch, purpose, provider, sample_hash, sample_format,
                duration_millis, policy_version, command_id_hash, payload_hash, status
            FROM voice_dh.sample_intents
            WHERE vault_id = %s AND command_id_hash = %s
            FOR SHARE
            """,
            (record.context.vault_id, record.command.command_id_hash),
        )
        row = cursor.fetchone()
        expected = {
            "id": record.id,
            "profile_version_id": record.command.profile_version_id,
            "owner_subject_id": record.context.owner_subject_id,
            "actor_subject_id": record.context.actor_subject_id,
            "authority_epoch": record.context.authority_epoch,
            "purpose": VoiceDHPurpose.TRAINING.value,
            "provider": VoiceDHProvider.VOLCENGINE_VOICE_CLONE.value,
            "sample_hash": record.command.sample_hash,
            "sample_format": record.command.sample_format,
            "duration_millis": record.command.duration_millis,
            "policy_version": record.command.policy_version,
            "command_id_hash": record.command.command_id_hash,
            "payload_hash": record.command.payload_hash,
            "status": record.status,
        }
        actual = PostgresVoiceDHAuthorityRepository._normalized_row(row, expected)
        if actual != expected:
            raise VoiceDHAuthorityConflict("sample intent replay conflicts with immutable authority")

    @staticmethod
    def _assert_matching_sample_intent_receipt(
        cursor: Any,
        receipt: VoiceDHBlockedSampleIntentReceipt,
    ) -> None:
        cursor.execute(
            """
            SELECT id, owner_subject_id, actor_subject_id, authority_epoch, resource_id,
                purpose, policy_version, operation, reason_code, payload_hash
            FROM voice_dh.authority_receipts
            WHERE vault_id = %s AND resource_kind = 'sampleIntent'
                AND command_id_hash = %s
            FOR SHARE
            """,
            (receipt.context.vault_id, receipt.command.command_id_hash),
        )
        row = cursor.fetchone()
        expected = {
            "id": receipt.id,
            "owner_subject_id": receipt.context.owner_subject_id,
            "actor_subject_id": receipt.context.actor_subject_id,
            "authority_epoch": receipt.context.authority_epoch,
            "resource_id": receipt.sample_intent_id,
            "purpose": VoiceDHPurpose.TRAINING.value,
            "policy_version": receipt.command.policy_version,
            "operation": receipt.operation,
            "reason_code": receipt.reason_code,
            "payload_hash": receipt.command.payload_hash,
        }
        actual = PostgresVoiceDHAuthorityRepository._normalized_row(row, expected)
        if actual != expected:
            raise VoiceDHAuthorityConflict("sample intent receipt replay conflicts with immutable authority")

    @staticmethod
    def _normalized_row(row: Any, expected: dict[str, object]) -> dict[str, object] | None:
        if row is None:
            return None
        return {
            key: str(row[key])
            if key in {"id", "resource_id", "profile_version_id"} and row[key] is not None
            else row[key]
            for key in expected
        }

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)


class VoiceDHAuthorityService:
    """A deliberately default-off admission seam for future Voice/DH flows."""

    def __init__(self, repository: VoiceDHAuthorityRepository, *, enabled: bool = False) -> None:
        self._repository = repository
        self._enabled = bool(enabled)

    def admit_self_profile_version(
        self,
        *,
        context: VoiceDHAuthorityContext,
        command: VoiceProfileVersionAdmissionCommand,
    ) -> VoiceDHAuthorityAdmissionResult:
        if not self._enabled:
            return VoiceDHAuthorityAdmissionResult(
                disposition=VoiceDHAuthorityDisposition.SHADOW_DISABLED,
            )
        if context.actor_subject_id != context.owner_subject_id:
            raise VoiceDHAuthorityAccessDenied("only the Vault Owner may create a future profile admission")
        if command.subject_id != context.owner_subject_id:
            raise VoiceDHAuthorityAccessDenied("G0 admission only supports the Owner as the living subject")

        profile_version_id = str(
            uuid5(
                _PROFILE_VERSION_NAMESPACE,
                f"{context.vault_id}:{command.profile_id}:{command.profile_version}:{command.command_id_hash}",
            )
        )
        record = VoiceProfileVersionAuthorityRecord(
            id=profile_version_id,
            context=context,
            command=command,
        )
        receipt = VoiceDHAuthorityReceipt(
            id=str(uuid5(_RECEIPT_NAMESPACE, f"{profile_version_id}:{command.command_id_hash}")),
            profile_version_id=profile_version_id,
            context=context,
            command=command,
        )
        outcome = self._repository.record_blocked_profile_version(record=record, receipt=receipt)
        return VoiceDHAuthorityAdmissionResult(
            disposition=VoiceDHAuthorityDisposition.BLOCKED_RECORDED,
            profile_version_id=record.id,
            receipt_id=receipt.id,
            outcome=outcome,
        )

    def admit_blocked_training_sample_intent(
        self,
        *,
        context: VoiceDHAuthorityContext,
        command: VoiceDHBlockedSampleIntentCommand,
    ) -> VoiceDHBlockedSampleIntentResult:
        """Persist only a blocked synthetic intent, never a media object or training command."""

        if not self._enabled:
            return VoiceDHBlockedSampleIntentResult(
                disposition=VoiceDHAuthorityDisposition.SHADOW_DISABLED,
            )
        if context.actor_subject_id != context.owner_subject_id:
            raise VoiceDHAuthorityAccessDenied("only the Vault Owner may record a blocked sample intent")
        sample_intent_id = str(
            uuid5(
                _SAMPLE_INTENT_NAMESPACE,
                f"{context.vault_id}:{command.profile_version_id}:{command.command_id_hash}",
            )
        )
        record = VoiceDHBlockedSampleIntentRecord(
            id=sample_intent_id,
            context=context,
            command=command,
        )
        receipt = VoiceDHBlockedSampleIntentReceipt(
            id=str(uuid5(_RECEIPT_NAMESPACE, f"sampleIntent:{sample_intent_id}:{command.command_id_hash}")),
            sample_intent_id=sample_intent_id,
            context=context,
            command=command,
        )
        outcome = self._repository.record_blocked_sample_intent(record=record, receipt=receipt)
        return VoiceDHBlockedSampleIntentResult(
            disposition=VoiceDHAuthorityDisposition.BLOCKED_RECORDED,
            sample_intent_id=record.id,
            receipt_id=receipt.id,
            outcome=outcome,
        )


__all__ = [
    "InMemoryVoiceDHAuthorityRepository",
    "PostgresVoiceDHAuthorityRepository",
    "VOICE_DH_AUTHORITY_SCHEMA_VERSION",
    "VoiceDHAuthorityAccessDenied",
    "VoiceDHAuthorityAdmissionResult",
    "VoiceDHAuthorityConflict",
    "VoiceDHAuthorityContext",
    "VoiceDHAuthorityDisposition",
    "VoiceDHAuthorityError",
    "VoiceDHAuthorityReceipt",
    "VoiceDHAuthorityService",
    "VoiceDHBlockedSampleIntentCommand",
    "VoiceDHBlockedSampleIntentReceipt",
    "VoiceDHBlockedSampleIntentRecord",
    "VoiceDHBlockedSampleIntentResult",
    "VoiceDHProvider",
    "VoiceDHPurpose",
    "VoiceProfileVersionAdmissionCommand",
    "VoiceProfileVersionAuthorityRecord",
]
