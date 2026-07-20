"""Default-off immutable-record planning for future Self Persona Authority.

This G0 module converts an already-admitted typed Self Persona command into a
deterministic *future persistence plan*.  The plan is deliberately not a
repository writer: no Persona, PersonaVersion or DecisionReceipt is stored and
no runtime/provider state is read or changed.  A later G2 aggregate must
repeat the policy/CAS checks and atomically persist the immutable records.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import re
from typing import Mapping
from uuid import UUID, uuid5

from app.services.owner_truth_persona_authority_command_shadow import (
    OWNER_TRUTH_PERSONA_AUTHORITY_COMMAND_SCHEMA_VERSION,
    OwnerTruthPersonaAuthorityCommandContext,
    OwnerTruthPersonaAuthorityCommandShadow,
    OwnerTruthPersonaAuthorityCommandDisposition,
    OwnerTruthSelfPersonaAuthorityCommand,
    preflight_self_persona_authority_command,
)


OWNER_TRUTH_PERSONA_AUTHORITY_RECEIPT_SCHEMA_VERSION = (
    "owner-truth-persona-authority-receipt-shadow-v1"
)
OWNER_TRUTH_PERSONA_PROFILE_CONTENT_SCHEMA_VERSION = "persona-profile-v1"
_PERSONA_VERSION_NAMESPACE = UUID("3e012a48-81a0-4bb8-a8db-2b94d2d3a55f")
_DECISION_RECEIPT_NAMESPACE = UUID("4d12c362-2ecf-4d54-a5e1-6b3ef6bd6c31")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class OwnerTruthPersonaAuthorityReceiptContractError(ValueError):
    """Raised when a future immutable Persona record plan is malformed."""


class OwnerTruthPersonaAuthorityReceiptDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    NOT_ADMITTED = "not_admitted"
    PLANNED_FOR_FUTURE_PERSISTENCE = "planned_for_future_persistence"


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise OwnerTruthPersonaAuthorityReceiptContractError(
            f"{field} must be an opaque identifier"
        )
    return normalized


def _uuid(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    try:
        return str(UUID(normalized))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthPersonaAuthorityReceiptContractError(
            f"{field} must be a UUID"
        ) from exc


def _non_negative(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise OwnerTruthPersonaAuthorityReceiptContractError(
            f"{field} must be a non-negative integer"
        )
    return value


def _hash(value: object, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _HASH_PATTERN.fullmatch(normalized):
        raise OwnerTruthPersonaAuthorityReceiptContractError(
            f"{field} must be a SHA-256 digest"
        )
    return normalized


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthPersonaAuthorityReceiptContractError(
            "persona record plan must be JSON serializable"
        ) from exc


def _digest(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class OwnerTruthPersonaVersionPlan:
    """One immutable future PersonaVersion; never a persisted record in G0."""

    version_id: str
    persona_id: str
    decision_receipt_id: str
    version_number: int
    expected_prior_version: int
    profile_hash: str
    command_hash: str
    payload_hash: str
    scope_hash: str
    authority_epoch: int
    policy_version: str
    content_schema_version: str = OWNER_TRUTH_PERSONA_PROFILE_CONTENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "version_id", _uuid(self.version_id, field="version_id"))
        object.__setattr__(self, "persona_id", _uuid(self.persona_id, field="persona_id"))
        object.__setattr__(
            self,
            "decision_receipt_id",
            _uuid(self.decision_receipt_id, field="decision_receipt_id"),
        )
        object.__setattr__(
            self,
            "expected_prior_version",
            _non_negative(self.expected_prior_version, field="expected_prior_version"),
        )
        if isinstance(self.version_number, bool) or not isinstance(self.version_number, int):
            raise OwnerTruthPersonaAuthorityReceiptContractError(
                "version_number must be a positive integer"
            )
        if self.version_number != self.expected_prior_version + 1:
            raise OwnerTruthPersonaAuthorityReceiptContractError(
                "PersonaVersion must advance exactly one expected version"
            )
        for field in ("profile_hash", "command_hash", "payload_hash", "scope_hash"):
            object.__setattr__(self, field, _hash(getattr(self, field), field=field))
        object.__setattr__(
            self,
            "authority_epoch",
            _non_negative(self.authority_epoch, field="authority_epoch"),
        )
        object.__setattr__(self, "policy_version", _identifier(self.policy_version, field="policy_version"))
        object.__setattr__(
            self,
            "content_schema_version",
            _identifier(self.content_schema_version, field="content_schema_version"),
        )


@dataclass(frozen=True)
class OwnerTruthPersonaDecisionReceiptPlan:
    """One immutable, terminal future decision receipt for a PersonaVersion."""

    receipt_id: str
    persona_id: str
    persona_version_id: str
    command_hash: str
    actor_subject_hash: str
    scope_hash: str
    expected_prior_version: int
    after_version: int
    authority_epoch: int
    policy_version: str
    decision: str = "confirm"
    before_state: str = "absent"
    after_state: str = "active"
    is_terminal: bool = True

    def __post_init__(self) -> None:
        for field in ("receipt_id", "persona_id", "persona_version_id"):
            object.__setattr__(self, field, _uuid(getattr(self, field), field=field))
        for field in ("command_hash", "actor_subject_hash", "scope_hash"):
            object.__setattr__(self, field, _hash(getattr(self, field), field=field))
        object.__setattr__(
            self,
            "expected_prior_version",
            _non_negative(self.expected_prior_version, field="expected_prior_version"),
        )
        if isinstance(self.after_version, bool) or not isinstance(self.after_version, int):
            raise OwnerTruthPersonaAuthorityReceiptContractError(
                "after_version must be a positive integer"
            )
        if self.after_version != self.expected_prior_version + 1:
            raise OwnerTruthPersonaAuthorityReceiptContractError(
                "DecisionReceipt must advance exactly one expected version"
            )
        object.__setattr__(
            self,
            "authority_epoch",
            _non_negative(self.authority_epoch, field="authority_epoch"),
        )
        object.__setattr__(self, "policy_version", _identifier(self.policy_version, field="policy_version"))
        if self.decision != "confirm" or not self.is_terminal:
            raise OwnerTruthPersonaAuthorityReceiptContractError(
                "Self Persona receipt must be a terminal confirm decision"
            )
        expected_before_state = "absent" if self.expected_prior_version == 0 else "active"
        if self.before_state != expected_before_state or self.after_state != "active":
            raise OwnerTruthPersonaAuthorityReceiptContractError(
                "Self Persona receipt state transition is invalid"
            )


@dataclass(frozen=True)
class OwnerTruthPersonaAuthorityReceiptShadow:
    """Non-mutating result containing zero or two future immutable records."""

    enabled: bool
    disposition: OwnerTruthPersonaAuthorityReceiptDisposition
    reason_codes: tuple[str, ...]
    preflight: OwnerTruthPersonaAuthorityCommandShadow | None = None
    persona_version: OwnerTruthPersonaVersionPlan | None = None
    decision_receipt: OwnerTruthPersonaDecisionReceiptPlan | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise OwnerTruthPersonaAuthorityReceiptContractError("enabled must be a boolean")
        if not isinstance(self.disposition, OwnerTruthPersonaAuthorityReceiptDisposition):
            raise OwnerTruthPersonaAuthorityReceiptContractError("disposition is required")
        normalized_reasons = tuple(
            sorted({_identifier(reason, field="reason_code") for reason in self.reason_codes})
        )
        if not normalized_reasons:
            raise OwnerTruthPersonaAuthorityReceiptContractError("at least one reason code is required")
        object.__setattr__(self, "reason_codes", normalized_reasons)
        if self.preflight is not None and not isinstance(
            self.preflight, OwnerTruthPersonaAuthorityCommandShadow
        ):
            raise OwnerTruthPersonaAuthorityReceiptContractError("preflight has an unsupported type")
        has_records = self.persona_version is not None or self.decision_receipt is not None
        if self.disposition is OwnerTruthPersonaAuthorityReceiptDisposition.PLANNED_FOR_FUTURE_PERSISTENCE:
            if self.preflight is None or not self.preflight.command_accepted_for_future_persistence:
                raise OwnerTruthPersonaAuthorityReceiptContractError(
                    "planned Persona records require an admitted command preflight"
                )
            if self.persona_version is None or self.decision_receipt is None:
                raise OwnerTruthPersonaAuthorityReceiptContractError(
                    "planned Persona records require both version and decision receipt"
                )
            if self.persona_version.decision_receipt_id != self.decision_receipt.receipt_id:
                raise OwnerTruthPersonaAuthorityReceiptContractError(
                    "PersonaVersion must reference its matching DecisionReceipt"
                )
            if self.persona_version.version_id != self.decision_receipt.persona_version_id:
                raise OwnerTruthPersonaAuthorityReceiptContractError(
                    "DecisionReceipt must reference its matching PersonaVersion"
                )
            if self.persona_version.persona_id != self.decision_receipt.persona_id:
                raise OwnerTruthPersonaAuthorityReceiptContractError(
                    "PersonaVersion and DecisionReceipt must target one Persona"
                )
            if self.persona_version.version_number != self.decision_receipt.after_version:
                raise OwnerTruthPersonaAuthorityReceiptContractError(
                    "PersonaVersion and DecisionReceipt version numbers must match"
                )
            if self.persona_version.authority_epoch != self.decision_receipt.authority_epoch:
                raise OwnerTruthPersonaAuthorityReceiptContractError(
                    "PersonaVersion and DecisionReceipt authority epochs must match"
                )
            if self.persona_version.command_hash != self.decision_receipt.command_hash:
                raise OwnerTruthPersonaAuthorityReceiptContractError(
                    "PersonaVersion and DecisionReceipt command hashes must match"
                )
        elif has_records:
            raise OwnerTruthPersonaAuthorityReceiptContractError(
                "non-admitted Persona receipt plan must not contain future records"
            )

    @property
    def future_persistence_required(self) -> bool:
        return self.disposition is OwnerTruthPersonaAuthorityReceiptDisposition.PLANNED_FOR_FUTURE_PERSISTENCE

    @property
    def records_written(self) -> bool:
        return False

    @property
    def persona_created(self) -> bool:
        return False

    @property
    def persona_version_written(self) -> bool:
        return False

    @property
    def decision_receipt_written(self) -> bool:
        return False

    def value_free_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {
            "decisionReceiptPlanned": self.decision_receipt is not None,
            "decisionReceiptWritten": self.decision_receipt_written,
            "enabled": self.enabled,
            "futurePersistenceRequired": self.future_persistence_required,
            "personaCreated": self.persona_created,
            "personaVersionPlanned": self.persona_version is not None,
            "personaVersionWritten": self.persona_version_written,
            "reasonCodes": list(self.reason_codes),
            "recordsWritten": self.records_written,
            "schemaVersion": OWNER_TRUTH_PERSONA_AUTHORITY_RECEIPT_SCHEMA_VERSION,
            "shadowOnly": True,
            "status": self.disposition.value,
        }
        if self.preflight is not None:
            summary["preflightStatus"] = self.preflight.disposition.value
        if self.persona_version is not None and self.decision_receipt is not None:
            summary.update(
                {
                    "authorityEpoch": self.persona_version.authority_epoch,
                    "commandHash": self.persona_version.command_hash,
                    "decisionReceiptHash": sha256(
                        self.decision_receipt.receipt_id.encode("utf-8")
                    ).hexdigest(),
                    "expectedPriorVersion": self.persona_version.expected_prior_version,
                    "payloadHash": self.persona_version.payload_hash,
                    "personaVersionNumber": self.persona_version.version_number,
                    "profileHash": self.persona_version.profile_hash,
                    "scopeHash": self.persona_version.scope_hash,
                }
            )
        return summary


def _not_admitted_plan(
    preflight: OwnerTruthPersonaAuthorityCommandShadow,
) -> OwnerTruthPersonaAuthorityReceiptShadow:
    return OwnerTruthPersonaAuthorityReceiptShadow(
        enabled=True,
        disposition=OwnerTruthPersonaAuthorityReceiptDisposition.NOT_ADMITTED,
        reason_codes=("personaAuthorityPreflightNotAdmitted",),
        preflight=preflight,
    )


def plan_self_persona_authority_receipt(
    payload: Mapping[str, object] | object,
    *,
    context: OwnerTruthPersonaAuthorityCommandContext | object,
    enabled: bool = False,
) -> OwnerTruthPersonaAuthorityReceiptShadow:
    """Plan immutable future records after a typed preflight, with zero writes."""

    if not enabled:
        return OwnerTruthPersonaAuthorityReceiptShadow(
            enabled=False,
            disposition=OwnerTruthPersonaAuthorityReceiptDisposition.SHADOW_DISABLED,
            reason_codes=("shadowDisabled",),
        )
    preflight = preflight_self_persona_authority_command(
        payload,
        context=context,
        enabled=True,
    )
    if not preflight.command_accepted_for_future_persistence:
        return _not_admitted_plan(preflight)
    if not isinstance(context, OwnerTruthPersonaAuthorityCommandContext):
        raise OwnerTruthPersonaAuthorityReceiptContractError(
            "admitted command requires a typed Persona Authority context"
        )
    command = OwnerTruthSelfPersonaAuthorityCommand.from_payload(payload)
    version_number = command.expected_version + 1
    receipt_id = str(
        uuid5(
            _DECISION_RECEIPT_NAMESPACE,
            f"persona-decision:{context.vault_id}:{command.persona_id}:{command.command_hash}",
        )
    )
    version_id = str(
        uuid5(
            _PERSONA_VERSION_NAMESPACE,
            f"persona-version:{context.vault_id}:{command.persona_id}:{version_number}:{command.command_hash}",
        )
    )
    profile_hash = _digest({"profile": dict(command.profile)})
    scope_hash = context.scope_hash(persona_id=command.persona_id)
    version = OwnerTruthPersonaVersionPlan(
        version_id=version_id,
        persona_id=command.persona_id,
        decision_receipt_id=receipt_id,
        version_number=version_number,
        expected_prior_version=command.expected_version,
        profile_hash=profile_hash,
        command_hash=command.command_hash,
        payload_hash=command.payload_hash,
        scope_hash=scope_hash,
        authority_epoch=context.authority_epoch,
        policy_version=context.policy_version,
    )
    receipt = OwnerTruthPersonaDecisionReceiptPlan(
        receipt_id=receipt_id,
        persona_id=command.persona_id,
        persona_version_id=version_id,
        command_hash=command.command_hash,
        actor_subject_hash=sha256(context.actor_subject_id.encode("utf-8")).hexdigest(),
        scope_hash=scope_hash,
        expected_prior_version=command.expected_version,
        after_version=version_number,
        authority_epoch=context.authority_epoch,
        policy_version=context.policy_version,
        before_state="absent" if command.expected_version == 0 else "active",
    )
    return OwnerTruthPersonaAuthorityReceiptShadow(
        enabled=True,
        disposition=OwnerTruthPersonaAuthorityReceiptDisposition.PLANNED_FOR_FUTURE_PERSISTENCE,
        reason_codes=(
            "futureWriterMustAtomicallyPersistPersonaVersionAndDecisionReceipt",
            "personaAuthorityPreflightAdmitted",
            "shadowReceiptPlanDoesNotWriteAuthority",
        ),
        preflight=preflight,
        persona_version=version,
        decision_receipt=receipt,
    )


__all__ = [
    "OWNER_TRUTH_PERSONA_AUTHORITY_RECEIPT_SCHEMA_VERSION",
    "OWNER_TRUTH_PERSONA_PROFILE_CONTENT_SCHEMA_VERSION",
    "OwnerTruthPersonaAuthorityReceiptContractError",
    "OwnerTruthPersonaAuthorityReceiptDisposition",
    "OwnerTruthPersonaAuthorityReceiptShadow",
    "OwnerTruthPersonaDecisionReceiptPlan",
    "OwnerTruthPersonaVersionPlan",
    "plan_self_persona_authority_receipt",
]
