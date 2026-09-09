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

from .contracts import MemoryKind, OwnerTruthContractError, require_nonblank, require_uuid
from .ontology import validate_memory_payload
from .projection_rights import (
    OwnerTruthProjectionRightsSnapshot,
    implicit_projection_rights_snapshot,
)
from .person_memory_model import build_person_memory_model


OWNER_TRUTH_MEMORY_PROJECTION_SCHEMA_VERSION = "owner-truth-memory-projection-v2"
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
        try:
            memory_kind = MemoryKind(self.memory_kind)
        except ValueError as exc:
            raise OwnerTruthMemoryProjectionError("memory_kind is unsupported") from exc
        validation = validate_memory_payload(
            kind=memory_kind,
            payload=self.content,
            schema_version=self.content_schema_version,
        )
        if not validation.accepted:
            raise OwnerTruthMemoryProjectionError(
                f"projection content is not admitted: {validation.code}"
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
    rights_snapshot: OwnerTruthProjectionRightsSnapshot | None = None,
    memory_revision: int = 0,
) -> dict[str, Any]:
    """Create a stable checkpoint from a complete set of current inputs."""

    normalized_vault_id, normalized_owner_id, rights, entries = (
        _validated_projection_entries(
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            authority_epoch=authority_epoch,
            inputs=inputs,
            rights_snapshot=rights_snapshot,
            memory_revision=memory_revision,
        )
    )
    return _ready_projection_from_entries(
        vault_id=normalized_vault_id,
        owner_subject_id=normalized_owner_id,
        authority_epoch=authority_epoch,
        memory_revision=memory_revision,
        rights=rights,
        entries=entries,
        person_memory_model=build_person_memory_model(entries),
    )


def hydrate_ready_memory_projection(
    *,
    vault_id: str,
    owner_subject_id: str,
    authority_epoch: int,
    inputs: Iterable[OwnerTruthMemoryProjectionInput],
    person_memory_model: Mapping[str, Any],
    rights_snapshot: OwnerTruthProjectionRightsSnapshot | None = None,
    memory_revision: int = 0,
    expected_source_hash: str | None = None,
    expected_checkpoint: str | None = None,
) -> dict[str, Any]:
    """Hydrate a persisted projection without rebuilding semantic derivatives.

    Normal reads validate the current revision and re-hash the persisted facts
    plus the persisted person model. Expensive semantic consolidation remains
    a rebuild/Worker responsibility rather than running once per query.
    """

    normalized_vault_id, normalized_owner_id, rights, entries = (
        _validated_projection_entries(
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            authority_epoch=authority_epoch,
            inputs=inputs,
            rights_snapshot=rights_snapshot,
            memory_revision=memory_revision,
        )
    )
    persisted_model = _copy_object(
        person_memory_model,
        field="persisted person_memory_model",
    )
    persisted_memory_count = persisted_model.get("memoryCount")
    if (
        not isinstance(persisted_memory_count, int)
        or isinstance(persisted_memory_count, bool)
        or persisted_memory_count != len(entries)
    ):
        raise OwnerTruthMemoryProjectionError(
            "persisted person-memory model count does not match projection entries"
        )
    snapshot = _ready_projection_from_entries(
        vault_id=normalized_vault_id,
        owner_subject_id=normalized_owner_id,
        authority_epoch=authority_epoch,
        memory_revision=memory_revision,
        rights=rights,
        entries=entries,
        person_memory_model=persisted_model,
    )
    if expected_source_hash is not None and snapshot["sourceHash"] != expected_source_hash:
        raise OwnerTruthMemoryProjectionError(
            "persisted memory projection source hash does not match"
        )
    if expected_checkpoint is not None and snapshot["checkpoint"] != expected_checkpoint:
        raise OwnerTruthMemoryProjectionError(
            "persisted memory projection checkpoint does not match"
        )
    return snapshot


