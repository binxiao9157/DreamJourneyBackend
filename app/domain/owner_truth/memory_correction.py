"""Immutable same-record MemoryVersion replacement for Owner corrections.

A correction Candidate is deliberately different from an initial Candidate:
it must supersede the cited version of the existing MemoryRecord rather than
create another record.  This module contains only deterministic plan building;
repositories remain responsible for locking and atomically persisting the
plan with the Owner DecisionReceipt.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any, Mapping
from uuid import UUID, uuid5

from .candidate_decisions import OwnerTruthCandidateReviewError, OwnerTruthCandidateSnapshot
from .contracts import CandidateDecision
from .memory_activation import OWNER_TRUTH_MEMORY_VERSION_SCHEMA_VERSION
from .ontology import validate_memory_payload


_CORRECTION_MEMORY_VERSION_NAMESPACE = UUID("ac0f3a9b-9d14-4a5d-94a4-47b0bdc9398b")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class OwnerTruthMemoryCorrectionError(OwnerTruthCandidateReviewError):
    """A correction cannot safely supersede its cited MemoryVersion."""


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthMemoryCorrectionError("correction values must be JSON serializable") from exc


def _digest(value: Any) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _mapping(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise OwnerTruthMemoryCorrectionError(f"{field} must be an object")
    normalized = json.loads(_canonical_json(dict(value)))
    if not isinstance(normalized, dict):  # defensive: mappings serialize to objects
        raise OwnerTruthMemoryCorrectionError(f"{field} must be an object")
    return normalized


def _uuid(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    try:
        return str(UUID(normalized))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthMemoryCorrectionError(f"{field} must be a UUID") from exc


def _sha256(value: object, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise OwnerTruthMemoryCorrectionError(f"{field} must be a SHA-256 digest")
    return normalized


@dataclass(frozen=True)
class OwnerTruthMemoryVersionSnapshot:
    """The locked authoritative predecessor needed to construct v(N+1)."""

    vault_id: str
    memory_id: str
    memory_version_id: str
    version_number: int
    is_current: bool
    authority_epoch: int
    source_id: str
    source_version: int
    content_schema_version: str
    content_hash: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "vault_id", str(self.vault_id or "").strip())
        if not self.vault_id:
            raise OwnerTruthMemoryCorrectionError("vault_id is required")
        object.__setattr__(self, "memory_id", _uuid(self.memory_id, field="memory_id"))
        object.__setattr__(
            self,
            "memory_version_id",
            _uuid(self.memory_version_id, field="memory_version_id"),
        )
        object.__setattr__(self, "source_id", _uuid(self.source_id, field="source_id"))
        if self.version_number < 1:
            raise OwnerTruthMemoryCorrectionError("version_number must be positive")
        if self.authority_epoch < 0:
            raise OwnerTruthMemoryCorrectionError("authority_epoch must not be negative")
        if self.source_version < 1:
            raise OwnerTruthMemoryCorrectionError("source_version must be positive")
        schema_version = str(self.content_schema_version or "").strip()
        if not schema_version:
            raise OwnerTruthMemoryCorrectionError("content_schema_version is required")
        object.__setattr__(self, "content_schema_version", schema_version)
        object.__setattr__(self, "content_hash", _sha256(self.content_hash, field="content_hash"))
        object.__setattr__(self, "payload", _mapping(self.payload, field="MemoryVersion payload"))


@dataclass(frozen=True)
class OwnerTruthMemoryCorrectionPlan:
    """One immutable replacement version plus its lineage metadata."""

    receipt_id: str
    candidate_id: str
    correction_request_id: str
    vault_id: str
    owner_subject_id: str
    memory_id: str
    superseded_memory_version_id: str
    superseded_memory_version: int
    replacement_memory_version_id: str
    replacement_memory_version: int
    authority_epoch: int
    source_id: str
    source_version: int
    content_schema_version: str
    content_hash: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class OwnerTruthMemoryCorrectionActivationResult:
    """Value-free evidence that a correction superseded one version."""

    outcome: str
    receipt_id: str
    candidate_id: str
    correction_request_id: str
    memory_id: str
    superseded_memory_version_id: str
    superseded_memory_version: int
    replacement_memory_version_id: str
    replacement_memory_version: int
    authority_epoch: int
    content_hash: str


def _merged_evidence_refs(
    *,
    predecessor_payload: Mapping[str, Any],
    correction_refs: tuple[dict[str, Any], ...],
) -> list[dict[str, Any]]:
    predecessor_refs = predecessor_payload.get("evidenceRefs")
    if not isinstance(predecessor_refs, list) or not predecessor_refs:
        raise OwnerTruthMemoryCorrectionError(
            "superseded MemoryVersion must preserve evidence references"
        )
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, reference in enumerate([*predecessor_refs, *correction_refs]):
        item = _mapping(reference, field=f"evidenceRefs[{index}]")
        source_id = _uuid(item.get("sourceId"), field=f"evidenceRefs[{index}].sourceId")
        try:
            source_version = int(item.get("sourceVersion"))
        except (TypeError, ValueError) as exc:
            raise OwnerTruthMemoryCorrectionError(
                f"evidenceRefs[{index}].sourceVersion must be positive"
            ) from exc
        if source_version < 1:
            raise OwnerTruthMemoryCorrectionError(
                f"evidenceRefs[{index}].sourceVersion must be positive"
            )
        item["sourceId"] = source_id
        item["sourceVersion"] = source_version
        key = _canonical_json(item)
        if key not in seen:
            seen.add(key)
            merged.append(item)
    return merged


def build_memory_correction_plan(
    *,
    candidate: OwnerTruthCandidateSnapshot,
    receipt_id: str,
    receipt_after_hash: str,
    corrected_value: Mapping[str, Any],
    corrected_value_schema_version: str,
    correction_request_id: str,
    reason_code_hash: str,
    predecessor: OwnerTruthMemoryVersionSnapshot,
) -> OwnerTruthMemoryCorrectionPlan:
    """Build the only valid v(N+1) for an accepted correction Candidate.

    The correction Source becomes the version-level provenance, while the
    predecessor's evidence is retained in the payload.  Raw correction text
    and raw rationale never enter the receipt or the public result.
    """

    if candidate.decision is not CandidateDecision.CORRECTED:
        raise OwnerTruthMemoryCorrectionError(
            "only a corrected Candidate can supersede a MemoryVersion"
        )
    if str(candidate.payload.get("reviewMode") or "") != "correction":
        raise OwnerTruthMemoryCorrectionError("Candidate is not a correction proposal")
    if not predecessor.is_current:
        raise OwnerTruthMemoryCorrectionError("superseded MemoryVersion is no longer current")
    if candidate.vault_id != predecessor.vault_id:
        raise OwnerTruthMemoryCorrectionError("correction Candidate belongs to another Vault")
    if candidate.authority_epoch != predecessor.authority_epoch:
        raise OwnerTruthMemoryCorrectionError("correction Candidate authority epoch is stale")
    if candidate.memory_kind.value != str(candidate.payload.get("candidateKind") or ""):
        raise OwnerTruthMemoryCorrectionError("correction Candidate kind is malformed")
    receipt_id = _uuid(receipt_id, field="receipt_id")
    correction_request_id = _uuid(correction_request_id, field="correction_request_id")
    reason_code_hash = _sha256(reason_code_hash, field="reason_code_hash")
    content_schema_version = str(corrected_value_schema_version or "").strip()
    if not content_schema_version:
        raise OwnerTruthMemoryCorrectionError("corrected_value_schema_version is required")
    content = _mapping(corrected_value, field="corrected_value")
    validation = validate_memory_payload(
        kind=candidate.memory_kind,
        payload=content,
        schema_version=content_schema_version,
    )
    if not validation.accepted:
        raise OwnerTruthMemoryCorrectionError(
            f"corrected_value is not admitted: {validation.code}"
        )
    content_hash = _digest(content)
    if content_hash != _sha256(receipt_after_hash, field="receipt_after_hash"):
        raise OwnerTruthMemoryCorrectionError(
            "DecisionReceipt content hash does not match corrected value"
        )
    correction_refs = candidate.source_refs
    source_versions = {
        int(reference["sourceVersion"])
        for reference in correction_refs
        if str(reference.get("sourceId") or "") == candidate.source_id
    }
    if len(source_versions) != 1:
        raise OwnerTruthMemoryCorrectionError(
            "correction Candidate must have one correction-source version"
        )
    source_version = next(iter(source_versions))
    evidence_refs = _merged_evidence_refs(
        predecessor_payload=predecessor.payload,
        correction_refs=correction_refs,
    )
    replacement_memory_version = predecessor.version_number + 1
    replacement_memory_version_id = str(
        uuid5(
            _CORRECTION_MEMORY_VERSION_NAMESPACE,
            "correction-memory-version:"
            f"{candidate.vault_id}:{predecessor.memory_id}:{receipt_id}:"
            f"{replacement_memory_version}",
        )
    )
    payload = {
        "schemaVersion": OWNER_TRUTH_MEMORY_VERSION_SCHEMA_VERSION,
        "contentSchemaVersion": content_schema_version,
        "content": content,
        "evidenceRefs": evidence_refs,
        "candidateId": candidate.candidate_id,
        "decisionReceiptId": receipt_id,
        "correctionRequestId": correction_request_id,
        "supersedesVersionId": predecessor.memory_version_id,
        "changeReasonHash": reason_code_hash,
        "previousContentHash": predecessor.content_hash,
    }
    return OwnerTruthMemoryCorrectionPlan(
        receipt_id=receipt_id,
        candidate_id=candidate.candidate_id,
        correction_request_id=correction_request_id,
        vault_id=candidate.vault_id,
        owner_subject_id=candidate.owner_subject_id,
        memory_id=predecessor.memory_id,
        superseded_memory_version_id=predecessor.memory_version_id,
        superseded_memory_version=predecessor.version_number,
        replacement_memory_version_id=replacement_memory_version_id,
        replacement_memory_version=replacement_memory_version,
        authority_epoch=predecessor.authority_epoch,
        source_id=candidate.source_id,
        source_version=source_version,
        content_schema_version=content_schema_version,
        content_hash=content_hash,
        payload=payload,
    )


__all__ = [
    "OwnerTruthMemoryCorrectionActivationResult",
    "OwnerTruthMemoryCorrectionError",
    "OwnerTruthMemoryCorrectionPlan",
    "OwnerTruthMemoryVersionSnapshot",
    "build_memory_correction_plan",
]
