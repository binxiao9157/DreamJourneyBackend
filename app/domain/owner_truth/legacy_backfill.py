"""Value-free legacy backfill admission planning for Owner Truth.

The existing legacy inventory classifies old records without creating V4
authority.  This module adds the next deliberately constrained step: a
deterministic plan that says which *future* review or replay action would be
required for every inventory entry.  It never creates Sources, Candidates,
DecisionReceipts or MemoryVersions, and it cannot authorize a cutover.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import re
from uuid import UUID, uuid5

from app.domain.owner_truth.legacy_migration import (
    LegacyMigrationClassification,
    LegacyMigrationDisposition,
    LegacyMigrationDomain,
    LegacyMigrationEntry,
    LegacyMigrationInventory,
    OwnerTruthLegacyMigrationError,
)


OWNER_TRUTH_LEGACY_BACKFILL_PLAN_SCHEMA_VERSION = (
    "owner-truth-legacy-backfill-admission-plan-v1"
)
_PLAN_NAMESPACE = UUID("cbfc2cd7-5e50-47e0-b5ad-24f1ec79ce3d")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class OwnerTruthLegacyBackfillPlanError(OwnerTruthLegacyMigrationError):
    """Raised when a value-free legacy admission plan is unsafe or malformed."""


class LegacyBackfillAdmissionAction(str, Enum):
    """The only non-authorizing dispositions emitted by C03."""

    REQUIRE_INDEPENDENT_LINEAGE_REPLAY = "requireIndependentLineageReplay"
    REQUIRE_OWNER_CANDIDATE_REVIEW = "requireOwnerCandidateReview"
    REQUIRE_EVIDENCE_REVIEW = "requireEvidenceReview"
    QUARANTINED = "quarantined"
    EXCLUDED = "excluded"


_ACTION_BY_DISPOSITION = {
    LegacyMigrationDisposition.MEMORY_V1_ELIGIBLE: (
        LegacyBackfillAdmissionAction.REQUIRE_INDEPENDENT_LINEAGE_REPLAY,
        "provenLegacyEvidenceRequiresIndependentLineageReplay",
    ),
    LegacyMigrationDisposition.CANDIDATE_ONLY: (
        LegacyBackfillAdmissionAction.REQUIRE_OWNER_CANDIDATE_REVIEW,
        "legacyObservationRequiresOwnerCandidateReview",
    ),
    LegacyMigrationDisposition.REVIEW_QUEUE: (
        LegacyBackfillAdmissionAction.REQUIRE_EVIDENCE_REVIEW,
        "legacyEvidenceRequiresReview",
    ),
    LegacyMigrationDisposition.QUARANTINE: (
        LegacyBackfillAdmissionAction.QUARANTINED,
        "legacyOwnerOrAuthorityConflictQuarantined",
    ),
    LegacyMigrationDisposition.EXCLUDED: (
        LegacyBackfillAdmissionAction.EXCLUDED,
        "legacyDomainExcludedFromBackfill",
    ),
}


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _nonblank(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise OwnerTruthLegacyBackfillPlanError(f"{field} is required")
    return normalized


def _sha256(value: object, *, field: str) -> str:
    normalized = _nonblank(value, field=field).lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise OwnerTruthLegacyBackfillPlanError(f"{field} must be a sha256 digest")
    return normalized


def _uuid(value: object, *, field: str) -> str:
    normalized = _nonblank(value, field=field)
    try:
        return str(UUID(normalized))
    except (TypeError, ValueError, AttributeError) as exc:
        raise OwnerTruthLegacyBackfillPlanError(f"{field} must be a UUID") from exc


def _action_for(entry: LegacyMigrationEntry) -> tuple[LegacyBackfillAdmissionAction, str]:
    if not isinstance(entry, LegacyMigrationEntry):
        raise OwnerTruthLegacyBackfillPlanError("legacy inventory entry is required")
    action = _ACTION_BY_DISPOSITION.get(entry.disposition)
    if action is None:
        raise OwnerTruthLegacyBackfillPlanError("legacy disposition has no admission action")
    return action


@dataclass(frozen=True)
class LegacyBackfillAdmissionPlanEntry:
    """A value-free action bound to exactly one immutable inventory entry."""

    domain: LegacyMigrationDomain
    legacy_id_hash: str
    record_hash: str
    classification: LegacyMigrationClassification
    disposition: LegacyMigrationDisposition
    action: LegacyBackfillAdmissionAction
    reason_code: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "domain", LegacyMigrationDomain(self.domain))
        object.__setattr__(self, "legacy_id_hash", _sha256(self.legacy_id_hash, field="legacy_id_hash"))
        object.__setattr__(self, "record_hash", _sha256(self.record_hash, field="record_hash"))
        object.__setattr__(
            self,
            "classification",
            LegacyMigrationClassification(self.classification),
        )
        object.__setattr__(
            self,
            "disposition",
            LegacyMigrationDisposition(self.disposition),
        )
        object.__setattr__(self, "action", LegacyBackfillAdmissionAction(self.action))
        expected = _ACTION_BY_DISPOSITION.get(self.disposition)
        if expected is None:
            raise OwnerTruthLegacyBackfillPlanError(
                "legacy disposition has no admission action"
            )
        expected_action, expected_reason = expected
        if self.action is not expected_action:
            raise OwnerTruthLegacyBackfillPlanError(
                "legacy admission action does not match the inventory disposition"
            )
        if _nonblank(self.reason_code, field="reason_code") != expected_reason:
            raise OwnerTruthLegacyBackfillPlanError(
                "legacy admission reason does not match the inventory disposition"
            )

    @classmethod
    def from_inventory_entry(cls, entry: LegacyMigrationEntry) -> "LegacyBackfillAdmissionPlanEntry":
        action, reason_code = _action_for(entry)
        return cls(
            domain=entry.domain,
            legacy_id_hash=entry.legacy_id_hash,
            record_hash=entry.record_hash,
            classification=entry.classification,
            disposition=entry.disposition,
            action=action,
            reason_code=reason_code,
        )

    def summary(self) -> dict[str, str]:
        return {
            "action": self.action.value,
            "classification": self.classification.value,
            "disposition": self.disposition.value,
            "domain": self.domain.value,
            "legacyIdHash": self.legacy_id_hash,
            "reasonCode": self.reason_code,
            "recordHash": self.record_hash,
            "targetState": "notCreated",
        }


@dataclass(frozen=True)
class LegacyBackfillAdmissionPlan:
    """Deterministic, non-authorizing C03 plan bound to one inventory snapshot."""

    plan_id: str
    inventory_run_id: str
    vault_id: str
    owner_subject_id: str
    classifier_version: str
    inventory_hash: str
    authority_epoch: int
    entries: tuple[LegacyBackfillAdmissionPlanEntry, ...]
    unavailable_domains: tuple[LegacyMigrationDomain, ...]
    scope_hash: str
    plan_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "plan_id", _uuid(self.plan_id, field="plan_id"))
        object.__setattr__(self, "inventory_run_id", _uuid(self.inventory_run_id, field="inventory_run_id"))
        object.__setattr__(self, "vault_id", _nonblank(self.vault_id, field="vault_id"))
        object.__setattr__(
            self,
            "owner_subject_id",
            _nonblank(self.owner_subject_id, field="owner_subject_id"),
        )
        object.__setattr__(
            self,
            "classifier_version",
            _nonblank(self.classifier_version, field="classifier_version"),
        )
        object.__setattr__(self, "inventory_hash", _sha256(self.inventory_hash, field="inventory_hash"))
        if isinstance(self.authority_epoch, bool) or not isinstance(self.authority_epoch, int):
            raise OwnerTruthLegacyBackfillPlanError("authority_epoch must be an integer")
        if self.authority_epoch < 0:
            raise OwnerTruthLegacyBackfillPlanError("authority_epoch must be non-negative")
        normalized_entries = tuple(
            sorted(
                (LegacyBackfillAdmissionPlanEntry(**entry.__dict__) for entry in self.entries),
                key=lambda item: (item.domain.value, item.legacy_id_hash),
            )
        )
        entry_keys = [(entry.domain.value, entry.legacy_id_hash) for entry in normalized_entries]
        if len(entry_keys) != len(set(entry_keys)):
            raise OwnerTruthLegacyBackfillPlanError("legacy admission plan contains duplicate entries")
        object.__setattr__(self, "entries", normalized_entries)
        object.__setattr__(
            self,
            "unavailable_domains",
            tuple(
                sorted(
                    {LegacyMigrationDomain(domain) for domain in self.unavailable_domains},
                    key=lambda item: item.value,
                )
            ),
        )
        object.__setattr__(self, "scope_hash", _sha256(self.scope_hash, field="scope_hash"))
        object.__setattr__(self, "plan_hash", _sha256(self.plan_hash, field="plan_hash"))
        if self.scope_hash != _scope_hash(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            authority_epoch=self.authority_epoch,
        ):
            raise OwnerTruthLegacyBackfillPlanError("legacy admission plan scope hash is invalid")
        if self.plan_hash != _plan_hash(
            inventory_run_id=self.inventory_run_id,
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            classifier_version=self.classifier_version,
            inventory_hash=self.inventory_hash,
            authority_epoch=self.authority_epoch,
            entries=normalized_entries,
            unavailable_domains=self.unavailable_domains,
        ):
            raise OwnerTruthLegacyBackfillPlanError("legacy admission plan hash is invalid")
        expected_plan_id = _plan_id(
            inventory_run_id=self.inventory_run_id,
            authority_epoch=self.authority_epoch,
            plan_hash=self.plan_hash,
        )
        if self.plan_id != expected_plan_id:
            raise OwnerTruthLegacyBackfillPlanError("legacy admission plan id is invalid")

    @property
    def action_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self.entries:
            counts[entry.action.value] = counts.get(entry.action.value, 0) + 1
        return dict(sorted(counts.items()))

    def summary(self) -> dict[str, object]:
        """Return a QA-safe report without raw legacy identifiers or payloads."""

        return {
            "actionCounts": self.action_counts,
            "authorityEpoch": self.authority_epoch,
            "classifierVersion": self.classifier_version,
            "cutoverAllowed": False,
            "entryCount": len(self.entries),
            "inventoryHash": self.inventory_hash,
            "inventoryRunId": self.inventory_run_id,
            "legacyWriterRetired": False,
            "planHash": self.plan_hash,
            "planId": self.plan_id,
            "schemaVersion": OWNER_TRUTH_LEGACY_BACKFILL_PLAN_SCHEMA_VERSION,
            "scopeHash": self.scope_hash,
            "targetState": "notCreated",
            "unavailableDomains": [domain.value for domain in self.unavailable_domains],
            "vaultId": self.vault_id,
        }


def _scope_hash(*, vault_id: str, owner_subject_id: str, authority_epoch: int) -> str:
    return _hash(
        {
            "authorityEpoch": authority_epoch,
            "ownerSubjectId": owner_subject_id,
            "vaultId": vault_id,
        }
    )


def _plan_hash(
    *,
    inventory_run_id: str,
    vault_id: str,
    owner_subject_id: str,
    classifier_version: str,
    inventory_hash: str,
    authority_epoch: int,
    entries: tuple[LegacyBackfillAdmissionPlanEntry, ...],
    unavailable_domains: tuple[LegacyMigrationDomain, ...],
) -> str:
    return _hash(
        {
            "authorityEpoch": authority_epoch,
            "classifierVersion": classifier_version,
            "entries": [entry.summary() for entry in entries],
            "inventoryHash": inventory_hash,
            "inventoryRunId": inventory_run_id,
            "ownerSubjectId": owner_subject_id,
            "schemaVersion": OWNER_TRUTH_LEGACY_BACKFILL_PLAN_SCHEMA_VERSION,
            "unavailableDomains": [domain.value for domain in unavailable_domains],
            "vaultId": vault_id,
        }
    )


def _plan_id(*, inventory_run_id: str, authority_epoch: int, plan_hash: str) -> str:
    return str(uuid5(_PLAN_NAMESPACE, f"{inventory_run_id}:{authority_epoch}:{plan_hash}"))


def build_legacy_backfill_admission_plan(
    *,
    inventory_run_id: str,
    inventory: LegacyMigrationInventory,
    owner_subject_id: str,
    authority_epoch: int,
) -> LegacyBackfillAdmissionPlan:
    """Build a stable C03 action plan without admitting any migration write.

    ``memoryV1Eligible`` means only that historic evidence is complete enough
    to queue an independently reviewed lineage replay.  It does not mean the
    record has a V4 target or may advance the Vault authority epoch.
    """

    if not isinstance(inventory, LegacyMigrationInventory):
        raise OwnerTruthLegacyBackfillPlanError("legacy inventory is required")
    run_id = _uuid(inventory_run_id, field="inventory_run_id")
    owner = _nonblank(owner_subject_id, field="owner_subject_id")
    if isinstance(authority_epoch, bool) or not isinstance(authority_epoch, int):
        raise OwnerTruthLegacyBackfillPlanError("authority_epoch must be an integer")
    if authority_epoch < 0:
        raise OwnerTruthLegacyBackfillPlanError("authority_epoch must be non-negative")

    entries = tuple(
        sorted(
            (LegacyBackfillAdmissionPlanEntry.from_inventory_entry(entry) for entry in inventory.entries),
            key=lambda item: (item.domain.value, item.legacy_id_hash),
        )
    )
    unavailable_domains = tuple(
        sorted({LegacyMigrationDomain(domain) for domain in inventory.unavailable_domains}, key=lambda item: item.value)
    )
    scope_hash = _scope_hash(
        vault_id=inventory.vault_id,
        owner_subject_id=owner,
        authority_epoch=authority_epoch,
    )
    plan_hash = _plan_hash(
        inventory_run_id=run_id,
        vault_id=inventory.vault_id,
        owner_subject_id=owner,
        classifier_version=inventory.classifier_version,
        inventory_hash=inventory.inventory_hash,
        authority_epoch=authority_epoch,
        entries=entries,
        unavailable_domains=unavailable_domains,
    )
    return LegacyBackfillAdmissionPlan(
        plan_id=_plan_id(
            inventory_run_id=run_id,
            authority_epoch=authority_epoch,
            plan_hash=plan_hash,
        ),
        inventory_run_id=run_id,
        vault_id=inventory.vault_id,
        owner_subject_id=owner,
        classifier_version=inventory.classifier_version,
        inventory_hash=inventory.inventory_hash,
        authority_epoch=authority_epoch,
        entries=entries,
        unavailable_domains=unavailable_domains,
        scope_hash=scope_hash,
        plan_hash=plan_hash,
    )


__all__ = [
    "OWNER_TRUTH_LEGACY_BACKFILL_PLAN_SCHEMA_VERSION",
    "LegacyBackfillAdmissionAction",
    "LegacyBackfillAdmissionPlan",
    "LegacyBackfillAdmissionPlanEntry",
    "OwnerTruthLegacyBackfillPlanError",
    "build_legacy_backfill_admission_plan",
]