def restore_persisted_ready_memory_projection(
    *,
    vault_id: str,
    owner_subject_id: str,
    authority_epoch: int,
    entries: Iterable[Mapping[str, Any]],
    person_memory_model: Mapping[str, Any],
    source_hash: str,
    checkpoint: str,
    rights_snapshot: OwnerTruthProjectionRightsSnapshot | None = None,
    memory_revision: int = 0,
) -> dict[str, Any]:
    """Restore a transactionally fenced persisted projection for normal reads.

    PostgreSQL validates every derived entry against its current MemoryVersion
    when it is written. The 0119 revision fence locks the ready checkpoint,
    current formal-memory revision and rights-trigger invalidation for the read
    transaction. Re-running ontology validation and semantic consolidation on
    every request would duplicate Worker work and collapse under concurrent
    Live/search traffic, so this path performs bounded structural checks only.
    """

    normalized_vault_id = require_nonblank(vault_id, field="vault_id")
    normalized_owner_id = require_nonblank(owner_subject_id, field="owner_subject_id")
    if authority_epoch < 0:
        raise OwnerTruthMemoryProjectionError("authority_epoch must not be negative")
    if not isinstance(memory_revision, int) or isinstance(memory_revision, bool) or memory_revision < 0:
        raise OwnerTruthMemoryProjectionError("memory_revision must be a non-negative integer")
    rights = rights_snapshot or implicit_projection_rights_snapshot(
        vault_id=normalized_vault_id,
        owner_subject_id=normalized_owner_id,
        authority_epoch=authority_epoch,
    )
    if (
        rights.vault_id != normalized_vault_id
        or rights.owner_subject_id != normalized_owner_id
        or rights.authority_epoch != authority_epoch
        or not rights.projection_allowed
    ):
        raise OwnerTruthMemoryProjectionError("persisted projection rights are invalid")
    normalized_source_hash = require_nonblank(source_hash, field="source_hash")
    normalized_checkpoint = require_nonblank(checkpoint, field="checkpoint")

    restored_entries: list[dict[str, Any]] = []
    seen_memory_ids: set[str] = set()
    seen_version_ids: set[str] = set()
    for raw_entry in entries:
        if not isinstance(raw_entry, Mapping):
            raise OwnerTruthMemoryProjectionError("persisted projection entry must be an object")
        entry = dict(raw_entry)
        memory_id = require_uuid(str(entry.get("memoryId") or ""), field="memory_id")
        version_id = require_uuid(
            str(entry.get("memoryVersionId") or ""),
            field="memory_version_id",
        )
        if memory_id in seen_memory_ids or version_id in seen_version_ids:
            raise OwnerTruthMemoryProjectionError(
                "persisted projection contains duplicate current memories"
            )
        if not isinstance(entry.get("content"), Mapping):
            raise OwnerTruthMemoryProjectionError("persisted projection content must be an object")
        if not isinstance(entry.get("evidenceRefs"), list):
            raise OwnerTruthMemoryProjectionError(
                "persisted projection evidenceRefs must be a list"
            )
        citation = entry.get("citation")
        if (
            not isinstance(citation, Mapping)
            or str(citation.get("memoryId") or "") != memory_id
            or str(citation.get("memoryVersionId") or "") != version_id
        ):
            raise OwnerTruthMemoryProjectionError("persisted projection citation is invalid")
        seen_memory_ids.add(memory_id)
        seen_version_ids.add(version_id)
        restored_entries.append(entry)
    restored_entries.sort(
        key=lambda item: (
            str(item["memoryId"]),
            int(item.get("memoryVersion") or 0),
            str(item["memoryVersionId"]),
        )
    )

    if not isinstance(person_memory_model, Mapping):
        raise OwnerTruthMemoryProjectionError(
            "persisted person-memory model must be an object"
        )
    persisted_model = dict(person_memory_model)
    persisted_memory_count = persisted_model.get("memoryCount")
    if (
        not isinstance(persisted_memory_count, int)
        or isinstance(persisted_memory_count, bool)
        or persisted_memory_count != len(restored_entries)
        or not str(persisted_model.get("modelVersion") or "").strip()
    ):
        raise OwnerTruthMemoryProjectionError(
            "persisted person-memory model does not match projection entries"
        )
    return {
        "schemaVersion": OWNER_TRUTH_MEMORY_PROJECTION_SCHEMA_VERSION,
        "projectionSource": OWNER_TRUTH_MEMORY_PROJECTION_SOURCE,
        "state": "ready",
        "vaultId": normalized_vault_id,
        "ownerSubjectId": normalized_owner_id,
        "authorityEpoch": authority_epoch,
        "memoryRevision": memory_revision,
        **rights.projection_fence(),
        "checkpoint": normalized_checkpoint,
        "sourceHash": normalized_source_hash,
        "entryCount": len(restored_entries),
        "entries": restored_entries,
        "personMemoryModel": persisted_model,
    }


def _validated_projection_entries(
    *,
    vault_id: str,
    owner_subject_id: str,
    authority_epoch: int,
    inputs: Iterable[OwnerTruthMemoryProjectionInput],
    rights_snapshot: OwnerTruthProjectionRightsSnapshot | None,
    memory_revision: int,
) -> tuple[str, str, OwnerTruthProjectionRightsSnapshot, list[dict[str, Any]]]:
    """Validate authority and materialize the stable fact-entry list once."""

    normalized_vault_id = require_nonblank(vault_id, field="vault_id")
    normalized_owner_id = require_nonblank(owner_subject_id, field="owner_subject_id")
    if authority_epoch < 0:
        raise OwnerTruthMemoryProjectionError("authority_epoch must not be negative")
    if not isinstance(memory_revision, int) or isinstance(memory_revision, bool) or memory_revision < 0:
        raise OwnerTruthMemoryProjectionError("memory_revision must be a non-negative integer")
    rights = rights_snapshot or implicit_projection_rights_snapshot(
        vault_id=normalized_vault_id,
        owner_subject_id=normalized_owner_id,
        authority_epoch=authority_epoch,
    )
    if (
        rights.vault_id != normalized_vault_id
        or rights.owner_subject_id != normalized_owner_id
        or rights.authority_epoch != authority_epoch
    ):
        raise OwnerTruthMemoryProjectionError("projection rights snapshot crosses authority boundary")
    if not rights.projection_allowed:
        raise OwnerTruthMemoryProjectionError("projection rights do not permit a ready checkpoint")

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
    return normalized_vault_id, normalized_owner_id, rights, entries


