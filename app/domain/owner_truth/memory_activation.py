"""Immutable MemoryVersion activation derived from an Owner DecisionReceipt.

This is the next Owner Truth boundary after Candidate review.  It deliberately
does not write a projection, publish a fact, or call a provider.  An accepted
or corrected Candidate can create exactly one MemoryRecord and its initial
current MemoryVersion; rejected and invalidated Candidates create neither.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Mapping
from uuid import UUID, uuid5

from .candidate_decisions import OwnerTruthCandidateReviewError, OwnerTruthCandidateSnapshot
from .contracts import CandidateDecision
from .ontology import validate_memory_payload


OWNER_TRUTH_MEMORY_VERSION_SCHEMA_VERSION = "owner-truth-memory-version-v1"
_MEMORY_NAMESPACE = UUID("bdb154ec-3339-4ac8-8bff-5b74d6cf8de0")
_MEMORY_VERSION_NAMESPACE = UUID("e4e3a9cb-72f4-4f20-b96b-169d436f3a4d")


class OwnerTruthMemoryActivationError(OwnerTruthCandidateReviewError):
    """A terminal DecisionReceipt cannot safely activate a MemoryVersion."""


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthMemoryActivationError("memory activation values must be JSON serializable") from exc


def _digest(value: Any) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _copy_mapping(value: Mapping[str, Any], *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise OwnerTruthMemoryActivationError(f"{field} must be an object")
    copied = json.loads(_canonical_json(dict(value)))
    if not isinstance(copied, dict):  # defensive: mappings decode to objects
        raise OwnerTruthMemoryActivationError(f"{field} must be an object")
    return copied


@dataclass(frozen=True)
class OwnerTruthMemoryActivationPlan:
    """All immutable values needed to write one initial memory version."""

    receipt_id: str
    candidate_id: str
    vault_id: str
    owner_subject_id: str
    memory_id: str
    memory_version_id: str
    memory_kind: str
    perspective_type: str
    epistemic_status: str
    sensitivity: str
    policy_version: str
    authority_epoch: int
    source_id: str
    source_version: int
    content_schema_version: str
    content_hash: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class OwnerTruthMemoryActivationResult:
    outcome: str
    receipt_id: str
    candidate_id: str
    decision: CandidateDecision
    memory_id: str | None
    memory_version_id: str | None
    memory_version: int | None
    authority_epoch: int | None
    content_hash: str | None


def build_memory_activation_plan(
    *,
    candidate: OwnerTruthCandidateSnapshot,
    receipt_id: str,
    receipt_decision: CandidateDecision,
    receipt_after_hash: str,
    corrected_value: Mapping[str, Any] | None = None,
    corrected_value_schema_version: str | None = None,
) -> OwnerTruthMemoryActivationPlan | None:
    """Resolve immutable accepted/corrected content into an initial version plan.

    The receipt remains the sole activation key.  Its deterministic memory ID
    lets retries return the existing version without adding a second history
    record.  All source evidence stays in the version payload for later
    Citation/Projection work.
    """

    try:
        decision = CandidateDecision(receipt_decision)
    except ValueError as exc:
        raise OwnerTruthMemoryActivationError("receipt contains an unsupported decision") from exc
    if decision in {CandidateDecision.REJECTED, CandidateDecision.INVALIDATED}:
        return None
    if decision not in {CandidateDecision.ACCEPTED, CandidateDecision.CORRECTED}:
        raise OwnerTruthMemoryActivationError("only terminal accepted/corrected decisions activate memory")
    if candidate.decision is not decision:
        raise OwnerTruthMemoryActivationError("candidate terminal decision does not match DecisionReceipt")

    content_schema_version = candidate.content_schema_version
    content = candidate.content
    if decision is CandidateDecision.CORRECTED:
        if corrected_value is None or not corrected_value_schema_version:
            raise OwnerTruthMemoryActivationError("corrected decision requires an immutable corrected value")
        content_schema_version = str(corrected_value_schema_version).strip()
        content = _copy_mapping(corrected_value, field="corrected_value")

    validation = validate_memory_payload(
        kind=candidate.memory_kind,
        payload=content,
        schema_version=content_schema_version,
    )
    if not validation.accepted:
        raise OwnerTruthMemoryActivationError(
            f"activated memory content is not admitted: {validation.code}"
        )
    content_hash = _digest(content)
    if content_hash != str(receipt_after_hash):
        raise OwnerTruthMemoryActivationError("DecisionReceipt content hash does not match activation content")

    source_refs = [
        _copy_mapping(item, field="candidate evidence reference")
        for item in candidate.source_refs
    ]
    source_versions = {int(item["sourceVersion"]) for item in source_refs}
    if len(source_versions) != 1:
        raise OwnerTruthMemoryActivationError(
            "Candidate source references must have one source version before activation"
        )
    source_version = next(iter(source_versions))
    if source_version < 1:
        raise OwnerTruthMemoryActivationError("activated MemoryVersion source version must be positive")

    normalized_receipt_id = str(receipt_id or "").strip()
    if not normalized_receipt_id:
        raise OwnerTruthMemoryActivationError("DecisionReceipt id is required")
    try:
        UUID(normalized_receipt_id)
    except (TypeError, ValueError) as exc:
        raise OwnerTruthMemoryActivationError("DecisionReceipt id must be a UUID") from exc

    memory_id = str(
        uuid5(
            _MEMORY_NAMESPACE,
            f"decision-receipt-memory:{candidate.vault_id}:{normalized_receipt_id}",
        )
    )
    memory_version_id = str(
        uuid5(
            _MEMORY_VERSION_NAMESPACE,
            f"decision-receipt-memory-version:{candidate.vault_id}:{normalized_receipt_id}:1",
        )
    )
    payload = {
        "schemaVersion": OWNER_TRUTH_MEMORY_VERSION_SCHEMA_VERSION,
        "contentSchemaVersion": content_schema_version,
        "content": content,
        "evidenceRefs": source_refs,
        "candidateId": candidate.candidate_id,
        "decisionReceiptId": normalized_receipt_id,
    }
    return OwnerTruthMemoryActivationPlan(
        receipt_id=normalized_receipt_id,
        candidate_id=candidate.candidate_id,
        vault_id=candidate.vault_id,
        owner_subject_id=candidate.owner_subject_id,
        memory_id=memory_id,
        memory_version_id=memory_version_id,
        memory_kind=candidate.memory_kind.value,
        perspective_type=candidate.perspective_type.value,
        epistemic_status=candidate.epistemic_status.value,
        sensitivity=candidate.sensitivity.value,
        policy_version=candidate.policy_version,
        authority_epoch=candidate.authority_epoch,
        source_id=candidate.source_id,
        source_version=source_version,
        content_schema_version=content_schema_version,
        content_hash=content_hash,
        payload=payload,
    )


__all__ = [
    "OWNER_TRUTH_MEMORY_VERSION_SCHEMA_VERSION",
    "OwnerTruthMemoryActivationError",
    "OwnerTruthMemoryActivationPlan",
    "OwnerTruthMemoryActivationResult",
    "build_memory_activation_plan",
]
