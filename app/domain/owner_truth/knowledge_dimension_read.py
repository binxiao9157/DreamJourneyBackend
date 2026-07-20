"""Fail-closed M0-B read adapter for Owner-confirmed knowledge dimensions.

Coverage never comes from memory text, an embedded payload annotation, KBLite,
or an AI label.  It comes only from a separately persisted, append-only Owner
confirmation receipt that matches a current Owner Truth ``MemoryVersion`` and
its exact content hash.  Replacing a memory version therefore invalidates the
old receipt naturally, without rewriting historical data.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
from typing import Any, Iterable, Mapping, Optional, Protocol

from .contracts import OwnerTruthContractError, require_nonblank
from .candidate_decisions import OwnerTruthCandidateReviewAccessDenied
from .knowledge_recommendations import (
    ConfirmedMemoryDimensionEvidence,
    DimensionProjection,
    KnowledgeDimensionProjector,
    KnowledgeRecommendationError,
)
from .memory_projection import OwnerTruthMemoryProjectionAccessDenied
from .source_commands import OwnerTruthCommandContext


# Retained only as a source-compatibility symbol for old QA callers.  The
# reader intentionally ignores this embedded payload annotation.
OWNER_TRUTH_KNOWLEDGE_DIMENSION_EVIDENCE_SCHEMA_VERSION = (
    "owner-truth-knowledge-dimension-evidence-v1"
)
OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_SCHEMA_VERSION = (
    "owner-truth-knowledge-dimension-confirmation-v1"
)
OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_METHOD = "ownerExplicitSelection"
OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_UI_SCHEMA_VERSION = (
    "knowledge-dimension-review-v1"
)
OWNER_TRUTH_KNOWLEDGE_DIMENSION_READ_SCHEMA_VERSION = "owner-truth-knowledge-dimension-read-v2"


class OwnerTruthKnowledgeDimensionReadError(OwnerTruthContractError):
    """A derived M0-B coverage projection cannot be read safely."""


class OwnerTruthKnowledgeDimensionReadState(str, Enum):
    READY = "ready"
    REBUILDING = "rebuilding"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class KnowledgeDimensionEvidenceExclusion:
    """A value-free reason why one current MemoryVersion was not counted."""

    memory_version_id: str
    reason_code: str

    def value_free_summary(self) -> dict[str, str]:
        return {
            "memoryVersionId": self.memory_version_id,
            "reasonCode": self.reason_code,
        }


@dataclass(frozen=True)
class OwnerTruthKnowledgeDimensionReadResult:
    """A safe M0-B view over one checked Owner Truth memory projection."""

    state: OwnerTruthKnowledgeDimensionReadState
    owner_subject_id: str
    vault_id: str
    authority_epoch: int
    checkpoint: Optional[str]
    memory_projection_checkpoint: Optional[str]
    confirmation_checkpoint: Optional[str]
    coverage: Optional[DimensionProjection]
    included_memory_version_ids: tuple[str, ...]
    exclusions: tuple[KnowledgeDimensionEvidenceExclusion, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", OwnerTruthKnowledgeDimensionReadState(self.state))
        object.__setattr__(
            self,
            "owner_subject_id",
            require_nonblank(self.owner_subject_id, field="owner_subject_id"),
        )
        object.__setattr__(self, "vault_id", require_nonblank(self.vault_id, field="vault_id"))
        if not isinstance(self.authority_epoch, int) or isinstance(self.authority_epoch, bool):
            raise OwnerTruthKnowledgeDimensionReadError("authority_epoch must be an integer")
        if self.authority_epoch < 0:
            raise OwnerTruthKnowledgeDimensionReadError("authority_epoch must not be negative")
        checkpoint = str(self.checkpoint or "").strip()
        memory_projection_checkpoint = str(self.memory_projection_checkpoint or "").strip()
        confirmation_checkpoint = str(self.confirmation_checkpoint or "").strip()
        if self.state is OwnerTruthKnowledgeDimensionReadState.READY:
            if (
                not checkpoint
                or not memory_projection_checkpoint
                or not confirmation_checkpoint
                or self.coverage is None
            ):
                raise OwnerTruthKnowledgeDimensionReadError(
                    "ready dimension read requires projection, receipt and combined checkpoints"
                )
            if (
                self.coverage.owner_subject_id != self.owner_subject_id
                or self.coverage.vault_id != self.vault_id
            ):
                raise OwnerTruthKnowledgeDimensionReadError("coverage scope does not match read scope")
        elif (
            self.coverage is not None
            or self.included_memory_version_ids
            or self.exclusions
            or checkpoint
            or memory_projection_checkpoint
            or confirmation_checkpoint
        ):
            raise OwnerTruthKnowledgeDimensionReadError(
                "non-ready dimension reads must not retain coverage evidence"
            )
        object.__setattr__(self, "checkpoint", checkpoint or None)
        object.__setattr__(self, "memory_projection_checkpoint", memory_projection_checkpoint or None)
        object.__setattr__(self, "confirmation_checkpoint", confirmation_checkpoint or None)

    def value_free_summary(self) -> dict[str, object]:
        """Export only opaque evidence references and policy-safe counts."""

        summary: dict[str, object] = {
            "schemaVersion": OWNER_TRUTH_KNOWLEDGE_DIMENSION_READ_SCHEMA_VERSION,
            "state": self.state.value,
            "vaultId": self.vault_id,
            "authorityEpoch": self.authority_epoch,
            "checkpoint": self.checkpoint,
            "memoryProjectionCheckpoint": self.memory_projection_checkpoint,
            "confirmationCheckpoint": self.confirmation_checkpoint,
            "includedMemoryVersionIds": list(self.included_memory_version_ids),
            "excluded": [item.value_free_summary() for item in self.exclusions],
        }
        if self.coverage is not None:
            summary["coverage"] = self.coverage.value_free_summary()
        return summary


class OwnerTruthMemoryProjectionReader(Protocol):
    def read(self, *, context: OwnerTruthCommandContext) -> Mapping[str, Any]:
        ...


class OwnerTruthKnowledgeDimensionConfirmationReader(Protocol):
    def list_for_projection(
        self,
        *,
        context: OwnerTruthCommandContext,
        memory_version_ids: Iterable[str],
    ) -> Iterable[Mapping[str, Any]]:
        ...


class OwnerTruthKnowledgeDimensionReadService:
    """Read M0-B coverage through current projections plus immutable receipts."""

    def __init__(
        self,
        memory_projection_reader: OwnerTruthMemoryProjectionReader,
        confirmation_reader: OwnerTruthKnowledgeDimensionConfirmationReader | None = None,
    ) -> None:
        self._memory_projection_reader = memory_projection_reader
        self._confirmation_reader = confirmation_reader

    def read(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthKnowledgeDimensionReadResult:
        if not isinstance(context, OwnerTruthCommandContext):
            raise OwnerTruthKnowledgeDimensionReadError("owner truth command context is required")
        if context.actor_subject_id != context.owner_subject_id:
            raise OwnerTruthMemoryProjectionAccessDenied(
                "only the Vault Owner may read knowledge dimension coverage"
            )
        try:
            snapshot = self._memory_projection_reader.read(context=context)
        except OwnerTruthCandidateReviewAccessDenied as error:
            # In-memory and persistence-backed projection readers may expose
            # their lower-level Vault denial directly. Normalize it at this
            # read boundary so callers never mistake an ownership failure for
            # an invalid receipt or candidate payload.
            raise OwnerTruthMemoryProjectionAccessDenied(str(error)) from error
        if (
            not isinstance(snapshot, Mapping)
            or str(snapshot.get("vaultId") or "").strip() != context.vault_id
            or str(snapshot.get("ownerSubjectId") or "").strip()
            != context.owner_subject_id
        ):
            # A repository may return a checkpoint belonging to another Owner
            # only when its scope boundary is broken. Treat that as denial,
            # rather than a malformed caller request, and never continue to
            # inspect receipts or coverage.
            raise OwnerTruthMemoryProjectionAccessDenied(
                "memory projection is not active for this Owner Vault"
            )
        confirmations: Iterable[Mapping[str, Any]] = ()
        if self._confirmation_reader is not None:
            confirmations = self._confirmation_reader.list_for_projection(
                context=context,
                memory_version_ids=_projection_memory_version_ids(snapshot),
            )
        return read_owner_confirmed_dimension_coverage(
            memory_projection=snapshot,
            owner_subject_id=context.owner_subject_id,
            vault_id=context.vault_id,
            confirmations=confirmations,
        )


def read_owner_confirmed_dimension_coverage(
    *,
    memory_projection: Mapping[str, Any],
    owner_subject_id: str,
    vault_id: str,
    confirmations: Iterable[Mapping[str, Any]] = (),
) -> OwnerTruthKnowledgeDimensionReadResult:
    """Map explicit receipt evidence from a complete current projection.

    Top-level projection corruption becomes unavailable.  Missing or malformed
    per-memory confirmation receipts are value-free exclusions rather than
    opportunities to infer coverage from a payload or model label.
    """

    owner = require_nonblank(owner_subject_id, field="owner_subject_id")
    vault = require_nonblank(vault_id, field="vault_id")
    if not isinstance(memory_projection, Mapping):
        raise OwnerTruthKnowledgeDimensionReadError("memory_projection must be an object")

    snapshot_owner = str(memory_projection.get("ownerSubjectId") or "").strip()
    snapshot_vault = str(memory_projection.get("vaultId") or "").strip()
    if snapshot_owner != owner or snapshot_vault != vault:
        raise OwnerTruthKnowledgeDimensionReadError("memory projection scope does not match Owner context")

    authority_epoch = memory_projection.get("authorityEpoch")
    if not isinstance(authority_epoch, int) or isinstance(authority_epoch, bool) or authority_epoch < 0:
        raise OwnerTruthKnowledgeDimensionReadError("memory projection authorityEpoch is invalid")

    state = str(memory_projection.get("state") or "").strip()
    if state == OwnerTruthKnowledgeDimensionReadState.REBUILDING.value:
        return _non_ready_result(
            state=OwnerTruthKnowledgeDimensionReadState.REBUILDING,
            owner_subject_id=owner,
            vault_id=vault,
            authority_epoch=authority_epoch,
        )
    if state != OwnerTruthKnowledgeDimensionReadState.READY.value:
        return _non_ready_result(
            state=OwnerTruthKnowledgeDimensionReadState.UNAVAILABLE,
            owner_subject_id=owner,
            vault_id=vault,
            authority_epoch=authority_epoch,
        )

    projection_checkpoint = str(memory_projection.get("checkpoint") or "").strip()
    entries = memory_projection.get("entries")
    if not projection_checkpoint or not isinstance(entries, list):
        return _non_ready_result(
            state=OwnerTruthKnowledgeDimensionReadState.UNAVAILABLE,
            owner_subject_id=owner,
            vault_id=vault,
            authority_epoch=authority_epoch,
        )

    receipt_index = _confirmation_index(confirmations)
    included: list[ConfirmedMemoryDimensionEvidence] = []
    included_ids: list[str] = []
    exclusions: list[KnowledgeDimensionEvidenceExclusion] = []
    for raw_entry in entries:
        if not isinstance(raw_entry, Mapping) or not _is_complete_projection_entry(raw_entry):
            return _non_ready_result(
                state=OwnerTruthKnowledgeDimensionReadState.UNAVAILABLE,
                owner_subject_id=owner,
                vault_id=vault,
                authority_epoch=authority_epoch,
            )
        memory_version_id = _memory_version_id(raw_entry)
        if memory_version_id is None:
            return _non_ready_result(
                state=OwnerTruthKnowledgeDimensionReadState.UNAVAILABLE,
                owner_subject_id=owner,
                vault_id=vault,
                authority_epoch=authority_epoch,
            )
        evidence, reason_code = _dimension_evidence_from_entry(
            raw_entry,
            receipts=receipt_index.get(memory_version_id, ()),
            vault_id=vault,
            owner_subject_id=owner,
            authority_epoch=authority_epoch,
        )
        if not evidence:
            exclusions.append(
                KnowledgeDimensionEvidenceExclusion(
                    memory_version_id=memory_version_id,
                    reason_code=reason_code or "ownerConfirmationUnavailable",
                )
            )
            continue
        included.extend(evidence)
        included_ids.append(memory_version_id)

    coverage = KnowledgeDimensionProjector().project(
        owner_subject_id=owner,
        vault_id=vault,
        evidence=included,
    )
    confirmation_checkpoint = _confirmation_checkpoint(receipt_index)
    combined_checkpoint = _digest(
        {
            "memoryProjectionCheckpoint": projection_checkpoint,
            "confirmationCheckpoint": confirmation_checkpoint,
        }
    )
    return OwnerTruthKnowledgeDimensionReadResult(
        state=OwnerTruthKnowledgeDimensionReadState.READY,
        owner_subject_id=owner,
        vault_id=vault,
        authority_epoch=authority_epoch,
        checkpoint=combined_checkpoint,
        memory_projection_checkpoint=projection_checkpoint,
        confirmation_checkpoint=confirmation_checkpoint,
        coverage=coverage,
        included_memory_version_ids=tuple(dict.fromkeys(included_ids)),
        exclusions=tuple(exclusions),
    )


def _non_ready_result(
    *,
    state: OwnerTruthKnowledgeDimensionReadState,
    owner_subject_id: str,
    vault_id: str,
    authority_epoch: int,
) -> OwnerTruthKnowledgeDimensionReadResult:
    return OwnerTruthKnowledgeDimensionReadResult(
        state=state,
        owner_subject_id=owner_subject_id,
        vault_id=vault_id,
        authority_epoch=authority_epoch,
        checkpoint=None,
        memory_projection_checkpoint=None,
        confirmation_checkpoint=None,
        coverage=None,
        included_memory_version_ids=(),
        exclusions=(),
    )


def _digest(value: Any) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthKnowledgeDimensionReadError("confirmation checkpoint values are invalid") from exc
    return sha256(encoded.encode("utf-8")).hexdigest()


def _projection_memory_version_ids(snapshot: Mapping[str, Any]) -> tuple[str, ...]:
    if not isinstance(snapshot, Mapping) or str(snapshot.get("state") or "") != "ready":
        return ()
    entries = snapshot.get("entries")
    if not isinstance(entries, list):
        return ()
    values: list[str] = []
    for entry in entries:
        if isinstance(entry, Mapping):
            memory_version_id = _memory_version_id(entry)
            if memory_version_id is not None:
                values.append(memory_version_id)
    return tuple(dict.fromkeys(values))


def _confirmation_index(
    confirmations: Iterable[Mapping[str, Any]],
) -> dict[str, tuple[Mapping[str, Any], ...]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    try:
        iterator = iter(confirmations)
    except TypeError as exc:
        raise OwnerTruthKnowledgeDimensionReadError("confirmations must be iterable") from exc
    for raw_receipt in iterator:
        if not isinstance(raw_receipt, Mapping):
            # Retain malformed entries under a synthetic key only so they cannot
            # become evidence; no raw detail crosses this boundary.
            continue
        memory_version_id = str(raw_receipt.get("memoryVersionId") or "").strip()
        if not memory_version_id:
            continue
        grouped.setdefault(memory_version_id, []).append(dict(raw_receipt))
    return {
        key: tuple(
            sorted(
                values,
                key=lambda item: (
                    str(item.get("dimension") or ""),
                    str(item.get("confirmationId") or ""),
                ),
            )
        )
        for key, values in grouped.items()
    }


def _confirmation_checkpoint(
    index: Mapping[str, tuple[Mapping[str, Any], ...]],
) -> str:
    records: list[dict[str, Any]] = []
    for memory_version_id in sorted(index):
        for receipt in index[memory_version_id]:
            records.append(
                {
                    "confirmationId": str(receipt.get("confirmationId") or ""),
                    "memoryVersionId": memory_version_id,
                    "boundContentHash": str(receipt.get("boundContentHash") or ""),
                    "authorityEpoch": receipt.get("authorityEpoch"),
                    "dimension": str(receipt.get("dimension") or ""),
                    "coveredFacets": list(receipt.get("coveredFacets") or []),
                    "confirmationMethod": str(receipt.get("confirmationMethod") or ""),
                    "schemaVersion": str(receipt.get("schemaVersion") or ""),
                    "uiSchemaVersion": str(receipt.get("uiSchemaVersion") or ""),
                }
            )
    return _digest(records)


def _memory_version_id(entry: Mapping[str, Any]) -> Optional[str]:
    citation = entry.get("citation")
    if not isinstance(citation, Mapping):
        return None
    identifier = str(citation.get("memoryVersionId") or "").strip()
    return identifier or None


def _is_complete_projection_entry(entry: Mapping[str, Any]) -> bool:
    """Distinguish a no-receipt memory from a corrupt projection checkpoint."""

    citation = entry.get("citation")
    if not isinstance(citation, Mapping):
        return False
    if not str(citation.get("memoryId") or "").strip():
        return False
    if not str(citation.get("memoryVersionId") or "").strip():
        return False
    if not str(citation.get("sourceId") or "").strip():
        return False
    if not str(citation.get("contentHash") or "").strip():
        return False
    return all(
        str(entry.get(field) or "").strip()
        for field in (
            "visibility",
            "memoryKind",
            "sensitivity",
            "perspectiveType",
            "epistemicStatus",
        )
    )


def _dimension_evidence_from_entry(
    entry: Mapping[str, Any],
    *,
    receipts: Iterable[Mapping[str, Any]],
    vault_id: str,
    owner_subject_id: str,
    authority_epoch: int,
) -> tuple[list[ConfirmedMemoryDimensionEvidence], Optional[str]]:
    if str(entry.get("visibility") or "") != "owner":
        return [], "ownerVisibilityRequired"
    if str(entry.get("memoryKind") or "") != "knowledge":
        return [], "memoryKindNotKnowledge"
    if str(entry.get("sensitivity") or "") != "standard":
        return [], "sensitivityNotStandard"
    if str(entry.get("perspectiveType") or "") == "inferred":
        return [], "inferredPerspective"
    if str(entry.get("epistemicStatus") or "") == "inferred":
        return [], "inferredEpistemicStatus"

    citation = entry.get("citation")
    if not isinstance(citation, Mapping):
        return [], "invalidCitation"
    memory_id = str(citation.get("memoryId") or "").strip()
    memory_version_id = str(citation.get("memoryVersionId") or "").strip()
    source_id = str(citation.get("sourceId") or "").strip()
    content_hash = str(citation.get("contentHash") or "").strip()
    if not memory_id or not memory_version_id or not source_id or not content_hash:
        return [], "invalidCitation"

    evidence: list[ConfirmedMemoryDimensionEvidence] = []
    seen_dimensions: set[str] = set()
    invalid_receipt = False
    for receipt in receipts:
        try:
            if (
                str(receipt.get("schemaVersion") or "")
                != OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_SCHEMA_VERSION
                or str(receipt.get("uiSchemaVersion") or "")
                != OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_UI_SCHEMA_VERSION
                or str(receipt.get("confirmationMethod") or "")
                != OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_METHOD
                or str(receipt.get("vaultId") or "") != vault_id
                or str(receipt.get("ownerSubjectId") or "") != owner_subject_id
                or str(receipt.get("actorSubjectId") or "") != owner_subject_id
                or int(receipt.get("authorityEpoch")) != authority_epoch
                or str(receipt.get("memoryId") or "") != memory_id
                or str(receipt.get("memoryVersionId") or "") != memory_version_id
                or str(receipt.get("boundContentHash") or "") != content_hash
            ):
                invalid_receipt = True
                continue
            dimension = str(receipt.get("dimension") or "")
            if dimension in seen_dimensions:
                invalid_receipt = True
                continue
            item = ConfirmedMemoryDimensionEvidence(
                memory_version_id=memory_version_id,
                source_id=source_id,
                vault_id=vault_id,
                owner_subject_id=owner_subject_id,
                dimension=dimension,
                covered_facets=tuple(receipt.get("coveredFacets") or ()),
                is_current_confirmed=True,
                is_accessible=True,
                is_deleted=False,
                is_revoked=False,
                is_disputed=False,
                is_ai_inference_only=False,
            )
        except (KnowledgeRecommendationError, TypeError, ValueError):
            invalid_receipt = True
            continue
        seen_dimensions.add(dimension)
        evidence.append(item)

    if evidence:
        return evidence, None
    if invalid_receipt:
        return [], "invalidOwnerConfirmationReceipt"
    return [], "missingOwnerConfirmationReceipt"


__all__ = [
    "KnowledgeDimensionEvidenceExclusion",
    "OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_METHOD",
    "OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_SCHEMA_VERSION",
    "OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_UI_SCHEMA_VERSION",
    "OWNER_TRUTH_KNOWLEDGE_DIMENSION_EVIDENCE_SCHEMA_VERSION",
    "OWNER_TRUTH_KNOWLEDGE_DIMENSION_READ_SCHEMA_VERSION",
    "OwnerTruthKnowledgeDimensionConfirmationReader",
    "OwnerTruthKnowledgeDimensionReadError",
    "OwnerTruthKnowledgeDimensionReadResult",
    "OwnerTruthKnowledgeDimensionReadService",
    "OwnerTruthKnowledgeDimensionReadState",
    "read_owner_confirmed_dimension_coverage",
]
