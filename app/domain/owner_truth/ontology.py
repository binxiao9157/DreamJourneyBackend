"""Owner Truth V1 memory ontology and schema quarantine policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from .contracts import MemoryKind


OWNER_TRUTH_SCHEMA_VERSION = "owner-truth-v1"


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


def validate_memory_payload(
    *,
    kind: MemoryKind,
    payload: Mapping[str, Any],
    schema_version: str,
) -> OntologyValidation:
    """Validate known V1 payloads and quarantine all unknown schema versions.

    Quarantine is deliberate: a future writer must not silently coerce a
    payload produced under an unknown ontology into an authoritative memory.
    """

    if str(schema_version or "").strip() != OWNER_TRUTH_SCHEMA_VERSION:
        return OntologyValidation(
            accepted=False,
            quarantined=True,
            code="unknownSchemaVersion",
        )
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
    return OntologyValidation(accepted=True, quarantined=False, code="accepted")
