"""Fail-closed G0 model for future Self Persona persistence admission.

The Persona Authority command and immutable record planner intentionally stop
before persistence. This companion shadow makes that final boundary explicit:
an admitted future record plan is useful input to a later G2 writer, but it is
not permission to create a table, write a PersonaVersion or store a terminal
DecisionReceipt.

This module has no route, repository, database, effect or provider dependency.
It is default-off and exposes only value-free evidence. A later G2/G4 change
must provide its own additive schema, transaction/CAS, uniqueness and rights
evidence; boolean claims supplied to this shadow cannot authorize a write.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re

from app.services.owner_truth_persona_authority_receipt_shadow import (
    OwnerTruthPersonaAuthorityReceiptDisposition,
    OwnerTruthPersonaAuthorityReceiptShadow,
)


OWNER_TRUTH_PERSONA_PERSISTENCE_ADMISSION_SHADOW_SCHEMA_VERSION = (
    "owner-truth-persona-persistence-admission-shadow-v1"
)
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")


class OwnerTruthPersonaPersistenceAdmissionContractError(ValueError):
    """Raised when a caller supplies a malformed G0 persistence envelope."""


class OwnerTruthPersonaPersistenceAdmissionDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    INVALID_ENVELOPE = "invalid_envelope"
    RECEIPT_NOT_ADMITTED = "receipt_not_admitted"
    EXTERNAL_G2_G4_REQUIRED = "external_g2_g4_required"


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise OwnerTruthPersonaPersistenceAdmissionContractError(
            f"{field} must be an opaque identifier"
        )
    return normalized


@dataclass(frozen=True)
class OwnerTruthPersonaPersistenceAdmissionClaims:
    """Untrusted declarations that are deliberately insufficient for a write.

    These fields exist so a future caller cannot mistake a locally asserted
    readiness flag for independent G2/G4 evidence. They are never persisted or
    sent to a Provider by this shadow.
    """

    additive_schema_ready: bool = False
    version_cas_ready: bool = False
    decision_receipt_uniqueness_ready: bool = False
    rights_evidence_ready: bool = False

    def __post_init__(self) -> None:
        for field in (
            "additive_schema_ready",
            "version_cas_ready",
            "decision_receipt_uniqueness_ready",
            "rights_evidence_ready",
        ):
            if not isinstance(getattr(self, field), bool):
                raise OwnerTruthPersonaPersistenceAdmissionContractError(
                    f"{field} must be a boolean"
                )

    @property
    def all_claimed(self) -> bool:
        return (
            self.additive_schema_ready
            and self.version_cas_ready
            and self.decision_receipt_uniqueness_ready
            and self.rights_evidence_ready
        )


@dataclass(frozen=True)
class OwnerTruthPersonaPersistenceAdmissionShadow:
    """Non-authorizing review of an otherwise admissible future record plan."""

    enabled: bool
    disposition: OwnerTruthPersonaPersistenceAdmissionDisposition
    reason_codes: tuple[str, ...]
    receipt_plan_present: bool = False
    claims_all_asserted: bool = False
    scope_hash: str | None = None
    command_hash: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise OwnerTruthPersonaPersistenceAdmissionContractError("enabled must be a boolean")
        if not isinstance(self.disposition, OwnerTruthPersonaPersistenceAdmissionDisposition):
            raise OwnerTruthPersonaPersistenceAdmissionContractError("disposition is required")
        normalized_reasons = tuple(
            sorted({_identifier(reason, field="reason_code") for reason in self.reason_codes})
        )
        if not normalized_reasons:
            raise OwnerTruthPersonaPersistenceAdmissionContractError("at least one reason code is required")
        object.__setattr__(self, "reason_codes", normalized_reasons)
        for field in ("receipt_plan_present", "claims_all_asserted"):
            if not isinstance(getattr(self, field), bool):
                raise OwnerTruthPersonaPersistenceAdmissionContractError(f"{field} must be a boolean")
        for field in ("scope_hash", "command_hash"):
            value = getattr(self, field)
            if value is not None and not re.fullmatch(r"[0-9a-f]{64}", value):
                raise OwnerTruthPersonaPersistenceAdmissionContractError(
                    f"{field} must be a SHA-256 digest"
                )

    @property
    def persistence_admitted(self) -> bool:
        return False

    @property
    def schema_changed(self) -> bool:
        return False

    @property
    def repository_written(self) -> bool:
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
            "claimsAllAsserted": self.claims_all_asserted,
            "decisionReceiptWritten": self.decision_receipt_written,
            "enabled": self.enabled,
            "persistenceAdmitted": self.persistence_admitted,
            "personaVersionWritten": self.persona_version_written,
            "providerOrRuntimeMutated": self.provider_or_runtime_mutated,
            "reasonCodes": list(self.reason_codes),
            "receiptPlanPresent": self.receipt_plan_present,
            "repositoryWritten": self.repository_written,
            "requiredExternalGates": ["G2", "G4"],
            "schemaChanged": self.schema_changed,
            "schemaVersion": OWNER_TRUTH_PERSONA_PERSISTENCE_ADMISSION_SHADOW_SCHEMA_VERSION,
            "shadowOnly": True,
            "status": self.disposition.value,
        }
        if self.scope_hash is not None:
            summary["scopeHash"] = self.scope_hash
        if self.command_hash is not None:
            summary["commandHash"] = self.command_hash
        return summary


def observe_self_persona_persistence_admission(
    receipt_plan: OwnerTruthPersonaAuthorityReceiptShadow | object,
    *,
    claims: OwnerTruthPersonaPersistenceAdmissionClaims | object | None = None,
    enabled: bool = False,
) -> OwnerTruthPersonaPersistenceAdmissionShadow:
    """Observe persistence prerequisites without authorizing or performing writes.

    A future transactional G2 writer must independently prove additive schema,
    scoped version CAS and DecisionReceipt uniqueness. Product/privacy approval
    remains a separate G4 gate. No combination of flags given here can replace
    those proofs.
    """

    if not enabled:
        return OwnerTruthPersonaPersistenceAdmissionShadow(
            enabled=False,
            disposition=OwnerTruthPersonaPersistenceAdmissionDisposition.SHADOW_DISABLED,
            reason_codes=("shadowDisabled",),
        )
    if not isinstance(receipt_plan, OwnerTruthPersonaAuthorityReceiptShadow):
        return OwnerTruthPersonaPersistenceAdmissionShadow(
            enabled=True,
            disposition=OwnerTruthPersonaPersistenceAdmissionDisposition.INVALID_ENVELOPE,
            reason_codes=("invalidPersonaPersistenceAdmissionEnvelope",),
        )
    if claims is None:
        claims = OwnerTruthPersonaPersistenceAdmissionClaims()
    if not isinstance(claims, OwnerTruthPersonaPersistenceAdmissionClaims):
        return OwnerTruthPersonaPersistenceAdmissionShadow(
            enabled=True,
            disposition=OwnerTruthPersonaPersistenceAdmissionDisposition.INVALID_ENVELOPE,
            reason_codes=("invalidPersonaPersistenceAdmissionClaims",),
        )
    if (
        receipt_plan.disposition
        is not OwnerTruthPersonaAuthorityReceiptDisposition.PLANNED_FOR_FUTURE_PERSISTENCE
        or receipt_plan.persona_version is None
        or receipt_plan.decision_receipt is None
    ):
        return OwnerTruthPersonaPersistenceAdmissionShadow(
            enabled=True,
            disposition=OwnerTruthPersonaPersistenceAdmissionDisposition.RECEIPT_NOT_ADMITTED,
            reason_codes=("futurePersonaReceiptPlanNotAdmitted",),
            receipt_plan_present=False,
            claims_all_asserted=claims.all_claimed,
        )

    reason_codes = {
        "additiveSchemaMigrationRequiresSeparateG2Proof",
        "decisionReceiptUniquenessRequiresTransactionalRepositoryProof",
        "rightsEvidenceRequiresSeparateG4Approval",
        "shadowPlanCannotSelfAuthorizePersonaPersistence",
        "versionCasRequiresTransactionalRepositoryProof",
    }
    if claims.all_claimed:
        reason_codes.add("shadowClaimsCannotAuthorizePersonaPersistence")
    else:
        reason_codes.add("independentG2G4EvidenceRequired")
    return OwnerTruthPersonaPersistenceAdmissionShadow(
        enabled=True,
        disposition=OwnerTruthPersonaPersistenceAdmissionDisposition.EXTERNAL_G2_G4_REQUIRED,
        reason_codes=tuple(reason_codes),
        receipt_plan_present=True,
        claims_all_asserted=claims.all_claimed,
        scope_hash=receipt_plan.persona_version.scope_hash,
        command_hash=receipt_plan.persona_version.command_hash,
    )


__all__ = [
    "OWNER_TRUTH_PERSONA_PERSISTENCE_ADMISSION_SHADOW_SCHEMA_VERSION",
    "OwnerTruthPersonaPersistenceAdmissionClaims",
    "OwnerTruthPersonaPersistenceAdmissionContractError",
    "OwnerTruthPersonaPersistenceAdmissionDisposition",
    "OwnerTruthPersonaPersistenceAdmissionShadow",
    "observe_self_persona_persistence_admission",
]
