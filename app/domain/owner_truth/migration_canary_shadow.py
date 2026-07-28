"""Default-off C06 non-authoritative canary and rollback-plane contract.

This module does not route traffic, issue a command, copy an object, claim a
job, call a Provider, mutate a Vault epoch, or retire a legacy writer. It only
binds opaque evidence references to the five C06 rollback planes so a future
canary implementation cannot silently treat synthetic parity as a GO.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import re


OWNER_TRUTH_MIGRATION_CANARY_SHADOW_SCHEMA_VERSION = "owner-truth-migration-canary-shadow-v1"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class OwnerTruthMigrationCanaryShadowError(ValueError):
    """Raised for an invalid synthetic C06 canary envelope."""


class MigrationCanaryLane(str, Enum):
    OWNER_TEXT_CORE = "ownerTextCore"
    MEDIA_INGESTION = "mediaIngestion"
    FAMILY_TIMELETTER = "familyTimeLetter"
    VOICE_DIGITAL_HUMAN = "voiceDigitalHuman"
    NOTIFICATION = "notification"


class MigrationCanaryCohortKind(str, Enum):
    INTERNAL_QA = "internalQa"
    PUBLIC = "public"


class MigrationCanaryRollbackPlane(str, Enum):
    UI_EXPOSURE = "uiExposure"
    CLIENT_ROUTING = "clientRouting"
    API_TRAFFIC = "apiTraffic"
    WORKER_PROVIDER = "workerProvider"
    SCHEMA_DATA = "schemaData"


class MigrationCanaryDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    INVALID_ENVELOPE = "invalid_envelope"
    PUBLIC_EXPOSURE_REJECTED = "public_exposure_rejected"
    CONTEXT_MISMATCH = "context_mismatch"
    EXTERNAL_APPROVAL_REQUIRED = "external_approval_required"


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise OwnerTruthMigrationCanaryShadowError(f"{field} must be an opaque identifier")
    return normalized


def _digest(value: object, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise OwnerTruthMigrationCanaryShadowError(f"{field} must be a SHA-256 digest")
    return normalized


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class OwnerTruthMigrationCanaryContext:
    """Current read-only authority binding for a future C06 cohort."""

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
            raise OwnerTruthMigrationCanaryShadowError("authority_epoch must be an integer")
        if self.authority_epoch < 0:
            raise OwnerTruthMigrationCanaryShadowError("authority_epoch must not be negative")


@dataclass(frozen=True)
class OwnerTruthMigrationCanaryScope:
    """Opaque scope references prepared by C04/C05; not a release decision."""

    vault_id: str
    owner_subject_id: str
    authority_epoch: int
    lane: MigrationCanaryLane
    cohort_id: str
    cohort_kind: MigrationCanaryCohortKind
    policy_version: str
    ios_build_hash: str
    backend_build_hash: str
    schema_head_hash: str
    c04_tail_report_hash: str
    c05_parity_report_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "vault_id", _identifier(self.vault_id, field="vault_id"))
        object.__setattr__(
            self,
            "owner_subject_id",
            _identifier(self.owner_subject_id, field="owner_subject_id"),
        )
        if isinstance(self.authority_epoch, bool) or not isinstance(self.authority_epoch, int):
            raise OwnerTruthMigrationCanaryShadowError("authority_epoch must be an integer")
        if self.authority_epoch < 0:
            raise OwnerTruthMigrationCanaryShadowError("authority_epoch must not be negative")
        object.__setattr__(self, "lane", MigrationCanaryLane(self.lane))
        object.__setattr__(self, "cohort_id", _identifier(self.cohort_id, field="cohort_id"))
        object.__setattr__(self, "cohort_kind", MigrationCanaryCohortKind(self.cohort_kind))
        object.__setattr__(
            self,
            "policy_version",
            _identifier(self.policy_version, field="policy_version"),
        )
        for field in (
            "ios_build_hash",
            "backend_build_hash",
            "schema_head_hash",
            "c04_tail_report_hash",
            "c05_parity_report_hash",
        ):
            object.__setattr__(self, field, _digest(getattr(self, field), field=field))

    @property
    def scope_hash(self) -> str:
        return _hash(
            {
                "authorityEpoch": self.authority_epoch,
                "backendBuildHash": self.backend_build_hash,
                "c04TailReportHash": self.c04_tail_report_hash,
                "c05ParityReportHash": self.c05_parity_report_hash,
                "cohortId": self.cohort_id,
                "cohortKind": self.cohort_kind.value,
                "iosBuildHash": self.ios_build_hash,
                "lane": self.lane.value,
                "ownerSubjectId": self.owner_subject_id,
                "policyVersion": self.policy_version,
                "schemaHeadHash": self.schema_head_hash,
                "vaultId": self.vault_id,
            }
        )


@dataclass(frozen=True)
class OwnerTruthMigrationCanaryEvidence:
    """Opaque references required before a human-approved C06 execution."""

    threshold_set_id: str
    observation_window_id: str
    rollback_drill_plan_id: str
    max_recovery_time_reference: str
    evidence_bundle_id: str
    approval_reference_hash: str

    def __post_init__(self) -> None:
        for field in (
            "threshold_set_id",
            "observation_window_id",
            "rollback_drill_plan_id",
            "max_recovery_time_reference",
            "evidence_bundle_id",
        ):
            object.__setattr__(self, field, _identifier(getattr(self, field), field=field))
        object.__setattr__(
            self,
            "approval_reference_hash",
            _digest(self.approval_reference_hash, field="approval_reference_hash"),
        )

    @property
    def evidence_hash(self) -> str:
        return _hash(
            {
                "approvalReferenceHash": self.approval_reference_hash,
                "evidenceBundleId": self.evidence_bundle_id,
                "maxRecoveryTimeReference": self.max_recovery_time_reference,
                "observationWindowId": self.observation_window_id,
                "rollbackDrillPlanId": self.rollback_drill_plan_id,
                "thresholdSetId": self.threshold_set_id,
            }
        )


_ROLLBACK_FENCE_ACTIONS: dict[MigrationCanaryRollbackPlane, tuple[str, ...]] = {
    MigrationCanaryRollbackPlane.UI_EXPOSURE: (
        "disableOptionalCapability",
        "hideInternalEntry",
        "preserveRightsFlow",
    ),
    MigrationCanaryRollbackPlane.CLIENT_ROUTING: (
        "cancelStaleGeneration",
        "restoreCompatibilityRead",
        "rejectLegacyDirectWrite",
    ),
    MigrationCanaryRollbackPlane.API_TRAFFIC: (
        "pauseCanaryRoute",
        "denyNewCanaryCommand",
        "keepLegacyAuthority",
    ),
    MigrationCanaryRollbackPlane.WORKER_PROVIDER: (
        "pauseNewClaimsAndRequests",
        "queryOrReconcileUnknownEffect",
        "rejectBlindRetry",
    ),
    MigrationCanaryRollbackPlane.SCHEMA_DATA: (
        "freezeCanaryMutation",
        "rebuildProjectionOrForwardFix",
        "neverRollbackAuthorityEpoch",
    ),
}


@dataclass(frozen=True)
class OwnerTruthMigrationCanaryShadow:
    """Value-free C06 planning result that cannot execute or authorize a canary."""

    enabled: bool
    disposition: MigrationCanaryDisposition
    reason_codes: tuple[str, ...]
    scope_hash: str | None = None
    evidence_hash: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise OwnerTruthMigrationCanaryShadowError("enabled must be boolean")
        if not isinstance(self.disposition, MigrationCanaryDisposition):
            raise OwnerTruthMigrationCanaryShadowError("disposition is required")
        normalized_reasons = tuple(
            sorted({_identifier(value, field="reason_code") for value in self.reason_codes})
        )
        if not normalized_reasons:
            raise OwnerTruthMigrationCanaryShadowError("at least one reason code is required")
        object.__setattr__(self, "reason_codes", normalized_reasons)
        for field in ("scope_hash", "evidence_hash"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _digest(value, field=field))

    @property
    def canary_execution_allowed(self) -> bool:
        return False

    @property
    def authority_epoch_changed(self) -> bool:
        return False

    @property
    def legacy_writer_retired(self) -> bool:
        return False

    def value_free_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {
            "authorityEpochChanged": self.authority_epoch_changed,
            "canaryExecutionAllowed": self.canary_execution_allowed,
            "commandEffectExecutionCount": 0,
            "enabled": self.enabled,
            "legacyWriterRetired": self.legacy_writer_retired,
            "objectOperationCount": 0,
            "providerCallCount": 0,
            "publicTrafficAllowed": False,
            "reasonCodes": list(self.reason_codes),
            "requiredExternalGates": ["G2", "G3", "G4"],
            "rollbackPlanes": [
                {
                    "fenceActionCodes": list(_ROLLBACK_FENCE_ACTIONS[plane]),
                    "plane": plane.value,
                }
                for plane in MigrationCanaryRollbackPlane
            ],
            "schemaVersion": OWNER_TRUTH_MIGRATION_CANARY_SHADOW_SCHEMA_VERSION,
            "shadowOnly": True,
            "status": self.disposition.value,
            "writeOperationCount": 0,
        }
        if self.scope_hash is not None:
            summary["scopeHash"] = self.scope_hash
        if self.evidence_hash is not None:
            summary["evidenceHash"] = self.evidence_hash
        return summary


def plan_owner_truth_migration_canary_shadow(
    *,
    scope: OwnerTruthMigrationCanaryScope | object,
    current_context: OwnerTruthMigrationCanaryContext | object,
    evidence: OwnerTruthMigrationCanaryEvidence | object,
    enabled: bool = False,
) -> OwnerTruthMigrationCanaryShadow:
    """Build a C06 rollback-plane plan without permitting its execution.

    Real cohort approval, observed thresholds, rollout routing, storage,
    worker, Provider and device evidence are deliberately outside this pure G0
    contract. A later implementation must use a new external Go/No-Go record
    and an independent command before it can enable any plane.
    """

    if not enabled:
        return OwnerTruthMigrationCanaryShadow(
            enabled=False,
            disposition=MigrationCanaryDisposition.SHADOW_DISABLED,
            reason_codes=("shadowDisabled",),
        )
    if not isinstance(scope, OwnerTruthMigrationCanaryScope) or not isinstance(
        current_context, OwnerTruthMigrationCanaryContext
    ) or not isinstance(evidence, OwnerTruthMigrationCanaryEvidence):
        return OwnerTruthMigrationCanaryShadow(
            enabled=True,
            disposition=MigrationCanaryDisposition.INVALID_ENVELOPE,
            reason_codes=("invalidCanaryEnvelope",),
        )

    scope_hash = scope.scope_hash
    if scope.cohort_kind is not MigrationCanaryCohortKind.INTERNAL_QA:
        return OwnerTruthMigrationCanaryShadow(
            enabled=True,
            disposition=MigrationCanaryDisposition.PUBLIC_EXPOSURE_REJECTED,
            reason_codes=("internalQaCohortRequired", "publicTrafficForbidden"),
            scope_hash=scope_hash,
            evidence_hash=evidence.evidence_hash,
        )
    if scope.vault_id != current_context.vault_id:
        return OwnerTruthMigrationCanaryShadow(
            enabled=True,
            disposition=MigrationCanaryDisposition.CONTEXT_MISMATCH,
            reason_codes=("vaultMismatch", "externalApprovalRequired"),
            scope_hash=scope_hash,
            evidence_hash=evidence.evidence_hash,
        )
    if scope.owner_subject_id != current_context.owner_subject_id:
        return OwnerTruthMigrationCanaryShadow(
            enabled=True,
            disposition=MigrationCanaryDisposition.CONTEXT_MISMATCH,
            reason_codes=("ownerMismatch", "externalApprovalRequired"),
            scope_hash=scope_hash,
            evidence_hash=evidence.evidence_hash,
        )
    if scope.authority_epoch != current_context.authority_epoch:
        return OwnerTruthMigrationCanaryShadow(
            enabled=True,
            disposition=MigrationCanaryDisposition.CONTEXT_MISMATCH,
            reason_codes=("authorityEpochMismatch", "externalApprovalRequired"),
            scope_hash=scope_hash,
            evidence_hash=evidence.evidence_hash,
        )
    return OwnerTruthMigrationCanaryShadow(
        enabled=True,
        disposition=MigrationCanaryDisposition.EXTERNAL_APPROVAL_REQUIRED,
        reason_codes=(
            "externalApprovalRequired",
            "independentGoNoGoRecordRequired",
            "nonAuthoritativeCanaryOnly",
            "rollbackDrillMustBeExecuted",
        ),
        scope_hash=scope_hash,
        evidence_hash=evidence.evidence_hash,
    )


__all__ = [
    "MigrationCanaryCohortKind",
    "MigrationCanaryDisposition",
    "MigrationCanaryLane",
    "MigrationCanaryRollbackPlane",
    "OWNER_TRUTH_MIGRATION_CANARY_SHADOW_SCHEMA_VERSION",
    "OwnerTruthMigrationCanaryContext",
    "OwnerTruthMigrationCanaryEvidence",
    "OwnerTruthMigrationCanaryScope",
    "OwnerTruthMigrationCanaryShadow",
    "OwnerTruthMigrationCanaryShadowError",
    "plan_owner_truth_migration_canary_shadow",
]
