"""Default-off preflight for a future versioned Self Persona Authority writer.

This G0 contract intentionally has no route, repository, database, provider,
runtime or effect dependency.  It proves the command shape and fail-closed
policy required before a later additive Persona aggregate can persist an
immutable PersonaVersion and DecisionReceipt.

Only a living Vault Owner's own allowlisted display profile is considered here.
Memorial representation requires its own controller, verification and rights
contracts; a deceased representation must never be treated as a login
principal.  Passing this preflight never creates or updates any authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from hashlib import sha256
import json
import re
from typing import Any, Mapping
from uuid import UUID


OWNER_TRUTH_PERSONA_AUTHORITY_COMMAND_SCHEMA_VERSION = (
    "owner-truth-persona-authority-command-shadow-v1"
)
PERSONA_PROFILE_ALLOWED_FIELD_NAMES = frozenset({"birthDate", "displayName", "gender"})
_COMMAND_FIELD_NAMES = frozenset({"commandId", "expectedVersion", "personaId", "profile"})
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MAX_DISPLAY_NAME_LENGTH = 160
_MAX_GENDER_LENGTH = 64


class OwnerTruthPersonaAuthorityCommandContractError(ValueError):
    """Raised for an invalid preflight-only Persona Authority command."""

    def __init__(self, message: str, *, reason_code: str = "invalidPersonaAuthorityCommand"):
        super().__init__(message)
        self.reason_code = reason_code


class OwnerTruthPersonaAuthorityCommandDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    INVALID_CONTEXT = "invalid_context"
    INVALID_COMMAND = "invalid_command"
    ORIGIN_NOT_ALLOWED = "origin_not_allowed"
    ACTOR_NOT_OWNER = "actor_not_owner"
    MEMORIAL_CONTROLLER_REQUIRED = "memorial_controller_required"
    EXPECTED_VERSION_CONFLICT = "expected_version_conflict"
    ACCEPTED_FOR_FUTURE_PERSISTENCE = "accepted_for_future_persistence"


class OwnerTruthPersonaAuthoritySubjectKind(str, Enum):
    SELF_OWNER = "self_owner"
    MEMORIAL_REPRESENTED = "memorial_represented"


class OwnerTruthPersonaAuthorityCommandOrigin(str, Enum):
    OWNER_INTERACTIVE = "owner_interactive"
    FAMILY = "family"
    ASSISTANT = "assistant"
    PROVIDER = "provider"
    RUNTIME = "runtime"
    UNKNOWN = "unknown"


_ORIGIN_DENIAL_REASON_CODES = {
    OwnerTruthPersonaAuthorityCommandOrigin.FAMILY: "familyCannotWritePersonaAuthority",
    OwnerTruthPersonaAuthorityCommandOrigin.ASSISTANT: "assistantCannotWritePersonaAuthority",
    OwnerTruthPersonaAuthorityCommandOrigin.PROVIDER: "providerCannotWritePersonaAuthority",
    OwnerTruthPersonaAuthorityCommandOrigin.RUNTIME: "runtimeCannotWritePersonaAuthority",
    OwnerTruthPersonaAuthorityCommandOrigin.UNKNOWN: "unknownOriginCannotWritePersonaAuthority",
}


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise OwnerTruthPersonaAuthorityCommandContractError(
            f"{field} must be an opaque identifier",
            reason_code="invalidPersonaAuthorityIdentifier",
        )
    return normalized


def _uuid(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    try:
        return str(UUID(normalized))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthPersonaAuthorityCommandContractError(
            f"{field} must be a UUID",
            reason_code="invalidPersonaIdentifier",
        ) from exc


def _non_negative_version(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise OwnerTruthPersonaAuthorityCommandContractError(
            f"{field} must be a non-negative integer",
            reason_code="invalidExpectedPersonaVersion",
        )
    return value


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthPersonaAuthorityCommandContractError(
            "persona command must be JSON serializable",
            reason_code="invalidPersonaAuthorityEnvelope",
        ) from exc


def _digest(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _normalize_profile_value(field: str, value: object) -> str:
    if not isinstance(value, str):
        raise OwnerTruthPersonaAuthorityCommandContractError(
            "persona profile value must be text",
            reason_code="invalidPersonaProfileValue",
        )
    normalized = value.strip()
    if not normalized:
        raise OwnerTruthPersonaAuthorityCommandContractError(
            "persona profile value must be nonblank",
            reason_code="invalidPersonaProfileValue",
        )
    if field == "displayName":
        if len(normalized) > _MAX_DISPLAY_NAME_LENGTH:
            raise OwnerTruthPersonaAuthorityCommandContractError(
                "displayName exceeds the Persona V1 limit",
                reason_code="invalidPersonaProfileValue",
            )
    elif field == "gender":
        if len(normalized) > _MAX_GENDER_LENGTH:
            raise OwnerTruthPersonaAuthorityCommandContractError(
                "gender exceeds the Persona V1 limit",
                reason_code="invalidPersonaProfileValue",
            )
    elif field == "birthDate":
        if not _DATE_PATTERN.fullmatch(normalized):
            raise OwnerTruthPersonaAuthorityCommandContractError(
                "birthDate must use YYYY-MM-DD",
                reason_code="invalidPersonaBirthDate",
            )
        try:
            date.fromisoformat(normalized)
        except ValueError as exc:
            raise OwnerTruthPersonaAuthorityCommandContractError(
                "birthDate must be a calendar date",
                reason_code="invalidPersonaBirthDate",
            ) from exc
    return normalized


@dataclass(frozen=True)
class OwnerTruthPersonaAuthorityCommandContext:
    """Synthetic read context for a future CAS-backed Self Persona command."""

    vault_id: str
    owner_subject_id: str
    actor_subject_id: str
    current_persona_version: int
    authority_epoch: int = 0
    subject_kind: OwnerTruthPersonaAuthoritySubjectKind = OwnerTruthPersonaAuthoritySubjectKind.SELF_OWNER
    policy_version: str = OWNER_TRUTH_PERSONA_AUTHORITY_COMMAND_SCHEMA_VERSION
    command_origin: OwnerTruthPersonaAuthorityCommandOrigin = (
        OwnerTruthPersonaAuthorityCommandOrigin.OWNER_INTERACTIVE
    )

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
            "current_persona_version",
            _non_negative_version(self.current_persona_version, field="current_persona_version"),
        )
        object.__setattr__(
            self,
            "authority_epoch",
            _non_negative_version(self.authority_epoch, field="authority_epoch"),
        )
        try:
            subject_kind = OwnerTruthPersonaAuthoritySubjectKind(self.subject_kind)
        except ValueError as exc:
            raise OwnerTruthPersonaAuthorityCommandContractError(
                "subject_kind is unsupported",
                reason_code="invalidPersonaSubjectKind",
            ) from exc
        object.__setattr__(self, "subject_kind", subject_kind)
        object.__setattr__(
            self,
            "policy_version",
            _identifier(self.policy_version, field="policy_version"),
        )
        try:
            command_origin = OwnerTruthPersonaAuthorityCommandOrigin(self.command_origin)
        except ValueError as exc:
            raise OwnerTruthPersonaAuthorityCommandContractError(
                "command_origin is unsupported",
                reason_code="invalidPersonaAuthorityCommandOrigin",
            ) from exc
        object.__setattr__(self, "command_origin", command_origin)

    def scope_hash(self, *, persona_id: str) -> str:
        return _digest(
            {
                "ownerSubjectId": self.owner_subject_id,
                "personaId": persona_id,
                "vaultId": self.vault_id,
            }
        )


@dataclass(frozen=True)
class OwnerTruthSelfPersonaAuthorityCommand:
    """A strict payload for an eventual immutable Self PersonaVersion writer."""

    command_id: str
    persona_id: str
    expected_version: int
    profile: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", _identifier(self.command_id, field="command_id"))
        object.__setattr__(self, "persona_id", _uuid(self.persona_id, field="persona_id"))
        object.__setattr__(
            self,
            "expected_version",
            _non_negative_version(self.expected_version, field="expected_version"),
        )
        if not isinstance(self.profile, Mapping):
            raise OwnerTruthPersonaAuthorityCommandContractError(
                "profile must be an object",
                reason_code="invalidPersonaProfileEnvelope",
            )
        profile = dict(self.profile)
        keys = {str(key) for key in profile}
        if not keys:
            raise OwnerTruthPersonaAuthorityCommandContractError(
                "profile must contain at least one allowlisted field",
                reason_code="emptyPersonaProfile",
            )
        if keys - PERSONA_PROFILE_ALLOWED_FIELD_NAMES:
            raise OwnerTruthPersonaAuthorityCommandContractError(
                "profile contains a field outside the Persona V1 allowlist",
                reason_code="personaProfileFieldNotAllowed",
            )
        normalized_profile = {
            key: _normalize_profile_value(key, profile[key])
            for key in sorted(keys)
        }
        object.__setattr__(self, "profile", normalized_profile)

    @classmethod
    def from_payload(cls, payload: Mapping[str, object] | object) -> "OwnerTruthSelfPersonaAuthorityCommand":
        if not isinstance(payload, Mapping):
            raise OwnerTruthPersonaAuthorityCommandContractError(
                "persona authority command must be an object",
                reason_code="invalidPersonaAuthorityEnvelope",
            )
        raw = dict(payload)
        keys = {str(key) for key in raw}
        if keys != _COMMAND_FIELD_NAMES:
            raise OwnerTruthPersonaAuthorityCommandContractError(
                "persona authority command fields must match the V1 contract",
                reason_code="personaAuthorityFieldNotAllowed",
            )
        return cls(
            command_id=raw["commandId"],
            persona_id=raw["personaId"],
            expected_version=raw["expectedVersion"],
            profile=raw["profile"],
        )

    @property
    def command_hash(self) -> str:
        return _digest({"commandId": self.command_id})

    @property
    def payload_hash(self) -> str:
        return _digest(
            {
                "expectedVersion": self.expected_version,
                "personaId": self.persona_id,
                "profile": dict(self.profile),
            }
        )

    @property
    def candidate_fields(self) -> tuple[str, ...]:
        return tuple(sorted(self.profile))


@dataclass(frozen=True)
class OwnerTruthPersonaAuthorityCommandShadow:
    """Value-free outcome of a non-mutating Persona Authority preflight."""

    enabled: bool
    disposition: OwnerTruthPersonaAuthorityCommandDisposition
    reason_codes: tuple[str, ...]
    candidate_fields: tuple[str, ...] = ()
    expected_version: int | None = None
    observed_version: int | None = None
    scope_hash: str | None = None
    command_hash: str | None = None
    payload_hash: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise OwnerTruthPersonaAuthorityCommandContractError("enabled must be a boolean")
        if not isinstance(self.disposition, OwnerTruthPersonaAuthorityCommandDisposition):
            raise OwnerTruthPersonaAuthorityCommandContractError("disposition is required")
        normalized_reasons = tuple(
            sorted({_identifier(reason, field="reason_code") for reason in self.reason_codes})
        )
        if not normalized_reasons:
            raise OwnerTruthPersonaAuthorityCommandContractError("at least one reason code is required")
        object.__setattr__(self, "reason_codes", normalized_reasons)
        normalized_fields = tuple(sorted(set(self.candidate_fields)))
        if any(field not in PERSONA_PROFILE_ALLOWED_FIELD_NAMES for field in normalized_fields):
            raise OwnerTruthPersonaAuthorityCommandContractError(
                "candidate field is outside the Persona V1 allowlist"
            )
        object.__setattr__(self, "candidate_fields", normalized_fields)
        for field in ("expected_version", "observed_version"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _non_negative_version(value, field=field))
        for field in ("scope_hash", "command_hash", "payload_hash"):
            value = getattr(self, field)
            if value is not None and not re.fullmatch(r"[0-9a-f]{64}", value):
                raise OwnerTruthPersonaAuthorityCommandContractError(
                    f"{field} must be a SHA-256 digest"
                )

    @property
    def command_accepted_for_future_persistence(self) -> bool:
        return self.disposition is OwnerTruthPersonaAuthorityCommandDisposition.ACCEPTED_FOR_FUTURE_PERSISTENCE

    @property
    def persona_created(self) -> bool:
        return False

    @property
    def persona_version_written(self) -> bool:
        return False

    @property
    def decision_receipt_written(self) -> bool:
        return False

    @property
    def provider_or_runtime_mutated(self) -> bool:
        return False

    def value_free_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {
            "candidatePersonaFields": list(self.candidate_fields),
            "commandAcceptedForFuturePersistence": self.command_accepted_for_future_persistence,
            "decisionReceiptWritten": self.decision_receipt_written,
            "enabled": self.enabled,
            "personaCreated": self.persona_created,
            "personaVersionWritten": self.persona_version_written,
            "providerOrRuntimeMutated": self.provider_or_runtime_mutated,
            "reasonCodes": list(self.reason_codes),
            "schemaVersion": OWNER_TRUTH_PERSONA_AUTHORITY_COMMAND_SCHEMA_VERSION,
            "shadowOnly": True,
            "status": self.disposition.value,
        }
        if self.expected_version is not None:
            summary["expectedVersion"] = self.expected_version
        if self.observed_version is not None:
            summary["observedVersion"] = self.observed_version
        if self.scope_hash is not None:
            summary["scopeHash"] = self.scope_hash
        if self.command_hash is not None:
            summary["commandHash"] = self.command_hash
        if self.payload_hash is not None:
            summary["payloadHash"] = self.payload_hash
        return summary


def _result(
    *,
    enabled: bool,
    disposition: OwnerTruthPersonaAuthorityCommandDisposition,
    reason_codes: tuple[str, ...],
    command: OwnerTruthSelfPersonaAuthorityCommand | None = None,
    context: OwnerTruthPersonaAuthorityCommandContext | None = None,
) -> OwnerTruthPersonaAuthorityCommandShadow:
    return OwnerTruthPersonaAuthorityCommandShadow(
        enabled=enabled,
        disposition=disposition,
        reason_codes=reason_codes,
        candidate_fields=command.candidate_fields if command is not None else (),
        expected_version=command.expected_version if command is not None else None,
        observed_version=context.current_persona_version if command is not None and context is not None else None,
        scope_hash=(
            context.scope_hash(persona_id=command.persona_id)
            if command is not None and context is not None
            else None
        ),
        command_hash=command.command_hash if command is not None else None,
        payload_hash=command.payload_hash if command is not None else None,
    )


def preflight_self_persona_authority_command(
    payload: Mapping[str, object] | object,
    *,
    context: OwnerTruthPersonaAuthorityCommandContext | object,
    enabled: bool = False,
) -> OwnerTruthPersonaAuthorityCommandShadow:
    """Fail closed without creating a Persona, version or DecisionReceipt.

    A later additive writer must re-run all policy checks transactionally and
    persist the actual PersonaVersion/DecisionReceipt under CAS.  This helper
    is intentionally evidence-only and cannot itself grant Persona authority.
    """

    if not enabled:
        return _result(
            enabled=False,
            disposition=OwnerTruthPersonaAuthorityCommandDisposition.SHADOW_DISABLED,
            reason_codes=("shadowDisabled",),
        )
    if not isinstance(context, OwnerTruthPersonaAuthorityCommandContext):
        return _result(
            enabled=True,
            disposition=OwnerTruthPersonaAuthorityCommandDisposition.INVALID_CONTEXT,
            reason_codes=("invalidPersonaAuthorityContext",),
        )
    if context.command_origin is not OwnerTruthPersonaAuthorityCommandOrigin.OWNER_INTERACTIVE:
        return _result(
            enabled=True,
            disposition=OwnerTruthPersonaAuthorityCommandDisposition.ORIGIN_NOT_ALLOWED,
            reason_codes=(_ORIGIN_DENIAL_REASON_CODES[context.command_origin],),
        )
    if context.subject_kind is OwnerTruthPersonaAuthoritySubjectKind.MEMORIAL_REPRESENTED:
        return _result(
            enabled=True,
            disposition=OwnerTruthPersonaAuthorityCommandDisposition.MEMORIAL_CONTROLLER_REQUIRED,
            reason_codes=("deceasedPersonaRequiresControllerNotLoginPrincipal",),
        )
    if context.actor_subject_id != context.owner_subject_id:
        return _result(
            enabled=True,
            disposition=OwnerTruthPersonaAuthorityCommandDisposition.ACTOR_NOT_OWNER,
            reason_codes=("selfPersonaRequiresVaultOwner",),
        )
    try:
        command = OwnerTruthSelfPersonaAuthorityCommand.from_payload(payload)
    except OwnerTruthPersonaAuthorityCommandContractError as exc:
        return _result(
            enabled=True,
            disposition=OwnerTruthPersonaAuthorityCommandDisposition.INVALID_COMMAND,
            reason_codes=(exc.reason_code,),
        )
    if command.expected_version != context.current_persona_version:
        return _result(
            enabled=True,
            disposition=OwnerTruthPersonaAuthorityCommandDisposition.EXPECTED_VERSION_CONFLICT,
            reason_codes=("expectedPersonaVersionMismatch",),
            command=command,
            context=context,
        )
    return _result(
        enabled=True,
        disposition=OwnerTruthPersonaAuthorityCommandDisposition.ACCEPTED_FOR_FUTURE_PERSISTENCE,
        reason_codes=(
            "futureWriterMustPersistVersionAndDecisionReceipt",
            "selfPersonaAllowlistValidated",
            "shadowPreflightDoesNotMutateAuthority",
        ),
        command=command,
        context=context,
    )


__all__ = [
    "OWNER_TRUTH_PERSONA_AUTHORITY_COMMAND_SCHEMA_VERSION",
    "PERSONA_PROFILE_ALLOWED_FIELD_NAMES",
    "OwnerTruthPersonaAuthorityCommandContext",
    "OwnerTruthPersonaAuthorityCommandContractError",
    "OwnerTruthPersonaAuthorityCommandDisposition",
    "OwnerTruthPersonaAuthorityCommandOrigin",
    "OwnerTruthPersonaAuthorityCommandShadow",
    "OwnerTruthPersonaAuthoritySubjectKind",
    "OwnerTruthSelfPersonaAuthorityCommand",
    "preflight_self_persona_authority_command",
]
