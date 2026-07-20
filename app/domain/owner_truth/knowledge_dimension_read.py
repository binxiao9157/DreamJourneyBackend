"""Fail-closed M0-B read adapter for Owner-confirmed knowledge dimensions.

The M0-B selector must never infer a life dimension from memory text, KBLite,
or an AI label.  This module consumes the existing owner-scoped
``MemoryVersion`` projection only when a current, standard knowledge memory
contains an explicit classification that a future Owner review flow has marked
as confirmed.  It is read-only and deliberately produces no Candidate,
MemoryVersion, projection checkpoint, outbox intent, or provider effect.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional, Protocol

from .contracts import OwnerTruthContractError, require_nonblank
from .knowledge_recommendations import (
    ConfirmedMemoryDimensionEvidence,
    DimensionProjection,
    KnowledgeDimensionProjector,
    KnowledgeRecommendationError,
)
from .memory_projection import OwnerTruthMemoryProjectionAccessDenied
from .source_commands import OwnerTruthCommandContext


OWNER_TRUTH_KNOWLEDGE_DIMENSION_EVIDENCE_SCHEMA_VERSION = (
    "owner-truth-knowledge-dimension-evidence-v1"
)
OWNER_TRUTH_KNOWLEDGE_DIMENSION_READ_SCHEMA_VERSION = "owner-truth-knowledge-dimension-read-v1"


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
        if self.state is OwnerTruthKnowledgeDimensionReadState.READY:
            if not checkpoint or self.coverage is None:
                raise OwnerTruthKnowledgeDimensionReadError(
                    "ready dimension read requires a checkpoint and coverage"
                )
            if (
                self.coverage.owner_subject_id != self.owner_subject_id
                or self.coverage.vault_id != self.vault_id
            ):
                raise OwnerTruthKnowledgeDimensionReadError("coverage scope does not match read scope")
        elif self.coverage is not None or self.included_memory_version_ids or self.exclusions:
            raise OwnerTruthKnowledgeDimensionReadError(
                "non-ready dimension reads must not retain coverage evidence"
            )
        object.__setattr__(self, "checkpoint", checkpoint or None)

    def value_free_summary(self) -> dict[str, object]:
        """Export only opaque evidence references and policy-safe counts."""

        summary: dict[str, object] = {
            "schemaVersion": OWNER_TRUTH_KNOWLEDGE_DIMENSION_READ_SCHEMA_VERSION,
            "state": self.state.value,
            "vaultId": self.vault_id,
            "authorityEpoch": self.authority_epoch,
            "checkpoint": self.checkpoint,
            "includedMemoryVersionIds": list(self.included_memory_version_ids),
            "excluded": [item.value_free_summary() for item in self.exclusions],
        }
        if self.coverage is not None:
            summary["coverage"] = self.coverage.value_free_summary()
        return summary


class OwnerTruthMemoryProjectionReader(Protocol):
    def read(self, *, context: OwnerTruthCommandContext) -> Mapping[str, Any]:
        ...


class OwnerTruthKnowledgeDimensionReadService:
    """Read M0-B coverage through the existing fail-closed MemoryVersion view."""

    def __init__(self, memory_projection_reader: OwnerTruthMemoryProjectionReader) -> None:
        self._memory_projection_reader = memory_projection_reader

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
        snapshot = self._memory_projection_reader.read(context=context)
        return read_owner_confirmed_dimension_coverage(
            memory_projection=snapshot,
            owner_subject_id=context.owner_subject_id,
            vault_id=context.vault_id,
        )


def read_owner_confirmed_dimension_coverage(
    *,
    memory_projection: Mapping[str, Any],
    owner_subject_id: str,
    vault_id: str,
) -> OwnerTruthKnowledgeDimensionReadResult:
    """Map explicit, Owner-confirmed annotations from a ready projection.

    The source projection has already checked active Owner/Vault/epoch state.
    This adapter checks the scope again and treats any malformed top-level
    snapshot as unavailable.  Per-memory classification omissions are normal
    during migration and become value-free exclusions, not guessed coverage.
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
        return OwnerTruthKnowledgeDimensionReadResult(
            state=OwnerTruthKnowledgeDimensionReadState.REBUILDING,
            owner_subject_id=owner,
            vault_id=vault,
            authority_epoch=authority_epoch,
            checkpoint=None,
            coverage=None,
            included_memory_version_ids=(),
            exclusions=(),
        )
    if state != OwnerTruthKnowledgeDimensionReadState.READY.value:
        return OwnerTruthKnowledgeDimensionReadResult(
            state=OwnerTruthKnowledgeDimensionReadState.UNAVAILABLE,
            owner_subject_id=owner,
            vault_id=vault,
            authority_epoch=authority_epoch,
            checkpoint=None,
            coverage=None,
            included_memory_version_ids=(),
            exclusions=(),
        )

    checkpoint = str(memory_projection.get("checkpoint") or "").strip()
    entries = memory_projection.get("entries")
    if not checkpoint or not isinstance(entries, list):
        return OwnerTruthKnowledgeDimensionReadResult(
            state=OwnerTruthKnowledgeDimensionReadState.UNAVAILABLE,
            owner_subject_id=owner,
            vault_id=vault,
            authority_epoch=authority_epoch,
            checkpoint=None,
            coverage=None,
            included_memory_version_ids=(),
            exclusions=(),
        )

    included: list[ConfirmedMemoryDimensionEvidence] = []
    exclusions: list[KnowledgeDimensionEvidenceExclusion] = []
    for raw_entry in entries:
        if not isinstance(raw_entry, Mapping):
            return _unavailable_result(
                owner_subject_id=owner,
                vault_id=vault,
                authority_epoch=authority_epoch,
            )
        if not _is_complete_projection_entry(raw_entry):
            return _unavailable_result(
                owner_subject_id=owner,
                vault_id=vault,
                authority_epoch=authority_epoch,
            )
        memory_version_id = _memory_version_id(raw_entry)
        if memory_version_id is None:
            return _unavailable_result(
                owner_subject_id=owner,
                vault_id=vault,
                authority_epoch=authority_epoch,
            )
        evidence, reason_code = _dimension_evidence_from_entry(
            raw_entry,
            vault_id=vault,
            owner_subject_id=owner,
        )
        if evidence is None:
            exclusions.append(
                KnowledgeDimensionEvidenceExclusion(
                    memory_version_id=memory_version_id,
                    reason_code=reason_code or "dimensionEvidenceUnavailable",
                )
            )
            continue
        included.append(evidence)

    coverage = KnowledgeDimensionProjector().project(
        owner_subject_id=owner,
        vault_id=vault,
        evidence=included,
    )
    return OwnerTruthKnowledgeDimensionReadResult(
        state=OwnerTruthKnowledgeDimensionReadState.READY,
        owner_subject_id=owner,
        vault_id=vault,
        authority_epoch=authority_epoch,
        checkpoint=checkpoint,
        coverage=coverage,
        included_memory_version_ids=tuple(item.memory_version_id for item in included),
        exclusions=tuple(exclusions),
    )


