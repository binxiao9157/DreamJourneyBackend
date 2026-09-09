"""Immutable write plans for applying a reviewed memory changeset.

The plan is calculated inside the same repository transaction that locks the
current formal versions.  It carries no side effect itself, which makes the
in-memory and PostgreSQL repositories exercise identical semantic decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json
from typing import Any, Iterable, Mapping
from uuid import UUID, uuid5

from .candidate_decisions import OwnerTruthCandidateSnapshot
from .contracts import CandidateDecision, MemoryKind
from .memory_activation import (
    OWNER_TRUTH_MEMORY_VERSION_SCHEMA_VERSION,
    OwnerTruthMemoryActivationError,
    build_memory_activation_plan,
)
from .memory_changeset import (
    OwnerTruthCurrentFormalMemory,
    OwnerTruthMemoryChangeOperationKind,
    OwnerTruthMemoryChangeSet,
    build_memory_changeset,
    merge_lossless_refinement_content,
)
from .ontology import (
    OWNER_TRUTH_SCHEMA_VERSION_V5,
    canonicalize_memory_payload,
    validate_memory_payload,
)


OWNER_TRUTH_MEMORY_CHANGESET_ACTIVATION_SCHEMA_VERSION = (
    "owner-truth-memory-changeset-activation-v1"
)
_VERSION_NAMESPACE = UUID("834d9ddf-a916-4a09-bc9f-1dfe95065287")


class OwnerTruthMemoryChangeSetActivationError(OwnerTruthMemoryActivationError):
    """A changeset cannot safely become a MemoryVersion write plan."""


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthMemoryChangeSetActivationError(
            "changeset activation values must be JSON serializable"
        ) from exc


def _digest(value: Any) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _copy_mapping(value: Mapping[str, Any], *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise OwnerTruthMemoryChangeSetActivationError(f"{field} must be an object")
    copied = json.loads(_canonical_json(dict(value)))
    if not isinstance(copied, dict):  # pragma: no cover - defensive JSON invariant
        raise OwnerTruthMemoryChangeSetActivationError(f"{field} must be an object")
    return copied


def _merge_refs(
    *groups: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Deduplicate only exact source-span evidence, preserving deterministic order."""

    merged: dict[str, dict[str, Any]] = {}
    for group in groups:
        for item in group:
            copied = _copy_mapping(item, field="evidence reference")
            key = _canonical_json(copied)
            merged.setdefault(key, copied)
    return [merged[key] for key in sorted(merged)]


