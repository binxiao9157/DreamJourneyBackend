"""Default-off C10 retirement-candidate and zero-use planning contract.

This module only evaluates opaque, caller-supplied evidence for one legacy
surface.  It never reads a live counter, routes a request, drains a worker,
changes authority, removes an implementation, or revokes a credential.  A
future G2/G3/G4 process owns those operations and must record an independent
approval before C11 can be considered.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import re


OWNER_TRUTH_MIGRATION_RETIREMENT_CANDIDATE_SHADOW_SCHEMA_VERSION = (
    "owner-truth-migration-retirement-candidate-shadow-v1"
)
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class OwnerTruthMigrationRetirementCandidateShadowError(ValueError):
    """Raised when a synthetic C10 candidate envelope is malformed."""


class RetirementSurfaceKind(str, Enum):
    CLIENT = "client"
    ROUTE = "route"
    WRITER = "writer"
    TIMER = "timer"
    STORE = "store"
    OBJECT_URL = "objectUrl"
    PROVIDER_ADAPTER = "providerAdapter"
    CREDENTIAL = "credential"
    RIGHTS_ROUTE = "rightsRoute"
    RECONCILIATION_ROUTE = "reconciliationRoute"


class RetirementCandidateLifecycleState(str, Enum):
    DISCOVERED = "discovered"
    DRAINING = "draining"
    ZERO_USE_OBSERVED = "zero_use_observed"
    CANDIDATE_APPROVED = "candidate_approved"
    REOPENED = "reopened"


class RetirementCandidateDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    INVALID_ENVELOPE = "invalid_envelope"
    PROTECTED_SURFACE_REJECTED = "protected_surface_rejected"
    DRAIN_REQUIRED = "drain_required"
    REOPEN_REQUIRED = "reopen_required"
    EXTERNAL_APPROVAL_REQUIRED = "external_approval_required"


class RuntimeUsageObservation(str, Enum):
    ZERO = "zero"
    POSITIVE = "positive"
    UNKNOWN = "unknown"


class InFlightObservation(str, Enum):
    TERMINAL = "terminal"
    ACTIVE = "active"
    UNKNOWN = "unknown"


class MinimumClientObservation(str, Enum):
    ZERO_OLD_CLIENTS = "zeroOldClients"
    OLD_CLIENT_PRESENT = "oldClientPresent"
    UNKNOWN = "unknown"


class EvidenceVerificationState(str, Enum):
    VERIFIED = "verified"
    MISSING = "missing"
    UNKNOWN = "unknown"


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise OwnerTruthMigrationRetirementCandidateShadowError(
            f"{field} must be an opaque identifier"
        )
    return normalized


def _digest(value: object, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise OwnerTruthMigrationRetirementCandidateShadowError(
            f"{field} must be a SHA-256 digest"
        )
    return normalized


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class OwnerTruthRetirementCandidateScope:
    """One opaque legacy surface; its raw reference never enters a summary."""

    surface_kind: RetirementSurfaceKind
    surface_reference: str
    source_inventory_hash: str
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
            "source_inventory_hash",
            _digest(self.source_inventory_hash, field="source_inventory_hash"),
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
                "policyVersion": self.policy_version,
                "sourceInventoryHash": self.source_inventory_hash,
                "surfaceKind": self.surface_kind.value,
                "surfaceReferenceHash": _hash(self.surface_reference),
            }
        )


@dataclass(frozen=True)
class OwnerTruthRetirementCandidateEvidence:
    """Value-free C10 evidence supplied by an external observation process."""

    zero_use_window_hash: str
    runtime_usage: RuntimeUsageObservation
    runtime_use_count: int
    in_flight: InFlightObservation
    minimum_client: MinimumClientObservation
    restore_replay: EvidenceVerificationState
    receipt: EvidenceVerificationState
    owner: EvidenceVerificationState
    evidence_bundle_hash: str
    approver_hashes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "zero_use_window_hash",
            _digest(self.zero_use_window_hash, field="zero_use_window_hash"),
        )
        object.__setattr__(self, "runtime_usage", RuntimeUsageObservation(self.runtime_usage))
        if isinstance(self.runtime_use_count, bool) or not isinstance(self.runtime_use_count, int):
            raise OwnerTruthMigrationRetirementCandidateShadowError(
                "runtime_use_count must be a non-negative integer"
            )
        if self.runtime_use_count < 0:
            raise OwnerTruthMigrationRetirementCandidateShadowError(
                "runtime_use_count must be a non-negative integer"
            )
        object.__setattr__(self, "in_flight", InFlightObservation(self.in_flight))
        object.__setattr__(
            self,
            "minimum_client",
            MinimumClientObservation(self.minimum_client),
        )
        for field in ("restore_replay", "receipt", "owner"):
            object.__setattr__(
                self,
                field,
                EvidenceVerificationState(getattr(self, field)),
            )
        object.__setattr__(
            self,
            "evidence_bundle_hash",
            _digest(self.evidence_bundle_hash, field="evidence_bundle_hash"),
        )
        normalized_approvers = tuple(
            sorted({_digest(value, field="approver_hash") for value in self.approver_hashes})
        )
        object.__setattr__(self, "approver_hashes", normalized_approvers)

    @property
    def evidence_hash(self) -> str:
        return _hash(
            {
                "approverHashes": self.approver_hashes,
                "evidenceBundleHash": self.evidence_bundle_hash,
                "inFlight": self.in_flight.value,
                "minimumClient": self.minimum_client.value,
                "owner": self.owner.value,
                "receipt": self.receipt.value,
                "restoreReplay": self.restore_replay.value,
                "runtimeUsage": self.runtime_usage.value,
                "runtimeUseCount": self.runtime_use_count,
                "zeroUseWindowHash": self.zero_use_window_hash,
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
class OwnerTruthRetirementCandidateShadow:
    """A C10 result which records evidence but cannot authorize C11."""

    enabled: bool
    lifecycle_state: RetirementCandidateLifecycleState
    disposition: RetirementCandidateDisposition
    reason_codes: tuple[str, ...]
    surface_kind: RetirementSurfaceKind | None = None
    scope_hash: str | None = None
    evidence_hash: str | None = None
    runtime_use_count: int | None = None
    in_flight: InFlightObservation | None = None
    minimum_client: MinimumClientObservation | None = None
    approver_hashes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise OwnerTruthMigrationRetirementCandidateShadowError("enabled must be boolean")
        object.__setattr__(
            self,
            "lifecycle_state",
            RetirementCandidateLifecycleState(self.lifecycle_state),
        )
        object.__setattr__(
            self,
            "disposition",
            RetirementCandidateDisposition(self.disposition),
        )
        normalized_reasons = tuple(
            sorted({_identifier(value, field="reason_code") for value in self.reason_codes})
        )
        if not normalized_reasons:
            raise OwnerTruthMigrationRetirementCandidateShadowError(
                "at least one reason code is required"
            )
        object.__setattr__(self, "reason_codes", normalized_reasons)
        if self.surface_kind is not None:
            object.__setattr__(self, "surface_kind", RetirementSurfaceKind(self.surface_kind))
        for field in ("scope_hash", "evidence_hash"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _digest(value, field=field))
        if self.runtime_use_count is not None:
            if isinstance(self.runtime_use_count, bool) or not isinstance(
                self.runtime_use_count, int
            ) or self.runtime_use_count < 0:
                raise OwnerTruthMigrationRetirementCandidateShadowError(
                    "runtime_use_count must be a non-negative integer"
                )
        if self.in_flight is not None:
            object.__setattr__(self, "in_flight", InFlightObservation(self.in_flight))
        if self.minimum_client is not None:
            object.__setattr__(
                self,
                "minimum_client",
                MinimumClientObservation(self.minimum_client),
            )
        normalized_approvers = tuple(
            sorted({_digest(value, field="approver_hash") for value in self.approver_hashes})
        )
        object.__setattr__(self, "approver_hashes", normalized_approvers)

    @property
    def candidate_approval_allowed(self) -> bool:
        return False

    @property
    def legacy_implementation_deleted(self) -> bool:
        return False

    @property
    def credential_revoked(self) -> bool:
        return False

    @property
    def live_runtime_counter_read(self) -> bool:
        return False

    def value_free_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {
            "candidateApprovalAllowed": self.candidate_approval_allowed,
            "credentialRevoked": self.credential_revoked,
            "implementationDeleted": self.legacy_implementation_deleted,
            "lifecycleState": self.lifecycle_state.value,
            "liveRuntimeCounterRead": self.live_runtime_counter_read,
            "reasonCodes": list(self.reason_codes),
            "schemaVersion": OWNER_TRUTH_MIGRATION_RETIREMENT_CANDIDATE_SHADOW_SCHEMA_VERSION,
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
        if self.runtime_use_count is not None:
            summary["runtimeUseCount"] = self.runtime_use_count
        if self.in_flight is not None:
            summary["inFlightState"] = self.in_flight.value
        if self.minimum_client is not None:
            summary["minimumClientState"] = self.minimum_client.value
        if self.surface_kind is not None:
            summary["approverCount"] = len(self.approver_hashes)
            summary["approverReferenceHashes"] = list(self.approver_hashes)
        return summary


def plan_owner_truth_migration_retirement_candidate_shadow(
    *,
    scope: OwnerTruthRetirementCandidateScope | object,
    evidence: OwnerTruthRetirementCandidateEvidence | object,
    enabled: bool = False,
) -> OwnerTruthRetirementCandidateShadow:
    """Evaluate one C10 candidate without authorizing its retirement.

    Disabled mode intentionally does not inspect either input.  A successful
    zero-use observation remains an external-approval-required result; C10
    may not manufacture ``candidate_approved`` and cannot perform C11 work.
    """

    if not enabled:
        return OwnerTruthRetirementCandidateShadow(
            enabled=False,
            lifecycle_state=RetirementCandidateLifecycleState.DISCOVERED,
            disposition=RetirementCandidateDisposition.SHADOW_DISABLED,
            reason_codes=("shadowDisabled",),
        )
    if not isinstance(scope, OwnerTruthRetirementCandidateScope) or not isinstance(
        evidence, OwnerTruthRetirementCandidateEvidence
    ):
        return OwnerTruthRetirementCandidateShadow(
            enabled=True,
            lifecycle_state=RetirementCandidateLifecycleState.DISCOVERED,
            disposition=RetirementCandidateDisposition.INVALID_ENVELOPE,
            reason_codes=("invalidRetirementCandidateEnvelope",),
        )

    common = {
        "enabled": True,
        "surface_kind": scope.surface_kind,
        "scope_hash": scope.scope_hash,
        "evidence_hash": evidence.evidence_hash,
        "runtime_use_count": evidence.runtime_use_count,
        "in_flight": evidence.in_flight,
        "minimum_client": evidence.minimum_client,
        "approver_hashes": evidence.approver_hashes,
    }
    if scope.surface_kind in {
        RetirementSurfaceKind.RIGHTS_ROUTE,
        RetirementSurfaceKind.RECONCILIATION_ROUTE,
    }:
        return OwnerTruthRetirementCandidateShadow(
            lifecycle_state=RetirementCandidateLifecycleState.REOPENED,
            disposition=RetirementCandidateDisposition.PROTECTED_SURFACE_REJECTED,
            reason_codes=("protectedRightsOrReconcileSurface", "retirementForbidden"),
            **common,
        )

    unknown_reasons: list[str] = []
    if evidence.runtime_usage is RuntimeUsageObservation.UNKNOWN:
        unknown_reasons.append("runtimeUsageUnknown")
    if evidence.in_flight is InFlightObservation.UNKNOWN:
        unknown_reasons.append("inFlightUnknown")
    if evidence.minimum_client is MinimumClientObservation.UNKNOWN:
        unknown_reasons.append("minimumClientUnknown")
    if evidence.restore_replay is not EvidenceVerificationState.VERIFIED:
        unknown_reasons.append("restoreReplayEvidenceMissingOrUnknown")
    if evidence.receipt is not EvidenceVerificationState.VERIFIED:
        unknown_reasons.append("receiptEvidenceMissingOrUnknown")
    if evidence.owner is not EvidenceVerificationState.VERIFIED:
        unknown_reasons.append("ownerEvidenceMissingOrUnknown")
    if unknown_reasons:
        return OwnerTruthRetirementCandidateShadow(
            lifecycle_state=RetirementCandidateLifecycleState.REOPENED,
            disposition=RetirementCandidateDisposition.REOPEN_REQUIRED,
            reason_codes=tuple(unknown_reasons) + ("zeroUseWindowReset",),
            **common,
        )

    if evidence.runtime_usage is RuntimeUsageObservation.POSITIVE or evidence.runtime_use_count > 0:
        return OwnerTruthRetirementCandidateShadow(
            lifecycle_state=RetirementCandidateLifecycleState.REOPENED,
            disposition=RetirementCandidateDisposition.REOPEN_REQUIRED,
            reason_codes=("runtimeUsageObserved", "zeroUseWindowReset"),
            **common,
        )

    if evidence.in_flight is InFlightObservation.ACTIVE:
        return OwnerTruthRetirementCandidateShadow(
            lifecycle_state=RetirementCandidateLifecycleState.DRAINING,
            disposition=RetirementCandidateDisposition.DRAIN_REQUIRED,
            reason_codes=("inFlightDrainRequired", "zeroUseNotYetObserved"),
            **common,
        )

    if evidence.minimum_client is not MinimumClientObservation.ZERO_OLD_CLIENTS:
        return OwnerTruthRetirementCandidateShadow(
            lifecycle_state=RetirementCandidateLifecycleState.REOPENED,
            disposition=RetirementCandidateDisposition.REOPEN_REQUIRED,
            reason_codes=("oldClientStillPresent", "zeroUseWindowReset"),
            **common,
        )

    return OwnerTruthRetirementCandidateShadow(
        lifecycle_state=RetirementCandidateLifecycleState.ZERO_USE_OBSERVED,
        disposition=RetirementCandidateDisposition.EXTERNAL_APPROVAL_REQUIRED,
        reason_codes=(
            "c11AuthorizationRequired",
            "independentApproverRequired",
            "sourceInventoryIsNotRuntimeProof",
            "zeroUseObservedWithoutSelfApproval",
        ),
        **common,
    )


__all__ = [
    "EvidenceVerificationState",
    "InFlightObservation",
    "MinimumClientObservation",
    "OWNER_TRUTH_MIGRATION_RETIREMENT_CANDIDATE_SHADOW_SCHEMA_VERSION",
    "OwnerTruthMigrationRetirementCandidateShadowError",
    "OwnerTruthRetirementCandidateEvidence",
    "OwnerTruthRetirementCandidateScope",
    "OwnerTruthRetirementCandidateShadow",
    "RetirementCandidateDisposition",
    "RetirementCandidateLifecycleState",
    "RetirementSurfaceKind",
    "RuntimeUsageObservation",
    "plan_owner_truth_migration_retirement_candidate_shadow",
]
