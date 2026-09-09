"""Owner Truth memory ontology and schema quarantine policy.

V2 extends the confirmed memory payload with reviewable facets. Facets are
descriptive evidence only: authorization code must continue to use the Vault,
principal and Grant contracts rather than relationship-shaped memory data.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Mapping, Optional

from .contracts import MemoryKind


OWNER_TRUTH_SCHEMA_VERSION = "owner-truth-v1"
OWNER_TRUTH_SCHEMA_VERSION_V2 = "owner-truth-v2"
OWNER_TRUTH_SCHEMA_VERSION_V3 = "owner-truth-v3"
OWNER_TRUTH_SCHEMA_VERSION_V4 = "owner-truth-v4"
OWNER_TRUTH_SCHEMA_VERSION_V5 = "owner-truth-v5"
OWNER_TRUTH_CURRENT_SCHEMA_VERSION = OWNER_TRUTH_SCHEMA_VERSION_V5
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
OWNER_TRUTH_MEMORY_DIMENSIONS = (
    "identity",
    "lifeEvents",
    "relationships",
    "knowledgeSkills",
    "preferences",
    "habits",
    "emotions",
    "values",
    "traits",
    "goals",
    "other",
)
OWNER_TRUTH_FACT_TYPES = (
    "attribute",
    "event",
    "relation",
    "knowledge",
    "preference",
    "habit",
    "affect",
    "value",
    "traitReport",
    "goal",
    "other",
)
OWNER_TRUTH_PROVENANCE_MODES = (
    "selfReport",
    "familyReport",
    "documented",
    "observed",
    "inferred",
    "unknown",
)
_FACT_TYPE_DEFAULTS = {
    MemoryKind.EXPERIENCE: ("event", "lifeEvents", "occurred"),
    MemoryKind.KNOWLEDGE: ("knowledge", "knowledgeSkills", "states"),
    MemoryKind.EMOTION: ("affect", "emotions", "felt"),
}
_FACT_TYPE_DIMENSION = {
    "attribute": "identity",
    "event": "lifeEvents",
    "relation": "relationships",
    "knowledge": "knowledgeSkills",
    "preference": "preferences",
    "habit": "habits",
    "affect": "emotions",
    "value": "values",
    "traitReport": "traits",
    "goal": "goals",
    "other": "other",
}
_FACT_TYPE_PREDICATE = {
    "attribute": "hasAttribute",
    "event": "occurred",
    "relation": "relatesTo",
    "knowledge": "states",
    "preference": "prefers",
    "habit": "practices",
    "affect": "felt",
    "value": "values",
    "traitReport": "reportedTrait",
    "goal": "intends",
    "other": "states",
}
_V5_ALLOWED_FACT_TYPES = {
    MemoryKind.EXPERIENCE: {
        "attribute",
        "event",
        "relation",
        "preference",
        "habit",
        "value",
        "traitReport",
        "goal",
        "other",
    },
    MemoryKind.KNOWLEDGE: {
        "attribute",
        "knowledge",
        "preference",
        "habit",
        "value",
        "traitReport",
        "goal",
        "other",
    },
    MemoryKind.EMOTION: {"affect", "other"},
}
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
        OWNER_TRUTH_SCHEMA_VERSION_V5,
    }:
        return OntologyValidation(
            accepted=False,
            quarantined=True,
            code="unknownSchemaVersion",
        )
    if normalized_schema == OWNER_TRUTH_SCHEMA_VERSION_V4:
        return _validate_v4_memory_payload(kind=kind, payload=payload)
    if normalized_schema == OWNER_TRUTH_SCHEMA_VERSION_V5:
        return _validate_v5_memory_payload(kind=kind, payload=payload)
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


def _optional_text(value: Any, *, maximum: int = 256) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized[:maximum] or None


def _v5_time(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    precision = str(raw.get("precision") or "unknown").strip()
    if precision not in {"exact", "day", "month", "year", "approximate", "unknown"}:
        precision = "unknown"
    return {
        "start": _optional_text(raw.get("start"), maximum=64),
        "end": _optional_text(raw.get("end"), maximum=64),
        "precision": precision,
        "expression": _optional_text(raw.get("expression"), maximum=256),
    }


def _v5_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str):
        label = _optional_text(value)
        return {"entityId": None, "label": label, "category": None} if label else None
    if not isinstance(value, Mapping):
        return None
    entity_id = _optional_text(value.get("entityId"), maximum=160)
    label = _optional_text(value.get("label"))
    category = _optional_text(value.get("category"), maximum=80)
    if entity_id is None and label is None and category is None:
        return None
    return {"entityId": entity_id, "label": label, "category": category}


def _v5_dimensions(
    *,
    kind: MemoryKind,
    fact_type: str,
    payload: Mapping[str, Any],
) -> list[str]:
    requested = payload.get("dimensions")
    dimensions = [
        str(item).strip()
        for item in requested
        if isinstance(item, str) and str(item).strip() in OWNER_TRUTH_MEMORY_DIMENSIONS
    ] if isinstance(requested, (list, tuple)) else []
    semantic = payload.get("semantic")
    facets = semantic.get("facets") if isinstance(semantic, Mapping) else []
    facet_mapping = {
        "identity": "identity",
        "lifeEvent": "lifeEvents",
        "relationship": "relationships",
        "knowledge": "knowledgeSkills",
        "emotion": "emotions",
        "value": "values",
        "personality": "traits",
        "habit": "habits",
        "goal": "goals",
    }
    if isinstance(facets, (list, tuple)):
        dimensions.extend(
            facet_mapping[str(value)]
            for value in facets
            if str(value) in facet_mapping
        )
    dimensions.append(_FACT_TYPE_DIMENSION.get(fact_type, _FACT_TYPE_DEFAULTS[kind][1]))
    return [
        dimension
        for dimension in OWNER_TRUTH_MEMORY_DIMENSIONS
        if dimension in set(dimensions)
    ]


def _v5_provenance(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    mode = str(raw.get("mode") or "unknown").strip()
    if mode not in OWNER_TRUTH_PROVENANCE_MODES:
        mode = "unknown"
    raw_refs = raw.get("evidenceRefs")
    refs: list[dict[str, Any]] = []
    if isinstance(raw_refs, (list, tuple)):
        for reference in raw_refs:
            if not isinstance(reference, Mapping):
                continue
            normalized = {
                "sourceId": _optional_text(reference.get("sourceId"), maximum=160),
                "sourceVersion": reference.get("sourceVersion"),
                "turnId": _optional_text(reference.get("turnId"), maximum=160),
                "relation": _optional_text(reference.get("relation"), maximum=32) or "supports",
            }
            if normalized["sourceId"]:
                refs.append(normalized)
    return {
        "mode": mode,
        "speakerPersonId": _optional_text(raw.get("speakerPersonId"), maximum=160),
        "contributorAccountId": _optional_text(raw.get("contributorAccountId"), maximum=160),
        "evidenceRefs": refs,
    }


def _v5_affect(
    *,
    kind: MemoryKind,
    payload: Mapping[str, Any],
    statement: str,
) -> dict[str, Any] | None:
    """Keep affect attribution explicit instead of treating an emotion as a trait.

    The normalizer deliberately leaves unknown participants and triggers as
    ``None``.  It never derives them from names or narrative phrasing.
    """

    if kind is not MemoryKind.EMOTION:
        return None
    raw = payload.get("affect")
    raw = raw if isinstance(raw, Mapping) else {}
    return {
        "experiencer": _v5_object(raw.get("experiencer")),
        "target": _v5_object(raw.get("target") or payload.get("targetPersonaId")),
        "trigger": _optional_text(raw.get("trigger") or payload.get("trigger")),
        "emotionExpression": _optional_text(
            raw.get("emotionExpression") or payload.get("expression") or statement
        ),
        "reporter": _v5_object(raw.get("reporter")),
    }


_PREFERENCE_NEGATION = re.compile(r"(?:不喜欢|不爱|讨厌|厌恶)")
_PREFERENCE_AFFIRMATION = re.compile(r"(?:最喜欢|最爱|喜欢|热爱|爱好|爱吃)")
_PREFERENCE_SUPERLATIVE = re.compile(r"(?:最喜欢|最爱|最钟爱)")


def _statement_preference_qualifiers(
    *,
    fact_type: str,
    statement: str,
    supplied_polarity: str,
    supplied_strength: str | None,
    supplied_superlative: bool,
) -> tuple[str, str | None, bool]:
    """Regenerate obvious preference semantics after an Owner edits text.

    Candidate extractors may provide a typed envelope, but a review edit is a
    new fact assertion.  Persisting ``不喜欢`` together with a stale positive
    polarity makes the single formal authority internally contradictory.  The
    small lexical rule below is intentionally limited to unambiguous Chinese
    preference wording; all other fact types retain their reviewed structure.
    """

    if fact_type != "preference":
        return supplied_polarity, supplied_strength, supplied_superlative
    if _PREFERENCE_NEGATION.search(statement):
        return "negative", supplied_strength, False
    if _PREFERENCE_AFFIRMATION.search(statement):
        if _PREFERENCE_SUPERLATIVE.search(statement):
            return "positive", supplied_strength or "最喜欢", True
        return "positive", supplied_strength, supplied_superlative
    return supplied_polarity, supplied_strength, supplied_superlative


def enrich_memory_payload_v5(
    *,
    kind: MemoryKind,
    payload: Mapping[str, Any],
    provenance: Mapping[str, Any] | None = None,
    memory_subject_id: str | None = None,
    claim_subject_id: str | None = None,
) -> dict[str, Any]:
    """Build the B-memory typed envelope without promoting or embellishing facts.

    V5 deliberately retains the V4 typed fields and semantic envelope so older
    readers remain useful during the additive migration. The extra fields are
    only structural descriptions of the same reviewed statement; they do not
    create a second authoritative fact store.
    """

    result = enrich_memory_payload_v4(kind=kind, payload=payload)
    default_fact_type, _default_dimension, default_predicate = _FACT_TYPE_DEFAULTS[kind]
    requested_fact_type = str(result.get("factType") or default_fact_type).strip()
    # Preserve an unsupported extractor type so validation can reject it.  A
    # silent fallback to ``other`` would make malformed model output look like
    # a reviewed user fact and erase the operator's reason to investigate it.
    fact_type = requested_fact_type
    primary = _primary_text(kind=kind, payload=result)
    raw_qualifiers = result.get("qualifiers")
    qualifiers = raw_qualifiers if isinstance(raw_qualifiers, Mapping) else {}
    raw_time = qualifiers.get("validTime") if isinstance(qualifiers, Mapping) else None
    if raw_time is None:
        raw_time = result.get("time")
    raw_place = qualifiers.get("place") if isinstance(qualifiers, Mapping) else None
    if raw_place is None:
        raw_place = result.get("location")
    polarity = str(qualifiers.get("polarity") or "unknown").strip()
    if polarity not in {"positive", "negative", "neutral", "unknown"}:
        polarity = "unknown"
    supplied_strength = _optional_text(
        qualifiers.get("strengthExpression"), maximum=256
    )
    supplied_superlative = (
        qualifiers.get("superlativeAsserted")
        if isinstance(qualifiers.get("superlativeAsserted"), bool)
        else False
    )
    polarity, strength_expression, superlative_asserted = _statement_preference_qualifiers(
        fact_type=fact_type,
        statement=primary,
        supplied_polarity=polarity,
        supplied_strength=supplied_strength,
        supplied_superlative=supplied_superlative,
    )
    current_applicability = str(
        qualifiers.get("currentApplicability") or "unknown"
    ).strip()
    if current_applicability not in {"current", "historical", "unknown"}:
        current_applicability = "unknown"
    source_provenance = provenance if provenance is not None else result.get("provenance")
    result.update(
        {
            "factType": fact_type,
            "dimensions": _v5_dimensions(
                kind=kind,
                fact_type=fact_type,
                payload=result,
            ),
            "statement": primary,
            "predicate": _optional_text(result.get("predicate"), maximum=80)
            or _FACT_TYPE_PREDICATE.get(fact_type, default_predicate),
            "object": _v5_object(result.get("object")),
            "qualifiers": {
                "polarity": polarity,
                "strengthExpression": strength_expression,
                "superlativeAsserted": superlative_asserted,
                "currentApplicability": current_applicability,
                "validTime": _v5_time(raw_time),
                "place": _v5_object(raw_place),
                "scenario": _optional_text(qualifiers.get("scenario"), maximum=256),
            },
            "affect": _v5_affect(kind=kind, payload=result, statement=primary),
            "provenance": _v5_provenance(source_provenance),
            "memorySubjectId": _optional_text(
                memory_subject_id if memory_subject_id is not None else result.get("memorySubjectId"),
                maximum=160,
            ),
            "claimSubjectId": _optional_text(
                claim_subject_id if claim_subject_id is not None else result.get("claimSubjectId"),
                maximum=160,
            ),
        }
    )
    return result


def canonicalize_memory_payload(
    *,
    kind: MemoryKind,
    payload: Mapping[str, Any],
    schema_version: str,
) -> dict[str, Any]:
    """Canonicalize a payload before it crosses an authority write boundary.

    V4 keeps a derived ``semantic`` envelope beside the reviewed fields. V5
    keeps that envelope and adds a typed fact/provenance view. An
    Owner correction can change the primary text or facets, so the envelope
    must be rebuilt before the corrected value is hashed and persisted.
    Earlier schemas remain compatible and are returned as normalized maps.
    """

    normalized = dict(payload)
    if str(schema_version or "").strip() == OWNER_TRUTH_SCHEMA_VERSION_V5:
        return enrich_memory_payload_v5(kind=kind, payload=normalized)
    if str(schema_version or "").strip() == OWNER_TRUTH_SCHEMA_VERSION_V4:
        return enrich_memory_payload_v4(kind=kind, payload=normalized)
    return normalized


_OWNER_CORRECTION_YEAR = re.compile(r"(?<!\d)(?P<year>[12]\d{3})\s*年")
_OWNER_CORRECTION_EMOTIONS = (
    "开心",
    "高兴",
    "快乐",
    "难过",
    "伤心",
    "悲伤",
    "愤怒",
    "焦虑",
    "紧张",
    "害怕",
    "想念",
    "怀念",
    "欣慰",
    "遗憾",
    "失落",
)


def _owner_correction_time(statement: str) -> dict[str, Any]:
    """Extract only an explicit year from an Owner replacement assertion."""

    match = _OWNER_CORRECTION_YEAR.search(statement)
    if match is None:
        return {"start": None, "end": None, "precision": "unknown", "expression": None}
    year = match.group("year")
    return {
        "start": year,
        "end": year,
        "precision": "year",
        "expression": f"{year}年",
    }


def _owner_correction_fact_type(*, kind: MemoryKind, statement: str) -> str:
    if kind is MemoryKind.EMOTION:
        return "affect"
    if _PREFERENCE_NEGATION.search(statement) or _PREFERENCE_AFFIRMATION.search(statement):
        return "preference"
    return _FACT_TYPE_DEFAULTS[kind][0]


def _owner_correction_emotion_label(statement: str) -> str:
    for value in _OWNER_CORRECTION_EMOTIONS:
        if value in statement:
            return value
    # V3/V5 require a nonblank label. This is a structural unknown rather than
    # a claimed emotion; the owner text remains the authoritative expression.
    return "未明确"


def reextract_owner_corrected_memory_payload(
    *,
    kind: MemoryKind,
    source_payload: Mapping[str, Any],
    corrected_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Rebuild a V5 correction from the Owner's changed assertion.

    Historical review clients copied an entire Candidate envelope and changed
    only its primary text. Treating that copied envelope as authority can
    retain stale people, time, polarity, degree, and object fields. This helper
    starts from the replacement assertion, re-extracts a minimal typed value,
    and retains facets only when the request explicitly changed them. It makes
    no external-model call, so preview and terminal activation use one result.
    """

    source = canonicalize_memory_payload(
        kind=kind,
        payload=source_payload,
        schema_version=OWNER_TRUTH_SCHEMA_VERSION_V5,
    )
    primary = _primary_text(kind=kind, payload=corrected_payload)
    if not primary:
        # Preserve the normal validation error for blank or malformed edits.
        return dict(corrected_payload)
    if primary == _primary_text(kind=kind, payload=source):
        # A facet-only correction leaves the underlying assertion unchanged.
        return canonicalize_memory_payload(
            kind=kind,
            payload=corrected_payload,
            schema_version=OWNER_TRUTH_SCHEMA_VERSION_V5,
        )

    time_value = _owner_correction_time(primary)
    fact_type = _owner_correction_fact_type(kind=kind, statement=primary)
    rebuilt: dict[str, Any] = {
        "facets": empty_memory_facets(confidence=1.0),
        "factType": fact_type,
        "object": None,
        "qualifiers": {
            "polarity": "unknown",
            "strengthExpression": None,
            "superlativeAsserted": False,
            "currentApplicability": "unknown",
            "validTime": time_value,
            "place": None,
            "scenario": None,
        },
    }
    if kind is MemoryKind.EXPERIENCE:
        rebuilt.update(
            {
                "event": primary,
                "time": time_value,
                "location": None,
                "participants": [],
                "actions": [],
                "outcome": None,
            }
        )
    elif kind is MemoryKind.KNOWLEDGE:
        rebuilt.update(
            {
                "statement": primary,
                "knowledgeType": (
                    "personal_preference" if fact_type == "preference" else "personal_experience"
                ),
                "domains": [],
                "applicability": None,
                "exceptions": [],
                "learnedFrom": None,
            }
        )
    else:
        rebuilt.update(
            {
                "emotion": _owner_correction_emotion_label(primary),
                "expression": primary,
                "trigger": None,
                "targetPersonaId": None,
                "time": None,
                "intensity": None,
                "affect": {
                    "experiencer": None,
                    "target": None,
                    "trigger": None,
                    "emotionExpression": primary,
                    "reporter": None,
                },
            }
        )

    # Source provenance and subject binding describe authority, not the text's
    # semantics. Preserve those fields while rebuilding all derived semantics.
    for field in ("provenance", "memorySubjectId", "claimSubjectId"):
        if field in source:
            rebuilt[field] = source[field]

    # The single-item editor may explicitly alter facets. Unchanged copied
    # facets are discarded; otherwise they would look like new owner claims.
    if "facets" in corrected_payload and corrected_payload.get("facets") != source.get("facets"):
        rebuilt["facets"] = corrected_payload.get("facets")

    return enrich_memory_payload_v5(kind=kind, payload=rebuilt)


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