def _provenance_refs(content: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    provenance = content.get("provenance")
    values = provenance.get("evidenceRefs") if isinstance(provenance, Mapping) else []
    return values if isinstance(values, (list, tuple)) else ()


def _with_merged_provenance(
    *,
    base_content: Mapping[str, Any],
    incoming_content: Mapping[str, Any],
    prefer_incoming: bool,
    kind: MemoryKind,
) -> dict[str, Any]:
    selected = dict(incoming_content if prefer_incoming else base_content)
    provenance = selected.get("provenance")
    provenance = dict(provenance) if isinstance(provenance, Mapping) else {}
    provenance["evidenceRefs"] = _merge_refs(
        _provenance_refs(base_content),
        _provenance_refs(incoming_content),
    )
    selected["provenance"] = provenance
    return canonicalize_memory_payload(
        kind=kind,
        payload=selected,
        schema_version=OWNER_TRUTH_SCHEMA_VERSION_V5,
    )


def _resolved_candidate(
    *,
    candidate: OwnerTruthCandidateSnapshot,
    content: Mapping[str, Any],
    schema_version: str,
) -> OwnerTruthCandidateSnapshot:
    payload = dict(candidate.payload)
    payload["content"] = _copy_mapping(content, field="resolved candidate content")
    payload["contentSchemaVersion"] = schema_version
    return replace(
        candidate,
        content_hash=_digest(payload["content"]),
        content_schema_version=schema_version,
        payload=payload,
    )


@dataclass(frozen=True)
class OwnerTruthMemoryChangeSetActivationPlan:
    change_set: OwnerTruthMemoryChangeSet
    outcome: str
    receipt_id: str
    candidate_id: str
    decision: CandidateDecision
    memory_id: str | None
    memory_version_id: str | None
    memory_version: int | None
    authority_epoch: int | None
    content_schema_version: str | None
    content_hash: str | None
    payload: Mapping[str, Any] | None
    source_id: str | None
    source_version: int | None
    memory_kind: str | None
    perspective_type: str | None
    epistemic_status: str | None
    sensitivity: str | None
    policy_version: str | None
    supersedes_version_id: str | None
    relation_to_memory_id: str | None
    relation_type: str | None

    @property
    def writes_memory_version(self) -> bool:
        return self.outcome in {"created", "revised"}

    @property
    def creates_memory_record(self) -> bool:
        return self.writes_memory_version and self.supersedes_version_id is None


def build_memory_changeset_activation_plan(
    *,
    candidate: OwnerTruthCandidateSnapshot,
    receipt_id: str,
    receipt_decision: CandidateDecision,
    receipt_after_hash: str,
    current_memories: Iterable[OwnerTruthCurrentFormalMemory],
    base_memory_revision: int,
    resolved_content: Mapping[str, Any] | None = None,
    resolved_content_schema_version: str | None = None,
) -> OwnerTruthMemoryChangeSetActivationPlan:
    """Build an idempotent plan from a terminal review receipt and current facts."""

    try:
        decision = CandidateDecision(receipt_decision)
    except ValueError as exc:
        raise OwnerTruthMemoryChangeSetActivationError("receipt decision is unsupported") from exc
    if candidate.decision is not decision:
        raise OwnerTruthMemoryChangeSetActivationError("candidate terminal decision does not match receipt")
    schema_version = resolved_content_schema_version or candidate.content_schema_version
    content = resolved_content if resolved_content is not None else candidate.content
    resolved = _resolved_candidate(
        candidate=candidate,
        content=content,
        schema_version=schema_version,
    )
    if resolved.content_hash != receipt_after_hash:
        raise OwnerTruthMemoryChangeSetActivationError(
            "DecisionReceipt content hash does not match resolved changeset content"
        )
    if decision in {CandidateDecision.REJECTED, CandidateDecision.INVALIDATED}:
        changeset = build_memory_changeset(
            candidate=resolved,
            current_memories=current_memories,
            base_memory_revision=base_memory_revision,
        )
        return OwnerTruthMemoryChangeSetActivationPlan(
            change_set=changeset,
            outcome="notApplicable",
            receipt_id=receipt_id,
            candidate_id=candidate.candidate_id,
            decision=decision,
            memory_id=None,
            memory_version_id=None,
            memory_version=None,
            authority_epoch=None,
            content_schema_version=None,
            content_hash=None,
            payload=None,
            source_id=None,
            source_version=None,
            memory_kind=None,
            perspective_type=None,
            epistemic_status=None,
            sensitivity=None,
            policy_version=None,
            supersedes_version_id=None,
            relation_to_memory_id=None,
            relation_type=None,
        )
    if decision not in {CandidateDecision.ACCEPTED, CandidateDecision.CORRECTED}:
        raise OwnerTruthMemoryChangeSetActivationError("terminal receipt cannot activate memory")

    current = tuple(current_memories)
    changeset = build_memory_changeset(
        candidate=resolved,
        current_memories=current,
        base_memory_revision=base_memory_revision,
    )
    operation = changeset.operation
    targets = {
        item.memory_id: item
        for item in current
    }
    target = targets.get(operation.target_memory_id or "")
    if operation.kind is OwnerTruthMemoryChangeOperationKind.NO_PERSONAL_FACT:
        return OwnerTruthMemoryChangeSetActivationPlan(
            change_set=changeset,
            outcome="notApplicable",
            receipt_id=receipt_id,
            candidate_id=candidate.candidate_id,
            decision=decision,
            memory_id=None,
            memory_version_id=None,
            memory_version=None,
            authority_epoch=None,
            content_schema_version=None,
            content_hash=None,
            payload=None,
            source_id=None,
            source_version=None,
            memory_kind=None,
            perspective_type=None,
            epistemic_status=None,
            sensitivity=None,
            policy_version=None,
            supersedes_version_id=None,
            relation_to_memory_id=None,
            relation_type=None,
        )
    if operation.kind is OwnerTruthMemoryChangeOperationKind.DUPLICATE:
        if target is None:
            raise OwnerTruthMemoryChangeSetActivationError("duplicate changeset has no current target")
        return OwnerTruthMemoryChangeSetActivationPlan(
            change_set=changeset,
            outcome="duplicate",
            receipt_id=receipt_id,
            candidate_id=candidate.candidate_id,
            decision=decision,
            memory_id=target.memory_id,
            memory_version_id=target.memory_version_id,
            memory_version=target.version_number,
            authority_epoch=candidate.authority_epoch,
            content_schema_version=target.content_schema_version,
            content_hash=_digest(target.content),
            payload=None,
            source_id=None,
            source_version=None,
            memory_kind=target.memory_kind.value,
            perspective_type=None,
            epistemic_status=None,
            sensitivity=None,
            policy_version=None,
            supersedes_version_id=None,
            relation_to_memory_id=None,
            relation_type=None,
        )

    if operation.kind in {
        OwnerTruthMemoryChangeOperationKind.ADD,
        OwnerTruthMemoryChangeOperationKind.TEMPORAL_CHANGE,
        OwnerTruthMemoryChangeOperationKind.DISPUTE,
    }:
        initial = build_memory_activation_plan(
            candidate=resolved,
            receipt_id=receipt_id,
            receipt_decision=decision,
            receipt_after_hash=receipt_after_hash,
            corrected_value=(resolved.content if decision is CandidateDecision.CORRECTED else None),
            corrected_value_schema_version=(
                resolved.content_schema_version
                if decision is CandidateDecision.CORRECTED
                else None
            ),
        )
        if initial is None:  # pragma: no cover - terminal decision check above
            raise OwnerTruthMemoryChangeSetActivationError("new changeset memory is not activatable")
        relation_type = {
            OwnerTruthMemoryChangeOperationKind.TEMPORAL_CHANGE: "derivesFrom",
            OwnerTruthMemoryChangeOperationKind.DISPUTE: "contradicts",
        }.get(operation.kind)
        return OwnerTruthMemoryChangeSetActivationPlan(
            change_set=changeset,
            outcome="created",
            receipt_id=receipt_id,
            candidate_id=candidate.candidate_id,
            decision=decision,
            memory_id=initial.memory_id,
            memory_version_id=initial.memory_version_id,
            memory_version=1,
            authority_epoch=initial.authority_epoch,
            content_schema_version=initial.content_schema_version,
            content_hash=initial.content_hash,
            payload=initial.payload,
            source_id=initial.source_id,
            source_version=initial.source_version,
            memory_kind=initial.memory_kind,
            perspective_type=initial.perspective_type,
            epistemic_status=initial.epistemic_status,
            sensitivity=initial.sensitivity,
            policy_version=initial.policy_version,
            supersedes_version_id=None,
            relation_to_memory_id=operation.target_memory_id if relation_type else None,
            relation_type=relation_type,
        )

    if target is None:
        raise OwnerTruthMemoryChangeSetActivationError("changeset operation requires a current target")
    if operation.kind not in {
        OwnerTruthMemoryChangeOperationKind.ADD_EVIDENCE,
        OwnerTruthMemoryChangeOperationKind.REFINE,
        OwnerTruthMemoryChangeOperationKind.CORRECT,
    }:
        raise OwnerTruthMemoryChangeSetActivationError("changeset operation cannot be applied")

    target_content = target.typed_content
    resolved_content_v5 = canonicalize_memory_payload(
        kind=resolved.memory_kind,
        payload=resolved.content,
        schema_version=OWNER_TRUTH_SCHEMA_VERSION_V5,
    )
    if operation.kind is OwnerTruthMemoryChangeOperationKind.REFINE:
        content_to_write = merge_lossless_refinement_content(
            base_content=target_content,
            incoming_content=resolved_content_v5,
            kind=target.memory_kind,
        )
    else:
        content_to_write = _with_merged_provenance(
            base_content=target_content,
            incoming_content=resolved_content_v5,
            prefer_incoming=operation.kind is OwnerTruthMemoryChangeOperationKind.CORRECT,
            kind=target.memory_kind,
        )
    validation = validate_memory_payload(
        kind=target.memory_kind,
        payload=content_to_write,
        schema_version=OWNER_TRUTH_SCHEMA_VERSION_V5,
    )
    if not validation.accepted:
        raise OwnerTruthMemoryChangeSetActivationError(
            f"changeset replacement content is not admitted: {validation.code}"
        )
    evidence_refs = _merge_refs(target.evidence_refs, resolved.source_refs)
    replacement_version = target.version_number + 1
    replacement_version_id = str(
        uuid5(
            _VERSION_NAMESPACE,
            f"{candidate.vault_id}:{receipt_id}:{target.memory_id}:{replacement_version}",
        )
    )
    payload = {
        "schemaVersion": OWNER_TRUTH_MEMORY_VERSION_SCHEMA_VERSION,
        "contentSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION_V5,
        "content": content_to_write,
        "evidenceRefs": evidence_refs,
        "candidateId": candidate.candidate_id,
        "decisionReceiptId": receipt_id,
        "changeSetId": changeset.change_set_id,
        "changeSetOperation": operation.kind.value,
        "supersedesVersionId": target.memory_version_id,
    }
    source_versions = {
        int(reference["sourceVersion"])
        for reference in resolved.source_refs
        if str(reference.get("sourceId") or "") == resolved.source_id
    }
    if len(source_versions) != 1:
        raise OwnerTruthMemoryChangeSetActivationError(
            "changeset replacement candidate source version is ambiguous"
        )
    return OwnerTruthMemoryChangeSetActivationPlan(
        change_set=changeset,
        outcome="revised",
        receipt_id=receipt_id,
        candidate_id=candidate.candidate_id,
        decision=decision,
        memory_id=target.memory_id,
        memory_version_id=replacement_version_id,
        memory_version=replacement_version,
        authority_epoch=candidate.authority_epoch,
        content_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V5,
        content_hash=_digest(content_to_write),
        payload=payload,
        source_id=resolved.source_id,
        source_version=next(iter(source_versions)),
        memory_kind=target.memory_kind.value,
        perspective_type=None,
        epistemic_status=None,
        sensitivity=None,
        policy_version=None,
        supersedes_version_id=target.memory_version_id,
        relation_to_memory_id=None,
        relation_type=None,
    )


__all__ = [
    "OWNER_TRUTH_MEMORY_CHANGESET_ACTIVATION_SCHEMA_VERSION",
    "OwnerTruthMemoryChangeSetActivationError",
    "OwnerTruthMemoryChangeSetActivationPlan",
    "build_memory_changeset_activation_plan",
]
