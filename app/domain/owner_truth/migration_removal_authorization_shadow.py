"""Default-off C11 irreversible-removal authorization planning contract.

This is a fail-closed record model for a future independently authorized C11
maintenance window. It never migrates a contract, deletes an artifact, revokes
a credential, starts post-monitoring, or calls a Provider. The only successful
G0 result states that external execution is still required.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import re

from app.domain.owner_truth.migration_retirement_candidate_shadow import (
    EvidenceVerificationState,
    InFlightObservation,
    RetirementCandidateLifecycleState,
    RetirementSurfaceKind,
)


OWNER_TRUTH_MIGRATION_REMOVAL_AUTHORIZATION_SHADOW_SCHEMA_VERSION = (
    "owner-truth-migration-removal-authorization-shadow-v1"
)
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class OwnerTruthMigrationRemovalAuthorizationShadowError(ValueError):
    """Raised for an invalid synthetic C11 authorization envelope."""


class OldBinaryObservation(str, Enum):
    ZERO = "zero"
    PRESENT = "present"
    UNKNOWN = "unknown"


class RemovalAuthorizationPhase(str, Enum):
    AUTHORIZATION = "authorization"
    COMPLETION = "completion"
    REOPENED = "reopened"


class RemovalAuthorizationDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    INVALID_ENVELOPE = "invalid_envelope"
    PROTECTED_SURFACE_REJECTED = "protected_surface_rejected"
    CANDIDATE_NOT_APPROVED = "candidate_not_approved"
    EVIDENCE_INCOMPLETE = "evidence_incomplete"
    EXTERNAL_EXECUTION_REQUIRED = "external_execution_required"


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise OwnerTruthMigrationRemovalAuthorizationShadowError(
            f"{field} must be an opaque identifier"
        )
    return normalized


def _digest(value: object, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise OwnerTruthMigrationRemovalAuthorizationShadowError(
            f"{field} must be a SHA-256 digest"
        )
    return normalized


def _optional_digest(value: object, *, field: str) -> str | None:
    normalized = str(value or "").strip().lower()
    return _digest(normalized, field=field) if normalized else None


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class OwnerTruthRemovalAuthorizationScope:
    """Opaque binding to one C10 candidate; raw surface data stays private."""

    surface_kind: RetirementSurfaceKind
    surface_reference: str
    candidate_lifecycle: RetirementCandidateLifecycleState
    c10_candidate_manifest_hash: str
    independent_authorization_hash: str | None
    policy_version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "surface_kind", RetirementSurfaceKind(self.surface_kind))
        object.__setattr__(
            self,
            "surface_reference",
            _identifier(self.surface_reference, field="surface_reference"),
        )
        object.__setattr__(
            self,
            "candidate_lifecycle",
            RetirementCandidateLifecycleState(self.candidate_lifecycle),
        )
        object.__setattr__(
            self,
            "c10_candidate_manifest_hash",
            _digest(self.c10_candidate_manifest_hash, field="c10_candidate_manifest_hash"),
        )
        object.__setattr__(
            self,
            "independent_authorization_hash",
            _optional_digest(
                self.independent_authorization_hash,
                field="independent_authorization_hash",
            ),
        )
        object.__setattr__(
            self,
            "policy_version",
            _identifier(self.policy_version, field="policy_version"),
        )

    @property
    def scope_hash(self) -> str:
        return _hash(
            {
                "c10CandidateManifestHash": self.c10_candidate_manifest_hash,
                "candidateLifecycle": self.candidate_lifecycle.value,
                "independentAuthorizationHash": self.independent_authorization_hash,
                "policyVersion": self.policy_version,
                "surfaceKind": self.surface_kind.value,
                "surfaceReferenceHash": _hash(self.surface_reference),
            }
        )


@dataclass(frozen=True)
class OwnerTruthRemovalAuthorizationEvidence:
    """Opaque C11 prerequisites supplied by real operations at a later gate."""

    contract_dry_run: EvidenceVerificationState
    final_restore_replay: EvidenceVerificationState
    old_binary: OldBinaryObservation
    in_flight: InFlightObservation
    credential_owner: EvidenceVerificationState
    post_monitor_plan_hash: str
    evidence_bundle_hash: str
    approval_reference_hashes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "contract_dry_run",
            EvidenceVerificationState(self.contract_dry_run),
        )
        object.__setattr__(
            self,
            "final_restore_replay",
            EvidenceVerificationState(self.final_restore_replay),
        )
        object.__setattr__(self, "old_binary", OldBinaryObservation(self.old_binary))
        object.__setattr__(self, "in_flight", InFlightObservation(self.in_flight))
        object.__setattr__(
            self,
            "credential_owner",
            EvidenceVerificationState(self.credential_owner),
        )
        object.__setattr__(
            self,
            "post_monitor_plan_hash",
            _digest(self.post_monitor_plan_hash, field="post_monitor_plan_hash"),
        )
        object.__setattr__(
            self,
            "evidence_bundle_hash",
            _digest(self.evidence_bundle_hash, field="evidence_bundle_hash"),
        )
        normalized_approvals = tuple(
            sorted(
                {
                    _digest(value, field="approval_reference_hash")
                    for value in self.approval_reference_hashes
                }
            )
        )
        object.__setattr__(self, "approval_reference_hashes", normalized_approvals)

    @property
    def evidence_hash(self) -> str:
        return _hash(
            {
                "approvalReferenceHashes": self.approval_reference_hashes,
                "contractDryRun": self.contract_dry_run.value,
                "credentialOwner": self.credential_owner.value,
                "evidenceBundleHash": self.evidence_bundle_hash,
                "finalRestoreReplay": self.final_restore_replay.value,
                "inFlight": self.in_flight.value,
                "oldBinary": self.old_binary.value,
                "postMonitorPlanHash": self.post_monitor_plan_hash,
            }
        )


def _required_external_gates(surface_kind: RetirementSurfaceKind | None) -> tuple[str, ...]:
    gates = ["G2", "G4"]
    if surface_kind in {
        RetirementSurfaceKind.PROVIDER_ADAPTER,
        RetirementSurfaceKind.CREDENTIAL,
    }:
        gates.insert(1, "G3")
    return tuple(gates)


@dataclass(frozen=True)
class OwnerTruthRemovalAuthorizationShadow:
    """A result which never turns C11 authorization into an execution command."""

    enabled: bool
    phase: RemovalAuthorizationPhase
    disposition: RemovalAuthorizationDisposition
    reason_codes: tuple[str, ...]
    surface_kind: RetirementSurfaceKind | None = None
    scope_hash: str | None = None
    evidence_hash: str | None = None
    approval_reference_hashes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise OwnerTruthMigrationRemovalAuthorizationShadowError("enabled must be boolean")
        object.__setattr__(self, "phase", RemovalAuthorizationPhase(self.phase))
        object.__setattr__(self, "disposition", RemovalAuthorizationDisposition(self.disposition))
        normalized_reasons = tuple(
            sorted({_identifier(value, field="reason_code") for value in self.reason_codes})
        )
        if not normalized_reasons:
            raise OwnerTruthMigrationRemovalAuthorizationShadowError(
                "at least one reason code is required"
            )
        object.__setattr__(self, "reason_codes", normalized_reasons)
        if self.surface_kind is not None:
            object.__setattr__(self, "surface_kind", RetirementSurfaceKind(self.surface_kind))
        for field in ("scope_hash", "evidence_hash"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _digest(value, field=field))
        normalized_approvals = tuple(
            sorted(
                {
                    _digest(value, field="approval_reference_hash")
                    for value in self.approval_reference_hashes
                }
            )
        )
        object.__setattr__(self, "approval_reference_hashes", normalized_approvals)

    @property
    def contract_migrated(self) -> bool:
        return False

    @property
    def removal_execution_allowed(self) -> bool:
        return False

    @property
    def legacy_artifact_removed(self) -> bool:
        return False

    @property
    def credential_revoked(self) -> bool:
        return False

    @property
    def post_monitor_started(self) -> bool:
        return False

    def value_free_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {
            "contractMigrated": self.contract_migrated,
            "credentialRevoked": self.credential_revoked,
            "legacyArtifactRemoved": self.legacy_artifact_removed,
            "phase": self.phase.value,
            "postMonitorStarted": self.post_monitor_started,
            "reasonCodes": list(self.reason_codes),
            "removalExecutionAllowed": self.removal_execution_allowed,
            "schemaVersion": OWNER_TRUTH_MIGRATION_REMOVAL_AUTHORIZATION_SHADOW_SCHEMA_VERSION,
            "shadowOnly": True,
            "status": self.disposition.value,
        }
        if self.surface_kind is not None:
            summary["surfaceKind"] = self.surface_kind.value
            summary["requiredExternalGates"] = list(_required_external_gates(self.surface_kind))
        if self.scope_hash is not None:
            summary["scopeHash"] = self.scope_hash
        if self.evidence_hash is not None:
            summary["evidenceHash"] = self.evidence_hash
        if self.surface_kind is not None:
            summary["approvalCount"] = len(self.approval_reference_hashes)
            summary["approvalReferenceHashes"] = list(self.approval_reference_hashes)
        return summary


def plan_owner_truth_migration_removal_authorization_shadow(
    *,
    scope: OwnerTruthRemovalAuthorizationScope | object,
    evidence: OwnerTruthRemovalAuthorizationEvidence | object,
    enabled: bool = False,
) -> OwnerTruthRemovalAuthorizationShadow:
    """Validate C11 prerequisites without running any irreversible operation."""

    if not enabled:
        return OwnerTruthRemovalAuthorizationShadow(
            enabled=False,
            phase=RemovalAuthorizationPhase.AUTHORIZATION,
            disposition=RemovalAuthorizationDisposition.SHADOW_DISABLED,
            reason_codes=("shadowDisabled",),
        )
    if not isinstance(scope, OwnerTruthRemovalAuthorizationScope) or not isinstance(
        evidence, OwnerTruthRemovalAuthorizationEvidence
    ):
        return OwnerTruthRemovalAuthorizationShadow(
            enabled=True,
            phase=RemovalAuthorizationPhase.AUTHORIZATION,
            disposition=RemovalAuthorizationDisposition.INVALID_ENVELOPE,
            reason_codes=("invalidRemovalAuthorizationEnvelope",),
        )

    common = {
        "enabled": True,
        "surface_kind": scope.surface_kind,
        "scope_hash": scope.scope_hash,
        "evidence_hash": evidence.evidence_hash,
        "approval_reference_hashes": evidence.approval_reference_hashes,
    }
    if scope.surface_kind in {
        RetirementSurfaceKind.RIGHTS_ROUTE,
        RetirementSurfaceKind.RECONCILIATION_ROUTE,
    }:
        return OwnerTruthRemovalAuthorizationShadow(
            phase=RemovalAuthorizationPhase.REOPENED,
            disposition=RemovalAuthorizationDisposition.PROTECTED_SURFACE_REJECTED,
            reason_codes=("protectedRightsOrReconcileSurface", "removalForbidden"),
            **common,
        )
    if (
        scope.candidate_lifecycle is not RetirementCandidateLifecycleState.CANDIDATE_APPROVED
        or scope.independent_authorization_hash is None
        or not evidence.approval_reference_hashes
    ):
        return OwnerTruthRemovalAuthorizationShadow(
            phase=RemovalAuthorizationPhase.REOPENED,
            disposition=RemovalAuthorizationDisposition.CANDIDATE_NOT_APPROVED,
            reason_codes=(
                "independentAuthorizationMissing",
                "retirementCandidateNotApproved",
                "removalNotAuthorized",
            ),
            **common,
        )

    incomplete_reasons: list[str] = []
    if evidence.contract_dry_run is not EvidenceVerificationState.VERIFIED:
        incomplete_reasons.append("contractDryRunEvidenceMissingOrUnknown")
    if evidence.final_restore_replay is not EvidenceVerificationState.VERIFIED:
        incomplete_reasons.append("finalRestoreReplayEvidenceMissingOrUnknown")
    if evidence.old_binary is not OldBinaryObservation.ZERO:
        incomplete_reasons.append("oldBinaryNotZeroOrUnknown")
    if evidence.in_flight is not InFlightObservation.TERMINAL:
        incomplete_reasons.append("inFlightNotTerminalOrUnknown")
    if scope.surface_kind in {
        RetirementSurfaceKind.PROVIDER_ADAPTER,
        RetirementSurfaceKind.CREDENTIAL,
    } and evidence.credential_owner is not EvidenceVerificationState.VERIFIED:
        incomplete_reasons.append("credentialOwnerEvidenceMissingOrUnknown")
    if incomplete_reasons:
        return OwnerTruthRemovalAuthorizationShadow(
            phase=RemovalAuthorizationPhase.REOPENED,
            disposition=RemovalAuthorizationDisposition.EVIDENCE_INCOMPLETE,
            reason_codes=tuple(incomplete_reasons) + ("removalNotAuthorized",),
            **common,
        )

    return OwnerTruthRemovalAuthorizationShadow(
        phase=RemovalAuthorizationPhase.AUTHORIZATION,
        disposition=RemovalAuthorizationDisposition.EXTERNAL_EXECUTION_REQUIRED,
        reason_codes=(
            "externalMaintenanceWindowRequired",
            "independentExecutionOwnerRequired",
            "postMonitorEvidenceMustBeCurrent",
            "removalNotAuthorizedByLocalGate",
        ),
        **common,
    )


__all__ = [
    "OldBinaryObservation",
    "OWNER_TRUTH_MIGRATION_REMOVAL_AUTHORIZATION_SHADOW_SCHEMA_VERSION",
    "OwnerTruthMigrationRemovalAuthorizationShadowError",
    "OwnerTruthRemovalAuthorizationEvidence",
    "OwnerTruthRemovalAuthorizationScope",
    "OwnerTruthRemovalAuthorizationShadow",
    "RemovalAuthorizationDisposition",
    "RemovalAuthorizationPhase",
    "plan_owner_truth_migration_removal_authorization_shadow",
]
