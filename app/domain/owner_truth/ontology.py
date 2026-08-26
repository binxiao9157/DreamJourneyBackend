"""Owner Truth memory ontology and schema quarantine policy.

V2 extends the confirmed memory payload with reviewable facets. Facets are
descriptive evidence only: authorization code must continue to use the Vault,
principal and Grant contracts rather than relationship-shaped memory data.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping, Optional

from .contracts import MemoryKind


OWNER_TRUTH_SCHEMA_VERSION = "owner-truth-v1"
OWNER_TRUTH_SCHEMA_VERSION_V2 = "owner-truth-v2"
OWNER_TRUTH_SCHEMA_VERSION_V3 = "owner-truth-v3"
OWNER_TRUTH_SCHEMA_VERSION_V4 = "owner-truth-v4"
OWNER_TRUTH_CURRENT_SCHEMA_VERSION = OWNER_TRUTH_SCHEMA_VERSION_V4
OWNER_TRUTH_BASE_FACET_NAMES = (
    "people",
    "time",
    "places",
    "relationships",
    "emotions",
    "values",
    "personality",
)
OWNER_TRUTH_EXTENDED_FACET_NAMES = (
    "habits",
    "goals",
    "identity",
    "reflections",
)
OWNER_TRUTH_FACET_NAMES = (
    *OWNER_TRUTH_BASE_FACET_NAMES,
    *OWNER_TRUTH_EXTENDED_FACET_NAMES,
)
OWNER_TRUTH_SEMANTIC_FACETS = (
    "lifeEvent",
    "knowledge",
    "emotion",
    "relationship",
    "value",
    "personality",
    "habit",
    "goal",
    "identity",
    "reflection",
)
OWNER_TRUTH_FACET_EVIDENCE_MODES = ("ownerStated", "inferred")
_MAX_FACET_VALUES_PER_KIND = 32
_MAX_FACET_VALUE_CHARACTERS = 256


@dataclass(frozen=True)
class MemoryOntologyDefinition:
    kind: MemoryKind
    required_fields: tuple[str, ...]


@dataclass(frozen=True)
class OntologyValidation:
    accepted: bool
    quarantined: bool
    code: str
    detail: Optional[str] = None


MEMORY_ONTOLOGY_V1: Mapping[MemoryKind, MemoryOntologyDefinition] = {
    MemoryKind.EXPERIENCE: MemoryOntologyDefinition(
        kind=MemoryKind.EXPERIENCE,
        required_fields=("summary",),
    ),
    MemoryKind.KNOWLEDGE: MemoryOntologyDefinition(
        kind=MemoryKind.KNOWLEDGE,
        required_fields=("claim",),
    ),
    MemoryKind.EMOTION: MemoryOntologyDefinition(
        kind=MemoryKind.EMOTION,
        required_fields=("label",),
    ),
}


def empty_memory_facets(*, confidence: float = 0.0) -> dict[str, Any]:
    """Return an explicit, value-free V2 facet set for a new Candidate.

    This is used only by new V2 writers. Historical V1 payloads are never
    backfilled with empty arrays, which keeps "not extracted" distinguishable
    from a newly reviewed V2 Candidate with no facet values.
    """

    return {
        **{name: [] for name in OWNER_TRUTH_FACET_NAMES},
        "confidence": confidence,
    }


def _confidence(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
        return None
    return normalized


def validate_memory_facets(
    value: Any,
    *,
    facet_names: tuple[str, ...] = OWNER_TRUTH_BASE_FACET_NAMES,
) -> OntologyValidation:
    if not isinstance(value, Mapping):
        return OntologyValidation(False, False, "invalidFacets", "facets")
    if _confidence(value.get("confidence")) is None:
        return OntologyValidation(False, False, "invalidFacetConfidence", "confidence")
    for facet_name in facet_names:
        entries = value.get(facet_name)
        if not isinstance(entries, list) or len(entries) > _MAX_FACET_VALUES_PER_KIND:
            return OntologyValidation(False, False, "invalidFacetList", facet_name)
        for index, entry in enumerate(entries):
            if not isinstance(entry, Mapping):
                return OntologyValidation(
                    False,
                    False,
                    "invalidFacetEntry",
                    f"{facet_name}[{index}]",
                )
            facet_value = entry.get("value")
            if (
                not isinstance(facet_value, str)
                or not facet_value.strip()
                or len(facet_value.strip()) > _MAX_FACET_VALUE_CHARACTERS
            ):
                return OntologyValidation(
                    False,
                    False,
                    "invalidFacetValue",
                    f"{facet_name}[{index}]",
                )
            if entry.get("evidenceMode") not in OWNER_TRUTH_FACET_EVIDENCE_MODES:
                return OntologyValidation(
                    False,
                    False,
                    "invalidFacetEvidenceMode",
                    f"{facet_name}[{index}]",
                )
            if _confidence(entry.get("confidence")) is None:
                return OntologyValidation(
                    False,
                    False,
                    "invalidFacetConfidence",
                    f"{facet_name}[{index}]",
                )
    return OntologyValidation(True, False, "accepted")


def flatten_memory_facets(value: Any) -> tuple[str, ...]:
    """Flatten known facet values for a private derived index.

    Only the allowlisted ``value`` field crosses this boundary. Provider
    metadata, confidence, relationship IDs and authority-looking extension
    fields remain payload data and cannot become identity or Grant inputs.
    """

    validation = validate_memory_facets(value)
    if not validation.accepted:
        return ()
    terms = {
        f"{facet_name}:{str(entry['value']).strip()}"
        for facet_name in OWNER_TRUTH_FACET_NAMES
        for entry in value.get(facet_name, [])
    }
    return tuple(sorted(terms))


def validate_memory_payload(
    *,
    kind: MemoryKind,
    payload: Mapping[str, Any],
    schema_version: str,
) -> OntologyValidation:
    """Validate known payloads and quarantine all unknown schema versions.

    Quarantine is deliberate: a future writer must not silently coerce a
    payload produced under an unknown ontology into an authoritative memory.
    """

    normalized_schema = str(schema_version or "").strip()
    if normalized_schema not in {
        OWNER_TRUTH_SCHEMA_VERSION,
        OWNER_TRUTH_SCHEMA_VERSION_V2,
        OWNER_TRUTH_SCHEMA_VERSION_V3,
        OWNER_TRUTH_SCHEMA_VERSION_V4,
    }:
        return OntologyValidation(
            accepted=False,
            quarantined=True,
            code="unknownSchemaVersion",
        )
    if normalized_schema == OWNER_TRUTH_SCHEMA_VERSION_V4:
        return _validate_v4_memory_payload(kind=kind, payload=payload)
    if normalized_schema == OWNER_TRUTH_SCHEMA_VERSION_V3:
        return _validate_v3_memory_payload(kind=kind, payload=payload)

    definition = MEMORY_ONTOLOGY_V1[kind]
    missing = [
        field
        for field in definition.required_fields
        if not str(payload.get(field) or "").strip()
    ]
    if missing:
        return OntologyValidation(
            accepted=False,
            quarantined=False,
            code="missingRequiredField",
            detail=",".join(missing),
        )
    if normalized_schema == OWNER_TRUTH_SCHEMA_VERSION_V2:
        return validate_memory_facets(payload.get("facets"))
    return OntologyValidation(accepted=True, quarantined=False, code="accepted")


def _nonblank_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(_nonblank_string(item) for item in value)


def _validate_v3_memory_payload(
    *,
    kind: MemoryKind,
    payload: Mapping[str, Any],
) -> OntologyValidation:
    """Validate the Stage 1 typed memory contract used by new organizers."""

    if kind is MemoryKind.EXPERIENCE:
        if not _nonblank_string(payload.get("event")):
            return OntologyValidation(False, False, "missingRequiredField", "event")
        time_value = payload.get("time")
        if not isinstance(time_value, Mapping):
            return OntologyValidation(False, False, "missingRequiredField", "time")
        precision = str(time_value.get("precision") or "").strip()
        if precision not in {"exact", "day", "month", "year", "approximate", "unknown"}:
            return OntologyValidation(False, False, "invalidTimePrecision", "time.precision")
        for field in ("start", "end"):
            value = time_value.get(field)
            if value is not None and not _nonblank_string(value):
                return OntologyValidation(False, False, "invalidTimeValue", f"time.{field}")
        for field in ("participants", "actions"):
            if field in payload and not _string_list(payload.get(field)):
                return OntologyValidation(False, False, "invalidStringList", field)
    elif kind is MemoryKind.KNOWLEDGE:
        if not _nonblank_string(payload.get("statement")):
            return OntologyValidation(False, False, "missingRequiredField", "statement")
        if not _nonblank_string(payload.get("knowledgeType")):
            return OntologyValidation(False, False, "missingRequiredField", "knowledgeType")
        if not _string_list(payload.get("domains")):
            return OntologyValidation(False, False, "invalidStringList", "domains")
        if "exceptions" in payload and not _string_list(payload.get("exceptions")):
            return OntologyValidation(False, False, "invalidStringList", "exceptions")
    elif kind is MemoryKind.EMOTION:
        if not _nonblank_string(payload.get("emotion")):
            return OntologyValidation(False, False, "missingRequiredField", "emotion")
        if not _nonblank_string(payload.get("expression")):
            return OntologyValidation(False, False, "missingRequiredField", "expression")
        intensity = payload.get("intensity")
        if intensity is not None and _confidence(intensity) is None:
            return OntologyValidation(False, False, "invalidIntensity", "intensity")

    return validate_memory_facets(payload.get("facets"))


def _facet_entries(value: Any, facet_name: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, Mapping):
        return []
    entries = value.get(facet_name)
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, Mapping)]


def _primary_text(*, kind: MemoryKind, payload: Mapping[str, Any]) -> str:
    keys = {
        MemoryKind.EXPERIENCE: ("event", "summary"),
        MemoryKind.KNOWLEDGE: ("statement", "claim"),
        MemoryKind.EMOTION: ("expression", "emotion", "label"),
    }[kind]
    for key in keys:
        value = payload.get(key)
        if _nonblank_string(value):
            return str(value).strip()
    return ""


def _semantic_facets(*, kind: MemoryKind, facets: Mapping[str, Any]) -> list[str]:
    values = {
        {
            MemoryKind.EXPERIENCE: "lifeEvent",
            MemoryKind.KNOWLEDGE: "knowledge",
            MemoryKind.EMOTION: "emotion",
        }[kind]
    }
    if _facet_entries(facets, "people") or _facet_entries(facets, "relationships"):
        values.add("relationship")
    mappings = {
        "emotions": "emotion",
        "values": "value",
        "personality": "personality",
        "habits": "habit",
        "goals": "goal",
        "identity": "identity",
        "reflections": "reflection",
    }
    for facet_name, semantic_name in mappings.items():
        if _facet_entries(facets, facet_name):
            values.add(semantic_name)
    return [name for name in OWNER_TRUTH_SEMANTIC_FACETS if name in values]


def _semantic_entities(facets: Mapping[str, Any]) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    for facet_name, entity_type in (("people", "person"), ("places", "place")):
        for entry in _facet_entries(facets, facet_name):
            entities.append(
                {
                    "entityType": entity_type,
                    "name": str(entry.get("value") or "").strip(),
                    "evidenceMode": str(entry.get("evidenceMode") or "ownerStated"),
                    "confidence": float(entry.get("confidence") or 0.0),
                }
            )
    return entities


def _emotion_evidence(
    *,
    kind: MemoryKind,
    payload: Mapping[str, Any],
    facets: Mapping[str, Any],
) -> list[dict[str, Any]]:
    values = [
        {
            "emotion": str(entry.get("value") or "").strip(),
            "evidenceMode": str(entry.get("evidenceMode") or "ownerStated"),
            "confidence": float(entry.get("confidence") or 0.0),
        }
        for entry in _facet_entries(facets, "emotions")
    ]
    if kind is MemoryKind.EMOTION and not values:
        emotion = str(payload.get("emotion") or payload.get("label") or "").strip()
        if emotion:
            values.append(
                {
                    "emotion": emotion,
                    "evidenceMode": "ownerStated",
                    "confidence": float(facets.get("confidence") or 0.0),
                }
            )
    return values


def enrich_memory_payload_v4(
    *,
    kind: MemoryKind,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the V4 multi-facet form without inventing new user facts.

    The three historical ``MemoryKind`` values remain routing keys for old
    clients and database constraints. ``semantic.facets`` is the non-exclusive
    classification used by person, graph and biography projections.
    """

    result = dict(payload)
    if kind is MemoryKind.EXPERIENCE:
        event = str(result.get("event") or result.get("summary") or "").strip()
        result["event"] = event
        time_value = result.get("time")
        if not isinstance(time_value, Mapping):
            time_value = {"start": None, "end": None, "precision": "unknown"}
        result["time"] = dict(time_value)
    elif kind is MemoryKind.KNOWLEDGE:
        result["statement"] = str(
            result.get("statement") or result.get("claim") or ""
        ).strip()
        result["knowledgeType"] = str(
            result.get("knowledgeType") or "personal_experience"
        ).strip()
        domains = result.get("domains")
        result["domains"] = list(domains) if isinstance(domains, (list, tuple)) else []
    else:
        emotion = str(result.get("emotion") or result.get("label") or "").strip()
        expression = str(
            result.get("expression") or result.get("label") or emotion
        ).strip()
        result["emotion"] = emotion
        result["expression"] = expression

    raw_facets = result.get("facets")
    facets = dict(raw_facets) if isinstance(raw_facets, Mapping) else {}
    for facet_name in OWNER_TRUTH_FACET_NAMES:
        entries = facets.get(facet_name)
        facets[facet_name] = list(entries) if isinstance(entries, (list, tuple)) else []
    confidence = _confidence(facets.get("confidence"))
    facets["confidence"] = confidence if confidence is not None else 0.0
    result["facets"] = facets

    narrative = _primary_text(kind=kind, payload=result)
    title = narrative.rstrip("。！？!?；;")[:72]
    time_value = result.get("time") if kind is MemoryKind.EXPERIENCE else None
    result["semantic"] = {
        "primaryKind": {
            MemoryKind.EXPERIENCE: "lifeEvent",
            MemoryKind.KNOWLEDGE: "knowledge",
            MemoryKind.EMOTION: "emotion",
        }[kind],
        "facets": _semantic_facets(kind=kind, facets=facets),
        "title": title,
        "narrative": narrative,
        "eventTime": dict(time_value) if isinstance(time_value, Mapping) else None,
        "entities": _semantic_entities(facets),
        "emotionEvidence": _emotion_evidence(
            kind=kind,
            payload=result,
            facets=facets,
        ),
    }
    return result


