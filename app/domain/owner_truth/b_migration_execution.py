"""Contracts for bounded execution of an approved B migration dry run.

Execution is intentionally an admission into the current Source/Candidate
review lane.  It never creates or updates a formal MemoryVersion.  Every entry
is bound to the immutable dry-run report and can therefore be resumed without
guessing at changed legacy content or authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import re
from uuid import UUID, uuid5

from app.domain.owner_truth.b_migration_dry_run import (
    OwnerTruthBMigrationDryRunEntry,
    OwnerTruthBMigrationDryRunError,
    OwnerTruthBMigrationDryRunReport,
)


OWNER_TRUTH_B_MIGRATION_EXECUTION_SCHEMA_VERSION = (
    "owner-truth-b-migration-execution-v1"
)
_RUN_NAMESPACE = UUID("2af5ae9d-58e5-48c8-a278-65e20b813b17")
_SOURCE_NAMESPACE = UUID("31c91bf5-fbcb-41ad-8352-699d9562274a")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class OwnerTruthBMigrationExecutionError(OwnerTruthBMigrationDryRunError):
    """The bounded migration cannot continue without violating its fence."""


class OwnerTruthBMigrationExecutionConflict(OwnerTruthBMigrationExecutionError):
    """The legacy row, report, or authority changed after the dry run."""


class OwnerTruthBMigrationExecutionUnavailable(OwnerTruthBMigrationExecutionError):
    """The default-off executor has not been explicitly enabled."""


class OwnerTruthBMigrationExecutionDisposition(str, Enum):
    SOURCE_QUEUED_FOR_REVIEW = "sourceQueuedForReview"
    MANUAL_EVIDENCE_REVIEW = "manualEvidenceReview"
    QUARANTINED = "quarantined"
    EXCLUDED = "excluded"
    BLOCKED_RECORD_CHANGED = "blockedRecordChanged"
    FAILED_RETRYABLE = "failedRetryable"


class OwnerTruthBMigrationExecutionState(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SOURCE_QUEUED_FOR_REVIEW = "sourceQueuedForReview"
    MANUAL_EVIDENCE_REVIEW = "manualEvidenceReview"
    QUARANTINED = "quarantined"
    EXCLUDED = "excluded"
    BLOCKED_RECORD_CHANGED = "blockedRecordChanged"
    FAILED_RETRYABLE = "failedRetryable"

    @property
    def is_terminal(self) -> bool:
        return self in {
            self.SOURCE_QUEUED_FOR_REVIEW,
            self.MANUAL_EVIDENCE_REVIEW,
            self.QUARANTINED,
            self.EXCLUDED,
            self.BLOCKED_RECORD_CHANGED,
        }


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _nonblank(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise OwnerTruthBMigrationExecutionError(f"{field} is required")
    return normalized


def _sha256(value: object, *, field: str) -> str:
    normalized = _nonblank(value, field=field).lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise OwnerTruthBMigrationExecutionError(f"{field} must be a sha256 digest")
    return normalized


def _uuid(value: object, *, field: str) -> str:
    normalized = _nonblank(value, field=field)
    try:
        return str(UUID(normalized))
    except (TypeError, ValueError, AttributeError) as error:
        raise OwnerTruthBMigrationExecutionError(f"{field} must be a UUID") from error


def execution_run_id(report: OwnerTruthBMigrationDryRunReport) -> str:
    if not isinstance(report, OwnerTruthBMigrationDryRunReport):
        raise OwnerTruthBMigrationExecutionError("B migration dry-run report is required")
    return str(
        uuid5(
            _RUN_NAMESPACE,
            f"{report.report_id}:{report.report_hash}:"
            f"{OWNER_TRUTH_B_MIGRATION_EXECUTION_SCHEMA_VERSION}",
        )
    )


def execution_source_id(
    *,
    report_id: str,
    domain: str,
    legacy_id_hash: str,
    record_hash: str,
) -> str:
    return str(
        uuid5(
            _SOURCE_NAMESPACE,
            ":".join(
                (
                    _uuid(report_id, field="report_id"),
                    _nonblank(domain, field="domain"),
                    _sha256(legacy_id_hash, field="legacy_id_hash"),
                    _sha256(record_hash, field="record_hash"),
                )
            ),
        )
    )


@dataclass(frozen=True)
class OwnerTruthBMigrationExecutionEntry:
    ordinal: int
    entry: OwnerTruthBMigrationDryRunEntry
    state: OwnerTruthBMigrationExecutionState
    attempt_count: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int) or self.ordinal < 0:
            raise OwnerTruthBMigrationExecutionError("ordinal must be a non-negative integer")
        if not isinstance(self.entry, OwnerTruthBMigrationDryRunEntry):
            raise OwnerTruthBMigrationExecutionError("dry-run entry is required")
        object.__setattr__(self, "state", OwnerTruthBMigrationExecutionState(self.state))
        if (
            isinstance(self.attempt_count, bool)
            or not isinstance(self.attempt_count, int)
            or self.attempt_count < 0
        ):
            raise OwnerTruthBMigrationExecutionError(
                "attempt_count must be a non-negative integer"
            )


@dataclass(frozen=True)
class OwnerTruthLegacyReplayMaterial:
    domain: str
    legacy_id_hash: str
    record_hash: str
    text: str | None
    material_state: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "domain", _nonblank(self.domain, field="domain"))
        object.__setattr__(
            self,
            "legacy_id_hash",
            _sha256(self.legacy_id_hash, field="legacy_id_hash"),
        )
        object.__setattr__(self, "record_hash", _sha256(self.record_hash, field="record_hash"))
        normalized_text = str(self.text or "").strip()
        object.__setattr__(self, "text", normalized_text or None)
        state = _nonblank(self.material_state, field="material_state")
        if state not in {"textReady", "manualEvidenceReview"}:
            raise OwnerTruthBMigrationExecutionError("material_state is unsupported")
        if state == "textReady" and not normalized_text:
            raise OwnerTruthBMigrationExecutionError("textReady material requires text")
        if state == "manualEvidenceReview" and normalized_text:
            raise OwnerTruthBMigrationExecutionError(
                "manualEvidenceReview material must not carry a source body"
            )
        object.__setattr__(self, "material_state", state)


@dataclass(frozen=True)
class OwnerTruthBMigrationExecutionClaim:
    run_id: str
    report: OwnerTruthBMigrationDryRunReport
    entries: tuple[OwnerTruthBMigrationExecutionEntry, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _uuid(self.run_id, field="run_id"))
        if self.run_id != execution_run_id(self.report):
            raise OwnerTruthBMigrationExecutionError("execution run id is invalid")
        normalized = tuple(self.entries)
        if len({item.ordinal for item in normalized}) != len(normalized):
            raise OwnerTruthBMigrationExecutionError("execution claim has duplicate ordinals")
        object.__setattr__(self, "entries", normalized)


@dataclass(frozen=True)
class OwnerTruthBMigrationEntryCompletion:
    ordinal: int
    disposition: OwnerTruthBMigrationExecutionDisposition
    reason_code: str
    source_id: str | None = None
    source_receipt_id: str | None = None
    effect_operation_id: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int) or self.ordinal < 0:
            raise OwnerTruthBMigrationExecutionError("ordinal must be a non-negative integer")
        object.__setattr__(
            self,
            "disposition",
            OwnerTruthBMigrationExecutionDisposition(self.disposition),
        )
        object.__setattr__(self, "reason_code", _nonblank(self.reason_code, field="reason_code"))
        identifiers = (self.source_id, self.source_receipt_id, self.effect_operation_id)
        if self.disposition is OwnerTruthBMigrationExecutionDisposition.SOURCE_QUEUED_FOR_REVIEW:
            if any(not str(value or "").strip() for value in identifiers):
                raise OwnerTruthBMigrationExecutionError(
                    "sourceQueuedForReview requires source and effect receipts"
                )
            object.__setattr__(self, "source_id", _uuid(self.source_id, field="source_id"))
            object.__setattr__(
                self,
                "source_receipt_id",
                _uuid(self.source_receipt_id, field="source_receipt_id"),
            )
            object.__setattr__(
                self,
                "effect_operation_id",
                _uuid(self.effect_operation_id, field="effect_operation_id"),
            )
        elif any(value is not None for value in identifiers):
            raise OwnerTruthBMigrationExecutionError(
                "non-source migration completion cannot reference a Source"
            )

    @property
    def state(self) -> OwnerTruthBMigrationExecutionState:
        return OwnerTruthBMigrationExecutionState(self.disposition.value)

    @property
    def receipt_hash(self) -> str:
        return _hash(
            {
                "disposition": self.disposition.value,
                "effectOperationId": self.effect_operation_id,
                "ordinal": self.ordinal,
                "reasonCode": self.reason_code,
                "schemaVersion": OWNER_TRUTH_B_MIGRATION_EXECUTION_SCHEMA_VERSION,
                "sourceId": self.source_id,
                "sourceReceiptId": self.source_receipt_id,
            }
        )


@dataclass(frozen=True)
class OwnerTruthBMigrationExecutionResult:
    run_id: str
    report_id: str
    report_hash: str
    status: str
    batch_processed_count: int
    checkpoint_ordinal: int
    entry_count: int
    disposition_counts: dict[str, int]
    retryable_count: int
    formal_memory_write_count: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _uuid(self.run_id, field="run_id"))
        object.__setattr__(self, "report_id", _uuid(self.report_id, field="report_id"))
        object.__setattr__(
            self,
            "report_hash",
            _sha256(self.report_hash, field="report_hash"),
        )
        if self.status not in {"running", "completed", "blocked"}:
            raise OwnerTruthBMigrationExecutionError("execution status is unsupported")
        for field_name in (
            "batch_processed_count",
            "checkpoint_ordinal",
            "entry_count",
            "retryable_count",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise OwnerTruthBMigrationExecutionError(
                    f"{field_name} must be a non-negative integer"
                )
        if self.formal_memory_write_count != 0:
            raise OwnerTruthBMigrationExecutionError(
                "B migration execution cannot write formal memory"
            )

    def public_summary(self) -> dict[str, object]:
        return {
            "batchProcessedCount": self.batch_processed_count,
            "checkpointOrdinal": self.checkpoint_ordinal,
            "dispositionCounts": dict(sorted(self.disposition_counts.items())),
            "entryCount": self.entry_count,
            "formalMemoryWriteCount": 0,
            "retryableCount": self.retryable_count,
            "reportHash": self.report_hash,
            "reportId": self.report_id,
            "runId": self.run_id,
            "schemaVersion": OWNER_TRUTH_B_MIGRATION_EXECUTION_SCHEMA_VERSION,
            "status": self.status,
            "targetState": "candidateReviewOnly",
        }


__all__ = [
    "OWNER_TRUTH_B_MIGRATION_EXECUTION_SCHEMA_VERSION",
    "OwnerTruthBMigrationEntryCompletion",
    "OwnerTruthBMigrationExecutionClaim",
    "OwnerTruthBMigrationExecutionConflict",
    "OwnerTruthBMigrationExecutionDisposition",
    "OwnerTruthBMigrationExecutionEntry",
    "OwnerTruthBMigrationExecutionError",
    "OwnerTruthBMigrationExecutionResult",
    "OwnerTruthBMigrationExecutionState",
    "OwnerTruthBMigrationExecutionUnavailable",
    "OwnerTruthLegacyReplayMaterial",
    "execution_run_id",
    "execution_source_id",
]
