"""C05 hash-only migration parity comparison for Owner Truth.

This module evaluates synthetic or collected *descriptors* from legacy and V4
paths.  It intentionally accepts only opaque SHA-256 values and enumerated
dimensions.  It cannot read application data, mutate a Vault, copy an object,
or call a Provider.  C04 remains the owner of would-run effect planning; C05
only decides whether the two observed descriptions are comparable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import json
import re
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple


OWNER_TRUTH_MIGRATION_PARITY_SHADOW_SCHEMA_VERSION = "owner-truth-migration-parity-shadow-v1"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_REASON_CODE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SCOPE_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class OwnerTruthMigrationParityShadowError(ValueError):
    """A parity comparison is ambiguous, unbounded, or not value-free."""


class OwnerTruthMigrationParityShadowConflict(OwnerTruthMigrationParityShadowError):
    """The same comparison identity was rebound to a different observation."""


class MigrationParitySurface(str, Enum):
    READ = "read"
    COMMAND = "command"
    PROJECTION = "projection"
    CONTEXT = "context"
    OBJECT_COPY = "objectCopy"


class MigrationParityMismatchCode(str, Enum):
    M01 = "M01"
    M02 = "M02"
    M03 = "M03"
    M04 = "M04"
    M05 = "M05"
    M06 = "M06"
    M07 = "M07"
    M08 = "M08"


class MigrationParitySeverity(str, Enum):
    BLOCKER = "blocker"
    HIGH = "high"
    REVIEWABLE = "reviewable"


class MigrationParityDimension(str, Enum):
    # M01: subject, Vault and recipient boundary.
    OWNER_SUBJECT_ID = "ownerSubjectId"
    VAULT_ID = "vaultId"
    PRINCIPAL = "principal"
    RECIPIENT = "recipient"

    # M02: record identity and deterministic target identity.
    RESOURCE_IDENTITY = "resourceIdentity"
    LEGACY_LOCATOR = "legacyLocator"
    TARGET_ID = "deterministicTargetId"

    # M03: query/visibility and rights boundary.
    VISIBILITY = "visibility"
    ACCESS_GRANT = "accessGrant"
    DELETED_STATE = "deletedState"
    SUSPENDED_STATE = "suspendedState"
    CLAIM_PENDING_STATE = "claimPendingState"

    # M04: authority and confirmed-version state.
    TERMINAL_DECISION = "terminalDecision"
    ACTIVE_MEMORY_VERSION = "activeMemoryVersion"
    ROW_VERSION = "rowVersion"
    AUTHORITY_EPOCH = "authorityEpoch"

    # M05: canonical content/version/state comparison.
    CANONICAL_CONTENT_HASH = "canonicalContentHash"
    VERSION_ORDER = "versionOrder"
    STATE_TRANSITION = "stateTransition"

    # M06: lineage and would-effect/object/provider/cost binding.
    SOURCE_LINEAGE = "sourceLineage"
    EVIDENCE_LINEAGE = "evidenceLineage"
    CITATION_LINEAGE = "citationLineage"
    OBJECT_STATE = "objectState"
    OBJECT_COPY_HASH = "objectCopyHash"
    COMMAND_EFFECT_PLAN = "commandEffectPlan"
    PROVIDER_EFFECT_KEY = "providerEffectKey"
    PROVIDER_STATE = "providerState"
    COST_ENVELOPE = "costEnvelope"

    # M07: projection/result-shape mechanics.
    COUNT = "count"
    SORT = "sort"
    UTC_TIME = "utcTime"
    PAGINATION = "pagination"
    CURSOR = "cursor"
    PROJECTION_CHECKPOINT = "projectionCheckpoint"

    # M08: reviewable only when a bounded approval exists.
    DISPLAY_NORMALIZATION = "displayNormalization"
    NON_AUTHORITATIVE_SORT = "nonAuthoritativeSort"
    OPTIONAL_LEGACY_METADATA = "optionalLegacyMetadata"


_MISMATCH_CODE_BY_DIMENSION = {
    MigrationParityDimension.OWNER_SUBJECT_ID: MigrationParityMismatchCode.M01,
    MigrationParityDimension.VAULT_ID: MigrationParityMismatchCode.M01,
    MigrationParityDimension.PRINCIPAL: MigrationParityMismatchCode.M01,
    MigrationParityDimension.RECIPIENT: MigrationParityMismatchCode.M01,
    MigrationParityDimension.RESOURCE_IDENTITY: MigrationParityMismatchCode.M02,
    MigrationParityDimension.LEGACY_LOCATOR: MigrationParityMismatchCode.M02,
    MigrationParityDimension.TARGET_ID: MigrationParityMismatchCode.M02,
    MigrationParityDimension.VISIBILITY: MigrationParityMismatchCode.M03,
    MigrationParityDimension.ACCESS_GRANT: MigrationParityMismatchCode.M03,
    MigrationParityDimension.DELETED_STATE: MigrationParityMismatchCode.M03,
    MigrationParityDimension.SUSPENDED_STATE: MigrationParityMismatchCode.M03,
    MigrationParityDimension.CLAIM_PENDING_STATE: MigrationParityMismatchCode.M03,
    MigrationParityDimension.TERMINAL_DECISION: MigrationParityMismatchCode.M04,
    MigrationParityDimension.ACTIVE_MEMORY_VERSION: MigrationParityMismatchCode.M04,
    MigrationParityDimension.ROW_VERSION: MigrationParityMismatchCode.M04,
    MigrationParityDimension.AUTHORITY_EPOCH: MigrationParityMismatchCode.M04,
    MigrationParityDimension.CANONICAL_CONTENT_HASH: MigrationParityMismatchCode.M05,
    MigrationParityDimension.VERSION_ORDER: MigrationParityMismatchCode.M05,
    MigrationParityDimension.STATE_TRANSITION: MigrationParityMismatchCode.M05,
    MigrationParityDimension.SOURCE_LINEAGE: MigrationParityMismatchCode.M06,
    MigrationParityDimension.EVIDENCE_LINEAGE: MigrationParityMismatchCode.M06,
    MigrationParityDimension.CITATION_LINEAGE: MigrationParityMismatchCode.M06,
    MigrationParityDimension.OBJECT_STATE: MigrationParityMismatchCode.M06,
    MigrationParityDimension.OBJECT_COPY_HASH: MigrationParityMismatchCode.M06,
    MigrationParityDimension.COMMAND_EFFECT_PLAN: MigrationParityMismatchCode.M06,
    MigrationParityDimension.PROVIDER_EFFECT_KEY: MigrationParityMismatchCode.M06,
    MigrationParityDimension.PROVIDER_STATE: MigrationParityMismatchCode.M06,
    MigrationParityDimension.COST_ENVELOPE: MigrationParityMismatchCode.M06,
    MigrationParityDimension.COUNT: MigrationParityMismatchCode.M07,
    MigrationParityDimension.SORT: MigrationParityMismatchCode.M07,
    MigrationParityDimension.UTC_TIME: MigrationParityMismatchCode.M07,
    MigrationParityDimension.PAGINATION: MigrationParityMismatchCode.M07,
    MigrationParityDimension.CURSOR: MigrationParityMismatchCode.M07,
    MigrationParityDimension.PROJECTION_CHECKPOINT: MigrationParityMismatchCode.M07,
    MigrationParityDimension.DISPLAY_NORMALIZATION: MigrationParityMismatchCode.M08,
    MigrationParityDimension.NON_AUTHORITATIVE_SORT: MigrationParityMismatchCode.M08,
    MigrationParityDimension.OPTIONAL_LEGACY_METADATA: MigrationParityMismatchCode.M08,
}
_SEVERITY_BY_CODE = {
    MigrationParityMismatchCode.M01: MigrationParitySeverity.BLOCKER,
    MigrationParityMismatchCode.M02: MigrationParitySeverity.BLOCKER,
    MigrationParityMismatchCode.M03: MigrationParitySeverity.BLOCKER,
    MigrationParityMismatchCode.M04: MigrationParitySeverity.BLOCKER,
    MigrationParityMismatchCode.M05: MigrationParitySeverity.HIGH,
    MigrationParityMismatchCode.M06: MigrationParitySeverity.HIGH,
    MigrationParityMismatchCode.M07: MigrationParitySeverity.HIGH,
    MigrationParityMismatchCode.M08: MigrationParitySeverity.REVIEWABLE,
}


def _sha256(value: object, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise OwnerTruthMigrationParityShadowError(
            "%s must be a lowercase SHA-256 digest" % field
        )
    return normalized


def _optional_sha256(value: object, *, field: str) -> Optional[str]:
    if value is None:
        return None
    return _sha256(value, field=field)


def _reason_code(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _REASON_CODE_PATTERN.fullmatch(normalized):
        raise OwnerTruthMigrationParityShadowError(
            "%s must be an opaque reason code" % field
        )
    return normalized


def _scope_identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _SCOPE_IDENTIFIER_PATTERN.fullmatch(normalized):
        raise OwnerTruthMigrationParityShadowError(
            "%s must be an opaque scope identifier" % field
        )
    return normalized


def _positive_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise OwnerTruthMigrationParityShadowError("%s must be a positive integer" % field)
    return value


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthMigrationParityShadowError(
            "migration parity shadow material must be serializable"
        ) from exc


def _digest(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _utc_datetime(value: object, *, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise OwnerTruthMigrationParityShadowError("%s must be a datetime" % field)
    if value.tzinfo is None or value.utcoffset() is None:
        raise OwnerTruthMigrationParityShadowError("%s must be timezone-aware" % field)
    return value.astimezone(timezone.utc)


def _utc_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_migration_parity_scope_hash(
    *,
    vault_id: str,
    owner_subject_id: str,
    authority_epoch: int,
) -> str:
    """Bind a report window to one active Owner/Vault/epoch without exporting it."""

    normalized_vault_id = _scope_identifier(vault_id, field="vault_id")
    normalized_owner_subject_id = _scope_identifier(
        owner_subject_id, field="owner_subject_id"
    )
    if isinstance(authority_epoch, bool) or not isinstance(authority_epoch, int):
        raise OwnerTruthMigrationParityShadowError("authority_epoch must be an integer")
    if authority_epoch < 0:
        raise OwnerTruthMigrationParityShadowError("authority_epoch must be non-negative")
    return _digest(
        {
            "authorityEpoch": authority_epoch,
            "ownerSubjectId": normalized_owner_subject_id,
            "vaultId": normalized_vault_id,
        }
    )


@dataclass(frozen=True)
class MigrationParityComparisonWindow:
    """Bounded comparison evidence without raw cohort, owner or threshold data."""

    window_reference_hash: str
    scope_hash: str
    denominator_source_hash: str
    threshold_source_hash: str
    expected_sample_count: int

    def __post_init__(self) -> None:
        for field in (
            "window_reference_hash",
            "scope_hash",
            "denominator_source_hash",
            "threshold_source_hash",
        ):
            object.__setattr__(self, field, _sha256(getattr(self, field), field=field))
        object.__setattr__(
            self,
            "expected_sample_count",
            _positive_int(self.expected_sample_count, field="expected_sample_count"),
        )

    def summary(self) -> dict[str, object]:
        return {
            "denominatorSourceHash": self.denominator_source_hash,
            "expectedSampleCount": self.expected_sample_count,
            "scopeHash": self.scope_hash,
            "thresholdSourceHash": self.threshold_source_hash,
            "windowReferenceHash": self.window_reference_hash,
        }


@dataclass(frozen=True)
class MigrationParityObservation:
    """One opaque legacy/V4 comparison observation.

    ``None`` represents an absent value.  The caller must have already reduced
    any raw subject, content, object or provider value to a trusted digest.
    """

    sample_id_hash: str
    surface: MigrationParitySurface
    dimension: MigrationParityDimension
    legacy_value_hash: Optional[str]
    v4_value_hash: Optional[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "sample_id_hash", _sha256(self.sample_id_hash, field="sample_id_hash"))
        object.__setattr__(self, "surface", MigrationParitySurface(self.surface))
        object.__setattr__(self, "dimension", MigrationParityDimension(self.dimension))
        object.__setattr__(
            self,
            "legacy_value_hash",
            _optional_sha256(self.legacy_value_hash, field="legacy_value_hash"),
        )
        object.__setattr__(
            self,
            "v4_value_hash",
            _optional_sha256(self.v4_value_hash, field="v4_value_hash"),
        )
        if self.legacy_value_hash is None and self.v4_value_hash is None:
            raise OwnerTruthMigrationParityShadowError(
                "both legacy_value_hash and v4_value_hash cannot be absent"
            )
        if self.mismatch_code is MigrationParityMismatchCode.M08 and self.surface in {
            MigrationParitySurface.COMMAND,
            MigrationParitySurface.OBJECT_COPY,
        }:
            raise OwnerTruthMigrationParityShadowError(
                "M08 reviewable dimensions cannot waive command or object-copy parity"
            )

    @property
    def mismatch_code(self) -> MigrationParityMismatchCode:
        return _MISMATCH_CODE_BY_DIMENSION[self.dimension]

    @property
    def severity(self) -> MigrationParitySeverity:
        return _SEVERITY_BY_CODE[self.mismatch_code]

    @property
    def is_match(self) -> bool:
        return self.legacy_value_hash == self.v4_value_hash

    @property
    def semantic_key(self) -> Tuple[str, str, str]:
        return (self.sample_id_hash, self.surface.value, self.dimension.value)

    @property
    def observation_hash(self) -> str:
        return _digest(
            {
                "dimension": self.dimension.value,
                "legacyValueHash": self.legacy_value_hash,
                "sampleIdHash": self.sample_id_hash,
                "surface": self.surface.value,
                "v4ValueHash": self.v4_value_hash,
            }
        )

    def summary(self) -> dict[str, object]:
        return {
            "dimension": self.dimension.value,
            "legacyValueHash": self.legacy_value_hash,
            "mismatchCode": self.mismatch_code.value,
            "observationHash": self.observation_hash,
            "sampleIdHash": self.sample_id_hash,
            "severity": self.severity.value,
            "surface": self.surface.value,
            "v4ValueHash": self.v4_value_hash,
        }


@dataclass(frozen=True)
class MigrationParityAllowance:
    """A bounded approval for exactly one M08 observation.

    The approval is deliberately a hashed external reference.  This G0 layer
    cannot assert that a product/data approver exists; it only rejects missing,
    expired or misbound approval material.
    """

    observation_hash: str
    reason_code: str
    approval_reference_hash: str
    expires_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "observation_hash", _sha256(self.observation_hash, field="observation_hash"))
        object.__setattr__(self, "reason_code", _reason_code(self.reason_code, field="reason_code"))
        object.__setattr__(
            self,
            "approval_reference_hash",
            _sha256(self.approval_reference_hash, field="approval_reference_hash"),
        )
        object.__setattr__(self, "expires_at", _utc_datetime(self.expires_at, field="expires_at"))

    def summary(self) -> dict[str, object]:
        return {
            "approvalReferenceHash": self.approval_reference_hash,
            "expiresAt": _utc_timestamp(self.expires_at),
            "observationHash": self.observation_hash,
            "reasonCode": self.reason_code,
        }


@dataclass(frozen=True)
class MigrationParityMismatch:
    observation: MigrationParityObservation
    allowance: Optional[MigrationParityAllowance]
    allowance_status: str

    def summary(self) -> dict[str, object]:
        result = self.observation.summary()
        result["allowanceStatus"] = self.allowance_status
        if self.allowance is not None:
            result["allowance"] = self.allowance.summary()
        return result


@dataclass(frozen=True)
class MigrationParityShadowReport:
    """Deterministic C05 comparison result that cannot authorize cutover."""

    window: MigrationParityComparisonWindow
    observations: Tuple[MigrationParityObservation, ...]
    mismatches: Tuple[MigrationParityMismatch, ...]
    duplicate_input_count: int
    observed_sample_count: int
    report_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.window, MigrationParityComparisonWindow):
            raise OwnerTruthMigrationParityShadowError("comparison window is required")
        normalized_observations = tuple(
            sorted(
                (MigrationParityObservation(**observation.__dict__) for observation in self.observations),
                key=lambda item: item.semantic_key,
            )
        )
        if len({item.semantic_key for item in normalized_observations}) != len(normalized_observations):
            raise OwnerTruthMigrationParityShadowError("parity observations must be unique")
        object.__setattr__(self, "observations", normalized_observations)
        if any(not isinstance(item, MigrationParityMismatch) for item in self.mismatches):
            raise OwnerTruthMigrationParityShadowError("mismatches must contain MigrationParityMismatch")
        normalized_mismatches = tuple(
            sorted(
                self.mismatches,
                key=lambda item: item.observation.semantic_key,
            )
        )
        mismatch_hashes = {item.observation.observation_hash for item in normalized_mismatches}
        expected_mismatch_hashes = {
            item.observation_hash for item in normalized_observations if not item.is_match
        }
        if mismatch_hashes != expected_mismatch_hashes:
            raise OwnerTruthMigrationParityShadowError(
                "mismatch observations must exactly match non-equal comparisons"
            )
        if len(mismatch_hashes) != len(normalized_mismatches):
            raise OwnerTruthMigrationParityShadowError("mismatch observations must be unique")
        for mismatch in normalized_mismatches:
            if mismatch.observation.is_match:
                raise OwnerTruthMigrationParityShadowError("matching observations cannot have a mismatch")
            if mismatch.observation.mismatch_code is MigrationParityMismatchCode.M08:
                if mismatch.allowance_status not in {"approved", "missing", "expired"}:
                    raise OwnerTruthMigrationParityShadowError("M08 allowance status is invalid")
            elif mismatch.allowance is not None or mismatch.allowance_status != "notApplicable":
                raise OwnerTruthMigrationParityShadowError(
                    "only M08 mismatches can carry an allowance"
                )
        object.__setattr__(self, "mismatches", normalized_mismatches)
        object.__setattr__(
            self,
            "duplicate_input_count",
            _nonnegative_int(self.duplicate_input_count, field="duplicate_input_count"),
        )
        object.__setattr__(
            self,
            "observed_sample_count",
            _positive_int(self.observed_sample_count, field="observed_sample_count"),
        )
        sample_ids = {item.sample_id_hash for item in normalized_observations}
        if self.observed_sample_count != len(sample_ids):
            raise OwnerTruthMigrationParityShadowError(
                "observed_sample_count must equal unique observation samples"
            )
        if self.observed_sample_count != self.window.expected_sample_count:
            raise OwnerTruthMigrationParityShadowError(
                "observed sample count must equal the declared comparison denominator"
            )
        object.__setattr__(self, "report_hash", _sha256(self.report_hash, field="report_hash"))
        if self.report_hash != _report_hash(
            window=self.window,
            observations=normalized_observations,
            mismatches=normalized_mismatches,
            duplicate_input_count=self.duplicate_input_count,
            observed_sample_count=self.observed_sample_count,
        ):
            raise OwnerTruthMigrationParityShadowError("migration parity report hash is invalid")

    @property
    def blocking_mismatch_count(self) -> int:
        return sum(
            1
            for mismatch in self.mismatches
            if mismatch.observation.mismatch_code is not MigrationParityMismatchCode.M08
        )

    @property
    def approved_m08_difference_count(self) -> int:
        return sum(1 for mismatch in self.mismatches if mismatch.allowance_status == "approved")

    @property
    def unresolved_m08_difference_count(self) -> int:
        return sum(
            1
            for mismatch in self.mismatches
            if mismatch.observation.mismatch_code is MigrationParityMismatchCode.M08
            and mismatch.allowance_status != "approved"
        )

    @property
    def ready_for_next_gate(self) -> bool:
        return self.blocking_mismatch_count == 0 and self.unresolved_m08_difference_count == 0

    def value_free_summary(self) -> dict[str, object]:
        mismatch_counts: Dict[str, int] = {}
        surface_counts: Dict[str, int] = {}
        severity_counts: Dict[str, int] = {}
        for mismatch in self.mismatches:
            code = mismatch.observation.mismatch_code.value
            surface = mismatch.observation.surface.value
            severity = mismatch.observation.severity.value
            mismatch_counts[code] = mismatch_counts.get(code, 0) + 1
            surface_counts[surface] = surface_counts.get(surface, 0) + 1
            severity_counts[severity] = severity_counts.get(severity, 0) + 1
        return {
            "approvedM08DifferenceCount": self.approved_m08_difference_count,
            "authorityEpochChanged": False,
            "blockingMismatchCount": self.blocking_mismatch_count,
            "commandEffectExecutionCount": 0,
            "cutoverAllowed": False,
            "duplicateInputCount": self.duplicate_input_count,
            "legacyWriterRetired": False,
            "matchCount": len(self.observations) - len(self.mismatches),
            "mismatchCount": len(self.mismatches),
            "mismatchCountsByCode": dict(sorted(mismatch_counts.items())),
            "mismatchCountsBySurface": dict(sorted(surface_counts.items())),
            "mismatches": [item.summary() for item in self.mismatches],
            "objectCopyExecutionCount": 0,
            "observedSampleCount": self.observed_sample_count,
            "providerCallCount": 0,
            "providerCostCharged": False,
            "readyForNextGate": self.ready_for_next_gate,
            "reportHash": self.report_hash,
            "schemaVersion": OWNER_TRUTH_MIGRATION_PARITY_SHADOW_SCHEMA_VERSION,
            "severityCounts": dict(sorted(severity_counts.items())),
            "shadowOnly": True,
            "unresolvedM08DifferenceCount": self.unresolved_m08_difference_count,
            "window": self.window.summary(),
            "writeOperationCount": 0,
        }


def _nonnegative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise OwnerTruthMigrationParityShadowError(
            "%s must be a non-negative integer" % field
        )
    return value


def _allowances_by_observation(
    allowances: Iterable[MigrationParityAllowance],
) -> Mapping[str, MigrationParityAllowance]:
    normalized = tuple(allowances)
    if any(not isinstance(item, MigrationParityAllowance) for item in normalized):
        raise OwnerTruthMigrationParityShadowError(
            "allowances must contain MigrationParityAllowance"
        )
    result = {item.observation_hash: item for item in normalized}
    if len(result) != len(normalized):
        raise OwnerTruthMigrationParityShadowError("allowances must be unique per observation")
    return result


def _report_hash(
    *,
    window: MigrationParityComparisonWindow,
    observations: Sequence[MigrationParityObservation],
    mismatches: Sequence[MigrationParityMismatch],
    duplicate_input_count: int,
    observed_sample_count: int,
) -> str:
    return _digest(
        {
            "duplicateInputCount": duplicate_input_count,
            "mismatches": [item.summary() for item in mismatches],
            "observations": [item.summary() for item in observations],
            "observedSampleCount": observed_sample_count,
            "schemaVersion": OWNER_TRUTH_MIGRATION_PARITY_SHADOW_SCHEMA_VERSION,
            "window": window.summary(),
        }
    )


def build_migration_parity_shadow_report(
    *,
    window: MigrationParityComparisonWindow,
    observations: Iterable[MigrationParityObservation],
    allowances: Iterable[MigrationParityAllowance] = (),
    as_of: datetime,
) -> MigrationParityShadowReport:
    """Compare hash-only legacy/V4 observations without execution capability.

    M01–M07 are always promotion-blocking.  M08 may be recorded only with a
    current, explicitly bound approval.  This function intentionally does not
    infer a missing comparator, execute a command, copy an object, query a
    Provider, or interpret an approval's human meaning.
    """

    if not isinstance(window, MigrationParityComparisonWindow):
        raise OwnerTruthMigrationParityShadowError("comparison window is required")
    normalized_as_of = _utc_datetime(as_of, field="as_of")
    source_observations = tuple(observations)
    if not source_observations:
        raise OwnerTruthMigrationParityShadowError("at least one parity observation is required")
    if any(not isinstance(item, MigrationParityObservation) for item in source_observations):
        raise OwnerTruthMigrationParityShadowError(
            "observations must contain MigrationParityObservation"
        )
    by_key: Dict[Tuple[str, str, str], MigrationParityObservation] = {}
    duplicate_input_count = 0
    for observation in source_observations:
        existing = by_key.get(observation.semantic_key)
        if existing is None:
            by_key[observation.semantic_key] = observation
        elif existing.observation_hash == observation.observation_hash:
            duplicate_input_count += 1
        else:
            raise OwnerTruthMigrationParityShadowConflict(
                "parity observation identity was rebound to different values"
            )
    normalized_observations = tuple(
        sorted(by_key.values(), key=lambda item: item.semantic_key)
    )
    allowances_by_observation = _allowances_by_observation(allowances)
    known_observation_hashes = {item.observation_hash for item in normalized_observations}
    unknown_allowances = set(allowances_by_observation) - known_observation_hashes
    if unknown_allowances:
        raise OwnerTruthMigrationParityShadowError(
            "allowance references an unknown parity observation"
        )
    mismatches = []
    for observation in normalized_observations:
        if observation.is_match:
            if observation.observation_hash in allowances_by_observation:
                raise OwnerTruthMigrationParityShadowError(
                    "allowance cannot target a matching observation"
                )
            continue
        allowance = allowances_by_observation.get(observation.observation_hash)
        if observation.mismatch_code is MigrationParityMismatchCode.M08:
            if allowance is None:
                allowance_status = "missing"
            elif allowance.expires_at <= normalized_as_of:
                allowance_status = "expired"
            else:
                allowance_status = "approved"
        else:
            if allowance is not None:
                raise OwnerTruthMigrationParityShadowError(
                    "allowance cannot waive an M01-M07 mismatch"
                )
            allowance_status = "notApplicable"
        mismatches.append(
            MigrationParityMismatch(
                observation=observation,
                allowance=allowance,
                allowance_status=allowance_status,
            )
        )
    observed_sample_count = len({item.sample_id_hash for item in normalized_observations})
    report_hash = _report_hash(
        window=window,
        observations=normalized_observations,
        mismatches=mismatches,
        duplicate_input_count=duplicate_input_count,
        observed_sample_count=observed_sample_count,
    )
    return MigrationParityShadowReport(
        window=window,
        observations=normalized_observations,
        mismatches=tuple(mismatches),
        duplicate_input_count=duplicate_input_count,
        observed_sample_count=observed_sample_count,
        report_hash=report_hash,
    )


__all__ = [
    "MigrationParityAllowance",
    "MigrationParityComparisonWindow",
    "MigrationParityDimension",
    "MigrationParityMismatch",
    "MigrationParityMismatchCode",
    "MigrationParityObservation",
    "MigrationParitySeverity",
    "MigrationParityShadowReport",
    "MigrationParitySurface",
    "OWNER_TRUTH_MIGRATION_PARITY_SHADOW_SCHEMA_VERSION",
    "OwnerTruthMigrationParityShadowConflict",
    "OwnerTruthMigrationParityShadowError",
    "build_migration_parity_scope_hash",
    "build_migration_parity_shadow_report",
]
