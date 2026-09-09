"""B-enhanced Owner Truth migration dry-run contract.

The legacy inventory deliberately contains hashes and evidence states rather
than private bodies.  This module turns that inventory's non-authorizing
admission plan into an auditable B-migration report without creating a
Source, Candidate, MemoryVersion, embedding or narrative artifact.  In
particular, a complete legacy lineage is *not* treated as permission to copy
old JSON into current formal memory: it must be replayed through the current
review path so V5 subject, provenance, polarity and time contracts apply.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
from uuid import UUID, uuid5

from app.domain.owner_truth.legacy_backfill import (
    LegacyBackfillAdmissionAction,
    LegacyBackfillAdmissionPlan,
    LegacyBackfillAdmissionPlanEntry,
    OwnerTruthLegacyBackfillPlanError,
)


OWNER_TRUTH_B_MIGRATION_DRY_RUN_SCHEMA_VERSION = (
    "owner-truth-b-migration-dry-run-v1"
)
_RUN_NAMESPACE = UUID("dfb75548-d4e2-430a-98c5-c2c82b9e7651")
_SHA256_LENGTH = 64


class OwnerTruthBMigrationDryRunError(OwnerTruthLegacyBackfillPlanError):
    """A B migration report is malformed or does not match its checkpoint."""


class OwnerTruthBMigrationDisposition(str, Enum):
    """What a legacy entry may do after this dry run, never a target write."""

    REPLAY_CURRENT_REVIEW_PATH = "replayCurrentReviewPath"
    OWNER_REVIEW_REQUIRED = "ownerReviewRequired"
    EVIDENCE_REVIEW_REQUIRED = "evidenceReviewRequired"
    QUARANTINED = "quarantined"
    EXCLUDED = "excluded"


_DISPOSITION_BY_ACTION = {
    LegacyBackfillAdmissionAction.REQUIRE_INDEPENDENT_LINEAGE_REPLAY: (
        OwnerTruthBMigrationDisposition.REPLAY_CURRENT_REVIEW_PATH,
        False,
        "verifiedLegacyLineageMustReplayThroughCurrentReviewPath",
    ),
    LegacyBackfillAdmissionAction.REQUIRE_OWNER_CANDIDATE_REVIEW: (
        OwnerTruthBMigrationDisposition.OWNER_REVIEW_REQUIRED,
        True,
        "legacyObservationRequiresOwnerReviewBeforeAnyCurrentCandidate",
    ),
    LegacyBackfillAdmissionAction.REQUIRE_EVIDENCE_REVIEW: (
        OwnerTruthBMigrationDisposition.EVIDENCE_REVIEW_REQUIRED,
        True,
        "legacyEvidenceRequiresOwnerReviewBeforeAnyCurrentCandidate",
    ),
    LegacyBackfillAdmissionAction.QUARANTINED: (
        OwnerTruthBMigrationDisposition.QUARANTINED,
        True,
        "legacyOwnerOrAuthorityConflictCannotEnterCurrentMemory",
    ),
    LegacyBackfillAdmissionAction.EXCLUDED: (
        OwnerTruthBMigrationDisposition.EXCLUDED,
        False,
        "legacyConversationOrExcludedDomainCannotEnterCurrentMemory",
    ),
}

_SEMANTIC_FIELDS = (
    "claimSubject",
    "provenance",
    "polarity",
    "timeRange",
    "perspective",
)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _nonblank(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise OwnerTruthBMigrationDryRunError(f"{field} is required")
    return normalized


def _sha256(value: object, *, field: str) -> str:
    normalized = _nonblank(value, field=field).lower()
    if len(normalized) != _SHA256_LENGTH or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise OwnerTruthBMigrationDryRunError(f"{field} must be a sha256 digest")
    return normalized


def _uuid(value: object, *, field: str) -> str:
    normalized = _nonblank(value, field=field)
    try:
        return str(UUID(normalized))
    except (TypeError, ValueError, AttributeError) as error:
        raise OwnerTruthBMigrationDryRunError(f"{field} must be a UUID") from error


@dataclass(frozen=True)
class OwnerTruthBMigrationDryRunEntry:
    """A value-free per-row mapping result bound to one admission-plan entry."""

    domain: str
    legacy_id_hash: str
    record_hash: str
    admission_action: LegacyBackfillAdmissionAction
    disposition: OwnerTruthBMigrationDisposition
    requires_owner_review: bool
    reason_code: str
    semantic_field_policy: tuple[str, ...]
    target_state: str = "notCreated"

    def __post_init__(self) -> None:
        object.__setattr__(self, "domain", _nonblank(self.domain, field="domain"))
        object.__setattr__(self, "legacy_id_hash", _sha256(self.legacy_id_hash, field="legacy_id_hash"))
        object.__setattr__(self, "record_hash", _sha256(self.record_hash, field="record_hash"))
        object.__setattr__(self, "admission_action", LegacyBackfillAdmissionAction(self.admission_action))
        object.__setattr__(self, "disposition", OwnerTruthBMigrationDisposition(self.disposition))
        if not isinstance(self.requires_owner_review, bool):
            raise OwnerTruthBMigrationDryRunError("requires_owner_review must be a boolean")
        expected = _DISPOSITION_BY_ACTION.get(self.admission_action)
        if expected is None:
            raise OwnerTruthBMigrationDryRunError("unsupported legacy admission action")
        expected_disposition, expected_review, expected_reason = expected
        if self.disposition is not expected_disposition:
            raise OwnerTruthBMigrationDryRunError("migration disposition does not match admission action")
        if self.requires_owner_review is not expected_review:
            raise OwnerTruthBMigrationDryRunError("owner review requirement does not match admission action")
        if _nonblank(self.reason_code, field="reason_code") != expected_reason:
            raise OwnerTruthBMigrationDryRunError("migration reason does not match admission action")
        policies = tuple(sorted({_nonblank(item, field="semantic_field_policy") for item in self.semantic_field_policy}))
        if policies != tuple(sorted(_SEMANTIC_FIELDS)):
            raise OwnerTruthBMigrationDryRunError(
                "every B semantic field must be explicitly preserved as unknown or replayed"
            )
        object.__setattr__(self, "semantic_field_policy", policies)
        if self.target_state != "notCreated":
            raise OwnerTruthBMigrationDryRunError("dry run must not create a migration target")

    @classmethod
    def from_plan_entry(cls, entry: LegacyBackfillAdmissionPlanEntry) -> "OwnerTruthBMigrationDryRunEntry":
        if not isinstance(entry, LegacyBackfillAdmissionPlanEntry):
            raise OwnerTruthBMigrationDryRunError("legacy admission plan entry is required")
        disposition, requires_review, reason = _DISPOSITION_BY_ACTION[entry.action]
        return cls(
            domain=entry.domain.value,
            legacy_id_hash=entry.legacy_id_hash,
            record_hash=entry.record_hash,
            admission_action=entry.action,
            disposition=disposition,
            requires_owner_review=requires_review,
            reason_code=reason,
            semantic_field_policy=_SEMANTIC_FIELDS,
        )

    def summary(self) -> dict[str, object]:
        return {
            "admissionAction": self.admission_action.value,
            "disposition": self.disposition.value,
            "domain": self.domain,
            "legacyIdHash": self.legacy_id_hash,
            "reasonCode": self.reason_code,
            "recordHash": self.record_hash,
            "requiresOwnerReview": self.requires_owner_review,
            "semanticFieldPolicy": list(self.semantic_field_policy),
            "targetState": self.target_state,
        }


def _report_hash(
    *,
    inventory_run_id: str,
    plan: LegacyBackfillAdmissionPlan,
    entries: tuple[OwnerTruthBMigrationDryRunEntry, ...],
) -> str:
    return _hash(
        {
            "authorityEpoch": plan.authority_epoch,
            "entries": [entry.summary() for entry in entries],
            "inventoryHash": plan.inventory_hash,
            "inventoryRunId": inventory_run_id,
            "ownerSubjectId": plan.owner_subject_id,
            "planHash": plan.plan_hash,
            "planId": plan.plan_id,
            "schemaVersion": OWNER_TRUTH_B_MIGRATION_DRY_RUN_SCHEMA_VERSION,
            "scopeHash": plan.scope_hash,
            "vaultId": plan.vault_id,
        }
    )


@dataclass(frozen=True)
class OwnerTruthBMigrationDryRunReport:
    """An immutable no-write report used to fence later B migration execution."""

    report_id: str
    inventory_run_id: str
    vault_id: str
    owner_subject_id: str
    authority_epoch: int
    inventory_hash: str
    plan_id: str
    plan_hash: str
    scope_hash: str
    entries: tuple[OwnerTruthBMigrationDryRunEntry, ...]
    report_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "report_id", _uuid(self.report_id, field="report_id"))
        object.__setattr__(self, "inventory_run_id", _uuid(self.inventory_run_id, field="inventory_run_id"))
        object.__setattr__(self, "vault_id", _nonblank(self.vault_id, field="vault_id"))
        object.__setattr__(self, "owner_subject_id", _nonblank(self.owner_subject_id, field="owner_subject_id"))
        if isinstance(self.authority_epoch, bool) or not isinstance(self.authority_epoch, int):
            raise OwnerTruthBMigrationDryRunError("authority_epoch must be an integer")
        if self.authority_epoch < 0:
            raise OwnerTruthBMigrationDryRunError("authority_epoch must be non-negative")
        object.__setattr__(self, "inventory_hash", _sha256(self.inventory_hash, field="inventory_hash"))
        object.__setattr__(self, "plan_id", _uuid(self.plan_id, field="plan_id"))
        object.__setattr__(self, "plan_hash", _sha256(self.plan_hash, field="plan_hash"))
        object.__setattr__(self, "scope_hash", _sha256(self.scope_hash, field="scope_hash"))
        normalized_entries = tuple(
            sorted(
                (
                    OwnerTruthBMigrationDryRunEntry(**entry.__dict__)
                    for entry in self.entries
                ),
                key=lambda item: (item.domain, item.legacy_id_hash),
            )
        )
        identity = [(entry.domain, entry.legacy_id_hash) for entry in normalized_entries]
        if len(identity) != len(set(identity)):
            raise OwnerTruthBMigrationDryRunError("dry run contains duplicate legacy entries")
        object.__setattr__(self, "entries", normalized_entries)
        object.__setattr__(self, "report_hash", _sha256(self.report_hash, field="report_hash"))
        expected_hash = _hash(
            {
                "authorityEpoch": self.authority_epoch,
                "entries": [entry.summary() for entry in normalized_entries],
                "inventoryHash": self.inventory_hash,
                "inventoryRunId": self.inventory_run_id,
                "ownerSubjectId": self.owner_subject_id,
                "planHash": self.plan_hash,
                "planId": self.plan_id,
                "schemaVersion": OWNER_TRUTH_B_MIGRATION_DRY_RUN_SCHEMA_VERSION,
                "scopeHash": self.scope_hash,
                "vaultId": self.vault_id,
            }
        )
        if self.report_hash != expected_hash:
            raise OwnerTruthBMigrationDryRunError("dry run report hash is invalid")
        expected_id = str(uuid5(_RUN_NAMESPACE, f"{self.inventory_run_id}:{self.report_hash}"))
        if self.report_id != expected_id:
            raise OwnerTruthBMigrationDryRunError("dry run report id is invalid")

    @property
    def disposition_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self.entries:
            counts[entry.disposition.value] = counts.get(entry.disposition.value, 0) + 1
        return dict(sorted(counts.items()))

    @property
    def requires_owner_review_count(self) -> int:
        return sum(1 for entry in self.entries if entry.requires_owner_review)

    def summary(self) -> dict[str, object]:
        """Value-free output; formal-memory writes are always zero in dry run."""

        return {
            "authorityEpoch": self.authority_epoch,
            "checkpoint": {
                "inventoryHash": self.inventory_hash,
                "planHash": self.plan_hash,
                "scopeHash": self.scope_hash,
            },
            "dispositionCounts": self.disposition_counts,
            "entryCount": len(self.entries),
            "formalMemoryWriteCount": 0,
            "inventoryRunId": self.inventory_run_id,
            "ownerReviewRequiredCount": self.requires_owner_review_count,
            "planId": self.plan_id,
            "reportHash": self.report_hash,
            "reportId": self.report_id,
            "restoreFence": "matchingInventoryPlanAndAuthorityEpochRequired",
            "schemaVersion": OWNER_TRUTH_B_MIGRATION_DRY_RUN_SCHEMA_VERSION,
            "targetState": "notCreated",
            "vaultId": self.vault_id,
        }


def build_owner_truth_b_migration_dry_run_report(
    *,
    inventory_run_id: str,
    plan: LegacyBackfillAdmissionPlan,
) -> OwnerTruthBMigrationDryRunReport:
    """Build one deterministic B report from the immutable admission plan."""

    if not isinstance(plan, LegacyBackfillAdmissionPlan):
        raise OwnerTruthBMigrationDryRunError("legacy admission plan is required")
    normalized_run_id = _uuid(inventory_run_id, field="inventory_run_id")
    if normalized_run_id != plan.inventory_run_id:
        raise OwnerTruthBMigrationDryRunError("inventory run does not match admission plan")
    entries = tuple(
        OwnerTruthBMigrationDryRunEntry.from_plan_entry(entry) for entry in plan.entries
    )
    report_hash = _report_hash(
        inventory_run_id=normalized_run_id,
        plan=plan,
        entries=entries,
    )
    return OwnerTruthBMigrationDryRunReport(
        report_id=str(uuid5(_RUN_NAMESPACE, f"{normalized_run_id}:{report_hash}")),
        inventory_run_id=normalized_run_id,
        vault_id=plan.vault_id,
        owner_subject_id=plan.owner_subject_id,
        authority_epoch=plan.authority_epoch,
        inventory_hash=plan.inventory_hash,
        plan_id=plan.plan_id,
        plan_hash=plan.plan_hash,
        scope_hash=plan.scope_hash,
        entries=entries,
        report_hash=report_hash,
    )


def require_owner_truth_b_migration_restore_compatible(
    *,
    report: OwnerTruthBMigrationDryRunReport,
    current_plan: LegacyBackfillAdmissionPlan,
) -> None:
    """Reject recovery if any current authority or immutable inventory changed."""

    if not isinstance(report, OwnerTruthBMigrationDryRunReport):
        raise OwnerTruthBMigrationDryRunError("B dry run report is required")
    if not isinstance(current_plan, LegacyBackfillAdmissionPlan):
        raise OwnerTruthBMigrationDryRunError("current legacy admission plan is required")
    if (
        report.inventory_run_id != current_plan.inventory_run_id
        or report.vault_id != current_plan.vault_id
        or report.owner_subject_id != current_plan.owner_subject_id
        or report.authority_epoch != current_plan.authority_epoch
        or report.inventory_hash != current_plan.inventory_hash
        or report.plan_id != current_plan.plan_id
        or report.plan_hash != current_plan.plan_hash
        or report.scope_hash != current_plan.scope_hash
    ):
        raise OwnerTruthBMigrationDryRunError(
            "B migration recovery requires the same inventory, plan and authority checkpoint"
        )


__all__ = [
    "OWNER_TRUTH_B_MIGRATION_DRY_RUN_SCHEMA_VERSION",
    "OwnerTruthBMigrationDisposition",
    "OwnerTruthBMigrationDryRunEntry",
    "OwnerTruthBMigrationDryRunError",
    "OwnerTruthBMigrationDryRunReport",
    "build_owner_truth_b_migration_dry_run_report",
    "require_owner_truth_b_migration_restore_compatible",
]