def canonicalize_memory_payload(
    *,
    kind: MemoryKind,
    payload: Mapping[str, Any],
    schema_version: str,
) -> dict[str, Any]:
    """Canonicalize a payload before it crosses an authority write boundary.

    V4 keeps a derived ``semantic`` envelope beside the reviewed fields. An
    Owner correction can change the primary text or facets, so the envelope
    must be rebuilt before the corrected value is hashed and persisted.
    Earlier schemas remain compatible and are returned as normalized maps.
    """

    normalized = dict(payload)
    if str(schema_version or "").strip() == OWNER_TRUTH_SCHEMA_VERSION_V4:
        return enrich_memory_payload_v4(kind=kind, payload=normalized)
    return normalized


def _validate_v4_memory_payload(
    *,
    kind: MemoryKind,
    payload: Mapping[str, Any],
) -> OntologyValidation:
    typed = _validate_v3_memory_payload(kind=kind, payload=payload)
    if not typed.accepted:
        return typed
    facets = validate_memory_facets(
        payload.get("facets"),
        facet_names=OWNER_TRUTH_FACET_NAMES,
    )
    if not facets.accepted:
        return facets
    semantic = payload.get("semantic")
    if not isinstance(semantic, Mapping):
        return OntologyValidation(False, False, "invalidSemanticMemory", "semantic")
    expected_semantic = enrich_memory_payload_v4(kind=kind, payload=payload)["semantic"]
    if dict(semantic) != expected_semantic:
        return OntologyValidation(False, False, "inconsistentSemanticProjection", "semantic")
    primary_kind = str(semantic.get("primaryKind") or "").strip()
    semantic_facets = semantic.get("facets")
    if primary_kind not in OWNER_TRUTH_SEMANTIC_FACETS:
        return OntologyValidation(False, False, "invalidSemanticPrimaryKind", "semantic.primaryKind")
    if (
        not isinstance(semantic_facets, list)
        or not semantic_facets
        or any(value not in OWNER_TRUTH_SEMANTIC_FACETS for value in semantic_facets)
        or len(set(semantic_facets)) != len(semantic_facets)
        or primary_kind not in semantic_facets
    ):
        return OntologyValidation(False, False, "invalidSemanticFacets", "semantic.facets")
    for field in ("title", "narrative"):
        if not _nonblank_string(semantic.get(field)):
            return OntologyValidation(False, False, "invalidSemanticText", f"semantic.{field}")
    if not isinstance(semantic.get("entities"), list) or not isinstance(
        semantic.get("emotionEvidence"), list
    ):
        return OntologyValidation(False, False, "invalidSemanticEvidence", "semantic")
    return OntologyValidation(True, False, "accepted")


__all__ = [
    "MEMORY_ONTOLOGY_V1",
    "OWNER_TRUTH_CURRENT_SCHEMA_VERSION",
    "OWNER_TRUTH_BASE_FACET_NAMES",
    "OWNER_TRUTH_EXTENDED_FACET_NAMES",
    "OWNER_TRUTH_FACET_EVIDENCE_MODES",
    "OWNER_TRUTH_FACET_NAMES",
    "OWNER_TRUTH_SEMANTIC_FACETS",
    "OWNER_TRUTH_SCHEMA_VERSION",
    "OWNER_TRUTH_SCHEMA_VERSION_V2",
    "OWNER_TRUTH_SCHEMA_VERSION_V3",
    "OWNER_TRUTH_SCHEMA_VERSION_V4",
    "MemoryOntologyDefinition",
    "OntologyValidation",
    "canonicalize_memory_payload",
    "empty_memory_facets",
    "enrich_memory_payload_v4",
    "flatten_memory_facets",
    "validate_memory_facets",
    "validate_memory_payload",
]
