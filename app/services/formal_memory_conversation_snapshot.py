"""Build the bounded, read-only memory context used by a Live session.

The snapshot is a transport view of the current formal-memory projection. It
is deliberately not persisted as a second authority and never includes
Source, Candidate, review, or revoked-data payloads.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Mapping

from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_memory_projection import (
    OwnerTruthMemoryProjectionService,
)


FORMAL_MEMORY_CONVERSATION_SNAPSHOT_SCHEMA_VERSION = "formal-memory-conversation-v1"
FORMAL_MEMORY_CONVERSATION_SNAPSHOT_MAX_CHARS = 32_768


class FormalMemoryConversationSnapshotError(ValueError):
    """The current formal-memory projection cannot be used for Live."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _text(value: Any, *, maximum: int = 1_200) -> str:
    normalized = " ".join(str(value or "").split()).strip()
    return normalized[:maximum]


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _hash(value: Any) -> str:
    return "sha256:" + sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _dimension_for_entry(entry: Mapping[str, Any]) -> str:
    content = entry.get("content")
    semantic = content.get("semantic") if isinstance(content, Mapping) else None
    if isinstance(semantic, Mapping):
        facets = semantic.get("facets")
        if isinstance(facets, list):
            for dimension in (
                "identity",
                "relationship",
                "lifeEvent",
                "knowledge",
                "emotion",
                "personality",
                "value",
                "habit",
                "goal",
                "reflection",
            ):
                if dimension in facets:
                    return dimension
        primary_kind = _text(semantic.get("primaryKind"), maximum=80)
        if primary_kind:
            return primary_kind
    return _text(entry.get("memoryKind"), maximum=80) or "other"


def _statement_for_entry(entry: Mapping[str, Any]) -> str:
    content = entry.get("content")
    if not isinstance(content, Mapping):
        return ""
    semantic = content.get("semantic")
    if isinstance(semantic, Mapping):
        narrative = _text(semantic.get("narrative"))
        if narrative:
            return narrative
    kind = _text(entry.get("memoryKind"), maximum=80)
    fields = {
        "experience": ("event", "summary"),
        "knowledge": ("statement", "claim"),
        "emotion": ("expression", "emotion", "label"),
    }.get(kind, ())
    for field in fields:
        statement = _text(content.get(field))
        if statement:
            return statement
    return ""


def _group_status_by_version(projection: Mapping[str, Any]) -> dict[str, str]:
    model = projection.get("personMemoryModel")
    consolidation = model.get("semanticConsolidation") if isinstance(model, Mapping) else None
    groups = consolidation.get("groups") if isinstance(consolidation, Mapping) else None
    if not isinstance(groups, list):
        return {}
    result: dict[str, str] = {}
    for group in groups:
        if not isinstance(group, Mapping):
            continue
        status = _text(group.get("status"), maximum=32) or "ready"
        version_ids = group.get("supportingMemoryVersionIds")
        if isinstance(version_ids, list):
            for version_id in version_ids:
                normalized = _text(version_id, maximum=160)
                if normalized:
                    result[normalized] = status
    return result


class FormalMemoryConversationSnapshotService:
    """Materialize one deterministic snapshot from current formal memory."""

    def __init__(
        self,
        store: Any,
        *,
        max_chars: int = FORMAL_MEMORY_CONVERSATION_SNAPSHOT_MAX_CHARS,
    ) -> None:
        self._store = store
        self._max_chars = max(1_024, int(max_chars))

    def build(
        self,
        *,
        context: OwnerTruthCommandContext,
        persona_scope: str = "personal",
        display_name: str = "",
    ) -> dict[str, Any]:
        projection = OwnerTruthMemoryProjectionService(self._store).read(context=context)
        if str(projection.get("state") or "") != "ready":
            raise FormalMemoryConversationSnapshotError(
                "formalMemorySnapshotUnavailable"
            )
        checkpoint = _text(projection.get("checkpoint"), maximum=160)
        if not checkpoint:
            raise FormalMemoryConversationSnapshotError(
                "formalMemorySnapshotUnavailable"
            )

        entries = projection.get("entries")
        if not isinstance(entries, list):
            raise FormalMemoryConversationSnapshotError(
                "formalMemorySnapshotUnavailable"
            )
        statuses = _group_status_by_version(projection)
        facts: list[dict[str, Any]] = []
        summaries: dict[str, list[str]] = {}
        for index, entry in enumerate(entries, start=1):
            if not isinstance(entry, Mapping):
                raise FormalMemoryConversationSnapshotError(
                    "formalMemorySnapshotUnavailable"
                )
            statement = _statement_for_entry(entry)
            version_id = _text(entry.get("memoryVersionId"), maximum=160)
            if not statement or not version_id:
                raise FormalMemoryConversationSnapshotError(
                    "formalMemorySnapshotUnavailable"
                )
            dimension = _dimension_for_entry(entry)
            ref = f"FM-{index:03d}"
            status = statuses.get(version_id, "ready")
            fact = {
                "ref": ref,
                "dimension": dimension,
                "statement": statement,
                "sourceMemoryVersionIds": [version_id],
                "status": status,
            }
            facts.append(fact)
            summaries.setdefault(dimension, []).append(statement)

        dimension_summaries = [
            {
                "dimension": dimension,
                "text": "；".join(values),
                "supportRefs": [
                    fact["ref"] for fact in facts if fact["dimension"] == dimension
                ],
            }
            for dimension, values in sorted(summaries.items())
        ]
        generated_at = datetime.now(timezone.utc).isoformat()
        body = {
            "schemaVersion": FORMAL_MEMORY_CONVERSATION_SNAPSHOT_SCHEMA_VERSION,
            "subjectId": context.owner_subject_id,
            "personaScope": _text(persona_scope, maximum=32) or "personal",
            "projectionCheckpoint": checkpoint,
            "authorityEpoch": int(projection.get("authorityEpoch") or 0),
            "generatedAt": generated_at,
            "persona": {
                "displayName": _text(display_name, maximum=120),
                "responsePerspective": "firstPerson",
                "aiDisclosureRequired": True,
            },
            "coreFacts": facts,
            "dimensionSummaries": dimension_summaries,
        }
        hash_material = deepcopy(body)
        hash_material.pop("generatedAt", None)
        body["contextHash"] = _hash(hash_material)
        serialized = _canonical_json(body)
        if len(serialized) > self._max_chars:
            raise FormalMemoryConversationSnapshotError(
                "formalMemorySnapshotTooLarge"
            )
        return body


__all__ = [
    "FORMAL_MEMORY_CONVERSATION_SNAPSHOT_MAX_CHARS",
    "FORMAL_MEMORY_CONVERSATION_SNAPSHOT_SCHEMA_VERSION",
    "FormalMemoryConversationSnapshotError",
    "FormalMemoryConversationSnapshotService",
]