def _validate_v5_memory_payload(
    *,
    kind: MemoryKind,
    payload: Mapping[str, Any],
) -> OntologyValidation:
    typed = _validate_v4_memory_payload(kind=kind, payload=payload)
    if not typed.accepted:
        return typed
    fact_type = str(payload.get("factType") or "").strip()
    if fact_type not in _V5_ALLOWED_FACT_TYPES[kind]:
        return OntologyValidation(False, False, "invalidFactType", "factType")
    dimensions = payload.get("dimensions")
    if (
        not isinstance(dimensions, list)
        or not dimensions
        or any(value not in OWNER_TRUTH_MEMORY_DIMENSIONS for value in dimensions)
        or len(set(dimensions)) != len(dimensions)
        or _FACT_TYPE_DIMENSION[fact_type] not in dimensions
    ):
        return OntologyValidation(False, False, "invalidMemoryDimensions", "dimensions")
    if not _nonblank_string(payload.get("statement")):
        return OntologyValidation(False, False, "invalidFactStatement", "statement")
    if not _nonblank_string(payload.get("predicate")):
        return OntologyValidation(False, False, "invalidFactPredicate", "predicate")
    qualifiers = payload.get("qualifiers")
    if not isinstance(qualifiers, Mapping):
        return OntologyValidation(False, False, "invalidFactQualifiers", "qualifiers")
    if qualifiers.get("polarity") not in {"positive", "negative", "neutral", "unknown"}:
        return OntologyValidation(False, False, "invalidFactPolarity", "qualifiers.polarity")
    if qualifiers.get("currentApplicability") not in {"current", "historical", "unknown"}:
        return OntologyValidation(
            False,
            False,
            "invalidCurrentApplicability",
            "qualifiers.currentApplicability",
        )
    if not isinstance(qualifiers.get("superlativeAsserted"), bool):
        return OntologyValidation(
            False,
            False,
            "invalidSuperlativeAssertion",
            "qualifiers.superlativeAsserted",
        )
    valid_time = qualifiers.get("validTime")
    if not isinstance(valid_time, Mapping):
        return OntologyValidation(False, False, "invalidFactTime", "qualifiers.validTime")
    if valid_time.get("precision") not in {
        "exact",
        "day",
        "month",
        "year",
        "approximate",
        "unknown",
    }:
        return OntologyValidation(False, False, "invalidTimePrecision", "qualifiers.validTime.precision")
    provenance = payload.get("provenance")
    if not isinstance(provenance, Mapping):
        return OntologyValidation(False, False, "invalidFactProvenance", "provenance")
    if provenance.get("mode") not in OWNER_TRUTH_PROVENANCE_MODES:
        return OntologyValidation(False, False, "invalidProvenanceMode", "provenance.mode")
    refs = provenance.get("evidenceRefs")
    if not isinstance(refs, list) or any(
        not isinstance(reference, Mapping)
        or not _nonblank_string(reference.get("sourceId"))
        or reference.get("relation") not in {"supports", "contradicts", "references"}
        for reference in refs
    ):
        return OntologyValidation(False, False, "invalidFactEvidenceRefs", "provenance.evidenceRefs")
    affect = payload.get("affect")
    if fact_type == "affect":
        if not isinstance(affect, Mapping) or not _nonblank_string(
            affect.get("emotionExpression")
        ):
            return OntologyValidation(False, False, "invalidAffectAttribution", "affect")
    elif affect is not None:
        return OntologyValidation(False, False, "invalidAffectAttribution", "affect")
    for field in ("memorySubjectId", "claimSubjectId"):
        value = payload.get(field)
        if value is not None and not _nonblank_string(value):
            return OntologyValidation(False, False, "invalidFactSubject", field)
    expected = enrich_memory_payload_v5(kind=kind, payload=payload)
    for field in (
        "factType",
        "dimensions",
        "statement",
        "predicate",
        "object",
        "qualifiers",
        "affect",
        "provenance",
        "memorySubjectId",
        "claimSubjectId",
    ):
        if payload.get(field) != expected.get(field):
            return OntologyValidation(False, False, "inconsistentTypedFact", field)
    return OntologyValidation(True, False, "accepted")


