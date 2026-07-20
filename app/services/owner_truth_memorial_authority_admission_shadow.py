"""Fail-closed G0 admission model for a future Memorial Persona Authority.

An enrolled MemorialVault must be controlled by a living, verified account
subject. The represented/deceased person is not a login principal, and a
family contributor, assistant, Provider or runtime cannot write Persona
authority. This is a default-off policy model only: it creates no Memorial
aggregate, controller appointment, verification record, claim, hold, Persona
version or DecisionReceipt.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import re
from uuid import UUID


OWNER_TRUTH_MEMORIAL_AUTHORITY_ADMISSION_SHADOW_SCHEMA_VERSION = (
    "owner-truth-memorial-authority-admission-shadow-v1"
)
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")


class MemorialPersonaAuthorityAdmissionContractError(ValueError):
    """Raised when a caller supplies an invalid Memorial Authority envelope."""


class MemorialPersonaAuthorityAdmissionDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    INVALID_ENVELOPE = "invalid_envelope"
    REPRESENTED_LOGIN_PRINCIPAL_FORBIDDEN = "represented_login_principal_forbidden"
    ORIGIN_NOT_ALLOWED = "origin_not_allowed"
    VERIFICATION_REQUIRED = "verification_required"
    RIGHTS_OR_CONFLICT_HOLD = "rights_or_conflict_hold"
    EXTERNAL_G2_G4_REQUIRED = "external_g2_g4_required"


class MemorialPersonaAuthorityCommandOrigin(str, Enum):
    MEMORIAL_CONTROLLER_INTERACTIVE = "memorial_controller_interactive"
    FAMILY_CONTRIBUTOR = "family_contributor"
    ASSISTANT = "assistant"
    PROVIDER = "provider"
    RUNTIME = "runtime"
    UNKNOWN = "unknown"


_ORIGIN_DENIAL_REASON_CODES = {
    MemorialPersonaAuthorityCommandOrigin.FAMILY_CONTRIBUTOR: (
        "familyContributorMayOnlySubmitSourceOrCandidate"
    ),
    MemorialPersonaAuthorityCommandOrigin.ASSISTANT: "assistantCannotWriteMemorialPersona",
    MemorialPersonaAuthorityCommandOrigin.PROVIDER: "providerCannotWriteMemorialPersona",
    MemorialPersonaAuthorityCommandOrigin.RUNTIME: "runtimeCannotWriteMemorialPersona",
    MemorialPersonaAuthorityCommandOrigin.UNKNOWN: "unknownOriginCannotWriteMemorialPersona",
}


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise MemorialPersonaAuthorityAdmissionContractError(
            f"{field} must be an opaque identifier"
        )
    return normalized


def _uuid(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    try:
        return str(UUID(normalized))
    except (TypeError, ValueError) as exc:
        raise MemorialPersonaAuthorityAdmissionContractError(
            f"{field} must be a UUID"
        ) from exc


def _digest(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class MemorialPersonaAuthorityAdmissionContext:
    """Read-only server context for a future Memorial authority command.

    `represented_login_subject_id` is present only to fail closed if a legacy
    adapter attempts to attach the represented/deceased person to a login
    principal. It is not an allowed production field.
    """

    vault_id: str
    represented_persona_id: str
    actor_subject_id: str
    represented_login_subject_id: str | None = None
    command_origin: MemorialPersonaAuthorityCommandOrigin = (
        MemorialPersonaAuthorityCommandOrigin.MEMORIAL_CONTROLLER_INTERACTIVE
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "vault_id", _identifier(self.vault_id, field="vault_id"))
        object.__setattr__(
            self,
            "represented_persona_id",
            _uuid(self.represented_persona_id, field="represented_persona_id"),
        )
        object.__setattr__(
            self,
            "actor_subject_id",
            _identifier(self.actor_subject_id, field="actor_subject_id"),
        )
        if self.represented_login_subject_id is not None:
            object.__setattr__(
                self,
                "represented_login_subject_id",
                _identifier(
                    self.represented_login_subject_id,
                    field="represented_login_subject_id",
                ),
            )
        try:
            command_origin = MemorialPersonaAuthorityCommandOrigin(self.command_origin)
        except ValueError as exc:
            raise MemorialPersonaAuthorityAdmissionContractError(
                "command_origin is unsupported"
            ) from exc
        object.__setattr__(self, "command_origin", command_origin)

    def scope_hash(self) -> str:
        return _digest(
            {
                "personaId": self.represented_persona_id,
                "vaultId": self.vault_id,
            }
        )


@dataclass(frozen=True)
class MemorialPersonaAuthorityAdmissionClaims:
    """Non-authoritative readiness assertions for a future Memorial command.

    A later G2/G4 implementation needs persisted, independently verified
    evidence. These booleans intentionally cannot stand in for records such as
    ControllerAppointment, KinshipDeathVerification, RightsClaim or
    ConflictHold.
    """

    controller_appointment_verified: bool = False
    death_and_kinship_verified: bool = False
    legal_policy_ready: bool = False
    rights_claim_active: bool = False
    conflict_hold_active: bool = False

    def __post_init__(self) -> None:
        for field in (
            "controller_appointment_verified",
            "death_and_kinship_verified",
            "legal_policy_ready",
            "rights_claim_active",
            "conflict_hold_active",
        ):
            if not isinstance(getattr(self, field), bool):
                raise MemorialPersonaAuthorityAdmissionContractError(
                    f"{field} must be a boolean"
                )

    @property
    def prerequisites_asserted(self) -> bool:
        return (
            self.controller_appointment_verified
            and self.death_and_kinship_verified
            and self.legal_policy_ready
            and not self.rights_claim_active
            and not self.conflict_hold_active
        )


@dataclass(frozen=True)
class MemorialPersonaAuthorityAdmissionShadow:
    """Value-free, non-authorizing result for a future Memorial Persona write."""

    enabled: bool
    disposition: MemorialPersonaAuthorityAdmissionDisposition
    reason_codes: tuple[str, ...]
    scope_hash: str | None = None
    prerequisites_asserted: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise MemorialPersonaAuthorityAdmissionContractError("enabled must be a boolean")
        if not isinstance(self.disposition, MemorialPersonaAuthorityAdmissionDisposition):
            raise MemorialPersonaAuthorityAdmissionContractError("disposition is required")
        normalized_reasons = tuple(
            sorted({_identifier(reason, field="reason_code") for reason in self.reason_codes})
        )
        if not normalized_reasons:
            raise MemorialPersonaAuthorityAdmissionContractError("at least one reason code is required")
        object.__setattr__(self, "reason_codes", normalized_reasons)
        if not isinstance(self.prerequisites_asserted, bool):
            raise MemorialPersonaAuthorityAdmissionContractError(
                "prerequisites_asserted must be a boolean"
            )
        if self.scope_hash is not None and not re.fullmatch(r"[0-9a-f]{64}", self.scope_hash):
            raise MemorialPersonaAuthorityAdmissionContractError("scope_hash must be a SHA-256 digest")

    @property
    def memorial_authority_admitted(self) -> bool:
        return False

    @property
    def records_written(self) -> bool:
        return False

    @property
    def persona_version_written(self) -> bool:
        return False

    @property
    def controller_appointment_written(self) -> bool:
        return False

    @property
    def provider_or_runtime_mutated(self) -> bool:
        return False

    def value_free_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {
            "controllerAppointmentWritten": self.controller_appointment_written,
            "enabled": self.enabled,
            "memorialAuthorityAdmitted": self.memorial_authority_admitted,
            "personaVersionWritten": self.persona_version_written,
            "prerequisitesAsserted": self.prerequisites_asserted,
            "providerOrRuntimeMutated": self.provider_or_runtime_mutated,
            "reasonCodes": list(self.reason_codes),
            "recordsWritten": self.records_written,
            "representedPersonaLoginPrincipal": False,
            "requiredExternalGates": ["G2", "G4"],
            "schemaVersion": OWNER_TRUTH_MEMORIAL_AUTHORITY_ADMISSION_SHADOW_SCHEMA_VERSION,
            "shadowOnly": True,
            "status": self.disposition.value,
        }
        if self.scope_hash is not None:
            summary["scopeHash"] = self.scope_hash
        return summary


def observe_memorial_persona_authority_admission(
    context: MemorialPersonaAuthorityAdmissionContext | object,
    *,
    claims: MemorialPersonaAuthorityAdmissionClaims | object | None = None,
    enabled: bool = False,
) -> MemorialPersonaAuthorityAdmissionShadow:
    """Fail closed until a real Memorial aggregate supplies independent proof."""

    if not enabled:
        return MemorialPersonaAuthorityAdmissionShadow(
            enabled=False,
            disposition=MemorialPersonaAuthorityAdmissionDisposition.SHADOW_DISABLED,
            reason_codes=("shadowDisabled",),
        )
    if not isinstance(context, MemorialPersonaAuthorityAdmissionContext):
        return MemorialPersonaAuthorityAdmissionShadow(
            enabled=True,
            disposition=MemorialPersonaAuthorityAdmissionDisposition.INVALID_ENVELOPE,
            reason_codes=("invalidMemorialAuthorityAdmissionContext",),
        )
    if context.represented_login_subject_id is not None:
        return MemorialPersonaAuthorityAdmissionShadow(
            enabled=True,
            disposition=(
                MemorialPersonaAuthorityAdmissionDisposition.REPRESENTED_LOGIN_PRINCIPAL_FORBIDDEN
            ),
            reason_codes=("representedPersonaCannotBeLoginPrincipal",),
            scope_hash=context.scope_hash(),
        )
    if context.command_origin is not MemorialPersonaAuthorityCommandOrigin.MEMORIAL_CONTROLLER_INTERACTIVE:
        return MemorialPersonaAuthorityAdmissionShadow(
            enabled=True,
            disposition=MemorialPersonaAuthorityAdmissionDisposition.ORIGIN_NOT_ALLOWED,
            reason_codes=(_ORIGIN_DENIAL_REASON_CODES[context.command_origin],),
            scope_hash=context.scope_hash(),
        )
    if claims is None:
        claims = MemorialPersonaAuthorityAdmissionClaims()
    if not isinstance(claims, MemorialPersonaAuthorityAdmissionClaims):
        return MemorialPersonaAuthorityAdmissionShadow(
            enabled=True,
            disposition=MemorialPersonaAuthorityAdmissionDisposition.INVALID_ENVELOPE,
            reason_codes=("invalidMemorialAuthorityAdmissionClaims",),
            scope_hash=context.scope_hash(),
        )
    if claims.rights_claim_active or claims.conflict_hold_active:
        reason_codes = set()
        if claims.rights_claim_active:
            reason_codes.add("activeRightsClaimBlocksMemorialAuthorityMutation")
        if claims.conflict_hold_active:
            reason_codes.add("activeConflictHoldBlocksMemorialAuthorityMutation")
        return MemorialPersonaAuthorityAdmissionShadow(
            enabled=True,
            disposition=MemorialPersonaAuthorityAdmissionDisposition.RIGHTS_OR_CONFLICT_HOLD,
            reason_codes=tuple(reason_codes),
            scope_hash=context.scope_hash(),
            prerequisites_asserted=False,
        )
    if not claims.prerequisites_asserted:
        reason_codes = set()
        if not claims.controller_appointment_verified:
            reason_codes.add("verifiedControllerAppointmentRequired")
        if not claims.death_and_kinship_verified:
            reason_codes.add("verifiedDeathAndKinshipEvidenceRequired")
        if not claims.legal_policy_ready:
            reason_codes.add("memorialLegalPolicyRequiresSeparateG4Approval")
        return MemorialPersonaAuthorityAdmissionShadow(
            enabled=True,
            disposition=MemorialPersonaAuthorityAdmissionDisposition.VERIFICATION_REQUIRED,
            reason_codes=tuple(reason_codes),
            scope_hash=context.scope_hash(),
            prerequisites_asserted=False,
        )
    return MemorialPersonaAuthorityAdmissionShadow(
        enabled=True,
        disposition=MemorialPersonaAuthorityAdmissionDisposition.EXTERNAL_G2_G4_REQUIRED,
        reason_codes=(
            "memorialAuthorityRequiresIndependentControllerAndRightsRecords",
            "shadowMemorialClaimsCannotAuthorizeAuthority",
            "transactionalMemorialVersionCasRequiresSeparateG2Proof",
        ),
        scope_hash=context.scope_hash(),
        prerequisites_asserted=True,
    )


__all__ = [
    "OWNER_TRUTH_MEMORIAL_AUTHORITY_ADMISSION_SHADOW_SCHEMA_VERSION",
    "MemorialPersonaAuthorityAdmissionClaims",
    "MemorialPersonaAuthorityAdmissionContext",
    "MemorialPersonaAuthorityAdmissionContractError",
    "MemorialPersonaAuthorityAdmissionDisposition",
    "MemorialPersonaAuthorityAdmissionShadow",
    "MemorialPersonaAuthorityCommandOrigin",
    "observe_memorial_persona_authority_admission",
]
