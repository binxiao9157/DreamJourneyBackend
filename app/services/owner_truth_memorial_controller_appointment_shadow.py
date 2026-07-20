"""Default-off G0 plans for a future Memorial primary-controller command.

This module models only the command boundary for a later
MemorialControllerAppointment aggregate. It does not create a MemorialVault,
store proof, write an appointment, revoke an appointment or change an
authority epoch. In particular, a client cannot select the represented Persona
or either controller subject through its command payload; those identities are
server-resolved typed context.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import re
from typing import Mapping
from uuid import UUID, uuid5

from app.services.owner_truth_memorial_authority_admission_shadow import (
    MemorialPersonaAuthorityCommandOrigin,
)


OWNER_TRUTH_MEMORIAL_CONTROLLER_APPOINTMENT_SHADOW_SCHEMA_VERSION = (
    "owner-truth-memorial-controller-appointment-shadow-v1"
)
_CONTROLLER_APPOINTMENT_NAMESPACE = UUID("cab6f9ed-e3b4-47be-84b1-a7b4bb88cc9d")
_CONTROLLER_RECEIPT_NAMESPACE = UUID("20f21e85-1c9d-4066-abda-5a01da248d9a")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_COMMAND_FIELD_NAMES = frozenset({"commandId", "expectedVersion", "operation"})


class MemorialControllerAppointmentContractError(ValueError):
    """Raised when a future Memorial controller command is malformed."""


class MemorialControllerAppointmentCommandDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    INVALID_CONTEXT = "invalid_context"
    INVALID_CLAIMS = "invalid_claims"
    INVALID_COMMAND = "invalid_command"
    REPRESENTED_LOGIN_PRINCIPAL_FORBIDDEN = "represented_login_principal_forbidden"
    ORIGIN_NOT_ALLOWED = "origin_not_allowed"
    ACTOR_NOT_CURRENT_CONTROLLER = "actor_not_current_controller"
    EXPECTED_VERSION_CONFLICT = "expected_version_conflict"
    PRIMARY_CONTROLLER_STATE_CONFLICT = "primary_controller_state_conflict"
    VERIFICATION_REQUIRED = "verification_required"
    RIGHTS_OR_CONFLICT_HOLD = "rights_or_conflict_hold"
    PLANNED_FOR_FUTURE_PERSISTENCE = "planned_for_future_persistence"


class MemorialControllerAppointmentOperation(str, Enum):
    BOOTSTRAP = "bootstrap"
    TRANSFER = "transfer"


_ORIGIN_DENIAL_REASON_CODES = {
    MemorialPersonaAuthorityCommandOrigin.FAMILY_CONTRIBUTOR: (
        "familyContributorMayOnlySubmitSourceOrCandidate"
    ),
    MemorialPersonaAuthorityCommandOrigin.ASSISTANT: "assistantCannotWriteMemorialController",
    MemorialPersonaAuthorityCommandOrigin.PROVIDER: "providerCannotWriteMemorialController",
    MemorialPersonaAuthorityCommandOrigin.RUNTIME: "runtimeCannotWriteMemorialController",
    MemorialPersonaAuthorityCommandOrigin.UNKNOWN: "unknownOriginCannotWriteMemorialController",
}


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise MemorialControllerAppointmentContractError(f"{field} must be an opaque identifier")
    return normalized


def _uuid(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    try:
        return str(UUID(normalized))
    except (TypeError, ValueError) as exc:
        raise MemorialControllerAppointmentContractError(f"{field} must be a UUID") from exc


def _non_negative(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MemorialControllerAppointmentContractError(f"{field} must be a non-negative integer")
    return value


def _digest(value: object) -> str:
    try:
        canonical = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise MemorialControllerAppointmentContractError(
            "controller appointment material must be JSON serializable"
        ) from exc
    return sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MemorialControllerAppointmentCommandContext:
    """Read-only, server-resolved inputs for a future controller command."""

    vault_id: str
    represented_persona_id: str
    actor_subject_id: str
    resolved_next_controller_subject_id: str
    current_appointment_version: int
    authority_epoch: int
    current_primary_controller_subject_id: str | None = None
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
        object.__setattr__(
            self,
            "resolved_next_controller_subject_id",
            _identifier(
                self.resolved_next_controller_subject_id,
                field="resolved_next_controller_subject_id",
            ),
        )
        object.__setattr__(
            self,
            "current_appointment_version",
            _non_negative(self.current_appointment_version, field="current_appointment_version"),
        )
        object.__setattr__(
            self,
            "authority_epoch",
            _non_negative(self.authority_epoch, field="authority_epoch"),
        )
        if self.current_primary_controller_subject_id is not None:
            object.__setattr__(
                self,
                "current_primary_controller_subject_id",
                _identifier(
                    self.current_primary_controller_subject_id,
                    field="current_primary_controller_subject_id",
                ),
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
            raise MemorialControllerAppointmentContractError(
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
class MemorialControllerAppointmentClaims:
    """Untrusted assertions that cannot replace persisted verification records."""

    actor_identity_verified: bool = False
    next_controller_identity_verified: bool = False
    death_and_kinship_verified: bool = False
    legal_policy_ready: bool = False
    rights_claim_active: bool = False
    conflict_hold_active: bool = False

    def __post_init__(self) -> None:
        for field in (
            "actor_identity_verified",
            "next_controller_identity_verified",
            "death_and_kinship_verified",
            "legal_policy_ready",
            "rights_claim_active",
            "conflict_hold_active",
        ):
            if not isinstance(getattr(self, field), bool):
                raise MemorialControllerAppointmentContractError(f"{field} must be a boolean")

    @property
    def prerequisites_asserted(self) -> bool:
        return (
            self.actor_identity_verified
            and self.next_controller_identity_verified
            and self.death_and_kinship_verified
            and self.legal_policy_ready
            and not self.rights_claim_active
            and not self.conflict_hold_active
        )


@dataclass(frozen=True)
class MemorialControllerAppointmentCommand:
    """Strict client command without Vault, Persona or controller identity fields."""

    command_id: str
    expected_version: int
    operation: MemorialControllerAppointmentOperation

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", _identifier(self.command_id, field="command_id"))
        object.__setattr__(
            self,
            "expected_version",
            _non_negative(self.expected_version, field="expected_version"),
        )
        try:
            operation = MemorialControllerAppointmentOperation(self.operation)
        except ValueError as exc:
            raise MemorialControllerAppointmentContractError("operation is unsupported") from exc
        object.__setattr__(self, "operation", operation)

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, object] | object,
    ) -> "MemorialControllerAppointmentCommand":
        if not isinstance(payload, Mapping):
            raise MemorialControllerAppointmentContractError("controller command must be an object")
        raw = dict(payload)
        if {str(key) for key in raw} != _COMMAND_FIELD_NAMES:
            raise MemorialControllerAppointmentContractError(
                "controller command fields must match the V1 contract"
            )
        return cls(
            command_id=raw["commandId"],
            expected_version=raw["expectedVersion"],
            operation=raw["operation"],
        )

    @property
    def command_hash(self) -> str:
        return _digest({"commandId": self.command_id})

    @property
    def payload_hash(self) -> str:
        return _digest(
            {
                "expectedVersion": self.expected_version,
                "operation": self.operation.value,
            }
        )


@dataclass(frozen=True)
class MemorialControllerAppointmentPlan:
    """Future immutable controller appointment/receipt plan; never stored in G0."""

    appointment_id: str
    decision_receipt_id: str
    represented_persona_id: str
    next_controller_subject_id: str
    previous_controller_subject_id: str | None
    operation: MemorialControllerAppointmentOperation
    expected_prior_version: int
    after_version: int
    captured_authority_epoch: int
    command_hash: str
    payload_hash: str
    scope_hash: str
    requires_atomic_prior_revoke: bool
    requires_session_and_grant_revoke: bool
    requires_high_risk_capability_reevaluation: bool

    def __post_init__(self) -> None:
        for field in ("appointment_id", "decision_receipt_id", "represented_persona_id"):
            object.__setattr__(self, field, _uuid(getattr(self, field), field=field))
        object.__setattr__(
            self,
            "next_controller_subject_id",
            _identifier(self.next_controller_subject_id, field="next_controller_subject_id"),
        )
        if self.previous_controller_subject_id is not None:
            object.__setattr__(
                self,
                "previous_controller_subject_id",
                _identifier(self.previous_controller_subject_id, field="previous_controller_subject_id"),
            )
        try:
            operation = MemorialControllerAppointmentOperation(self.operation)
        except ValueError as exc:
            raise MemorialControllerAppointmentContractError("plan operation is unsupported") from exc
        object.__setattr__(self, "operation", operation)
        for field in ("expected_prior_version", "captured_authority_epoch"):
            object.__setattr__(self, field, _non_negative(getattr(self, field), field=field))
        if isinstance(self.after_version, bool) or not isinstance(self.after_version, int):
            raise MemorialControllerAppointmentContractError("after_version must be a positive integer")
        if self.after_version != self.expected_prior_version + 1:
            raise MemorialControllerAppointmentContractError(
                "appointment version must advance exactly one expected version"
            )
        for field in ("command_hash", "payload_hash", "scope_hash"):
            value = getattr(self, field)
            if not re.fullmatch(r"[0-9a-f]{64}", value):
                raise MemorialControllerAppointmentContractError(
                    f"{field} must be a SHA-256 digest"
                )
        for field in (
            "requires_atomic_prior_revoke",
            "requires_session_and_grant_revoke",
            "requires_high_risk_capability_reevaluation",
        ):
            if not isinstance(getattr(self, field), bool):
                raise MemorialControllerAppointmentContractError(f"{field} must be a boolean")
        if self.operation is MemorialControllerAppointmentOperation.BOOTSTRAP:
            if (
                self.previous_controller_subject_id is not None
                or self.requires_atomic_prior_revoke
                or self.requires_session_and_grant_revoke
                or self.requires_high_risk_capability_reevaluation
            ):
                raise MemorialControllerAppointmentContractError(
                    "bootstrap cannot revoke a prior primary controller"
                )
        elif (
            self.previous_controller_subject_id is None
            or not self.requires_atomic_prior_revoke
            or not self.requires_session_and_grant_revoke
            or not self.requires_high_risk_capability_reevaluation
        ):
            raise MemorialControllerAppointmentContractError(
                "transfer must revoke prior access and re-evaluate high-risk capability"
            )

    @property
    def authority_epoch_changed(self) -> bool:
        return False


@dataclass(frozen=True)
class MemorialControllerAppointmentShadow:
    """Value-free result that cannot be treated as a committed appointment."""

    enabled: bool
    disposition: MemorialControllerAppointmentCommandDisposition
    reason_codes: tuple[str, ...]
    appointment_plan: MemorialControllerAppointmentPlan | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise MemorialControllerAppointmentContractError("enabled must be a boolean")
        if not isinstance(self.disposition, MemorialControllerAppointmentCommandDisposition):
            raise MemorialControllerAppointmentContractError("disposition is required")
        normalized_reasons = tuple(
            sorted({_identifier(reason, field="reason_code") for reason in self.reason_codes})
        )
        if not normalized_reasons:
            raise MemorialControllerAppointmentContractError("at least one reason code is required")
        object.__setattr__(self, "reason_codes", normalized_reasons)
        if self.appointment_plan is not None and not isinstance(
            self.appointment_plan,
            MemorialControllerAppointmentPlan,
        ):
            raise MemorialControllerAppointmentContractError("appointment_plan has an unsupported type")
        if self.disposition is MemorialControllerAppointmentCommandDisposition.PLANNED_FOR_FUTURE_PERSISTENCE:
            if self.appointment_plan is None:
                raise MemorialControllerAppointmentContractError(
                    "planned appointment requires a future appointment plan"
                )
        elif self.appointment_plan is not None:
            raise MemorialControllerAppointmentContractError(
                "non-planned appointment result must not contain a future plan"
            )

    @property
    def future_persistence_required(self) -> bool:
        return self.disposition is MemorialControllerAppointmentCommandDisposition.PLANNED_FOR_FUTURE_PERSISTENCE

    @property
    def records_written(self) -> bool:
        return False

    @property
    def controller_appointment_written(self) -> bool:
        return False

    @property
    def persona_version_written(self) -> bool:
        return False

    @property
    def provider_or_runtime_mutated(self) -> bool:
        return False

    def value_free_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {
            "controllerAppointmentPlanned": self.appointment_plan is not None,
            "controllerAppointmentWritten": self.controller_appointment_written,
            "authorityEpochChanged": False,
            "enabled": self.enabled,
            "futurePersistenceRequired": self.future_persistence_required,
            "personaVersionWritten": self.persona_version_written,
            "providerOrRuntimeMutated": self.provider_or_runtime_mutated,
            "reasonCodes": list(self.reason_codes),
            "recordsWritten": self.records_written,
            "requiredExternalGates": ["G2", "G4"],
            "schemaVersion": OWNER_TRUTH_MEMORIAL_CONTROLLER_APPOINTMENT_SHADOW_SCHEMA_VERSION,
            "shadowOnly": True,
            "status": self.disposition.value,
        }
        if self.appointment_plan is not None:
            summary.update(
                {
                    "appointmentReceiptHash": sha256(
                        self.appointment_plan.decision_receipt_id.encode("utf-8")
                    ).hexdigest(),
                    "atomicPriorRevokeRequired": self.appointment_plan.requires_atomic_prior_revoke,
                    "commandHash": self.appointment_plan.command_hash,
                    "capturedAuthorityEpoch": self.appointment_plan.captured_authority_epoch,
                    "expectedPriorVersion": self.appointment_plan.expected_prior_version,
                    "operation": self.appointment_plan.operation.value,
                    "payloadHash": self.appointment_plan.payload_hash,
                    "requiresHighRiskCapabilityReevaluation": (
                        self.appointment_plan.requires_high_risk_capability_reevaluation
                    ),
                    "requiresSessionAndGrantRevoke": (
                        self.appointment_plan.requires_session_and_grant_revoke
                    ),
                    "scopeHash": self.appointment_plan.scope_hash,
                }
            )
        return summary


def _result(
    *,
    disposition: MemorialControllerAppointmentCommandDisposition,
    reason_codes: tuple[str, ...],
    appointment_plan: MemorialControllerAppointmentPlan | None = None,
    enabled: bool = True,
) -> MemorialControllerAppointmentShadow:
    return MemorialControllerAppointmentShadow(
        enabled=enabled,
        disposition=disposition,
        reason_codes=reason_codes,
        appointment_plan=appointment_plan,
    )


def _claim_failure(
    claims: MemorialControllerAppointmentClaims,
) -> tuple[MemorialControllerAppointmentCommandDisposition, tuple[str, ...]] | None:
    if claims.rights_claim_active or claims.conflict_hold_active:
        reasons = set()
        if claims.rights_claim_active:
            reasons.add("activeRightsClaimBlocksMemorialControllerMutation")
        if claims.conflict_hold_active:
            reasons.add("activeConflictHoldBlocksMemorialControllerMutation")
        return MemorialControllerAppointmentCommandDisposition.RIGHTS_OR_CONFLICT_HOLD, tuple(reasons)
    if not claims.prerequisites_asserted:
        reasons = set()
        if not claims.actor_identity_verified:
            reasons.add("verifiedActorIdentityRequired")
        if not claims.next_controller_identity_verified:
            reasons.add("verifiedNextControllerIdentityRequired")
        if not claims.death_and_kinship_verified:
            reasons.add("verifiedDeathAndKinshipEvidenceRequired")
        if not claims.legal_policy_ready:
            reasons.add("memorialLegalPolicyRequiresSeparateG4Approval")
        return MemorialControllerAppointmentCommandDisposition.VERIFICATION_REQUIRED, tuple(reasons)
    return None


def _state_failure(
    command: MemorialControllerAppointmentCommand,
    context: MemorialControllerAppointmentCommandContext,
) -> tuple[MemorialControllerAppointmentCommandDisposition, tuple[str, ...]] | None:
    current = context.current_primary_controller_subject_id
    if command.operation is MemorialControllerAppointmentOperation.BOOTSTRAP:
        if current is not None or context.current_appointment_version != 0:
            return (
                MemorialControllerAppointmentCommandDisposition.PRIMARY_CONTROLLER_STATE_CONFLICT,
                ("bootstrapRequiresNoActivePrimaryController",),
            )
        if context.actor_subject_id != context.resolved_next_controller_subject_id:
            return (
                MemorialControllerAppointmentCommandDisposition.ACTOR_NOT_CURRENT_CONTROLLER,
                ("bootstrapRequiresActorToBeResolvedPrimaryController",),
            )
        return None
    if current is None or context.current_appointment_version == 0:
        return (
            MemorialControllerAppointmentCommandDisposition.PRIMARY_CONTROLLER_STATE_CONFLICT,
            ("transferRequiresActivePrimaryController",),
        )
    if context.actor_subject_id != current:
        return (
            MemorialControllerAppointmentCommandDisposition.ACTOR_NOT_CURRENT_CONTROLLER,
            ("transferRequiresCurrentPrimaryController",),
        )
    if context.resolved_next_controller_subject_id == current:
        return (
            MemorialControllerAppointmentCommandDisposition.PRIMARY_CONTROLLER_STATE_CONFLICT,
            ("transferRequiresDifferentResolvedController",),
        )
    return None


def plan_memorial_controller_appointment(
    payload: Mapping[str, object] | object,
    *,
    context: MemorialControllerAppointmentCommandContext | object,
    claims: MemorialControllerAppointmentClaims | object | None = None,
    enabled: bool = False,
) -> MemorialControllerAppointmentShadow:
    """Plan a future controller bootstrap/transfer with zero writes in G0."""

    if not enabled:
        return _result(
            enabled=False,
            disposition=MemorialControllerAppointmentCommandDisposition.SHADOW_DISABLED,
            reason_codes=("shadowDisabled",),
        )
    if not isinstance(context, MemorialControllerAppointmentCommandContext):
        return _result(
            disposition=MemorialControllerAppointmentCommandDisposition.INVALID_CONTEXT,
            reason_codes=("invalidMemorialControllerContext",),
        )
    if context.represented_login_subject_id is not None:
        return _result(
            disposition=(
                MemorialControllerAppointmentCommandDisposition.REPRESENTED_LOGIN_PRINCIPAL_FORBIDDEN
            ),
            reason_codes=("representedPersonaCannotBeLoginPrincipal",),
        )
    if context.command_origin is not MemorialPersonaAuthorityCommandOrigin.MEMORIAL_CONTROLLER_INTERACTIVE:
        return _result(
            disposition=MemorialControllerAppointmentCommandDisposition.ORIGIN_NOT_ALLOWED,
            reason_codes=(_ORIGIN_DENIAL_REASON_CODES[context.command_origin],),
        )
    if claims is None:
        claims = MemorialControllerAppointmentClaims()
    if not isinstance(claims, MemorialControllerAppointmentClaims):
        return _result(
            disposition=MemorialControllerAppointmentCommandDisposition.INVALID_CLAIMS,
            reason_codes=("invalidMemorialControllerClaims",),
        )
    claim_failure = _claim_failure(claims)
    if claim_failure is not None:
        disposition, reason_codes = claim_failure
        return _result(disposition=disposition, reason_codes=reason_codes)
    try:
        command = MemorialControllerAppointmentCommand.from_payload(payload)
    except MemorialControllerAppointmentContractError:
        return _result(
            disposition=MemorialControllerAppointmentCommandDisposition.INVALID_COMMAND,
            reason_codes=("invalidMemorialControllerCommand",),
        )
    if command.expected_version != context.current_appointment_version:
        return _result(
            disposition=MemorialControllerAppointmentCommandDisposition.EXPECTED_VERSION_CONFLICT,
            reason_codes=("expectedMemorialControllerVersionMismatch",),
        )
    state_failure = _state_failure(command, context)
    if state_failure is not None:
        disposition, reason_codes = state_failure
        return _result(disposition=disposition, reason_codes=reason_codes)

    scope_hash = context.scope_hash()
    requires_revoke = command.operation is MemorialControllerAppointmentOperation.TRANSFER
    previous_controller = context.current_primary_controller_subject_id if requires_revoke else None
    server_binding = {
        "commandHash": command.command_hash,
        "expectedVersion": command.expected_version,
        "nextControllerSubjectId": context.resolved_next_controller_subject_id,
        "operation": command.operation.value,
        "previousControllerSubjectId": previous_controller,
        "scopeHash": scope_hash,
    }
    server_binding_hash = _digest(server_binding)
    receipt_id = str(
        uuid5(
            _CONTROLLER_RECEIPT_NAMESPACE,
            f"memorial-controller-receipt:{server_binding_hash}",
        )
    )
    appointment_id = str(
        uuid5(
            _CONTROLLER_APPOINTMENT_NAMESPACE,
            f"memorial-controller:{server_binding_hash}",
        )
    )
    plan = MemorialControllerAppointmentPlan(
        appointment_id=appointment_id,
        decision_receipt_id=receipt_id,
        represented_persona_id=context.represented_persona_id,
        next_controller_subject_id=context.resolved_next_controller_subject_id,
        previous_controller_subject_id=previous_controller,
        operation=command.operation,
        expected_prior_version=command.expected_version,
        after_version=command.expected_version + 1,
        captured_authority_epoch=context.authority_epoch,
        command_hash=command.command_hash,
        payload_hash=command.payload_hash,
        scope_hash=scope_hash,
        requires_atomic_prior_revoke=requires_revoke,
        requires_session_and_grant_revoke=requires_revoke,
        requires_high_risk_capability_reevaluation=requires_revoke,
    )
    return _result(
        disposition=MemorialControllerAppointmentCommandDisposition.PLANNED_FOR_FUTURE_PERSISTENCE,
        reason_codes=(
            "futureWriterMustAtomicallyRevokeAndActivatePrimaryController",
            "futureWriterMustPersistMemorialControllerAppointmentAndDecisionReceipt",
            "shadowControllerPlanDoesNotWriteAuthority",
        ),
        appointment_plan=plan,
    )


__all__ = [
    "OWNER_TRUTH_MEMORIAL_CONTROLLER_APPOINTMENT_SHADOW_SCHEMA_VERSION",
    "MemorialControllerAppointmentClaims",
    "MemorialControllerAppointmentCommand",
    "MemorialControllerAppointmentCommandContext",
    "MemorialControllerAppointmentCommandDisposition",
    "MemorialControllerAppointmentContractError",
    "MemorialControllerAppointmentOperation",
    "MemorialControllerAppointmentPlan",
    "MemorialControllerAppointmentShadow",
    "plan_memorial_controller_appointment",
]
