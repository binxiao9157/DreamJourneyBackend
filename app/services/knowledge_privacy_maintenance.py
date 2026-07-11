from copy import deepcopy
from typing import Any, Dict

from app.services.knowledge_source_refs import source_ref_title
from app.services.knowledge_store import (
    KB_OPERATION_MUTATION,
    KNOWLEDGE_ENTITY_TYPES,
    knowledge_operation_payload_fingerprint,
    normalize_kb_mutation_v2,
)


class KnowledgePrivacyMetadataError(ValueError):
    """Historical knowledge metadata could not be canonicalized safely."""


def canonicalize_persisted_knowledge_entity(entity: Any) -> Dict[str, Any]:
    if not isinstance(entity, dict):
        raise KnowledgePrivacyMetadataError("knowledge entity must be an object")

    canonical = deepcopy(entity)
    metadata = canonical.get("privacyMetadata")
    if metadata is None:
        return canonical
    if not isinstance(metadata, dict):
        raise KnowledgePrivacyMetadataError("privacyMetadata must be an object")

    source_refs = metadata.get("sourceRefs")
    if source_refs is None:
        return canonical
    if not isinstance(source_refs, list):
        raise KnowledgePrivacyMetadataError("sourceRefs must be an array")

    canonical_refs = []
    for source_ref in source_refs:
        if not isinstance(source_ref, dict):
            raise KnowledgePrivacyMetadataError("sourceRefs entries must be objects")
        canonical_ref = deepcopy(source_ref)
        canonical_ref["title"] = source_ref_title(
            str(canonical_ref.get("kind") or "unknown")
        )
        canonical_refs.append(canonical_ref)
    metadata["sourceRefs"] = canonical_refs
    return canonical


def canonicalize_persisted_knowledge_graph(graph: Any) -> Dict[str, Any]:
    if not isinstance(graph, dict):
        raise KnowledgePrivacyMetadataError("knowledge graph must be an object")

    canonical = deepcopy(graph)
    for entity_type in KNOWLEDGE_ENTITY_TYPES:
        if entity_type not in canonical:
            continue
        entities = canonical[entity_type]
        if not isinstance(entities, list):
            raise KnowledgePrivacyMetadataError(
                f"knowledge graph {entity_type} must be an array"
            )
        canonical[entity_type] = [
            canonicalize_persisted_knowledge_entity(entity) for entity in entities
        ]
    return canonical


def canonicalize_persisted_knowledge_mutation(mutation: Any) -> Dict[str, Any]:
    if not isinstance(mutation, dict):
        raise KnowledgePrivacyMetadataError("knowledge mutation must be an object")
    try:
        normalized = normalize_kb_mutation_v2(
            mutation.get("upserts", {}),
            mutation.get("tombstones", []),
        )
    except ValueError as exc:
        raise KnowledgePrivacyMetadataError(
            "knowledge mutation is not a valid V2 payload"
        ) from exc
    upserts = mutation.get("upserts", {})
    for entities in upserts.values():
        for entity in entities:
            canonicalize_persisted_knowledge_entity(entity)
    return normalized


def canonicalize_persisted_receipt_result(
    result: Any,
    *,
    require_v2_mutation: bool,
) -> Dict[str, Any]:
    if not isinstance(result, dict):
        raise KnowledgePrivacyMetadataError("receipt result must be an object")

    canonical = deepcopy(result)
    if "graph" in canonical:
        canonical["graph"] = canonicalize_persisted_knowledge_graph(
            canonical["graph"]
        )

    mutation = canonical.get("mutation")
    result_schema_version = canonical.get("mutationSchemaVersion")
    has_v2_mutation = require_v2_mutation or result_schema_version == 2
    if has_v2_mutation:
        if mutation is None:
            raise KnowledgePrivacyMetadataError(
                "V2 receipt result must include a mutation"
            )
        canonical["mutation"] = canonicalize_persisted_knowledge_mutation(mutation)
    return canonical


def canonical_receipt_payload_hash(
    *,
    operation_kind: str,
    schema_version: int,
    canonical_result: Dict[str, Any],
    current_payload_hash: str,
) -> str:
    if operation_kind != KB_OPERATION_MUTATION or schema_version != 2:
        return current_payload_hash
    mutation = canonical_result.get("mutation")
    if not isinstance(mutation, dict):
        raise KnowledgePrivacyMetadataError(
            "V2 mutation receipt must include a canonical mutation"
        )
    return knowledge_operation_payload_fingerprint(
        KB_OPERATION_MUTATION,
        2,
        mutation,
    )
