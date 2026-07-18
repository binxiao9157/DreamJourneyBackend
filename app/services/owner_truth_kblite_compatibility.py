"""Default-off, read-only KBLite compatibility adapter for Owner Truth.

The adapter is deliberately not a legacy KBLite writer and is not consumed by
``/context/build`` in this slice.  It reads only a current Owner Truth
MemoryVersion projection, fails closed when that projection is stale, and maps
only explicit confirmed knowledge claims to compatibility facts.  Experiences,
emotions, and unsupported shapes are never guessed into people, places, or
events.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from app.domain.owner_truth.memory_projection import (
    OwnerTruthMemoryProjectionAccessDenied,
    OwnerTruthMemoryProjectionError,
)
from app.domain.owner_truth.ontology import OWNER_TRUTH_SCHEMA_VERSION
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_memory_projection import (
    OwnerTruthMemoryProjectionService,
    OwnerTruthMemoryProjectionStore,
)


OWNER_TRUTH_KBLITE_COMPATIBILITY_SCHEMA_VERSION = "owner-truth-kblite-compatibility-v1"
OWNER_TRUTH_KBLITE_COMPATIBILITY_SOURCE = "owner-truth-memory-projection"


def _empty_graph() -> dict[str, list[dict[str, Any]]]:
    return {"people": [], "places": [], "events": [], "facts": []}


def _assert_owner_context(context: OwnerTruthCommandContext) -> None:
    if not isinstance(context, OwnerTruthCommandContext):
        raise OwnerTruthMemoryProjectionError("owner truth command context is required")
    if context.actor_subject_id != context.owner_subject_id:
        raise OwnerTruthMemoryProjectionAccessDenied(
            "only the Vault Owner may read KBLite compatibility data"
        )


def _nonblank_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _citation_from_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    citation = entry.get("citation")
    if not isinstance(citation, Mapping):
        raise OwnerTruthMemoryProjectionError("projection entry citation must be an object")
    memory_version = entry.get("memoryVersion")
    if isinstance(memory_version, bool) or not isinstance(memory_version, int) or memory_version < 1:
        raise OwnerTruthMemoryProjectionError("projection entry memoryVersion must be positive")

    result: dict[str, Any] = {"memoryVersion": memory_version}
    for field in ("memoryId", "memoryVersionId", "sourceId", "contentHash"):
        value = _nonblank_text(citation.get(field))
        if value is None:
            raise OwnerTruthMemoryProjectionError(
                f"projection entry citation {field} must be nonblank"
            )
        result[field] = value
    source_version = citation.get("sourceVersion")
    if isinstance(source_version, bool) or not isinstance(source_version, int) or source_version < 1:
        raise OwnerTruthMemoryProjectionError("projection entry citation sourceVersion must be positive")
    result["sourceVersion"] = source_version
    return result


def _filtered_entry(
    entry: Mapping[str, Any],
    *,
    citation: Mapping[str, Any],
    reason: str,
) -> dict[str, Any]:
    return {
        "citation": dict(citation),
        "memoryKind": str(entry.get("memoryKind") or ""),
        "contentSchemaVersion": str(entry.get("contentSchemaVersion") or ""),
        "reason": reason,
    }


def _compatibility_fact(
    entry: Mapping[str, Any],
    *,
    citation: Mapping[str, Any],
) -> dict[str, Any] | None:
    if str(entry.get("memoryKind") or "") != "knowledge":
        return None
    if str(entry.get("contentSchemaVersion") or "") != OWNER_TRUTH_SCHEMA_VERSION:
        return None
    content = entry.get("content")
    if not isinstance(content, Mapping):
        return None
    claim = _nonblank_text(content.get("claim"))
    if claim is None:
        return None
    return {
        # The immutable MemoryVersion identity makes this deterministic across
        # adapter reads while preventing a mutable KBLite identifier from
        # becoming a new fact authority.
        "id": f"owner_truth_fact_{citation['memoryVersionId']}",
        "statement": claim,
        "confidence": "confirmed",
        "evidenceStatus": "confirmed",
        "compatibilitySource": OWNER_TRUTH_KBLITE_COMPATIBILITY_SOURCE,
        "citation": dict(citation),
    }


class OwnerTruthKBLiteCompatibilityReadService:
    """Expose a non-authoritative compatibility graph only when explicitly enabled."""

    def __init__(self, store: OwnerTruthMemoryProjectionStore, *, enabled: bool = False) -> None:
        self._store = store
        self._enabled = bool(enabled)

    def read(self, *, context: OwnerTruthCommandContext) -> dict[str, Any]:
        _assert_owner_context(context)
        if not self._enabled:
            return self._disabled_result(context=context)

        projection = OwnerTruthMemoryProjectionService(self._store).read(context=context)
        if str(projection.get("state") or "") != "ready":
            return self._rebuilding_result(context=context, projection=projection)
        return self._ready_result(context=context, projection=projection)

    @staticmethod
    def _base_result(
        *,
        context: OwnerTruthCommandContext,
        state: str,
        authority_epoch: int | None,
        checkpoint: str | None,
    ) -> dict[str, Any]:
        return {
            "schemaVersion": OWNER_TRUTH_KBLITE_COMPATIBILITY_SCHEMA_VERSION,
            "compatibilitySource": OWNER_TRUTH_KBLITE_COMPATIBILITY_SOURCE,
            "state": state,
            "vaultId": context.vault_id,
            "ownerSubjectId": context.owner_subject_id,
            "authorityEpoch": authority_epoch,
            "projectionCheckpoint": checkpoint,
            "graph": _empty_graph(),
            "factCount": 0,
            "filteredEntries": [],
        }

    def _disabled_result(self, *, context: OwnerTruthCommandContext) -> dict[str, Any]:
        return self._base_result(
            context=context,
            state="disabled",
            authority_epoch=None,
            checkpoint=None,
        )

    def _rebuilding_result(
        self,
        *,
        context: OwnerTruthCommandContext,
        projection: Mapping[str, Any],
    ) -> dict[str, Any]:
        authority_epoch = projection.get("authorityEpoch")
        if isinstance(authority_epoch, bool) or not isinstance(authority_epoch, int):
            authority_epoch = None
        return self._base_result(
            context=context,
            state="rebuilding",
            authority_epoch=authority_epoch,
            checkpoint=None,
        )

    def _ready_result(
        self,
        *,
        context: OwnerTruthCommandContext,
        projection: Mapping[str, Any],
    ) -> dict[str, Any]:
        authority_epoch = projection.get("authorityEpoch")
        checkpoint = _nonblank_text(projection.get("checkpoint"))
        entries = projection.get("entries")
        if isinstance(authority_epoch, bool) or not isinstance(authority_epoch, int) or authority_epoch < 0:
            raise OwnerTruthMemoryProjectionError("ready projection authorityEpoch must be non-negative")
        if checkpoint is None:
            raise OwnerTruthMemoryProjectionError("ready projection checkpoint must be nonblank")
        if not isinstance(entries, list):
            raise OwnerTruthMemoryProjectionError("ready projection entries must be a list")

        result = self._base_result(
            context=context,
            state="ready",
            authority_epoch=authority_epoch,
            checkpoint=checkpoint,
        )
        facts = result["graph"]["facts"]
        filtered_entries = result["filteredEntries"]
        for entry in entries:
            if not isinstance(entry, Mapping):
                raise OwnerTruthMemoryProjectionError("projection entry must be an object")
            citation = _citation_from_entry(entry)
            fact = _compatibility_fact(entry, citation=citation)
            if fact is not None:
                facts.append(fact)
                continue

            memory_kind = str(entry.get("memoryKind") or "")
            content_schema_version = str(entry.get("contentSchemaVersion") or "")
            content = entry.get("content")
            if memory_kind != "knowledge":
                reason = "memory_kind_not_compatibility_fact"
            elif content_schema_version != OWNER_TRUTH_SCHEMA_VERSION:
                reason = "content_schema_not_supported"
            elif not isinstance(content, Mapping) or _nonblank_text(content.get("claim")) is None:
                reason = "knowledge_claim_missing"
            else:  # pragma: no cover - _compatibility_fact covers every valid branch above.
                reason = "compatibility_mapping_unavailable"
            filtered_entries.append(_filtered_entry(entry, citation=citation, reason=reason))

        result["factCount"] = len(facts)
        return result


def compatibility_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    """Return a QA-safe adapter summary without confirmed fact text."""

    graph = result.get("graph")
    facts = graph.get("facts") if isinstance(graph, Mapping) else None
    filtered_entries = result.get("filteredEntries")
    if not isinstance(facts, list) or not isinstance(filtered_entries, list):
        raise OwnerTruthMemoryProjectionError("compatibility result has an invalid graph")

    fact_summaries: list[dict[str, Any]] = []
    for fact in facts:
        if not isinstance(fact, Mapping):
            raise OwnerTruthMemoryProjectionError("compatibility fact must be an object")
        citation = fact.get("citation")
        if not isinstance(citation, Mapping):
            raise OwnerTruthMemoryProjectionError("compatibility fact citation must be an object")
        fact_summaries.append(
            {
                "id": str(fact.get("id") or ""),
                "confidence": str(fact.get("confidence") or ""),
                "evidenceStatus": str(fact.get("evidenceStatus") or ""),
                "citation": deepcopy(dict(citation)),
            }
        )
    return {
        "schemaVersion": str(result.get("schemaVersion") or ""),
        "compatibilitySource": str(result.get("compatibilitySource") or ""),
        "state": str(result.get("state") or ""),
        "vaultId": str(result.get("vaultId") or ""),
        "authorityEpoch": result.get("authorityEpoch"),
        "projectionCheckpoint": result.get("projectionCheckpoint"),
        "factCount": int(result.get("factCount") or 0),
        "facts": fact_summaries,
        "filteredEntries": deepcopy(filtered_entries),
    }


__all__ = [
    "OWNER_TRUTH_KBLITE_COMPATIBILITY_SCHEMA_VERSION",
    "OWNER_TRUTH_KBLITE_COMPATIBILITY_SOURCE",
    "OwnerTruthKBLiteCompatibilityReadService",
    "compatibility_summary",
]
