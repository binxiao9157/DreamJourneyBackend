"""Typed, storage-independent Owner Truth V1 contracts.

The V1 slice is intentionally inert: it describes records and policy state,
but does not make legacy Archive/KBLite data authoritative or expose a writer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional
from uuid import UUID


class OwnerTruthContractError(ValueError):
    """Raised when a value cannot participate in the Owner Truth contract."""


class SourceKind(str, Enum):
    TEXT = "text"
    ARCHIVE_ITEM = "archiveItem"
    CONVERSATION = "conversation"
    IMPORT = "import"


class SourceState(str, Enum):
    ACTIVE = "active"
    REDACTED = "redacted"
    DELETED = "deleted"


class MemoryKind(str, Enum):
    EXPERIENCE = "experience"
    KNOWLEDGE = "knowledge"
    EMOTION = "emotion"


class PerspectiveType(str, Enum):
    FIRST_PERSON = "firstPerson"
    REPORTED = "reported"
    INFERRED = "inferred"


class EpistemicStatus(str, Enum):
    OBSERVED = "observed"
    RECALLED = "recalled"
    REPORTED = "reported"
    INFERRED = "inferred"
    UNCERTAIN = "uncertain"


class SensitivityLevel(str, Enum):
    STANDARD = "standard"
    SENSITIVE = "sensitive"
    RESTRICTED = "restricted"


class CandidateDecision(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CORRECTED = "corrected"
    INVALIDATED = "invalidated"

    @property
    def is_terminal(self) -> bool:
        return self is not CandidateDecision.PENDING


def decision_receipt_matches_candidate(
    *,
    candidate_decision: CandidateDecision,
    receipt_decision: CandidateDecision,
) -> bool:
    return candidate_decision.is_terminal and candidate_decision is receipt_decision


def require_nonblank(value: str, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise OwnerTruthContractError(f"{field} must be non-empty")
    return normalized


def require_uuid(value: str, *, field: str) -> str:
    normalized = require_nonblank(value, field=field)
    try:
        return str(UUID(normalized))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthContractError(f"{field} must be a UUID") from exc


def advance_candidate_decision(
    current: CandidateDecision,
    requested: CandidateDecision,
) -> CandidateDecision:
    """Allow exactly one transition out of pending.

    Decision receipts are append-only.  This helper mirrors the database
    trigger so callers cannot accidentally model terminal state as mutable.
    """

    if current.is_terminal:
        if current is requested:
            return current
        raise OwnerTruthContractError("terminal candidate decision is immutable")
    if requested is CandidateDecision.PENDING:
        return current
    return requested


@dataclass(frozen=True)
class SourceRef:
    vault_id: str
    source_id: str
    source_version: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "vault_id", require_nonblank(self.vault_id, field="vault_id"))
        object.__setattr__(self, "source_id", require_uuid(self.source_id, field="source_id"))
        if self.source_version < 1:
            raise OwnerTruthContractError("source_version must be positive")


@dataclass(frozen=True)
class OwnerTruthSource:
    source_id: str
    vault_id: str
    owner_subject_id: str
    kind: SourceKind
    state: SourceState
    source_version: int
    content_hash: str
    policy_version: str
    authority_epoch: int
    metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", require_uuid(self.source_id, field="source_id"))
        object.__setattr__(self, "vault_id", require_nonblank(self.vault_id, field="vault_id"))
        object.__setattr__(self, "owner_subject_id", require_nonblank(self.owner_subject_id, field="owner_subject_id"))
        object.__setattr__(self, "content_hash", require_nonblank(self.content_hash, field="content_hash"))
        object.__setattr__(self, "policy_version", require_nonblank(self.policy_version, field="policy_version"))
        if self.source_version < 1:
            raise OwnerTruthContractError("source_version must be positive")
        if self.authority_epoch < 0:
            raise OwnerTruthContractError("authority_epoch must not be negative")


@dataclass(frozen=True)
class OwnerTruthMemoryRecord:
    memory_id: str
    vault_id: str
    owner_subject_id: str
    kind: MemoryKind
    perspective: PerspectiveType
    epistemic_status: EpistemicStatus
    sensitivity: SensitivityLevel
    status: str
    source_ref: Optional[SourceRef]
    policy_version: str
    content_hash: str
    authority_epoch: int
    row_version: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "memory_id", require_uuid(self.memory_id, field="memory_id"))
        object.__setattr__(self, "vault_id", require_nonblank(self.vault_id, field="vault_id"))
        object.__setattr__(self, "owner_subject_id", require_nonblank(self.owner_subject_id, field="owner_subject_id"))
        object.__setattr__(self, "status", require_nonblank(self.status, field="status"))
        object.__setattr__(self, "policy_version", require_nonblank(self.policy_version, field="policy_version"))
        object.__setattr__(self, "content_hash", require_nonblank(self.content_hash, field="content_hash"))
        if self.authority_epoch < 0:
            raise OwnerTruthContractError("authority_epoch must not be negative")
        if self.row_version < 1:
            raise OwnerTruthContractError("row_version must be positive")


@dataclass(frozen=True)
class OwnerTruthMemoryVersion:
    version_id: str
    memory_id: str
    vault_id: str
    version_number: int
    is_current: bool
    content_hash: str
    schema_version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "version_id", require_uuid(self.version_id, field="version_id"))
        object.__setattr__(self, "memory_id", require_uuid(self.memory_id, field="memory_id"))
        object.__setattr__(self, "vault_id", require_nonblank(self.vault_id, field="vault_id"))
        object.__setattr__(self, "content_hash", require_nonblank(self.content_hash, field="content_hash"))
        object.__setattr__(self, "schema_version", require_nonblank(self.schema_version, field="schema_version"))
        if self.version_number < 1:
            raise OwnerTruthContractError("version_number must be positive")