def _unavailable_result(
    *,
    owner_subject_id: str,
    vault_id: str,
    authority_epoch: int,
) -> OwnerTruthKnowledgeDimensionReadResult:
    return OwnerTruthKnowledgeDimensionReadResult(
        state=OwnerTruthKnowledgeDimensionReadState.UNAVAILABLE,
        owner_subject_id=owner_subject_id,
        vault_id=vault_id,
        authority_epoch=authority_epoch,
        checkpoint=None,
        coverage=None,
        included_memory_version_ids=(),
        exclusions=(),
    )


def _memory_version_id(entry: Mapping[str, Any]) -> Optional[str]:
    citation = entry.get("citation")
    if not isinstance(citation, Mapping):
        return None
    identifier = str(citation.get("memoryVersionId") or "").strip()
    return identifier or None


def _is_complete_projection_entry(entry: Mapping[str, Any]) -> bool:
    """Distinguish an unclassified MemoryVersion from a corrupt checkpoint."""

    citation = entry.get("citation")
    if not isinstance(citation, Mapping):
        return False
    if not str(citation.get("memoryVersionId") or "").strip():
        return False
    if not str(citation.get("sourceId") or "").strip():
        return False
    if not isinstance(entry.get("content"), Mapping):
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
    vault_id: str,
    owner_subject_id: str,
) -> tuple[Optional[ConfirmedMemoryDimensionEvidence], Optional[str]]:
    if str(entry.get("visibility") or "") != "owner":
        return None, "ownerVisibilityRequired"
    if str(entry.get("memoryKind") or "") != "knowledge":
        return None, "memoryKindNotKnowledge"
    if str(entry.get("sensitivity") or "") != "standard":
        return None, "sensitivityNotStandard"
    if str(entry.get("perspectiveType") or "") == "inferred":
        return None, "inferredPerspective"
    if str(entry.get("epistemicStatus") or "") == "inferred":
        return None, "inferredEpistemicStatus"

    content = entry.get("content")
    if not isinstance(content, Mapping):
        return None, "missingDimensionEvidence"
    annotation = content.get("knowledgeDimensionEvidence")
    if not isinstance(annotation, Mapping):
        return None, "missingDimensionEvidence"
    if str(annotation.get("schemaVersion") or "") != OWNER_TRUTH_KNOWLEDGE_DIMENSION_EVIDENCE_SCHEMA_VERSION:
        return None, "unsupportedDimensionEvidenceSchema"
    if annotation.get("classificationConfirmedByOwner") is not True:
        return None, "dimensionClassificationNotOwnerConfirmed"
    if annotation.get("isAiInferenceOnly") is not False:
        return None, "aiInferenceOnly"

    citation = entry.get("citation")
    if not isinstance(citation, Mapping):
        return None, "invalidCitation"
    try:
        evidence = ConfirmedMemoryDimensionEvidence(
            memory_version_id=str(citation.get("memoryVersionId") or ""),
            source_id=str(citation.get("sourceId") or ""),
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            dimension=str(annotation.get("dimension") or ""),
            covered_facets=tuple(annotation.get("coveredFacets") or ()),
            is_current_confirmed=True,
            is_accessible=True,
            is_deleted=False,
            is_revoked=False,
            is_disputed=False,
            is_ai_inference_only=False,
        )
    except (KnowledgeRecommendationError, TypeError, ValueError):
        return None, "invalidDimensionEvidence"
    return evidence, None


__all__ = [
    "KnowledgeDimensionEvidenceExclusion",
    "OWNER_TRUTH_KNOWLEDGE_DIMENSION_EVIDENCE_SCHEMA_VERSION",
    "OWNER_TRUTH_KNOWLEDGE_DIMENSION_READ_SCHEMA_VERSION",
    "OwnerTruthKnowledgeDimensionReadError",
    "OwnerTruthKnowledgeDimensionReadResult",
    "OwnerTruthKnowledgeDimensionReadService",
    "OwnerTruthKnowledgeDimensionReadState",
    "read_owner_confirmed_dimension_coverage",
]
