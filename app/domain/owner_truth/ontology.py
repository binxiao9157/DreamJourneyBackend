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
OWNER_TRUTH_CURRENT_SCHEMA_VERSION = OWNER_TRUTH_SCHEMA_VERSION_V3
OWNER_TRUTH_FACET_NAMES = (
    "people",
    "time",
    "places",
    "relationships",
    "emotions",
    "values",
    "personality",
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


def validate_memory_facets(value: Any) -> OntologyValidation:
    if not isinstance(value, Mapping):
        return OntologyValidation(False, False, "invalidFacets", "facets")
    if _confidence(value.get("confidence")) is None:
        return OntologyValidation(False, False, "invalidFacetConfidence", "confidence")
    for facet_name in OWNER_TRUTH_FACET_NAMES:
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
    }:
        return OntologyValidation(
            accepted=False,
            quarantined=True,
            code="unknownSchemaVersion",
        )
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


__all__ = [
    "MEMORY_ONTOLOGY_V1",
    "OWNER_TRUTH_CURRENT_SCHEMA_VERSION",
    "OWNER_TRUTH_FACET_EVIDENCE_MODES",
    "OWNER_TRUTH_FACET_NAMES",
    "OWNER_TRUTH_SCHEMA_VERSION",
    "OWNER_TRUTH_SCHEMA_VERSION_V2",
    "OWNER_TRUTH_SCHEMA_VERSION_V3",
    "MemoryOntologyDefinition",
    "OntologyValidation",
    "empty_memory_facets",
    "flatten_memory_facets",
    "validate_memory_facets",
    "validate_memory_payload",
]
