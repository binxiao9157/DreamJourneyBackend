"""Default-off C08 per-lane activation and rollback planning contract.

This module models a single future lane at a time. It cannot enable a route,
start a worker, promote an object reference, call a Provider, change Owner
Authority, or use a global activation switch. Real lane promotion is outside
this G0 contract and requires independent evidence and authorization.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import re


OWNER_TRUTH_MIGRATION_LANE_ACTIVATION_SHADOW_SCHEMA_VERSION = (
    "owner-truth-migration-lane-activation-shadow-v1"
)
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class OwnerTruthMigrationLaneActivationShadowError(ValueError):
    """Raised for an invalid synthetic C08 lane envelope."""


class MigrationActivationLane(str, Enum):
    PROJECTION = "projection"
    TIME_LETTER_WORKER = "timeLetterWorker"
    ECHO_WORKER = "echoWorker"
    RIGHTS_WORKER = "rightsWorker"
    OBJECT_REFERENCE = "objectReference"
    OPTIONAL_PROVIDER = "optionalProvider"


class MigrationLaneCohortKind(str, Enum):
    INTERNAL_QA = "internalQa"
    PUBLIC = "public"


class MigrationLaneActivationDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    INVALID_ENVELOPE = "invalid_envelope"
    PUBLIC_EXPOSURE_REJECTED = "public_exposure_rejected"
    CONTEXT_MISMATCH = "context_mismatch"
    EXTERNAL_READINESS_REQUIRED = "external_readiness_required"


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise OwnerTruthMigrationLaneActivationShadowError(
            f"{field} must be an opaque identifier"
        )
    return normalized


def _digest(value: object, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise OwnerTruthMigrationLaneActivationShadowError(
            f"{field} must be a SHA-256 digest"
        )
    return normalized


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class OwnerTruthMigrationLaneActivationContext:
    """Read-only authority binding for one future C08 lane."""

    vault_id: str
    owner_subject_id: str
    authority_epoch: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "vault_id", _identifier(self.vault_id, field="vault_id"))
        object.__setattr__(
            self,
            "owner_subject_id",
            _identifier(self.owner_subject_id, field="owner_subject_id"),
        )
        if isinstance(self.authority_epoch, bool) or not isinstance(self.authority_epoch, int):
            raise OwnerTruthMigrationLaneActivationShadowError(
                "authority_epoch must be an integer"
            )
        if self.authority_epoch < 0:
            raise OwnerTruthMigrationLaneActivationShadowError(
                "authority_epoch must not be negative"
            )


@dataclass(frozen=True)
class OwnerTruthMigrationLaneActivationScope:
    """Opaque C08 scope; one lane per result, never a global switch."""

    vault_id: str
    owner_subject_id: str
    authority_epoch: int
    lane: MigrationActivationLane
    cohort_kind: MigrationLaneCohortKind
    cohort_reference_hash: str
    policy_version: str
    c07_admission_reference_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "vault_id", _identifier(self.vault_id, field="vault_id"))
        object.__setattr__(
            self,
            "owner_subject_id",
            _identifier(self.owner_subject_id, field="owner_subject_id"),
        )
        if isinstance(self.authority_epoch, bool) or not isinstance(self.authority_epoch, int):
            raise OwnerTruthMigrationLaneActivationShadowError(
                "authority_epoch must be an integer"
            )
        if self.authority_epoch < 0:
            raise OwnerTruthMigrationLaneActivationShadowError(
                "authority_epoch must not be negative"
            )
        object.__setattr__(self, "lane", MigrationActivationLane(self.lane))
        object.__setattr__(self, "cohort_kind", MigrationLaneCohortKind(self.cohort_kind))
        object.__setattr__(
            self,
            "cohort_reference_hash",
            _digest(self.cohort_reference_hash, field="cohort_reference_hash"),
        )
        object.__setattr__(
            self,
            "policy_version",
            _identifier(self.policy_version, field="policy_version"),
        )
        object.__setattr__(
            self,
            "c07_admission_reference_hash",
            _digest(
                self.c07_admission_reference_hash,
                field="c07_admission_reference_hash",
            ),
        )

    @property
    def scope_hash(self) -> str:
        return _hash(
            {
                "authorityEpoch": self.authority_epoch,
                "c07AdmissionReferenceHash": self.c07_admission_reference_hash,
                "cohortKind": self.cohort_kind.value,
                "cohortReferenceHash": self.cohort_reference_hash,
                "lane": self.lane.value,
                "ownerSubjectId": self.owner_subject_id,
                "policyVersion": self.policy_version,
                "vaultId": self.vault_id,
            }
        )


@dataclass(frozen=True)
class OwnerTruthMigrationLaneReadinessEvidence:
    """Opaque evidence references required before a real lane decision."""

    compatibility_receipt_hash: str
    rollback_plan_hash: str
    readiness_report_hash: str

    def __post_init__(self) -> None:
        for field in (
            "compatibility_receipt_hash",
            "rollback_plan_hash",
            "readiness_report_hash",
        ):
            object.__setattr__(self, field, _digest(getattr(self, field), field=field))

    @property
    def evidence_hash(self) -> str:
        return _hash(
            {
                "compatibilityReceiptHash": self.compatibility_receipt_hash,
                "readinessReportHash": self.readiness_report_hash,
                "rollbackPlanHash": self.rollback_plan_hash,
            }
        )


_ROLLBACK_ACTIONS: dict[MigrationActivationLane, tuple[str, ...]] = {
    MigrationActivationLane.PROJECTION: (
        "disableProjectionCapability",
        "restoreCompatibilityRead",
        "rebuildProjectionOrReconcile",
    ),
    MigrationActivationLane.TIME_LETTER_WORKER: (
        "pauseTimeLetterClaims",
        "reconcileUnknownDelivery",
        "preserveRightsFlow",
    ),
    MigrationActivationLane.ECHO_WORKER: (
        "pauseEchoClaims",
        "reconcileUnknownReply",
        "restoreTextOnlyFallback",
    ),
    MigrationActivationLane.RIGHTS_WORKER: (
        "pauseNonTerminalRightsWork",
        "preserveAccessFirstFence",
        "reconcileLayerReceipts",
    ),
    MigrationActivationLane.OBJECT_REFERENCE: (
        "freezeReferencePromotion",
        "reconcileObjectMetadata",
        "preserveLocalOrTextFallback",
    ),
    MigrationActivationLane.OPTIONAL_PROVIDER: (
        "pauseProviderRequests",
        "queryOrReconcileUnknownEffect",
        "preserveProviderFreeFallback",
    ),
}


def _required_external_gates(lane: MigrationActivationLane) -> tuple[str, ...]:
    gates = ["G1", "G2", "G4"]
    if lane is MigrationActivationLane.OPTIONAL_PROVIDER:
        gates.insert(2, "G3")
    return tuple(gates)


@dataclass(frozen=True)
class OwnerTruthMigrationLaneActivationShadow:
    """A value-free result that cannot enable a C08 lane."""

    enabled: bool
    disposition: MigrationLaneActivationDisposition
    reason_codes: tuple[str, ...]
    lane: MigrationActivationLane | None = None
    scope_hash: str | None = None
    evidence_hash: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise OwnerTruthMigrationLaneActivationShadowError("enabled must be boolean")
        if not isinstance(self.disposition, MigrationLaneActivationDisposition):
            raise OwnerTruthMigrationLaneActivationShadowError("disposition is required")
        normalized_reasons = tuple(
            sorted({_identifier(value, field="reason_code") for value in self.reason_codes})
        )
        if not normalized_reasons:
            raise OwnerTruthMigrationLaneActivationShadowError(
                "at least one reason code is required"
            )
        object.__setattr__(self, "reason_codes", normalized_reasons)
        if self.lane is not None:
            object.__setattr__(self, "lane", MigrationActivationLane(self.lane))
        for field in ("scope_hash", "evidence_hash"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _digest(value, field=field))

    @property
    def lane_activation_allowed(self) -> bool:
        return False

    @property
    def global_activation_allowed(self) -> bool:
        return False

    @property
    def authority_epoch_changed(self) -> bool:
        return False

    @property
    def worker_or_provider_started(self) -> bool:
        return False

    @property
    def object_reference_promoted(self) -> bool:
        return False

    def value_free_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {
            "authorityEpochChanged": self.authority_epoch_changed,
            "globalActivationAllowed": self.global_activation_allowed,
            "laneActivationAllowed": self.lane_activation_allowed,
            "laneCount": 1 if self.lane is not None else 0,
            "objectReferencePromoted": self.object_reference_promoted,
            "reasonCodes": list(self.reason_codes),
            "schemaVersion": OWNER_TRUTH_MIGRATION_LANE_ACTIVATION_SHADOW_SCHEMA_VERSION,
            "shadowOnly": True,
            "status": self.disposition.value,
            "workerOrProviderStarted": self.worker_or_provider_started,
        }
        if self.lane is not None:
            summary["lane"] = self.lane.value
            summary["requiredExternalGates"] = list(_required_external_gates(self.lane))
            summary["rollbackFenceActionCodes"] = list(_ROLLBACK_ACTIONS[self.lane])
        if self.scope_hash is not None:
            summary["scopeHash"] = self.scope_hash
        if self.evidence_hash is not None:
            summary["evidenceHash"] = self.evidence_hash
        return summary


def plan_owner_truth_migration_lane_activation_shadow(
    *,
    scope: OwnerTruthMigrationLaneActivationScope | object,
    current_context: OwnerTruthMigrationLaneActivationContext | object,
    evidence: OwnerTruthMigrationLaneReadinessEvidence | object,
    enabled: bool = False,
) -> OwnerTruthMigrationLaneActivationShadow:
    """Bind one prospective C08 lane to evidence without enabling it."""

    if not enabled:
        return OwnerTruthMigrationLaneActivationShadow(
            enabled=False,
            disposition=MigrationLaneActivationDisposition.SHADOW_DISABLED,
            reason_codes=("shadowDisabled",),
        )
    if not isinstance(scope, OwnerTruthMigrationLaneActivationScope) or not isinstance(
        current_context, OwnerTruthMigrationLaneActivationContext
    ) or not isinstance(evidence, OwnerTruthMigrationLaneReadinessEvidence):
        return OwnerTruthMigrationLaneActivationShadow(
            enabled=True,
            disposition=MigrationLaneActivationDisposition.INVALID_ENVELOPE,
            reason_codes=("invalidLaneActivationEnvelope",),
        )

    scope_hash = scope.scope_hash
    if scope.cohort_kind is not MigrationLaneCohortKind.INTERNAL_QA:
        return OwnerTruthMigrationLaneActivationShadow(
            enabled=True,
            disposition=MigrationLaneActivationDisposition.PUBLIC_EXPOSURE_REJECTED,
            reason_codes=("internalQaCohortRequired", "publicActivationForbidden"),
            lane=scope.lane,
            scope_hash=scope_hash,
            evidence_hash=evidence.evidence_hash,
        )
    if scope.vault_id != current_context.vault_id:
        return OwnerTruthMigrationLaneActivationShadow(
            enabled=True,
            disposition=MigrationLaneActivationDisposition.CONTEXT_MISMATCH,
            reason_codes=("externalReadinessRequired", "vaultMismatch"),
            lane=scope.lane,
            scope_hash=scope_hash,
            evidence_hash=evidence.evidence_hash,
        )
    if scope.owner_subject_id != current_context.owner_subject_id:
        return OwnerTruthMigrationLaneActivationShadow(
            enabled=True,
            disposition=MigrationLaneActivationDisposition.CONTEXT_MISMATCH,
            reason_codes=("externalReadinessRequired", "ownerMismatch"),
            lane=scope.lane,
            scope_hash=scope_hash,
            evidence_hash=evidence.evidence_hash,
        )
    if scope.authority_epoch != current_context.authority_epoch:
        return OwnerTruthMigrationLaneActivationShadow(
            enabled=True,
            disposition=MigrationLaneActivationDisposition.CONTEXT_MISMATCH,
            reason_codes=("authorityEpochMismatch", "externalReadinessRequired"),
            lane=scope.lane,
            scope_hash=scope_hash,
            evidence_hash=evidence.evidence_hash,
        )
    return OwnerTruthMigrationLaneActivationShadow(
        enabled=True,
        disposition=MigrationLaneActivationDisposition.EXTERNAL_READINESS_REQUIRED,
        reason_codes=(
            "independentLaneApprovalRequired",
            "noGlobalActivationSwitch",
            "nonAuthoritativeLanePlanOnly",
            "rollbackReceiptMustBeCurrent",
        ),
        lane=scope.lane,
        scope_hash=scope_hash,
        evidence_hash=evidence.evidence_hash,
    )


__all__ = [
    "MigrationActivationLane",
    "MigrationLaneActivationDisposition",
    "MigrationLaneCohortKind",
    "OWNER_TRUTH_MIGRATION_LANE_ACTIVATION_SHADOW_SCHEMA_VERSION",
    "OwnerTruthMigrationLaneActivationContext",
    "OwnerTruthMigrationLaneActivationScope",
    "OwnerTruthMigrationLaneActivationShadow",
    "OwnerTruthMigrationLaneActivationShadowError",
    "OwnerTruthMigrationLaneReadinessEvidence",
    "plan_owner_truth_migration_lane_activation_shadow",
]