__all__ = [
    "MEMORY_ONTOLOGY_V1",
    "OWNER_TRUTH_CURRENT_SCHEMA_VERSION",
    "OWNER_TRUTH_BASE_FACET_NAMES",
    "OWNER_TRUTH_EXTENDED_FACET_NAMES",
    "OWNER_TRUTH_FACET_EVIDENCE_MODES",
    "OWNER_TRUTH_FACET_NAMES",
    "OWNER_TRUTH_FACT_TYPES",
    "OWNER_TRUTH_MEMORY_DIMENSIONS",
    "OWNER_TRUTH_PROVENANCE_MODES",
    "OWNER_TRUTH_SEMANTIC_FACETS",
    "OWNER_TRUTH_SCHEMA_VERSION",
    "OWNER_TRUTH_SCHEMA_VERSION_V2",
    "OWNER_TRUTH_SCHEMA_VERSION_V3",
    "OWNER_TRUTH_SCHEMA_VERSION_V4",
    "OWNER_TRUTH_SCHEMA_VERSION_V5",
    "MemoryOntologyDefinition",
    "OntologyValidation",
    "canonicalize_memory_payload",
    "empty_memory_facets",
    "enrich_memory_payload_v4",
    "enrich_memory_payload_v5",
    "flatten_memory_facets",
    "reextract_owner_corrected_memory_payload",
    "validate_memory_facets",
    "validate_memory_payload",
]
