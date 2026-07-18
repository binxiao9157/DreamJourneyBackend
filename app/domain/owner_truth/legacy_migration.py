"""Value-free legacy evidence classification for Owner Truth migration.

This module is deliberately a *read-only* migration boundary.  It turns
legacy record metadata and hashes into a deterministic disposition without
creating Sources, Candidates, Memories, or any public read-path effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
from typing import Iterable, Mapping


class OwnerTruthLegacyMigrationError(ValueError):
    """Raised when a legacy inventory cannot be safely classified."""


class LegacyMigrationDomain(str, Enum):
    ARCHIVE_ITEM = "archiveItem"
    KB_SNAPSHOT = "kbSnapshot"
    KB_CHANGE = "kbChange"
    KB_RECEIPT = "kbReceipt"
    MEMORY = "memory"
    CONVERSATION_CACHE = "conversationCache"


class LegacyMigrationClassification(str, Enum):
    PROVEN_CONFIRMED = "proven_confirmed"
    NEEDS_REVIEW = "needs_review"
    OBSERVED_CANDIDATE = "observed_candidate"
    QUARANTINE = "quarantine"
    DO_NOT_MIGRATE = "do_not_migrate"


class LegacyEvidenceState(str, Enum):
    VERIFIED = "verified"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    NOT_APPLICABLE = "notApplicable"


class LegacyMigrationDisposition(str, Enum):
    MEMORY_V1_ELIGIBLE = "memoryV1Eligible"
    CANDIDATE_ONLY = "candidateOnly"
    REVIEW_QUEUE = "reviewQueue"
    QUARANTINE = "quarantine"
    EXCLUDED = "excluded"


class LegacyShadowParityComparisonStatus(str, Enum):
    """Why a legacy/Projection observation is not eligible for cutover."""

    PROJECTION_REBUILDING = "projectionRebuilding"
    LEGACY_EVIDENCE_INCOMPLETE = "legacyEvidenceIncomplete"
    LEGACY_RECORD_MAPPING_REQUIRED = "legacyRecordMappingRequired"


_HEX_DIGEST_LENGTH = 64
_ACTIVE_AUTHORITY_STATE = "active"


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _normalized_text(value: object, *, field: str, required: bool = True) -> str:
    normalized = str(value or "").strip()
    if required and not normalized:
        raise OwnerTruthLegacyMigrationError(f"{field} is required")
    return normalized


def _digest(value: object, *, field: str) -> str:
    normalized = _normalized_text(value, field=field)
    if len(normalized) != _HEX_DIGEST_LENGTH or any(
        character not in "0123456789abcdef" for character in normalized.lower()
    ):
        raise OwnerTruthLegacyMigrationError(f"{field} must be a sha256 hex digest")
    return normalized.lower()


@dataclass(frozen=True)
class LegacyMigrationRecord:
    """A transient legacy record descriptor; raw payload never leaves the collector."""

    domain: LegacyMigrationDomain
    legacy_id: str
    record_hash: str
    canonical_owner_subject_id: str
    observed_owner_subject_id: str | None
    authority_state: str = _ACTIVE_AUTHORITY_STATE
    source_evidence_id: str | None = None
    decision_receipt_id: str | None = None
    decision_is_terminal: bool = False
    revision_evidence_id: str | None = None
    observed_only: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "domain", LegacyMigrationDomain(self.domain))
        object.__setattr__(
            self,
            "legacy_id",
            _normalized_text(self.legacy_id, field="legacy_id"),
        )
        object.__setattr__(
            self,
            "record_hash",
            _digest(self.record_hash, field="record_hash"),
        )
        object.__setattr__(
            self,
            "canonical_owner_subject_id",
            _normalized_text(
                self.canonical_owner_subject_id,
                field="canonical_owner_subject_id",
            ),
        )
        observed_owner = _normalized_text(
            self.observed_owner_subject_id,
            field="observed_owner_subject_id",
            required=False,
        )
        object.__setattr__(self, "observed_owner_subject_id", observed_owner or None)
        object.__setattr__(
            self,
            "authority_state",
            _normalized_text(self.authority_state, field="authority_state").lower(),
        )
        source_evidence = _normalized_text(
            self.source_evidence_id,
            field="source_evidence_id",
            required=False,
        )
        decision_evidence = _normalized_text(
            self.decision_receipt_id,
            field="decision_receipt_id",
            required=False,
        )
        object.__setattr__(self, "source_evidence_id", source_evidence or None)
        object.__setattr__(self, "decision_receipt_id", decision_evidence or None)
        if not isinstance(self.decision_is_terminal, bool):
            raise OwnerTruthLegacyMigrationError("decision_is_terminal must be boolean")
        revision_evidence = _normalized_text(
            self.revision_evidence_id,
            field="revision_evidence_id",
            required=False,
        )
        object.__setattr__(self, "revision_evidence_id", revision_evidence or None)

    @property
    def legacy_id_hash(self) -> str:
        return _hash(f"{self.domain.value}|{self.legacy_id}")

    @property
    def has_complete_memory_v1_evidence(self) -> bool:
        """Legacy data may create a Memory v1 only with all required evidence.

        A receipt identifier by itself does not prove that the historical
        review reached a terminal decision, nor that a revision exists.  The
        inventory therefore remains conservative until the collector can
        demonstrate every link in the Owner Truth chain.
        """

        return bool(
            self.source_evidence_id
            and self.decision_receipt_id
            and self.decision_is_terminal
            and self.revision_evidence_id
        )


@dataclass(frozen=True)
class LegacyMigrationEntry:
    domain: LegacyMigrationDomain
    legacy_id_hash: str
    record_hash: str
    classification: LegacyMigrationClassification
    disposition: LegacyMigrationDisposition
    owner_evidence_state: LegacyEvidenceState
    source_evidence_state: LegacyEvidenceState
    decision_evidence_state: LegacyEvidenceState
    reason_code: str

    def summary(self) -> dict[str, str]:
        """Return a persistable/public-QA-safe descriptor with no raw identifiers."""

        return {
            "classification": self.classification.value,
            "decisionEvidenceState": self.decision_evidence_state.value,
            "disposition": self.disposition.value,
            "domain": self.domain.value,
            "legacyIdHash": self.legacy_id_hash,
            "ownerEvidenceState": self.owner_evidence_state.value,
            "reasonCode": self.reason_code,
            "recordHash": self.record_hash,
            "sourceEvidenceState": self.source_evidence_state.value,
        }


@dataclass(frozen=True)
class LegacyMigrationInventory:
    vault_id: str
    classifier_version: str
    inventory_hash: str
    entries: tuple[LegacyMigrationEntry, ...]
    unavailable_domains: tuple[LegacyMigrationDomain, ...] = ()

    def summary(self) -> dict[str, object]:
        classification_counts: dict[str, int] = {}
        domain_counts: dict[str, int] = {}
        for entry in self.entries:
            classification_counts[entry.classification.value] = (
                classification_counts.get(entry.classification.value, 0) + 1
            )
            domain_counts[entry.domain.value] = domain_counts.get(entry.domain.value, 0) + 1
        return {
            "classifierVersion": self.classifier_version,
            "classificationCounts": dict(sorted(classification_counts.items())),
            "domainCounts": dict(sorted(domain_counts.items())),
            "entryCount": len(self.entries),
            "inventoryHash": self.inventory_hash,
            "unavailableDomains": [domain.value for domain in self.unavailable_domains],
            "vaultId": self.vault_id,
        }


LEGACY_SHADOW_PARITY_SCHEMA_VERSION = "owner-truth-legacy-shadow-parity-v1"


@dataclass(frozen=True)
class LegacyShadowParityReport:
    """A value-free, fail-closed observation before any legacy cutover.

    A legacy inventory and a V4 Projection do not become comparable merely
    because they belong to the same Owner.  Every legacy record still needs a
    separately approved mapping to Source/Decision/MemoryVersion lineage.  The
    report intentionally records that missing prerequisite instead of trying
    to infer or create one.
    """

    vault_id: str
    inventory_run_id: str
    classifier_version: str
    inventory_hash: str
    legacy_entry_count: int
    legacy_disposition_counts: Mapping[str, int]
    legacy_eligible_entry_count: int
    projection_state: str
    projection_authority_epoch: int
    projection_checkpoint: str | None
    projection_source_hash: str | None
    projection_entry_count: int
    comparison_status: LegacyShadowParityComparisonStatus
    mapped_record_count: int
    cutover_allowed: bool
    authority_epoch_changed: bool
    legacy_writer_retired: bool
    reason_codes: tuple[str, ...]
    report_hash: str

    def summary(self) -> dict[str, object]:
        """Return the QA-safe observation without legacy or memory payloads."""

        return {
            "authorityEpochChanged": self.authority_epoch_changed,
            "classifierVersion": self.classifier_version,
            "comparisonStatus": self.comparison_status.value,
            "cutoverAllowed": self.cutover_allowed,
            "inventoryHash": self.inventory_hash,
            "inventoryRunId": self.inventory_run_id,
            "legacyDispositionCounts": dict(self.legacy_disposition_counts),
            "legacyEligibleEntryCount": self.legacy_eligible_entry_count,
            "legacyEntryCount": self.legacy_entry_count,
            "legacyWriterRetired": self.legacy_writer_retired,
            "mappedRecordCount": self.mapped_record_count,
            "projection": {
                "authorityEpoch": self.projection_authority_epoch,
                "checkpoint": self.projection_checkpoint,
                "entryCount": self.projection_entry_count,
                "sourceHash": self.projection_source_hash,
                "state": self.projection_state,
            },
            "reasonCodes": list(self.reason_codes),
            "reportHash": self.report_hash,
            "schemaVersion": LEGACY_SHADOW_PARITY_SCHEMA_VERSION,
            "vaultId": self.vault_id,
        }


def build_legacy_shadow_parity_report(
    *,
    inventory_run_id: str,
    inventory: LegacyMigrationInventory,
    owner_subject_id: str,
    projection_snapshot: Mapping[str, object],
) -> LegacyShadowParityReport:
    """Describe readiness for a future shadow comparison without promotion.

    The current migration collector has no approved per-record lineage mapping
    from legacy hashes to V4 MemoryVersions.  Consequently this function is
    deliberately unable to emit a successful parity/cutover verdict.  It can
    only state which prerequisite is still missing, using hashes and counts.
    """

    if not isinstance(inventory, LegacyMigrationInventory):
        raise OwnerTruthLegacyMigrationError("legacy inventory is required")
    run_id = _normalized_text(inventory_run_id, field="inventory_run_id")
    owner = _normalized_text(owner_subject_id, field="owner_subject_id")
    if not isinstance(projection_snapshot, Mapping):
        raise OwnerTruthLegacyMigrationError("projection_snapshot must be an object")
    if str(projection_snapshot.get("vaultId") or "") != inventory.vault_id:
        raise OwnerTruthLegacyMigrationError("projection snapshot crosses Vault boundary")
    if str(projection_snapshot.get("ownerSubjectId") or "") != owner:
        raise OwnerTruthLegacyMigrationError("projection snapshot crosses Owner boundary")

    projection_state = str(projection_snapshot.get("state") or "").strip()
    if projection_state not in {"ready", "rebuilding"}:
        raise OwnerTruthLegacyMigrationError("projection state is invalid")
    try:
        projection_epoch = int(projection_snapshot.get("authorityEpoch") or 0)
        projection_entry_count = int(projection_snapshot.get("entryCount") or 0)
    except (TypeError, ValueError) as exc:
        raise OwnerTruthLegacyMigrationError("projection summary values are invalid") from exc
    if projection_epoch < 0 or projection_entry_count < 0:
        raise OwnerTruthLegacyMigrationError("projection summary values are invalid")

    projection_checkpoint_value = projection_snapshot.get("checkpoint")
    projection_checkpoint = (
        None
        if projection_checkpoint_value is None
        else _digest(projection_checkpoint_value, field="projection_checkpoint")
    )
    projection_source_hash_value = projection_snapshot.get("sourceHash")
    projection_source_hash = (
        None
        if projection_source_hash_value is None
        else _digest(projection_source_hash_value, field="projection_source_hash")
    )

    disposition_counts: dict[str, int] = {}
    eligible_count = 0
    for entry in inventory.entries:
        disposition = entry.disposition.value
        disposition_counts[disposition] = disposition_counts.get(disposition, 0) + 1
        if entry.disposition is LegacyMigrationDisposition.MEMORY_V1_ELIGIBLE:
            eligible_count += 1

    reason_codes = {"authorityEpochCutoverRequiresSeparateGate"}
    if projection_state != "ready":
        comparison_status = LegacyShadowParityComparisonStatus.PROJECTION_REBUILDING
        reason_codes.add("projectionRebuilding")
    elif eligible_count == 0:
        comparison_status = LegacyShadowParityComparisonStatus.LEGACY_EVIDENCE_INCOMPLETE
        reason_codes.add("legacyEvidenceIncomplete")
    else:
        comparison_status = LegacyShadowParityComparisonStatus.LEGACY_RECORD_MAPPING_REQUIRED
        reason_codes.add("legacyRecordMappingUnavailable")

    normalized_counts = dict(sorted(disposition_counts.items()))
    normalized_reasons = tuple(sorted(reason_codes))
    report_hash = _hash(
        _canonical_json(
            {
                "classifierVersion": inventory.classifier_version,
                "comparisonStatus": comparison_status.value,
                "inventoryHash": inventory.inventory_hash,
                "inventoryRunId": run_id,
                "legacyDispositionCounts": normalized_counts,
                "legacyEligibleEntryCount": eligible_count,
                "legacyEntryCount": len(inventory.entries),
                "mappedRecordCount": 0,
                "projection": {
                    "authorityEpoch": projection_epoch,
                    "checkpoint": projection_checkpoint,
                    "entryCount": projection_entry_count,
                    "sourceHash": projection_source_hash,
                    "state": projection_state,
                },
                "reasonCodes": normalized_reasons,
                "schemaVersion": LEGACY_SHADOW_PARITY_SCHEMA_VERSION,
                "vaultId": inventory.vault_id,
            }
        )
    )
    return LegacyShadowParityReport(
        vault_id=inventory.vault_id,
        inventory_run_id=run_id,
        classifier_version=inventory.classifier_version,
        inventory_hash=inventory.inventory_hash,
        legacy_entry_count=len(inventory.entries),
        legacy_disposition_counts=normalized_counts,
        legacy_eligible_entry_count=eligible_count,
        projection_state=projection_state,
        projection_authority_epoch=projection_epoch,
        projection_checkpoint=projection_checkpoint,
        projection_source_hash=projection_source_hash,
        projection_entry_count=projection_entry_count,
        comparison_status=comparison_status,
        mapped_record_count=0,
        cutover_allowed=False,
        authority_epoch_changed=False,
        legacy_writer_retired=False,
        reason_codes=normalized_reasons,
        report_hash=report_hash,
    )


def classify_legacy_record(record: LegacyMigrationRecord) -> LegacyMigrationEntry:
    """Classify one record without creating an authoritative migration target."""

    if record.domain is LegacyMigrationDomain.CONVERSATION_CACHE:
        return LegacyMigrationEntry(
            domain=record.domain,
            legacy_id_hash=record.legacy_id_hash,
            record_hash=record.record_hash,
            classification=LegacyMigrationClassification.DO_NOT_MIGRATE,
            disposition=LegacyMigrationDisposition.EXCLUDED,
            owner_evidence_state=LegacyEvidenceState.NOT_APPLICABLE,
            source_evidence_state=LegacyEvidenceState.NOT_APPLICABLE,
            decision_evidence_state=LegacyEvidenceState.NOT_APPLICABLE,
            reason_code="conversationCacheNotOwnerMemory",
        )

    if record.authority_state != _ACTIVE_AUTHORITY_STATE:
        owner_state = LegacyEvidenceState.AMBIGUOUS
        owner_reason = "legacyAuthorityNotActive"
    elif record.observed_owner_subject_id is None:
        owner_state = LegacyEvidenceState.MISSING
        owner_reason = "legacyOwnerEvidenceMissing"
    elif record.observed_owner_subject_id != record.canonical_owner_subject_id:
        owner_state = LegacyEvidenceState.AMBIGUOUS
        owner_reason = "legacyOwnerEvidenceMismatch"
    else:
        owner_state = LegacyEvidenceState.VERIFIED
        owner_reason = ""

    source_state = (
        LegacyEvidenceState.VERIFIED
        if record.source_evidence_id
        else LegacyEvidenceState.MISSING
    )
    decision_state = (
        LegacyEvidenceState.VERIFIED
        if record.decision_receipt_id and record.decision_is_terminal and record.revision_evidence_id
        else LegacyEvidenceState.MISSING
    )

    if owner_state is not LegacyEvidenceState.VERIFIED:
        return LegacyMigrationEntry(
            domain=record.domain,
            legacy_id_hash=record.legacy_id_hash,
            record_hash=record.record_hash,
            classification=LegacyMigrationClassification.QUARANTINE,
            disposition=LegacyMigrationDisposition.QUARANTINE,
            owner_evidence_state=owner_state,
            source_evidence_state=source_state,
            decision_evidence_state=decision_state,
            reason_code=owner_reason,
        )

    if record.has_complete_memory_v1_evidence:
        return LegacyMigrationEntry(
            domain=record.domain,
            legacy_id_hash=record.legacy_id_hash,
            record_hash=record.record_hash,
            classification=LegacyMigrationClassification.PROVEN_CONFIRMED,
            disposition=LegacyMigrationDisposition.MEMORY_V1_ELIGIBLE,
            owner_evidence_state=owner_state,
            source_evidence_state=source_state,
            decision_evidence_state=decision_state,
            reason_code="ownerSourceDecisionEvidenceComplete",
        )

    if record.observed_only or record.domain in {
        LegacyMigrationDomain.ARCHIVE_ITEM,
        LegacyMigrationDomain.KB_SNAPSHOT,
        LegacyMigrationDomain.KB_CHANGE,
        LegacyMigrationDomain.KB_RECEIPT,
    }:
        return LegacyMigrationEntry(
            domain=record.domain,
            legacy_id_hash=record.legacy_id_hash,
            record_hash=record.record_hash,
            classification=LegacyMigrationClassification.OBSERVED_CANDIDATE,
            disposition=LegacyMigrationDisposition.CANDIDATE_ONLY,
            owner_evidence_state=owner_state,
            source_evidence_state=source_state,
            decision_evidence_state=decision_state,
            reason_code="legacyObservationRequiresOwnerDecision",
        )

    return LegacyMigrationEntry(
        domain=record.domain,
        legacy_id_hash=record.legacy_id_hash,
        record_hash=record.record_hash,
        classification=LegacyMigrationClassification.NEEDS_REVIEW,
        disposition=LegacyMigrationDisposition.REVIEW_QUEUE,
        owner_evidence_state=owner_state,
        source_evidence_state=source_state,
        decision_evidence_state=decision_state,
        reason_code="legacySourceOrDecisionEvidenceMissing",
    )


def build_legacy_migration_inventory(
    *,
    vault_id: str,
    classifier_version: str,
    records: Iterable[LegacyMigrationRecord],
    unavailable_domains: Iterable[LegacyMigrationDomain] = (),
) -> LegacyMigrationInventory:
    """Create a deterministic, value-free report for a legacy migration pass."""

    normalized_vault_id = _normalized_text(vault_id, field="vault_id")
    normalized_classifier_version = _normalized_text(
        classifier_version,
        field="classifier_version",
    )
    entries = tuple(
        sorted(
            (classify_legacy_record(record) for record in records),
            key=lambda item: (item.domain.value, item.legacy_id_hash),
        )
    )
    keys = [(entry.domain.value, entry.legacy_id_hash) for entry in entries]
    if len(set(keys)) != len(keys):
        raise OwnerTruthLegacyMigrationError(
            "legacy inventory contains duplicate domain/id identities"
        )
    unavailable = tuple(
        sorted({LegacyMigrationDomain(domain) for domain in unavailable_domains}, key=lambda item: item.value)
    )
    inventory_hash = _hash(
        _canonical_json(
            {
                "classifierVersion": normalized_classifier_version,
                "entries": [entry.summary() for entry in entries],
                "unavailableDomains": [domain.value for domain in unavailable],
                "vaultId": normalized_vault_id,
            }
        )
    )
    return LegacyMigrationInventory(
        vault_id=normalized_vault_id,
        classifier_version=normalized_classifier_version,
        inventory_hash=inventory_hash,
        entries=entries,
        unavailable_domains=unavailable,
    )


__all__ = [
    "LEGACY_SHADOW_PARITY_SCHEMA_VERSION",
    "LegacyShadowParityComparisonStatus",
    "LegacyShadowParityReport",
    "LegacyEvidenceState",
    "LegacyMigrationClassification",
    "LegacyMigrationDisposition",
    "LegacyMigrationDomain",
    "LegacyMigrationEntry",
    "LegacyMigrationInventory",
    "LegacyMigrationRecord",
    "OwnerTruthLegacyMigrationError",
    "build_legacy_migration_inventory",
    "build_legacy_shadow_parity_report",
    "classify_legacy_record",
]
