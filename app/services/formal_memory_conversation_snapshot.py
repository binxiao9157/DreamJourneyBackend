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

from app.domain.owner_truth.formal_fact_eligibility import (
    FormalFactEligibilityError,
    evaluate_formal_fact_eligibility,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_memory_projection import (
    OwnerTruthMemoryProjectionService,
)


FORMAL_MEMORY_CONVERSATION_SNAPSHOT_SCHEMA_VERSION = "formal-memory-conversation-v2"
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


def _transport_json(value: Any) -> str:
    """Return the ordinary JSON representation sent over the Live boundary.

    The budget is a transport contract, not merely a canonical-hash contract.
    Measure the less compact encoder too so adding a different JSON encoder at
    the HTTP boundary cannot turn a "fits" snapshot into an oversized request.
    """

    return json.dumps(value, ensure_ascii=False)


def _hash(value: Any) -> str:
    return "sha256:" + sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _dimension_for_entry(entry: Mapping[str, Any]) -> str:
    content = entry.get("content")
    if isinstance(content, Mapping):
        dimensions = content.get("dimensions")
        if isinstance(dimensions, list):
            for dimension in dimensions:
                normalized = _text(dimension, maximum=80)
                if normalized:
                    return normalized
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
    statement = _text(content.get("statement"))
    if statement:
        return statement
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


def _typed_fact_context(entry: Mapping[str, Any]) -> dict[str, Any]:
    """Expose answer-critical qualifiers without sending private Source text."""

    content = entry.get("content")
    if not isinstance(content, Mapping):
        return {
            "factType": "other",
            "predicate": "states",
            "object": None,
            "qualifiers": {
                "polarity": "unknown",
                "currentApplicability": "unknown",
                "validTime": {"precision": "unknown"},
            },
            "provenanceMode": "unknown",
        }
    object_value = content.get("object")
    object_context = None
    if isinstance(object_value, Mapping):
        object_context = {
            "label": _text(object_value.get("label"), maximum=256) or None,
            "category": _text(object_value.get("category"), maximum=80) or None,
        }
    qualifiers = content.get("qualifiers")
    qualifiers = qualifiers if isinstance(qualifiers, Mapping) else {}
    time_value = qualifiers.get("validTime")
    time_value = time_value if isinstance(time_value, Mapping) else {}
    place_value = qualifiers.get("place")
    place_value = place_value if isinstance(place_value, Mapping) else {}
    provenance = content.get("provenance")
    provenance = provenance if isinstance(provenance, Mapping) else {}
    return {
        "factType": _text(content.get("factType"), maximum=80) or "other",
        "predicate": _text(content.get("predicate"), maximum=128) or "states",
        "object": object_context,
        "qualifiers": {
            "polarity": _text(qualifiers.get("polarity"), maximum=32) or "unknown",
            "strengthExpression": _text(
                qualifiers.get("strengthExpression"), maximum=256
            )
            or None,
            "currentApplicability": _text(
                qualifiers.get("currentApplicability"), maximum=32
            )
            or "unknown",
            "validTime": {
                "start": _text(time_value.get("start"), maximum=64) or None,
                "end": _text(time_value.get("end"), maximum=64) or None,
                "precision": _text(time_value.get("precision"), maximum=32)
                or "unknown",
                "expression": _text(time_value.get("expression"), maximum=256)
                or None,
            },
            "place": {
                "label": _text(place_value.get("label"), maximum=256) or None,
                "category": _text(place_value.get("category"), maximum=80) or None,
            }
            if place_value
            else None,
            "scenario": _text(qualifiers.get("scenario"), maximum=256) or None,
        },
        "provenanceMode": _text(provenance.get("mode"), maximum=32) or "unknown",
    }


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


def _coverage_order(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pick one fact per dimension before filling remaining stable order."""

    first_by_dimension: dict[str, dict[str, Any]] = {}
    for fact in facts:
        first_by_dimension.setdefault(str(fact["dimension"]), fact)
    selected_ids = {id(fact) for fact in first_by_dimension.values()}
    return [first_by_dimension[key] for key in sorted(first_by_dimension)] + [
        fact for fact in facts if id(fact) not in selected_ids
    ]


def _numbered_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, fact in enumerate(facts, start=1):
        copied = dict(fact)
        copied["ref"] = f"FM-{index:03d}"
        result.append(copied)
    return result


def _compact_dimension_summaries(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Describe coverage without duplicating every private statement twice."""

    grouped: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        grouped.setdefault(str(fact["dimension"]), []).append(fact)
    return [
        {
            "dimension": dimension,
            "includedFactCount": len(values),
            "supportRefs": [str(value["ref"]) for value in values],
        }
        for dimension, values in sorted(grouped.items())
    ]


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
        # A cached projection may still look structurally ready after its
        # authority is revoked.  Live context is private derived data, so it
        # must be fenced by both lifecycle state and the current rights state.
        if (
            str(projection.get("state") or "") != "ready"
            or str(projection.get("rightsState") or "") != "active"
        ):
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
        try:
            eligibility = evaluate_formal_fact_eligibility(projection)
        except FormalFactEligibilityError as error:
            raise FormalMemoryConversationSnapshotError(
                "formalMemorySnapshotUnavailable"
            ) from error
        statuses = _group_status_by_version(projection)
        memory_revision = projection.get("memoryRevision")
        if not isinstance(memory_revision, int) or isinstance(memory_revision, bool) or memory_revision < 0:
            raise FormalMemoryConversationSnapshotError(
                "formalMemorySnapshotUnavailable"
            )
        candidates: list[dict[str, Any]] = []
        for entry in eligibility.eligible_entries:
            if not isinstance(entry, Mapping):
                raise FormalMemoryConversationSnapshotError(
                    "formalMemorySnapshotUnavailable"
                )
            statement = _text(_statement_for_entry(entry), maximum=420)
            version_id = _text(entry.get("memoryVersionId"), maximum=160)
            if not statement or not version_id:
                raise FormalMemoryConversationSnapshotError(
                    "formalMemorySnapshotUnavailable"
                )
            dimension = _dimension_for_entry(entry)
            status = statuses.get(version_id, "ready")
            fact = {
                "dimension": dimension,
                "statement": statement,
                "sourceMemoryVersionIds": [version_id],
                "status": status,
                **_typed_fact_context(entry),
            }
            candidates.append(fact)
        generated_at = datetime.now(timezone.utc).isoformat()
        total_eligible = len(candidates)

        def build_body(selected: list[dict[str, Any]]) -> dict[str, Any]:
            numbered = _numbered_facts(selected)
            omitted = total_eligible - len(numbered)
            return {
                "schemaVersion": FORMAL_MEMORY_CONVERSATION_SNAPSHOT_SCHEMA_VERSION,
                "subjectId": context.owner_subject_id,
                "personaScope": _text(persona_scope, maximum=32) or "personal",
                "projectionCheckpoint": checkpoint,
                "authorityEpoch": int(projection.get("authorityEpoch") or 0),
                "memoryRevision": memory_revision,
                "generatedAt": generated_at,
                "persona": {
                    "displayName": _text(display_name, maximum=120),
                    "responsePerspective": "firstPerson",
                    "aiDisclosureRequired": True,
                },
                "coreFacts": numbered,
                "dimensionSummaries": _compact_dimension_summaries(numbered),
                "coverage": {
                    "eligibleFactCount": total_eligible,
                    "includedFactCount": len(numbered),
                    "omittedFactCount": omitted,
                    "truncated": omitted > 0,
                    "selectionPolicy": "dimensionFirstThenStableOrder",
                    "reason": "budgetedFormalFactSnapshot" if omitted else "allEligibleFactsIncluded",
                },
                "factEligibility": eligibility.public_summary(),
            }

        ordered_candidates = _coverage_order(candidates)

        def fits(candidate_count: int) -> bool:
            trial = build_body(ordered_candidates[:candidate_count])
            # Reserve the deterministic context hash before deciding whether a
            # fact fits; otherwise a just-fitting body can overflow after the
            # hash is appended.
            trial["contextHash"] = "sha256:" + ("0" * 64)
            return len(_transport_json(trial)) <= self._max_chars

        # Snapshot size grows monotonically for this stable prefix order. A
        # binary search avoids rebuilding and serializing the whole growing
        # payload once per fact under concurrent Live starts.
        lower = 0
        upper = len(ordered_candidates)
        while lower < upper:
            midpoint = (lower + upper + 1) // 2
            if fits(midpoint):
                lower = midpoint
            else:
                upper = midpoint - 1
        selected = ordered_candidates[:lower]
        body = build_body(selected)
        hash_material = deepcopy(body)
        hash_material.pop("generatedAt", None)
        body["contextHash"] = _hash(hash_material)
        serialized = _transport_json(body)
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