def _ready_projection_from_entries(
    *,
    vault_id: str,
    owner_subject_id: str,
    authority_epoch: int,
    memory_revision: int,
    rights: OwnerTruthProjectionRightsSnapshot,
    entries: list[dict[str, Any]],
    person_memory_model: Mapping[str, Any],
) -> dict[str, Any]:
    """Hash one ready projection from already validated persisted material."""

    copied_model = _copy_object(person_memory_model, field="person_memory_model")
    source_hash = _digest(
        {
            "schemaVersion": OWNER_TRUTH_MEMORY_PROJECTION_SCHEMA_VERSION,
            "vaultId": vault_id,
            "ownerSubjectId": owner_subject_id,
            "authorityEpoch": authority_epoch,
            "memoryRevision": memory_revision,
            "rights": rights.projection_fence(),
            "entries": entries,
        }
    )
    projection_hash = _digest(
        {
            "projectionSource": OWNER_TRUTH_MEMORY_PROJECTION_SOURCE,
            "sourceHash": source_hash,
            "entries": entries,
            "personMemoryModel": copied_model,
        }
    )
    return {
        "schemaVersion": OWNER_TRUTH_MEMORY_PROJECTION_SCHEMA_VERSION,
        "projectionSource": OWNER_TRUTH_MEMORY_PROJECTION_SOURCE,
        "state": "ready",
        "vaultId": vault_id,
        "ownerSubjectId": owner_subject_id,
        "authorityEpoch": authority_epoch,
        "memoryRevision": memory_revision,
        **rights.projection_fence(),
        "checkpoint": projection_hash,
        "sourceHash": source_hash,
        "entryCount": len(entries),
        "entries": entries,
        "personMemoryModel": copied_model,
    }


def build_rebuilding_memory_projection(
    *,
    vault_id: str,
    owner_subject_id: str,
    authority_epoch: int,
    rights_snapshot: OwnerTruthProjectionRightsSnapshot | None = None,
    rebuild_reason: str = "projectionUnavailable",
) -> dict[str, Any]:
    """Fail closed when a compatible current checkpoint is unavailable."""

    normalized_vault_id = require_nonblank(vault_id, field="vault_id")
    normalized_owner_id = require_nonblank(owner_subject_id, field="owner_subject_id")
    if authority_epoch < 0:
        raise OwnerTruthMemoryProjectionError("authority_epoch must not be negative")
    rights = rights_snapshot or implicit_projection_rights_snapshot(
        vault_id=normalized_vault_id,
        owner_subject_id=normalized_owner_id,
        authority_epoch=authority_epoch,
    )
    if (
        rights.vault_id != normalized_vault_id
        or rights.owner_subject_id != normalized_owner_id
        or rights.authority_epoch != authority_epoch
    ):
        raise OwnerTruthMemoryProjectionError("projection rights snapshot crosses authority boundary")
    normalized_reason = require_nonblank(rebuild_reason, field="rebuild_reason")
    return {
        "schemaVersion": OWNER_TRUTH_MEMORY_PROJECTION_SCHEMA_VERSION,
        "projectionSource": OWNER_TRUTH_MEMORY_PROJECTION_SOURCE,
        "state": "rebuilding",
        "vaultId": normalized_vault_id,
        "ownerSubjectId": normalized_owner_id,
        "authorityEpoch": authority_epoch,
        **rights.projection_fence(),
        "rebuildReason": normalized_reason,
        "checkpoint": None,
        "entryCount": 0,
        "entries": [],
        "personMemoryModel": None,
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
        "rightsRevision": int(snapshot.get("rightsRevision") or 0),
        "rightsState": str(snapshot.get("rightsState") or ""),
        "rightsSnapshotHash": snapshot.get("rightsSnapshotHash"),
        "rebuildReason": snapshot.get("rebuildReason"),
        "checkpoint": snapshot.get("checkpoint"),
        "entryCount": int(snapshot.get("entryCount") or 0),
        "personMemoryModelVersion": (
            snapshot.get("personMemoryModel", {}).get("modelVersion")
            if isinstance(snapshot.get("personMemoryModel"), Mapping)
            else None
        ),
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
    "hydrate_ready_memory_projection",
    "restore_persisted_ready_memory_projection",
    "build_rebuilding_memory_projection",
    "projection_summary",
]
