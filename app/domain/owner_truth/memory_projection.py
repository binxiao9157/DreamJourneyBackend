"""Deterministic Owner Truth compatibility projection contracts.

The projection is a derived read model, never a second authority.  Its input
is the current, confirmed ``MemoryVersion`` chain only.  Decision receipts,
Candidate proposals, and review rationale deliberately do not cross this
boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Iterable, Mapping

from .contracts import OwnerTruthContractError, require_nonblank, require_uuid


OWNER_TRUTH_MEMORY_PROJECTION_SCHEMA_VERSION = "owner-truth-memory-projection-v1"
OWNER_TRUTH_MEMORY_PROJECTION_SOURCE = "v4"
OWNER_TRUTH_MEMORY_PROJECTION_VISIBILITY = "owner"


class OwnerTruthMemoryProjectionError(OwnerTruthContractError):
    """A projection cannot safely be built or read."""


class OwnerTruthMemoryProjectionAccessDenied(OwnerTruthMemoryProjectionError):
    """The requested Owner Vault is not active for the current actor."""


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthMemoryProjectionError(
            "memory projection values must be JSON serializable"
        ) from exc


def _digest(value: Any) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _copy_object(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise OwnerTruthMemoryProjectionError(f"{field} must be an object")
    copied = json.loads(_canonical_json(dict(value)))
    if not isinstance(copied, dict):  # defensive: mappings decode to objects
        raise OwnerTruthMemoryProjectionError(f"{field} must be an object")
    return copied


def _copy_object_list(value: Any, *, field: str) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        raise OwnerTruthMemoryProjectionError(f"{field} must be a list")
    return tuple(_copy_object(item, field=f"{field} item") for item in value)


@dataclass(frozen=True)
class OwnerTruthMemoryProjectionInput:
    """One current MemoryVersion eligible for an Owner-only projection."""

    memory_id: str
    memory_version_id: str
    vault_id: str
    owner_subject_id: str
    authority_epoch: int
    version_number: int
    source_id: str
    source_version: int
    memory_kind: str
    perspective_type: str
    epistemic_status: str
    sensitivity: str
    content_schema_version: str
    content_hash: str
    content: Mapping[str, Any]
    evidence_refs: tuple[Mapping[str, Any], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "memory_id", require_uuid(self.memory_id, field="memory_id"))
        object.__setattr__(
            self,
            "memory_version_id",
            require_uuid(self.memory_version_id, field="memory_version_id"),
        )
        object.__setattr__(self, "vault_id", require_nonblank(self.vault_id, field="vault_id"))
        object.__setattr__(
            self,
            "owner_subject_id",
            require_nonblank(self.owner_subject_id, field="owner_subject_id"),
        )
        object.__setattr__(self, "source_id", require_uuid(self.source_id, field="source_id"))
        for field in (
            "memory_kind",
            "perspective_type",
            "epistemic_status",
            "sensitivity",
            "content_schema_version",
            "content_hash",
        ):
            object.__setattr__(self, field, require_nonblank(getattr(self, field), field=field))
        if self.authority_epoch < 0:
            raise OwnerTruthMemoryProjectionError("authority_epoch must not be negative")
        if self.version_number < 1:
            raise OwnerTruthMemoryProjectionError("version_number must be positive")
        if self.source_version < 1:
            raise OwnerTruthMemoryProjectionError("source_version must be positive")
        object.__setattr__(self, "content", _copy_object(self.content, field="content"))
        object.__setattr__(
            self,
            "evidence_refs",
            _copy_object_list(self.evidence_refs, field="evidence_refs"),
        )

    def entry(self) -> dict[str, Any]:
        """Build the only payload persisted in the compatibility projection."""

        return {
            "memoryId": self.memory_id,
            "memoryVersionId": self.memory_version_id,
            "memoryVersion": self.version_number,
            "sourceId": self.source_id,
            "sourceVersion": self.source_version,
            "memoryKind": self.memory_kind,
            "perspectiveType": self.perspective_type,
            "epistemicStatus": self.epistemic_status,
            "sensitivity": self.sensitivity,
            "visibility": OWNER_TRUTH_MEMORY_PROJECTION_VISIBILITY,
            "contentSchemaVersion": self.content_schema_version,
            "contentHash": self.content_hash,
            "content": _copy_object(self.content, field="content"),
            "evidenceRefs": [
                _copy_object(item, field="evidence_refs item") for item in self.evidence_refs
            ],
            "citation": {
                "memoryId": self.memory_id,
                "memoryVersionId": self.memory_version_id,
                "sourceId": self.source_id,
                "sourceVersion": self.source_version,
                "contentHash": self.content_hash,
            },
        }


@dataclass(frozen=True)
class OwnerTruthMemoryProjectionResult:
    outcome: str
    snapshot: Mapping[str, Any]


def build_ready_memory_projection(
    *,
    vault_id: str,
    owner_subject_id: str,
    authority_epoch: int,
    inputs: Iterable[OwnerTruthMemoryProjectionInput],
) -> dict[str, Any]:
    """Create a stable checkpoint from a complete set of current inputs."""

    normalized_vault_id = require_nonblank(vault_id, field="vault_id")
    normalized_owner_id = require_nonblank(owner_subject_id, field="owner_subject_id")
    if authority_epoch < 0:
        raise OwnerTruthMemoryProjectionError("authority_epoch must not be negative")

    entries: list[dict[str, Any]] = []
    seen_memory_ids: set[str] = set()
    seen_version_ids: set[str] = set()
    for item in inputs:
        if item.vault_id != normalized_vault_id:
            raise OwnerTruthMemoryProjectionError("projection input crosses Vault boundary")
        if item.owner_subject_id != normalized_owner_id:
            raise OwnerTruthMemoryProjectionError("projection input crosses Owner boundary")
        if item.authority_epoch != authority_epoch:
            raise OwnerTruthMemoryProjectionError("projection input authority epoch is stale")
        if item.memory_id in seen_memory_ids or item.memory_version_id in seen_version_ids:
            raise OwnerTruthMemoryProjectionError("projection inputs must have one current version per memory")
        seen_memory_ids.add(item.memory_id)
        seen_version_ids.add(item.memory_version_id)
        entries.append(item.entry())

    entries.sort(key=lambda item: (item["memoryId"], item["memoryVersion"], item["memoryVersionId"]))
    source_hash = _digest(
        {
            "schemaVersion": OWNER_TRUTH_MEMORY_PROJECTION_SCHEMA_VERSION,
            "vaultId": normalized_vault_id,
            "ownerSubjectId": normalized_owner_id,
            "authorityEpoch": authority_epoch,
            "entries": entries,
        }
    )
    projection_hash = _digest(
        {
            "projectionSource": OWNER_TRUTH_MEMORY_PROJECTION_SOURCE,
            "sourceHash": source_hash,
            "entries": entries,
        }
    )
    return {
        "schemaVersion": OWNER_TRUTH_MEMORY_PROJECTION_SCHEMA_VERSION,
        "projectionSource": OWNER_TRUTH_MEMORY_PROJECTION_SOURCE,
        "state": "ready",
        "vaultId": normalized_vault_id,
        "ownerSubjectId": normalized_owner_id,
        "authorityEpoch": authority_epoch,
        "checkpoint": projection_hash,
        "sourceHash": source_hash,
        "entryCount": len(entries),
        "entries": entries,
    }


def build_rebuilding_memory_projection(
    *,
    vault_id: str,
    owner_subject_id: str,
    authority_epoch: int,
) -> dict[str, Any]:
    """Fail closed when a compatible current checkpoint is unavailable."""

    normalized_vault_id = require_nonblank(vault_id, field="vault_id")
    normalized_owner_id = require_nonblank(owner_subject_id, field="owner_subject_id")
    if authority_epoch < 0:
        raise OwnerTruthMemoryProjectionError("authority_epoch must not be negative")
    return {
        "schemaVersion": OWNER_TRUTH_MEMORY_PROJECTION_SCHEMA_VERSION,
        "projectionSource": OWNER_TRUTH_MEMORY_PROJECTION_SOURCE,
        "state": "rebuilding",
        "vaultId": normalized_vault_id,
        "ownerSubjectId": normalized_owner_id,
        "authorityEpoch": authority_epoch,
        "checkpoint": None,
        "entryCount": 0,
        "entries": [],
    }


def projection_summary(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Return a QA-safe summary without exposing projected memory content."""

    entries = snapshot.get("entries")
    if not isinstance(entries, list):
        raise OwnerTruthMemoryProjectionError("projection snapshot entries must be a list")
    summarized_entries = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise OwnerTruthMemoryProjectionError("projection entry must be an object")
        citation = _copy_object(entry.get("citation"), field="projection citation")
        summarized_entries.append(
            {
                "citation": citation,
                "memoryKind": str(entry.get("memoryKind") or ""),
                "perspectiveType": str(entry.get("perspectiveType") or ""),
                "sensitivity": str(entry.get("sensitivity") or ""),
                "visibility": str(entry.get("visibility") or ""),
            }
        )
    return {
        "schemaVersion": str(snapshot.get("schemaVersion") or ""),
        "projectionSource": str(snapshot.get("projectionSource") or ""),
        "state": str(snapshot.get("state") or ""),
        "vaultId": str(snapshot.get("vaultId") or ""),
        "authorityEpoch": int(snapshot.get("authorityEpoch") or 0),
        "checkpoint": snapshot.get("checkpoint"),
        "entryCount": int(snapshot.get("entryCount") or 0),
        "entries": summarized_entries,
    }


__all__ = [
    "OWNER_TRUTH_MEMORY_PROJECTION_SCHEMA_VERSION",
    "OWNER_TRUTH_MEMORY_PROJECTION_SOURCE",
    "OWNER_TRUTH_MEMORY_PROJECTION_VISIBILITY",
    "OwnerTruthMemoryProjectionAccessDenied",
    "OwnerTruthMemoryProjectionError",
    "OwnerTruthMemoryProjectionInput",
    "OwnerTruthMemoryProjectionResult",
    "build_ready_memory_projection",
    "build_rebuilding_memory_projection",
    "projection_summary",
]
